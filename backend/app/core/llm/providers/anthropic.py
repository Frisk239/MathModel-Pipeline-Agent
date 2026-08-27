"""Anthropic Messages API Provider。"""

import json as _json
import re
import time

from anthropic import AsyncAnthropic
from app.core.llm.providers.base import (
    BaseProvider,
    HTTP_USER_AGENT,
    llm_http_timeout,
)
from app.core.llm.types import StandardResponse, ToolCall, Usage

# 思考深度档位到 budget_tokens 的换算表（Anthropic 协议只接受数值预算）
EFFORT_TO_BUDGET: dict[str, int] = {
    "low": 8192,
    "medium": 16384,
    "high": 32768,
    "max": 65536,
    "minimal": 2048,
    "xhigh": 65536,
}


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API (/v1/messages) 实现。"""

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
        client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"User-Agent": HTTP_USER_AGENT},
            timeout=llm_http_timeout(),
        )

        system_prompt, anthropic_messages = self._convert_messages(messages)

        effective_max_tokens = max_tokens or 4096

        kwargs: dict = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": effective_max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if top_p is not None:
            kwargs["top_p"] = top_p
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            if tool_choice:
                kwargs["tool_choice"] = self._convert_tool_choice(tool_choice)

        thinking = self._build_thinking(
            reasoning_effort, thinking_budget, effective_max_tokens
        )
        if thinking:
            kwargs["thinking"] = thinking

        # 流式 + 聚合：GLM 等 anthropic 兼容端对大 max_tokens（>16384）强制要求
        # 流式（"Streaming is required for operations that may take longer than
        # 10 minutes"）；流式聚合结果与非流式 Message 同构，且长请求不易被中间层掐断。
        # on_delta 提供时逐事件上抛 thinking/text 增量（过程展示）。
        t0 = time.monotonic()
        first_token_ms = 0
        async with client.messages.stream(**kwargs) as stream:
            if on_delta is not None:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if not first_token_ms:
                        first_token_ms = round((time.monotonic() - t0) * 1000)
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "thinking_delta":
                        await on_delta("thinking", getattr(delta, "thinking", "") or "")
                    elif delta_type == "text_delta":
                        await on_delta("text", getattr(delta, "text", "") or "")
            response = await stream.get_final_message()

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(getattr(block, "thinking", "") or "")
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=_json.dumps(block.input),
                ))

        content = "".join(content_parts) if content_parts else None
        if content and tools and not tool_calls:
            allowed_names = self._get_tool_names(tools)
            content, text_tool_calls = self._extract_text_tool_calls(
                content, allowed_names
            )
            tool_calls.extend(text_tool_calls)

        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )
        usage.first_token_ms = first_token_ms
        usage.latency_ms = round((time.monotonic() - t0) * 1000)

        return StandardResponse(
            content=content,
            reasoning_content="".join(thinking_parts) or None,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=response.stop_reason,
        )

    def _build_thinking(
        self,
        reasoning_effort: str | None,
        thinking_budget: int | None,
        max_tokens: int,
    ) -> dict | None:
        """组装 Anthropic thinking 参数。

        off 无条件关闭；显式 thinking_budget 优先；否则按 effort 档位换算。
        Anthropic 要求 1024 <= budget_tokens < max_tokens，越界时收敛到边界。
        """
        if reasoning_effort == "off":
            return None
        budget = thinking_budget
        if budget is None and reasoning_effort:
            # 未收录的档位（部分供应商的自定义档）无法换算，保持不开启
            budget = EFFORT_TO_BUDGET.get(reasoning_effort)
        if budget is None:
            return None

        budget = max(1024, min(budget, max_tokens - 1024))
        return {"type": "enabled", "budget_tokens": budget}

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """将 OpenAI 格式 messages 转为 Anthropic 格式。"""
        system_prompt = None
        converted: list[dict] = []

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system" and system_prompt is None:
                system_prompt = msg["content"]
                continue

            if role == "assistant" and "tool_calls" in msg and msg["tool_calls"]:
                content_blocks: list[dict] = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": _json.loads(tc["function"]["arguments"]),
                    })
                converted.append({"role": "assistant", "content": content_blocks})
                continue

            if role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }],
                })
                continue

            converted.append(msg)

        return system_prompt, converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """将 OpenAI tools 转为 Anthropic 格式，并接受已转换的 schema。"""
        converted = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                converted.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            elif tool.get("name") and tool.get("input_schema"):
                converted.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["input_schema"],
                })
        return converted

    def _get_tool_names(self, tools: list[dict]) -> set[str]:
        """提取当前请求实际声明的工具名，用于文本兜底白名单。"""
        names: set[str] = set()
        for tool in tools:
            if tool.get("type") == "function":
                name = tool.get("function", {}).get("name")
            else:
                name = tool.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        return names

    def _extract_text_tool_calls(
        self, content: str, allowed_names: set[str]
    ) -> tuple[str | None, list[ToolCall]]:
        """归一化兼容端放进 ``<tool_call>`` 文本块的工具调用。

        只接收当前请求已声明的工具名和对象型参数；无法解析的文本原样保留。
        """
        pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
        calls: list[ToolCall] = []
        retained: list[str] = []
        last_end = 0

        for match in pattern.finditer(content):
            try:
                payload = _json.loads(match.group(1))
            except (_json.JSONDecodeError, TypeError):
                continue

            name = payload.get("name") if isinstance(payload, dict) else None
            arguments = (
                payload.get("arguments", payload.get("input", {}))
                if isinstance(payload, dict)
                else None
            )
            if name not in allowed_names:
                continue
            if isinstance(arguments, str):
                try:
                    parsed_arguments = _json.loads(arguments)
                except _json.JSONDecodeError:
                    continue
                if not isinstance(parsed_arguments, dict):
                    continue
                arguments_json = arguments
            elif isinstance(arguments, dict):
                arguments_json = _json.dumps(arguments, ensure_ascii=False)
            else:
                continue

            retained.append(content[last_end:match.start()])
            last_end = match.end()
            call_id = payload.get("id") or f"text-tool-call-{len(calls) + 1}"
            calls.append(
                ToolCall(id=str(call_id), name=name, arguments=arguments_json)
            )

        if not calls:
            xml_pattern = re.compile(
                r"<tool_call>\s*<name>\s*([^<]+?)\s*</name>\s*"
                r"<code>\s*(.*?)\s*</code>\s*</tool_call>",
                re.DOTALL,
            )
            for match in xml_pattern.finditer(content):
                name = match.group(1).strip()
                if name not in allowed_names:
                    continue
                retained.append(content[last_end:match.start()])
                last_end = match.end()
                calls.append(
                    ToolCall(
                        id=f"text-tool-call-{len(calls) + 1}",
                        name=name,
                        arguments=_json.dumps(
                            {"code": match.group(2).strip()}, ensure_ascii=False
                        ),
                    )
                )

        if not calls:
            return content, []

        retained.append(content[last_end:])
        remaining_content = "".join(retained).strip() or None
        return remaining_content, calls

    def _convert_tool_choice(self, tool_choice: str) -> dict:
        """转换 tool_choice 为 Anthropic 格式。"""
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
        return {"type": "auto"}
