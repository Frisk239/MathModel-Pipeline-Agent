"""LLM 交互模块，封装大语言模型的调用、重试和消息发送。"""

from typing import Any
from dataclasses import asdict
from app.utils.common_utils import transform_link, split_footnotes
from app.utils.log_util import logger
import asyncio
import contextlib
import time
from app.schemas.response import (
    CoderMessage,
    WriterMessage,
    ModelerMessage,
    SystemMessage,
    CoordinatorMessage,
)
from app.services.redis_manager import redis_manager
from app.config.setting import settings, ApiType, resolve_model_chain
from app.schemas.enums import AgentType
from app.schemas.response import StreamDeltaMessage
from app.core.llm.types import StandardResponse
from app.core.llm.providers.base import BaseProvider
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.providers.anthropic import AnthropicProvider


class LLMConfigError(RuntimeError):
    """LLM 配置缺失时抛出，与 JSON 解析的 ValueError 区分开，避免被重试循环误捕获。"""


def _is_conn_error(err: BaseException) -> bool:
    """503/timeout/overloaded/rate-limit/流式中断: switch model, do not burn MAX_RETRIES."""
    err_msg = str(err)
    return (
        "Connection error" in err_msg
        or "Connection refused" in err_msg
        or "timeout" in err_msg.lower()
        or "APITimeoutError" in err_msg
        or "503" in err_msg
        or "529" in err_msg
        or "overloaded" in err_msg.lower()
        or "rate limit" in err_msg.lower()
        # 供应商/中间层流式传输中途掐断（httpx RemoteProtocolError）：
        # 重试幂等，与连接类错误同待遇切模型
        or "peer closed connection" in err_msg
        or "incomplete chunked read" in err_msg.lower()
    )


async def _publish_best_effort(task_id: str, message: Any, *, tag: str) -> None:
    """前端推送是 best-effort：Redis 抖动不应让已成功的 LLM 调用进重试回路。"""
    try:
        await redis_manager.publish_message(task_id, message)
    except Exception as e:
        logger.warning(f"[{tag}] 消息推送失败（不影响调用）: {e}")


# 输出预算上限与自适应放大入口值。GLM 等 anthropic 兼容端 budget_tokens 是
# 软约束（思考可膨胀挤占正文）；openai 系端点未显式配置时默认预算可能过小
# （hy3 空响应事故：content=None + stop_reason=None 连发 6 次耗尽重试）。
MAX_TOKENS_CEILING = 128000
WIDEN_ENTRY_TOKENS = 16384


