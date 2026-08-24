"""LLM 交互模块，封装大语言模型的调用、重试和消息发送。"""

from typing import Any
from app.utils.common_utils import transform_link, split_footnotes
from app.utils.log_util import logger
import time
from app.schemas.response import (
    CoderMessage,
    WriterMessage,
    ModelerMessage,
    SystemMessage,
    CoordinatorMessage,
)
from app.services.redis_manager import redis_manager
from app.config.setting import settings
from app.schemas.enums import AgentType
from app.schemas.response import StreamDeltaMessage
from app.config.setting import ApiType
from app.core.llm.types import StandardResponse
from app.core.llm.providers.base import BaseProvider
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.providers.anthropic import AnthropicProvider


class LLMConfigError(RuntimeError):
    """LLM 配置缺失时抛出，与 JSON 解析的 ValueError 区分开，避免被重试循环误捕获。"""


class LLM:
    """大语言模型封装类，提供对话调用、重试和工具调用验证功能。"""

    def __init__(
        self,
        api_type: ApiType | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        task_id: str = "",
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        thinking_budget: int | None = None,
    ):
        self.api_type = api_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.chat_count = 0
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.thinking_budget = thinking_budget
        self.task_id = task_id
        self.provider = self._create_provider(api_type)

    def _create_provider(self, api_type: ApiType | None) -> BaseProvider:
        """根据 api_type 创建对应的 Provider。"""
        match api_type:
            case ApiType.OPENAI_RESPONSES:
                return OpenAIResponsesProvider()
            case ApiType.ANTHROPIC:
                return AnthropicProvider()
            case _:
                # 默认使用 OpenAI Chat Completions（兼容未配置 api_type 的情况）
                return OpenAIChatProvider()

    def _validate_config(self, agent_name: str) -> None:
        """验证 LLM 配置是否完整。"""
        if not self.model or not str(self.model).strip():
            raise LLMConfigError(f"{agent_name} 未配置模型 ID，请设置对应的 *_MODEL")
        if not self.api_key or not str(self.api_key).strip():
            raise LLMConfigError(f"{agent_name} 未配置 API Key，请设置对应的 *_API_KEY")

    async def chat(
        self,
        history: list | None = None,
        tools: list | None = None,
        tool_choice: str | None = None,
        max_retries: int | None = None,
        retry_delay: float = 1.0,
        top_p: float | None = None,
        agent_name: str = "SystemAgent",
        sub_title: str | None = None,
    ) -> StandardResponse:
        self._validate_config(agent_name)

        # 重试上限：显式参数 > 全局配置 > 兜底值，避免 API 持续失败时无限重试
        effective_max_retries = (
            max_retries if max_retries is not None else (settings.MAX_RETRIES or 5)
        )

        # 验证和修复工具调用完整性（仅对 OpenAI 格式的历史有效）
        if history:
            history = self._validate_and_fix_tool_calls(history)

        messages = history or []

        # 流式增量节流推送（100ms 合帧 + 尾部 flush + done）：
        # token 级 delta 直接发布会淹没 0.1s 轮询转发的 WS 通道
        stream_agent_type = None
        try:
            stream_agent_type = AgentType(agent_name)
        except ValueError:
            pass  # G2Review 等非流水线角色不推流式
        pending: dict[str, list[str]] = {"thinking": [], "text": []}
        last_flush = time.monotonic()

        async def _flush_delta() -> None:
            nonlocal last_flush
            for kind, buf in pending.items():
                if not buf:
                    continue
                await redis_manager.publish_message(
                    self.task_id,
                    StreamDeltaMessage(
                        agent_type=stream_agent_type,  # type: ignore[arg-type]
                        kind=kind,  # type: ignore[arg-type]
                        delta="".join(buf),
                    ),
                )
                buf.clear()
            last_flush = time.monotonic()

        async def on_delta(kind: str, text: str) -> None:
            if stream_agent_type is None or not self.task_id:
                return
            buf = pending.get(kind)
            if buf is None:
                return
            buf.append(text)
            if time.monotonic() - last_flush >= 0.1:
                await _flush_delta()

        # 思考挤占输出预算的自适应放大：GLM 等 anthropic 兼容端不遵守
        # budget_tokens 硬约束，思考可膨胀至上万 token，把正文挤到零或截断
        # （stop_reason=max_tokens）。此时重发同样请求没有意义，放大
        # max_tokens 重试一次（×2，封顶模型上限），仅一次，仍失败交给上层重试。
        MAX_TOKENS_CEILING = 128000
        adaptive_max_tokens = self.max_tokens
        tokens_widened = False

        attempt = 0
        while True:
            try:
                response = await self.provider.call(
                    messages=messages,
                    model=self.model,  # type: ignore[arg-type]
                    api_key=self.api_key,  # type: ignore[arg-type]
                    base_url=self.base_url,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=adaptive_max_tokens,
                    top_p=top_p,
                    reasoning_effort=self.reasoning_effort,
                    thinking_budget=self.thinking_budget,
                    on_delta=on_delta,
                )
                if stream_agent_type is not None and self.task_id:
                    await _flush_delta()
                    await redis_manager.publish_message(
                        self.task_id,
                        StreamDeltaMessage(
                            agent_type=stream_agent_type,  # type: ignore[arg-type]
                            done=True,
                        ),
                    )
                logger.info(
                    f"API返回: content={response.content!r}, "
                    f"tool_calls={len(response.tool_calls)}, "
                    f"stop_reason={response.stop_reason}"
                )
                if (
                    response.stop_reason == "max_tokens"
                    and not response.tool_calls
                    and adaptive_max_tokens is not None
                    and adaptive_max_tokens < MAX_TOKENS_CEILING
                    and not tokens_widened
                ):
                    widened = min(adaptive_max_tokens * 2, MAX_TOKENS_CEILING)
                    logger.warning(
                        f"[{agent_name}] 输出被 max_tokens={adaptive_max_tokens} 截断"
                        f"（思考挤占输出预算），放大至 {widened} 重试一次"
                    )
                    adaptive_max_tokens = widened
                    tokens_widened = True
                    continue
                self.chat_count += 1
                await self.send_message(response, agent_name, sub_title)
                return response
            except Exception as e:
                attempt += 1
                logger.error(f"第{attempt}次重试: {str(e)}")
                if attempt >= effective_max_retries:
                    raise
                err_msg = str(e)
                is_conn_error = (
                    "Connection error" in err_msg
                    or "Connection refused" in err_msg
                    or "timeout" in err_msg.lower()
                    or "APITimeoutError" in err_msg
                    # 上游过载/限流（hy3 等聚合端风暴期）：退避等待而非快速烧完重试
                    or "503" in err_msg
                    or "529" in err_msg
                    or "overloaded" in err_msg.lower()
                    or "rate limit" in err_msg.lower()
                )
                if is_conn_error:
                    # 网络/上游瞬断：指数退避（5s→15s→30s→60s），等待恢复而非快速烧完重试
                    wait = min(5 * (3 ** (attempt - 1)), 60)
                    logger.warning(f"连接类错误，退避 {wait}s 后重试")
                    time.sleep(wait)
                else:
                    time.sleep(retry_delay * min(attempt, 10))

    def _validate_and_fix_tool_calls(self, history: list) -> list:
        """验证并修复工具调用完整性。"""
        if not history:
            return history

        fixed_history = []
        i = 0

        while i < len(history):
            msg = history[i]

            if isinstance(msg, dict) and "tool_calls" in msg and msg["tool_calls"]:
                valid_tool_calls = []
                for tool_call in msg["tool_calls"]:
                    tool_call_id = tool_call.get("id")
                    if tool_call_id:
                        found_response = False
                        for j in range(i + 1, len(history)):
                            if (
                                history[j].get("role") == "tool"
                                and history[j].get("tool_call_id") == tool_call_id
                            ):
                                found_response = True
                                break
                        if found_response:
                            valid_tool_calls.append(tool_call)

                if valid_tool_calls:
                    fixed_msg = msg.copy()
                    fixed_msg["tool_calls"] = valid_tool_calls
                    fixed_history.append(fixed_msg)
                else:
                    cleaned_msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                    if cleaned_msg.get("content"):
                        fixed_history.append(cleaned_msg)

            elif isinstance(msg, dict) and msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                found_call = False
                for j in range(len(fixed_history)):
                    if fixed_history[j].get("tool_calls") and any(
                        tc.get("id") == tool_call_id
                        for tc in fixed_history[j]["tool_calls"]
                    ):
                        found_call = True
                        break
                if found_call:
                    fixed_history.append(msg)
            else:
                fixed_history.append(msg)

            i += 1

        return fixed_history

    async def send_message(
        self,
        response: StandardResponse,
        agent_name: str,
        sub_title: str | None = None,
    ):
        """将 LLM 响应通过 Redis 发送给前端。"""
        content = response.content

        if content is None:
            return

        agent_msg: Any = None
        match agent_name:
            case AgentType.CODER:
                agent_msg = CoderMessage(content=content)
            case AgentType.WRITER:
                content, _ = split_footnotes(content)
                content = transform_link(self.task_id, content)
                agent_msg = WriterMessage(content=content, sub_title=sub_title)
            case AgentType.MODELER:
                agent_msg = ModelerMessage(content=content)
            case AgentType.SYSTEM:
                agent_msg = SystemMessage(content=content)
            case AgentType.COORDINATOR:
                agent_msg = CoordinatorMessage(content=content)
            case _:
                # 评审/预审等非流水线角色：降级为系统消息推送，不中断调用
                agent_msg = SystemMessage(content=content)

        await redis_manager.publish_message(self.task_id, agent_msg)


async def simple_chat(model: LLM, history: list) -> str:
    """使用 LLM 进行简单的单轮对话。"""
    response = await model.provider.call(
        messages=history,
        model=model.model,  # type: ignore[arg-type]
        api_key=model.api_key,  # type: ignore[arg-type]
        base_url=model.base_url,
    )
    return response.content or ""
