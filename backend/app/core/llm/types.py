"""LLM 响应标准化类型定义。"""

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """标准化工具调用。"""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class Usage:
    """Token 用量与耗时（StatsLine 统计数据源）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 单次调用总耗时（毫秒）；流式首帧到达时间（非流式等于总耗时）
    latency_ms: int = 0
    first_token_ms: int = 0


@dataclass
class StandardResponse:
    """LLM 响应的标准化格式。

    Agent 侧统一使用此格式访问 LLM 结果，不感知底层 API 差异。
    """

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # anthropic stop_reason / openai finish_reason 的归一化（max_tokens 截断诊断用）
    stop_reason: str | None = None
