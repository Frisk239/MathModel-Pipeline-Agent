"""建模任务路由模块，提供任务创建、API 验证和配置管理等接口。"""

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from app.core.workflow import MathModelWorkFlow
from app.schemas.enums import CompTemplate, FormatOutPut
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.request import Problem
from app.schemas.response import SystemMessage
from app.utils.common_utils import (
    create_task_id,
    create_work_dir,
    get_current_files,
    md_2_docx,
)
import os
import asyncio
from fastapi import HTTPException
from app.schemas.request import ExampleRequest
from pydantic import BaseModel
from app.config.setting import settings, ApiType
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.base import BaseProvider, HTTP_USER_AGENT
from app.services.model_capability import get_capability, save_capability
from anthropic import AsyncAnthropic
from anthropic import BadRequestError as AnthropicBadRequestError
from openai import AsyncOpenAI, BadRequestError, RateLimitError
import requests

router = APIRouter()

# 任务注册表: task_id -> (asyncio.Task, asyncio.Event)
_active_tasks: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}

# 上传附件的扩展名白名单与单文件大小上限
UPLOAD_ALLOWED_EXTS = {".csv", ".xlsx", ".xls", ".txt", ".json", ".pdf"}
UPLOAD_MAX_BYTES = 50 * 1024 * 1024


class ValidateApiKeyRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model_id: str
    api_type: str = "openai-chat"


class ValidateOpenalexEmailRequest(BaseModel):
    email: str


class ValidateOpenalexEmailResponse(BaseModel):
    valid: bool
    message: str


class ValidateApiKeyResponse(BaseModel):
    valid: bool
    message: str


class SaveApiConfigRequest(BaseModel):
    coordinator: dict
    modeler: dict
    coder: dict
    writer: dict
    openalex_email: str
    hil_config: dict | None = None  # {autoMode, problem_split, model_selection, ...}


@router.post("/save-api-config")
async def save_api_config(request: SaveApiConfigRequest):
    """
    保存验证成功的 API 配置到 settings
    """
    try:
        # 更新各个模块的设置
        if request.coordinator:
            settings.COORDINATOR_API_KEY = request.coordinator.get("apiKey", "")
            settings.COORDINATOR_MODEL = request.coordinator.get("modelId", "")
            settings.COORDINATOR_BASE_URL = request.coordinator.get("baseUrl", "")
            if api_type := request.coordinator.get("apiType"):
                settings.COORDINATOR_API_TYPE = api_type
            if cw := request.coordinator.get("contextWindow"):
                settings.COORDINATOR_CONTEXT_WINDOW = int(cw)
            settings.COORDINATOR_REASONING_EFFORT = (
                request.coordinator.get("reasoningEffort") or None
            )
            settings.COORDINATOR_THINKING_BUDGET = (
                int(tb) if (tb := request.coordinator.get("thinkingBudget")) else None
            )
            settings.COORDINATOR_MODELS = (
                request.coordinator.get("fallbackModels") or None
            )

        if request.modeler:
            settings.MODELER_API_KEY = request.modeler.get("apiKey", "")
            settings.MODELER_MODEL = request.modeler.get("modelId", "")
            settings.MODELER_BASE_URL = request.modeler.get("baseUrl", "")
            if api_type := request.modeler.get("apiType"):
                settings.MODELER_API_TYPE = api_type
            if cw := request.modeler.get("contextWindow"):
                settings.MODELER_CONTEXT_WINDOW = int(cw)
            settings.MODELER_REASONING_EFFORT = (
                request.modeler.get("reasoningEffort") or None
            )
            settings.MODELER_THINKING_BUDGET = (
                int(tb) if (tb := request.modeler.get("thinkingBudget")) else None
            )
            settings.MODELER_MODELS = request.modeler.get("fallbackModels") or None

        if request.coder:
            settings.CODER_API_KEY = request.coder.get("apiKey", "")
            settings.CODER_MODEL = request.coder.get("modelId", "")
            settings.CODER_BASE_URL = request.coder.get("baseUrl", "")
            if api_type := request.coder.get("apiType"):
                settings.CODER_API_TYPE = api_type
            if cw := request.coder.get("contextWindow"):
                settings.CODER_CONTEXT_WINDOW = int(cw)
            settings.CODER_REASONING_EFFORT = (
                request.coder.get("reasoningEffort") or None
            )
            settings.CODER_THINKING_BUDGET = (
                int(tb) if (tb := request.coder.get("thinkingBudget")) else None
            )
            settings.CODER_MODELS = request.coder.get("fallbackModels") or None

        if request.writer:
            settings.WRITER_API_KEY = request.writer.get("apiKey", "")
            settings.WRITER_MODEL = request.writer.get("modelId", "")
            settings.WRITER_BASE_URL = request.writer.get("baseUrl", "")
            if api_type := request.writer.get("apiType"):
                settings.WRITER_API_TYPE = api_type
            if cw := request.writer.get("contextWindow"):
                settings.WRITER_CONTEXT_WINDOW = int(cw)
            settings.WRITER_REASONING_EFFORT = (
                request.writer.get("reasoningEffort") or None
            )
            settings.WRITER_THINKING_BUDGET = (
                int(tb) if (tb := request.writer.get("thinkingBudget")) else None
            )
            settings.WRITER_MODELS = request.writer.get("fallbackModels") or None

        if request.openalex_email:
            settings.OPENALEX_EMAIL = request.openalex_email

        # 人工检查点配置（AUTO_MODE 一票全关）
        if request.hil_config:
            hc = request.hil_config
            if "autoMode" in hc:
                settings.AUTO_MODE = bool(hc["autoMode"])
            for key in ("problem_split", "model_selection", "code_review", "paper_review"):
                if key in hc:
                    settings.HIL_CHECKPOINTS[key] = bool(hc[key])

        return {"success": True, "message": "配置保存成功"}
    except Exception as e:
        logger.error(f"保存配置失败: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"保存配置失败: {str(e)}"
        ) from e