def _widen_output_budget(
    response: StandardResponse,
    current: int | None,
    *,
    already_widened: bool,
) -> int | None:
    """返回下次重试应使用的输出预算；None 表示不应触发预算调整重试。

    触发条件（无工具调用、且本次会话尚未调整过）：
    - 截断：stop_reason=max_tokens（openai 的 length 已在 provider 归一化）
    - 异常空输出：正文为空且非正常结束（end_turn）——思考挤占或端点默认预算过小
    已配置预算则 ×2 放大；未配置则注入入口值。均封顶上限，整个会话仅一次。
    """
    if already_widened or response.tool_calls:
        return None
    truncated = response.stop_reason == "max_tokens"
    empty_abnormal = not response.content and response.stop_reason != "end_turn"
    if not (truncated or empty_abnormal):
        return None
    if current is None:
        return WIDEN_ENTRY_TOKENS
    if current >= MAX_TOKENS_CEILING:
        return None
    return min(current * 2, MAX_TOKENS_CEILING)


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
        fallback_models: str | None = None,
    ):
        self.api_type = api_type
        self.api_key = api_key
        self.base_url = base_url
        self.chat_count = 0
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.thinking_budget = thinking_budget
        self.task_id = task_id
        self._model_chain = resolve_model_chain(model, fallback_models)
        self._active_index = 0
        self.model = self._model_chain[0] if self._model_chain else model
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
        notify: bool = True,
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
        # G2Review 等非流水线角色不推流式
        stream_agent_type: AgentType | None = None
        with contextlib.suppress(ValueError):
            stream_agent_type = AgentType(agent_name)
        pending: dict[str, list[str]] = {"thinking": [], "text": []}
        last_flush = time.monotonic()

        async def _flush_delta() -> None:
            nonlocal last_flush
            for kind, buf in pending.items():
                if not buf:
                    continue
                await _publish_best_effort(
                    self.task_id,
                    StreamDeltaMessage(
                        agent_type=stream_agent_type,  # type: ignore[arg-type]
                        kind=kind,  # type: ignore[arg-type]
                        delta="".join(buf),
                    ),
                    tag="流式增量",
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

        # 输出预算自适应调整（截断/异常空输出时放大或注入，见 _widen_output_budget）
        adaptive_max_tokens = self.max_tokens
        tokens_widened = False

        attempt = 0
        laps_done = 0
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
                    await _publish_best_effort(
                        self.task_id,
                        StreamDeltaMessage(
                            agent_type=stream_agent_type,  # type: ignore[arg-type]
                            done=True,
                        ),
                        tag="流式结束",
                    )
                logger.info(
                    f"API返回: content={response.content!r}, "
                    f"tool_calls={len(response.tool_calls)}, "
                    f"stop_reason={response.stop_reason}"
                )
                widened = _widen_output_budget(
                    response,
                    adaptive_max_tokens,
                    already_widened=tokens_widened,
                )
                if widened is not None:
                    logger.warning(
                        f"[{agent_name}] 输出为空或被截断"
                        f"（stop_reason={response.stop_reason}, "
                        f"max_tokens={adaptive_max_tokens}），"
                        f"调整输出预算至 {widened} 重试一次"
                    )
                    adaptive_max_tokens = widened
                    tokens_widened = True
                    continue
                self.chat_count += 1
                if notify:
                    try:
                        await self.send_message(response, agent_name, sub_title)
                    except Exception as notify_err:
                        # 终稿通知失败不丢弃已成功的响应（Redis 抖动只需告警）
                        logger.warning(
                            f"[{agent_name}] 终稿消息推送失败（不影响调用）: {notify_err}"
                        )
                return response
            except Exception as e:
                logger.error(f"LLM 调用失败: {str(e)}")
                if stream_agent_type is not None and self.task_id:
                    await _flush_delta()
                    await _publish_best_effort(
                        self.task_id,
                        StreamDeltaMessage(
                            agent_type=stream_agent_type,  # type: ignore[arg-type]
                            done=True,
                        ),
                        tag="流式结束",
                    )
                if _is_conn_error(e):
                    chain = self._model_chain or [
                        m for m in (self.model,) if m
                    ]
                    n = len(chain)
                    # 有备用模型：过载立即切下一个；单模型仍走下面的 MAX_RETRIES 退避
                    if n > 1:
                        next_index = (self._active_index + 1) % n
                        wrapping = next_index == 0
                        if wrapping:
                            laps_done += 1
                            if laps_done >= 2:
                                raise
                        old = self.model
                        self._active_index = next_index
                        self.model = chain[next_index]
                        logger.warning(
                            f"[{agent_name}] 连接类错误，从 {old} 切换到 {self.model}"
                        )
                        if self.task_id:
                            # best-effort：这里的 Redis 异常若上抛会掩盖原始的模型过载错误
                            await _publish_best_effort(
                                self.task_id,
                                SystemMessage(
                                    # type 必须是 info：warning/error/success 会让前端 isRunning=false
                                    content=f"模型过载或超时，从 {old} 切换到 {self.model}",
                                    type="info",
                                ),
                                tag="模型切换通知",
                            )
                        if wrapping:
                            wait = min(5 * (3 ** (laps_done - 1)), 60)
                            logger.warning(
                                f"备用链已轮询一圈，退避 {wait}s 后再试"
                            )
                            await asyncio.sleep(wait)
                        continue
                attempt += 1
                if attempt >= effective_max_retries:
                    raise
                if _is_conn_error(e):
                    wait = min(5 * (3 ** (attempt - 1)), 60)
                    logger.warning(f"连接类错误，退避 {wait}s 后重试")
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(retry_delay * min(attempt, 10))

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

        # 流水线四角色的终稿带本次调用用量/耗时（前端 StatsLine 统计源）
        usage_dict = asdict(response.usage) if response.usage else None

        agent_msg: Any = None
        match agent_name:
            case AgentType.CODER:
                agent_msg = CoderMessage(content=content, usage=usage_dict)
            case AgentType.WRITER:
                content, _ = split_footnotes(content)
                content = transform_link(self.task_id, content)
                agent_msg = WriterMessage(
                    content=content, sub_title=sub_title, usage=usage_dict
                )
            case AgentType.MODELER:
                agent_msg = ModelerMessage(content=content, usage=usage_dict)
            case AgentType.SYSTEM:
                agent_msg = SystemMessage(content=content)
            case AgentType.COORDINATOR:
                agent_msg = CoordinatorMessage(content=content, usage=usage_dict)
            case _:
                # 评审/预审等非流水线角色：降级为系统消息推送，不中断调用
                agent_msg = SystemMessage(content=content)

        await redis_manager.publish_message(self.task_id, agent_msg)


async def simple_chat(model: LLM, history: list) -> str:
    """使用 LLM 进行简单的单轮对话（走 failover，不推流/不发终稿）。"""
    response = await model.chat(
        history=history,
        agent_name="MemoryCompress",
        notify=False,
    )
    return response.content or ""
