"""本地代码解释器模块，通过本地 Jupyter 内核执行 Python 代码。"""

from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.matplotlib_setup import build_matplotlib_init_code
from app.tools.notebook_serializer import NotebookSerializer
import asyncio
import jupyter_client
from app.utils.log_util import logger
import os
import time
from app.services.redis_manager import redis_manager
from app.schemas.response import (
    OutputItem,
    ResultModel,
    StdErrModel,
    SystemMessage,
)

# 单次执行总时长上限与 interrupt 宽限。MILP 求解可静默运行数分钟无 iopub
# 输出，不能用"无消息间隔"判死；20260827 活锁事故（kernel 死锁不回 idle，
# 采码循环无限 continue 并阻塞事件循环）后以总时长 + kernel 存活检测兜底。
EXECUTE_TOTAL_TIMEOUT = 1800  # 秒：30 分钟，覆盖大 MILP 的正常求解时长
EXECUTE_INTERRUPT_GRACE = 120  # 秒：interrupt 后等待内核回 idle 的宽限


class LocalCodeInterpreter(BaseCodeInterpreter):
    """基于本地 Jupyter 内核的代码解释器。"""
    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ):
        super().__init__(task_id, work_dir, notebook_serializer)
        self.km, self.kc = None, None
        self.interrupt_signal = False

    async def initialize(self):
        # 本地内核一般不需异步上传文件，直接切换目录即可
        # 初始化 Jupyter 内核管理器和客户端
        logger.info("初始化本地内核")
        # 设置 UTF-8 编码环境，避免 Windows 中文环境下 GBK 编码导致的乱码问题
        kernel_env = os.environ.copy()
        kernel_env["PYTHONIOENCODING"] = "utf-8"
        kernel_env["PYTHONUTF8"] = "1"
        self.km, self.kc = jupyter_client.manager.start_new_kernel(
            kernel_name="python3", env=kernel_env
        )
        font_msg, font_type = await asyncio.to_thread(self._pre_execute_code)
        if font_msg:
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=font_msg, type=font_type),
            )

    def _pre_execute_code(self) -> tuple[str | None, str]:
        """执行 matplotlib 初始化，并解析字体加载结果供前端展示。

        Returns:
            (消息文案, SystemMessage.type)；无可用信息时文案为 None。
        """
        init_code = build_matplotlib_init_code(self.work_dir)
        execution = self.execute_code_(init_code)
        stdout = "\n".join(text for mark, text in execution if mark == "stdout")
        for line in stdout.splitlines():
            line = line.strip()
            if "中文字体已加载" in line:
                # 去掉日志前缀，前端只展示关键结论
                content = line.removeprefix("[matplotlib_setup] ").strip()
                return content, "success"
            if "未找到中文字体" in line:
                content = line.removeprefix("[matplotlib_setup] ").strip()
                return content, "warning"
        return None, "info"

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        logger.info(f"执行代码: {code}")
        #  添加代码到notebook
        self.notebook_serializer.add_code_cell_to_notebook(code)

        text_to_gpt: list[str] = []
        content_to_display: list[OutputItem] | None = []
        error_occurred: bool = False
        error_message: str = ""

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="开始执行代码"),
        )
        # 执行 Python 代码（放线程池：长求解/挂起期间事件循环与 /status、
        # 流式推送保持可用，20260827 事故中同步调用曾让后端整体失联 10+ 分钟）
        logger.info("开始在本地执行代码...")
        execution = await asyncio.to_thread(self.execute_code_, code)
        logger.info("代码执行完成，开始处理结果...")

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="代码执行完成"),
        )

        for mark, out_str in execution:
            if mark in ("stdout", "execute_result_text", "display_text"):
                text_to_gpt.append(self._truncate_text(f"[{mark}]\n{out_str}"))
                #  添加text到notebook
                content_to_display.append(
                    ResultModel(res_type="result", format="text", msg=out_str)
                )
                self.notebook_serializer.add_code_cell_output_to_notebook(out_str)

            elif mark in (
                "execute_result_png",
                "execute_result_jpeg",
                "display_png",
                "display_jpeg",
            ):
                # TODO: 视觉模型解释图像
                text_to_gpt.append(f"[{mark} 图片已生成，内容为 base64，未展示]")

                #  添加image到notebook
                if "png" in mark:
                    self.notebook_serializer.add_image_to_notebook(out_str, "image/png")
                    content_to_display.append(
                        ResultModel(res_type="result", format="png", msg=out_str)
                    )
                else:
                    self.notebook_serializer.add_image_to_notebook(
                        out_str, "image/jpeg"
                    )
                    content_to_display.append(
                        ResultModel(res_type="result", format="jpeg", msg=out_str)
                    )

            elif mark == "error":
                error_occurred = True
                error_message = self.delete_color_control_char(out_str)
                error_message = self._truncate_text(error_message)
                logger.error(f"执行错误: {error_message}")
                text_to_gpt.append(error_message)
                #  添加error到notebook
                self.notebook_serializer.add_code_cell_error_to_notebook(out_str)
                content_to_display.append(StdErrModel(msg=out_str))

        if error_occurred:
            # 错误详情已进入日志、WebSocket 和模型反思上下文；交付用 notebook
            # 只保留成功执行的单元，避免 G2 修复轮无法清除历史失败尝试。
            self.notebook_serializer.discard_last_code_cell()

        logger.info(f"text_to_gpt: {text_to_gpt}")
        combined_text = "\n".join(text_to_gpt)

        await self._push_to_websocket(content_to_display)

        return (
            combined_text,
            error_occurred,
            error_message,
        )

    def execute_code_(
        self,
        code,
        total_timeout: float | None = None,
        interrupt_grace: float | None = None,
    ) -> list[tuple[str, str]]:
        assert self.kc is not None
        assert self.km is not None
        deadline = time.monotonic() + (total_timeout or EXECUTE_TOTAL_TIMEOUT)
        grace = interrupt_grace if interrupt_grace is not None else EXECUTE_INTERRUPT_GRACE
        self.kc.execute(code)
        logger.info(f"执行代码: {code}")
        # Get the output of the code
        msg_list = []
        interrupted = False
        interrupt_deadline: float | None = None
        while True:
            now = time.monotonic()
            if self.km.is_alive() is False:
                # 内核已崩溃：重启基础设施并报错，交给 Agent 反思回路
                self._recover_dead_kernel()
                return [(
                    "error",
                    "执行失败：Jupyter 内核已崩溃（无响应）。内核已自动重启，"
                    "内存中的变量已清空，请基于工作目录中的文件重新加载数据后重试。",
                )]
            if not interrupted and (now > deadline or self.interrupt_signal):
                logger.warning("代码执行超时或收到中断信号，向内核发送 interrupt")
                self.km.interrupt_kernel()
                self.interrupt_signal = False
                interrupted = True
                interrupt_deadline = now + grace
            elif interrupted and now > (interrupt_deadline or deadline):
                self._recover_dead_kernel()
                return [(
                    "error",
                    "执行超时且中断无效（内核疑似死锁）：内核已重启，内存状态已清空。"
                    "请检查是否存在死循环或不可行的求解规模，缩小问题规模后重试。",
                )]
            try:
                iopub_msg = self.kc.get_iopub_msg(timeout=1)
                msg_list.append(iopub_msg)
                if (
                    iopub_msg["msg_type"] == "status"
                    and iopub_msg["content"].get("execution_state") == "idle"
                ):
                    break
            except Exception:
                continue

        all_output: list[tuple[str, str]] = []
        for iopub_msg in msg_list:
            if iopub_msg["msg_type"] == "stream":
                if iopub_msg["content"].get("name") == "stdout":
                    output = iopub_msg["content"]["text"]
                    all_output.append(("stdout", output))
            elif iopub_msg["msg_type"] == "execute_result":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("execute_result_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("execute_result_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("execute_result_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("execute_result_jpeg", output))
            elif iopub_msg["msg_type"] == "display_data":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("display_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("display_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("display_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("display_jpeg", output))
            elif (
                iopub_msg["msg_type"] == "error"
                and "traceback" in iopub_msg["content"]
            ):
                # TODO: 正确返回格式
                output = "\n".join(iopub_msg["content"]["traceback"])
                cleaned_output = self.delete_color_control_char(output)
                all_output.append(("error", cleaned_output))
        return all_output

    def _recover_dead_kernel(self) -> None:
        """内核死锁/崩溃后的基础设施恢复：重启内核，失败仅告警不抛出。"""
        try:
            self.restart_jupyter_kernel()
            logger.warning("Jupyter 内核已重启（此前无响应或超时）")
        except Exception as e:
            logger.error(f"内核重启失败: {e}")

    async def get_created_images(self, section: str) -> list[str]:
        """获取新创建的图片列表"""
        current_images = set()
        files = os.listdir(self.work_dir)
        for file in files:
            if file.endswith((".png", ".jpg", ".jpeg")):
                current_images.add(file)

        # 计算新增的图片
        new_images = current_images - self.last_created_images

        # 更新last_created_images为当前的图片集合
        self.last_created_images = current_images

        logger.info(f"新创建的图片列表: {new_images}")
        return list(new_images)  # 最后转换为list返回

    async def cleanup(self):
        # 关闭内核
        assert self.kc is not None
        assert self.km is not None
        self.kc.shutdown()
        logger.info("关闭内核")
        self.km.shutdown_kernel()

    def send_interrupt_signal(self):
        self.interrupt_signal = True

    def restart_jupyter_kernel(self):
        """Restart the Jupyter kernel and recreate the work directory."""
        assert self.kc is not None
        self.kc.shutdown()
        # 设置 UTF-8 编码环境，避免 Windows 中文环境下 GBK 编码导致的乱码问题
        kernel_env = os.environ.copy()
        kernel_env["PYTHONIOENCODING"] = "utf-8"
        kernel_env["PYTHONUTF8"] = "1"
        self.km, self.kc = jupyter_client.manager.start_new_kernel(
            kernel_name="python3", env=kernel_env
        )
        self.interrupt_signal = False
        self._create_work_dir()
        self._pre_execute_code()

    def _create_work_dir(self):
        """Ensure the working directory exists after a restart."""
        os.makedirs(self.work_dir, exist_ok=True)
