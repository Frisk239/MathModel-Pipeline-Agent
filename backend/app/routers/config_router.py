"""配置与探测路由：API 配置保存、Key 校验、模型列举、思考档位探测。"""

import asyncio

import requests
from anthropic import AsyncAnthropic
from anthropic import BadRequestError as AnthropicBadRequestError
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI, BadRequestError, RateLimitError
from pydantic import BaseModel

from app.config.setting import settings, ApiType
from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.base import BaseProvider, HTTP_USER_AGENT
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.services.model_capability import get_capability, save_capability
from app.utils.log_util import logger

router = APIRouter()


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

        return ValidateApiKeyResponse(valid=True, message="模型 API 验证成功")
    except Exception as e:
        error_msg = str(e)

        # 解析不同类型的错误
        if "401" in error_msg or "Unauthorized" in error_msg:
            return ValidateApiKeyResponse(valid=False, message="API Key 无效或已过期")
        elif "404" in error_msg or "Not Found" in error_msg:
            return ValidateApiKeyResponse(
                valid=False, message="模型 ID 不存在或 Base URL 错误"
            )
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            return ValidateApiKeyResponse(
                valid=False, message="请求过于频繁，请稍后再试"
            )
        elif "403" in error_msg or "Forbidden" in error_msg:
            return ValidateApiKeyResponse(
                valid=False, message="API 权限不足或账户余额不足"
            )
        else:
            return ValidateApiKeyResponse(
                valid=False, message=f"验证失败: {error_msg[:50]}..."
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
            valid=True, message="OpenAlex Email 验证成功"
        )
    except Exception as e:
        return ValidateOpenalexEmailResponse(
            valid=False, message=f"OpenAlex Email 验证失败: {str(e)}"
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
            message=f"探测到 {len(model_ids)} 个可用模型",
        )
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            message = "API Key 无效或已过期"
        elif "404" in error_msg or "Not Found" in error_msg:
            message = "Base URL 错误或该协议不支持模型列表接口"
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            message = "请求过于频繁，请稍后再试"
        else:
            message = f"探测失败: {error_msg[:50]}..."
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
                message="命中能力缓存（再次点击探测可强制刷新）",
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
                message = "该模型支持思考（建议用预算数值精确控制）"
            except AnthropicBadRequestError:
                supported = ["off"]
                message = "该模型未检出思考支持，仅可关闭"
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
                success=False, supported=[], message=f"模型不可用: {str(e)[:80]}"
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
            message = "该模型未检出思考档位，仅支持关闭（默认）"
        else:
            message = f"探测到 {len(supported) - 1} 个思考档位"
        save_capability(request.api_type, request.base_url, request.model_id, supported)
        return ProbeReasoningResponse(success=True, supported=supported, message=message)
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            message = "API Key 无效或已过期"
        elif "404" in error_msg or "Not Found" in error_msg:
            message = "模型 ID 不存在或 Base URL 错误"
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            message = "请求过于频繁，请稍后再试"
        else:
            message = f"探测失败: {error_msg[:50]}..."
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
