"""建模任务路由模块：任务创建、执行与取消（配置与探测见 config_router，审批见 approval_router）。"""

import asyncio
import os

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.workflow import MathModelWorkFlow
from app.schemas.enums import CompTemplate, FormatOutPut
from app.schemas.request import ExampleRequest, Problem
from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.utils.common_utils import (
    create_task_id,
    create_work_dir,
    get_current_files,
    md_2_docx,
)
from app.utils.log_util import logger

router = APIRouter()

# 任务注册表: task_id -> (asyncio.Task, asyncio.Event)
_active_tasks: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}

# 上传附件的扩展名白名单与单文件大小上限
UPLOAD_ALLOWED_EXTS = {".csv", ".xlsx", ".xls", ".txt", ".json", ".pdf"}
UPLOAD_MAX_BYTES = 50 * 1024 * 1024


@router.post("/example")
async def exampleModeling(
    example_request: ExampleRequest,
    background_tasks: BackgroundTasks,
):
    task_id = create_task_id()
    work_dir = create_work_dir(task_id)
    example_dir = os.path.join("app", "example", "example", example_request.source)
    with open(os.path.join(example_dir, "questions.txt"), encoding="utf-8") as f:
        ques_all = f.read()

    current_files = get_current_files(example_dir, "data")
    for file in current_files:
        src_file = os.path.join(example_dir, file)
        dst_file = os.path.join(work_dir, file)
        with open(src_file, "rb") as src, open(dst_file, "wb") as dst:
            dst.write(src.read())
    # 存储任务ID
    await redis_manager.set(f"task_id:{task_id}", task_id)

    logger.info(f"Adding background task for task_id: {task_id}")
    # 将任务添加到后台执行
    background_tasks.add_task(
        run_modeling_task_async,
        task_id,
        ques_all,
        CompTemplate.CHINA,
        FormatOutPut.Markdown,
    )
    return {"task_id": task_id, "status": "processing"}


@router.post("/modeling")
async def modeling(
    background_tasks: BackgroundTasks,
    ques_all: str = Form(...),  # 从表单获取
    comp_template: CompTemplate = Form(...),  # 从表单获取
    format_output: FormatOutPut = Form(...),  # 从表单获取
    files: list[UploadFile] = File(default=None),
):
    task_id = create_task_id()
    work_dir = create_work_dir(task_id)

    # 如果有上传文件，保存文件
    if files:
        logger.info(f"开始处理上传的文件，工作目录: {work_dir}")
        for file in files:
            # 统一斜杠后取 basename；若原始名仍含路径成分则视为目录穿越，整体拒绝
            raw_name = (file.filename or "").strip()
            filename = os.path.basename(raw_name.replace("\\", "/"))
            if not raw_name or filename != raw_name or filename in (".", ".."):
                raise HTTPException(
                    status_code=400, detail=f"非法文件名: {file.filename!r}"
                )

            ext = os.path.splitext(filename)[1].lower()
            if ext not in UPLOAD_ALLOWED_EXTS:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的文件类型: {filename}，"
                    f"允许: {', '.join(sorted(UPLOAD_ALLOWED_EXTS))}",
                )

            content = await file.read()
            if not content:
                logger.warning(f"文件 {filename} 内容为空")
                continue
            if len(content) > UPLOAD_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"文件 {filename} 超过单文件大小上限 "
                    f"{UPLOAD_MAX_BYTES // (1024 * 1024)}MB",
                )

            data_file_path = os.path.join(work_dir, filename)
            logger.info(f"保存文件: {filename} -> {data_file_path}")
            try:
                with open(data_file_path, "wb") as f:
                    f.write(content)
            except OSError as e:
                logger.error(f"保存文件 {filename} 失败: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"保存文件 {filename} 失败"
                ) from e
            logger.info(f"成功保存文件: {data_file_path}")
    else:
        logger.warning("没有上传文件")

    # 存储任务ID
    await redis_manager.set(f"task_id:{task_id}", task_id)

    logger.info(f"Adding background task for task_id: {task_id}")
    # 将任务添加到后台执行
    background_tasks.add_task(
        run_modeling_task_async, task_id, ques_all, comp_template, format_output
    )
    return {"task_id": task_id, "status": "processing"}


async def run_modeling_task_async(
    task_id: str,
    ques_all: str,
    comp_template: CompTemplate,
    format_output: FormatOutPut,
):
    """异步执行建模任务。

    Args:
        task_id: 任务 ID。
        ques_all: 完整题目信息。
        comp_template: 竞赛模板类型。
        format_output: 输出格式。
    """
    logger.info(f"run modeling task for task_id: {task_id}")

    problem = Problem(
        task_id=task_id,
        ques_all=ques_all,
        comp_template=comp_template,
        format_output=format_output,
    )

    # 创建取消信号
    cancel_event = asyncio.Event()

    # 发送任务开始状态
    await redis_manager.publish_message(
        task_id,
        SystemMessage(content="任务开始处理"),
    )

    # 给一个短暂的延迟，确保 WebSocket 有机会连接
    await asyncio.sleep(1)

    # 创建工作流并传入取消事件
    workflow = MathModelWorkFlow()
    workflow.cancel_event = cancel_event

    # 创建任务并注册到全局表
    task = asyncio.create_task(workflow.execute(problem))
    _active_tasks[task_id] = (task, cancel_event)

    task_completed = False
    try:
        # 设置超时时间（5 小时）
        await asyncio.wait_for(task, timeout=3600 * 5)
        task_completed = True

        # 发送任务完成状态
        await redis_manager.publish_message(
            task_id,
            SystemMessage(content="任务处理完成", type="success"),
        )
    except asyncio.CancelledError:
        logger.info(f"任务 {task_id} 被取消")
        await redis_manager.publish_message(
            task_id,
            SystemMessage(content="任务已停止", type="warning"),
        )
    except Exception as e:
        logger.error(f"任务 {task_id} 执行失败: {e}")
        await redis_manager.publish_message(
            task_id,
            SystemMessage(content=f"任务执行失败: {str(e)}", type="error"),
        )
    finally:
        # 从注册表中清理
        _active_tasks.pop(task_id, None)
        # 仅在正常完成时转换 md 为 docx
        if task_completed:
            md_2_docx(task_id)


class CancelTaskResponse(BaseModel):
    success: bool
    message: str


@router.post("/modeling/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(task_id: str):
    """取消正在运行的任务。"""
    if task_id not in _active_tasks:
        return CancelTaskResponse(
            success=False,
            message="任务不存在或已完成",
        )

    _, cancel_event = _active_tasks[task_id]
    cancel_event.set()
    logger.info(f"已发送取消信号给任务 {task_id}")

    return CancelTaskResponse(
        success=True,
        message="停止指令已发送",
    )
