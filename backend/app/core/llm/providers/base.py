"""LLM Provider 抽象基类。"""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from app.core.llm.types import StandardResponse

# 出站请求统一 User-Agent：部分中转站 WAF 直接屏蔽 OpenAI/Python 等 SDK 默认 UA
HTTP_USER_AGENT = "MathModelAgent/0.1"

# 流式增量回调：(kind, delta_text)，kind ∈ {"thinking", "text"}
OnDelta = Callable[[str, str], Awaitable[None]]


class BaseProvider(ABC):
    """LLM Provider 基类，定义统一的调用接口。"""

    @abstractmethod
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
        on_delta: OnDelta | None = None,
    ) -> StandardResponse:
        """调用 LLM 并返回标准化响应。

        Args:
            messages: 消息历史（OpenAI 格式）。
            model: 模型 ID。
            api_key: API 密钥。
            base_url: API 基础 URL。
            tools: 工具定义列表（OpenAI 格式）。
            tool_choice: 工具选择策略。
            max_tokens: 最大生成 token 数。
            top_p: 采样温度参数。
            reasoning_effort: 思考深度档位（如 low/medium/high/max），由各协议映射。
            thinking_budget: Anthropic 协议的思考 token 预算（budget_tokens）。
            on_delta: 流式增量回调（协议不支持流式时忽略）。

        Returns:
            标准化响应。
        """
        ...