@router.post("/validate-api-key", response_model=ValidateApiKeyResponse)
async def validate_api_key(request: ValidateApiKeyRequest):
    """
    验证 API Key 的有效性
    """
    try:
        provider: BaseProvider
        match request.api_type:
            case ApiType.OPENAI_RESPONSES:
                provider = OpenAIResponsesProvider()
            case ApiType.ANTHROPIC:
                provider = AnthropicProvider()
            case _:
                provider = OpenAIChatProvider()

        await provider.call(
            messages=[{"role": "user", "content": "Hi"}],
            model=request.model_id,
            api_key=request.api_key,
            base_url=request.base_url
            if request.base_url != "https://api.openai.com/v1"
            else None,
            max_tokens=1,
        )

        return ValidateApiKeyResponse(valid=True, message="✓ 模型 API 验证成功")
    except Exception as e:
        error_msg = str(e)

        # 解析不同类型的错误
        if "401" in error_msg or "Unauthorized" in error_msg:
            return ValidateApiKeyResponse(valid=False, message="✗ API Key 无效或已过期")
        elif "404" in error_msg or "Not Found" in error_msg:
            return ValidateApiKeyResponse(
                valid=False, message="✗ 模型 ID 不存在或 Base URL 错误"
            )
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            return ValidateApiKeyResponse(
                valid=False, message="✗ 请求过于频繁，请稍后再试"
            )
        elif "403" in error_msg or "Forbidden" in error_msg:
            return ValidateApiKeyResponse(
                valid=False, message="✗ API 权限不足或账户余额不足"
            )
        else:
            return ValidateApiKeyResponse(
                valid=False, message=f"✗ 验证失败: {error_msg[:50]}..."
            )


@router.post("/validate-openalex-email", response_model=ValidateOpenalexEmailResponse)
async def validate_openalex_email(request: ValidateOpenalexEmailRequest):
    """
    验证 OpenAlex Email 的有效性
    """
    try:
        params = {"mailto": request.email}
        if settings.OPENALEX_API_KEY:
            params["api_key"] = settings.OPENALEX_API_KEY

        response = requests.get("https://api.openalex.org/works", params=params)
        logger.debug(f"OpenAlex Email 验证响应: {response}")
        response.raise_for_status()
        return ValidateOpenalexEmailResponse(
            valid=True, message="✓ OpenAlex Email 验证成功"
        )
    except Exception as e:
        return ValidateOpenalexEmailResponse(
            valid=False, message=f"✗ OpenAlex Email 验证失败: {str(e)}"
        )


class ListModelsRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    api_type: str = "openai-chat"


class ListModelsResponse(BaseModel):
    success: bool
    models: list[str] = []
    message: str = ""


@router.post("/list-models", response_model=ListModelsResponse)
async def list_models(request: ListModelsRequest):
    """
    探测 Base URL 在指定协议下可用的模型列表
    """
    base_url = (
        request.base_url
        if request.base_url != "https://api.openai.com/v1"
        else None
    )
    try:
        match request.api_type:
            case ApiType.ANTHROPIC:
                client = AsyncAnthropic(
                    api_key=request.api_key,
                    base_url=base_url,
                    default_headers={"User-Agent": HTTP_USER_AGENT},
                )
                page = await client.models.list()
                model_ids = [m.id for m in page.data]
            case _:
                # openai-chat 与 openai-responses 共用 /models 接口
                client = AsyncOpenAI(
                    api_key=request.api_key,
                    base_url=base_url,
                    default_headers={"User-Agent": HTTP_USER_AGENT},
                )
                page = await client.models.list()
                model_ids = [m.id for m in page.data]

        return ListModelsResponse(
            success=True,
            models=sorted(model_ids),
            message=f"✓ 探测到 {len(model_ids)} 个可用模型",
        )
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            message = "✗ API Key 无效或已过期"
        elif "404" in error_msg or "Not Found" in error_msg:
            message = "✗ Base URL 错误或该协议不支持模型列表接口"
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            message = "✗ 请求过于频繁，请稍后再试"
        else:
            message = f"✗ 探测失败: {error_msg[:50]}..."
        return ListModelsResponse(success=False, models=[], message=message)


class ProbeReasoningRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model_id: str
    api_type: str = "openai-chat"
    force: bool = False


class ProbeReasoningResponse(BaseModel):
    success: bool
    supported: list[str] = []
    message: str = ""


# 探测时逐个试探的思考档位（off = 不传参数，恒可用，无需试探）
PROBE_EFFORTS = ["minimal", "low", "medium", "high", "max", "xhigh"]


