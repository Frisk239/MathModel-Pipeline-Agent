"""Anthropic Messages API Provider。"""

import json as _json
from anthropic import AsyncAnthropic
from app.core.llm.providers.base import BaseProvider, HTTP_USER_AGENT
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
    ) -> StandardResponse:
        client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"User-Agent": HTTP_USER_AGENT},
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

        response = await client.messages.create(**kwargs)

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=_json.dumps(block.input),
                ))

        content = "".join(content_parts) if content_parts else None

        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

        return StandardResponse(content=content, tool_calls=tool_calls, usage=usage)

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
        """将 OpenAI tools 格式转为 Anthropic 格式。"""
        converted = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                converted.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
        return converted

    def _convert_tool_choice(self, tool_choice: str) -> dict:
        """转换 tool_choice 为 Anthropic 格式。"""
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
        return {"type": "auto"}
