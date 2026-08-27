"""OpenAI Chat Completions API Provider（流式聚合）。"""

import time

from openai import AsyncOpenAI, BadRequestError
from app.core.llm.providers.base import (
    BaseProvider,
    HTTP_USER_AGENT,
    llm_http_timeout,
)
from app.core.llm.types import StandardResponse, ToolCall, Usage

# openai finish_reason → 归一化 stop_reason（anthropic 词表）。
# llm 层的输出预算放大判断的是 "max_tokens"，不翻译则 openai 系协议
# 的 length 截断永远不会触发放大（hy3 空响应事故的根因之一）。
_OPENAI_FINISH_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}


class OpenAIChatProvider(BaseProvider):
    """OpenAI Chat Completions API (/v1/chat/completions) 实现。

    统一走流式（stream=True）+ 客户端聚合：
    - reasoning_content 增量（GLM/DeepSeek 类兼容端）经 on_delta 上抛做思考展示
    - 长请求不易被中间层掐断（与 anthropic provider 同理）
    """

    async def call(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        base_url: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        thinking_budget: int | None = None,
        on_delta=None,
    ) -> StandardResponse:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"User-Agent": HTTP_USER_AGENT},
            timeout=llm_http_timeout(),
        )

        kwargs: dict = {"model": model, "messages": messages, "stream": True}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if reasoning_effort and reasoning_effort != "off":
            kwargs["reasoning_effort"] = reasoning_effort

        async def _consume() -> StandardResponse:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            # tool_calls 按 index 聚合：id/name 首帧到达，arguments 逐帧拼接
            tc_acc: dict[int, dict] = {}
            finish_reason: str | None = None
            usage = Usage()
            first_token_ms = 0
            async for chunk in stream:
                if not first_token_ms:
                    first_token_ms = round((time.monotonic() - t0) * 1000)
                if getattr(chunk, "usage", None):
                    usage = Usage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                    )
                for choice in chunk.choices or []:
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    if delta is None:
                        continue
                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        reasoning_parts.append(rc)
                        if on_delta is not None:
                            await on_delta("thinking", rc)
                    if delta.content:
                        content_parts.append(delta.content)
                        if on_delta is not None:
                            await on_delta("text", delta.content)
                    for tc in delta.tool_calls or []:
                        acc = tc_acc.setdefault(
                            tc.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function and tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            acc["arguments"] += tc.function.arguments

            usage.first_token_ms = first_token_ms
            usage.latency_ms = round((time.monotonic() - t0) * 1000)
            return StandardResponse(
                content="".join(content_parts) or None,
                reasoning_content="".join(reasoning_parts) or None,
                tool_calls=[
                    ToolCall(id=a["id"], name=a["name"], arguments=a["arguments"])
                    for _, a in sorted(tc_acc.items())
                ],
                usage=usage,
                stop_reason=_OPENAI_FINISH_TO_STOP.get(finish_reason, finish_reason),
            )

        t0 = time.monotonic()
        try:
            stream = await client.chat.completions.create(**kwargs)
            return await _consume()
        except BadRequestError:
            # 部分 OpenAI 兼容端点不支持 reasoning_effort 参数，去掉后重试一次
            if "reasoning_effort" not in kwargs:
                raise
            kwargs.pop("reasoning_effort")
            t0 = time.monotonic()
            stream = await client.chat.completions.create(**kwargs)
            return await _consume()