@router.post("/probe-reasoning", response_model=ProbeReasoningResponse)
async def probe_reasoning(request: ProbeReasoningRequest):
    """
    探测指定模型支持的思考深度档位。

    对每个档位发送一条最小请求（max_tokens 极小、内容 "Hi"），
    按思考参数是否被 API 接受判断档位可用性；Anthropic 协议只认数值预算，
    试探一次最小 budget 即可判定全部档位。
    """
    base_url = (
        request.base_url if request.base_url != "https://api.openai.com/v1" else None
    )
    messages = [{"role": "user", "content": "Hi"}]

    if not request.force:
        cached = get_capability(request.api_type, request.base_url, request.model_id)
        if cached:
            return ProbeReasoningResponse(
                success=True,
                supported=cached["supported"],
                message="✓ 命中能力缓存（再次点击探测可强制刷新）",
            )

    try:
        if request.api_type == ApiType.ANTHROPIC:
            try:
                client = AsyncAnthropic(
                    api_key=request.api_key,
                    base_url=base_url,
                    default_headers={"User-Agent": HTTP_USER_AGENT},
                )
                await client.messages.create(
                    model=request.model_id,
                    messages=messages,
                    max_tokens=2048,
                    thinking={"type": "enabled", "budget_tokens": 1024},
                )
                supported = ["off", *PROBE_EFFORTS]
                message = "✓ 该模型支持思考（建议用预算数值精确控制）"
            except AnthropicBadRequestError:
                supported = ["off"]
                message = "✓ 该模型未检出思考支持，仅可关闭"
            save_capability(request.api_type, request.base_url, request.model_id, supported)
            return ProbeReasoningResponse(success=True, supported=supported, message=message)

        async def call_openai(effort: str | None) -> None:
            """发送探测请求；effort 为 None 时不带思考参数（基线），异常即失败。"""
            client = AsyncOpenAI(
                api_key=request.api_key,
                base_url=base_url,
                default_headers={"User-Agent": HTTP_USER_AGENT},
            )
            if request.api_type == ApiType.OPENAI_RESPONSES:
                kwargs: dict = {
                    "model": request.model_id,
                    "input": messages,
                    "max_output_tokens": 64,
                }
                if effort:
                    kwargs["reasoning"] = {"effort": effort}
                await client.responses.create(**kwargs)
            else:
                kwargs = {
                    "model": request.model_id,
                    "messages": messages,
                    "max_tokens": 64,
                }
                if effort:
                    kwargs["reasoning_effort"] = effort
                await client.chat.completions.create(**kwargs)

        async def call_openai_with_retry(effort: str | None) -> None:
            """探测请求遇限流时退避重试一次。"""
            try:
                await call_openai(effort)
            except RateLimitError:
                await asyncio.sleep(10.0)
                await call_openai(effort)

        # 基线：不带思考参数的请求也失败，说明模型/配置本身不可用
        try:
            await call_openai_with_retry(None)
        except BadRequestError as e:
            return ProbeReasoningResponse(
                success=False, supported=[], message=f"✗ 模型不可用: {str(e)[:80]}"
            )

        # 串行逐档位试探：并发小请求容易被中转站 WAF 拦截，档位间限速避免触发 429
        supported = ["off"]
        for effort in PROBE_EFFORTS:
            try:
                await call_openai_with_retry(effort)
                supported.append(effort)
            except BadRequestError:
                continue
            await asyncio.sleep(0.5)
        if len(supported) == 1:
            message = "✓ 该模型未检出思考档位，仅支持关闭（默认）"
        else:
            message = f"✓ 探测到 {len(supported) - 1} 个思考档位"
        save_capability(request.api_type, request.base_url, request.model_id, supported)
        return ProbeReasoningResponse(success=True, supported=supported, message=message)
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            message = "✗ API Key 无效或已过期"
        elif "404" in error_msg or "Not Found" in error_msg:
            message = "✗ 模型 ID 不存在或 Base URL 错误"
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            message = "✗ 请求过于频繁，请稍后再试"
        else:
            message = f"✗ 探测失败: {error_msg[:50]}..."
        return ProbeReasoningResponse(success=False, supported=[], message=message)


class GetCapabilityRequest(BaseModel):
    base_url: str
    model_id: str
    api_type: str = "openai-chat"


class GetCapabilityResponse(BaseModel):
    found: bool
    supported: list[str] = []
    probed_at: int | None = None


@router.post("/model-capability", response_model=GetCapabilityResponse)
async def get_model_capability(request: GetCapabilityRequest):
    """
    查询已缓存的模型思考档位（探测结果的只读查询，无需 API Key）
    """
    cached = get_capability(request.api_type, request.base_url, request.model_id)
    if not cached:
        return GetCapabilityResponse(found=False)
    return GetCapabilityResponse(
        found=True,
        supported=cached["supported"],
        probed_at=cached.get("probed_at"),
    )


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


class ApprovalDecisionRequest(BaseModel):
    action: str  # approve / revise / reject
    feedback: str = ""


@router.get("/approval/{task_id}")
async def get_pending_approval(task_id: str):
    """获取当前挂起的审批材料（前端弹窗消费）。"""
    from app.services import approval as approval_service

    p = approval_service.get_pending(task_id)
    if p is None:
        return {"pending": False}
    return {
        "pending": True,
        "checkpoint": p.checkpoint,
        "payload": p.payload,
    }


@router.post("/approval/{task_id}")
async def submit_approval(task_id: str, request: ApprovalDecisionRequest):
    """提交审批决策（三分支：批准/带意见返工/否决）。"""
    from app.services import approval as approval_service

    ok = approval_service.submit_decision(task_id, request.action, request.feedback)
    if not ok:
        return {"success": False, "message": "该任务当前无挂起的审批"}
    return {"success": True, "message": f"决策已提交: {request.action}"}
