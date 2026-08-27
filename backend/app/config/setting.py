"""应用配置模块，基于 pydantic-settings 管理环境变量和全局配置。"""

from enum import Enum

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Annotated, Optional


class ApiType(str, Enum):
    """LLM API 类型。"""
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC = "anthropic"


def parse_cors(value: str) -> list[str]:
    """解析 CORS 配置字符串为 URL 列表。

    Args:
        value: 逗号分隔的 URL 字符串，或 "*" 表示允许所有来源。

    Returns:
        解析后的 URL 列表。
    """
    if value == "*":
        return ["*"]
    if "," in value:
        return [url.strip() for url in value.split(",")]
    return [value]


def _empty_to_none(v):
    """空字符串转 None（前端清空输入框保存时 .env 会产生空值）。"""
    if isinstance(v, str) and not v.strip():
        return None
    return v


def resolve_model_chain(primary: str | None, extras: str | None) -> list[str]:
    """Build a failover chain: primary first, then comma-separated extras.

    Strips whitespace, drops empties, de-duplicates while preserving order.
    Empty extras → ``[primary]`` (or ``[]`` if primary is also empty).
    """
    chain: list[str] = []
    seen: set[str] = set()
    for raw in (primary, *(extras or "").split(",")):
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        chain.append(name)
    return chain


OptionalInt = Annotated[Optional[int], BeforeValidator(_empty_to_none)]


class Settings(BaseSettings):
    """全局应用配置，从环境变量和 .env 文件加载。"""
    ENV: str = "dev"

    COORDINATOR_API_TYPE: Optional[ApiType] = None
    COORDINATOR_API_KEY: Optional[str] = None
    COORDINATOR_MODEL: Optional[str] = None
    COORDINATOR_MODELS: Optional[str] = None  # comma-separated extras
    COORDINATOR_BASE_URL: Optional[str] = None
    COORDINATOR_MAX_TOKENS: OptionalInt = None
    COORDINATOR_CONTEXT_WINDOW: int = 128000
    COORDINATOR_REASONING_EFFORT: Optional[str] = None
    COORDINATOR_THINKING_BUDGET: OptionalInt = None

    MODELER_API_TYPE: Optional[ApiType] = None
    MODELER_API_KEY: Optional[str] = None
    MODELER_MODEL: Optional[str] = None
    MODELER_MODELS: Optional[str] = None  # comma-separated extras
    MODELER_BASE_URL: Optional[str] = None
    MODELER_MAX_TOKENS: OptionalInt = None
    MODELER_CONTEXT_WINDOW: int = 128000
    MODELER_REASONING_EFFORT: Optional[str] = None
    MODELER_THINKING_BUDGET: OptionalInt = None

    CODER_API_TYPE: Optional[ApiType] = None
    CODER_API_KEY: Optional[str] = None
    CODER_MODEL: Optional[str] = None
    CODER_MODELS: Optional[str] = None  # comma-separated extras
    CODER_BASE_URL: Optional[str] = None
    CODER_MAX_TOKENS: OptionalInt = None
    CODER_CONTEXT_WINDOW: int = 128000
    CODER_REASONING_EFFORT: Optional[str] = None
    CODER_THINKING_BUDGET: OptionalInt = None

    WRITER_API_TYPE: Optional[ApiType] = None
    WRITER_API_KEY: Optional[str] = None
    WRITER_MODEL: Optional[str] = None
    WRITER_MODELS: Optional[str] = None  # comma-separated extras
    WRITER_BASE_URL: Optional[str] = None
    WRITER_MAX_TOKENS: OptionalInt = None
    WRITER_CONTEXT_WINDOW: int = 128000
    WRITER_REASONING_EFFORT: Optional[str] = None
    WRITER_THINKING_BUDGET: OptionalInt = None

    # 评审模型（G2-L2 代码评审 / G4 终审 / 建模预审）；缺省回退 Coordinator 配置
    REVIEW_API_TYPE: Optional[ApiType] = None
    REVIEW_API_KEY: Optional[str] = None
    REVIEW_MODEL: Optional[str] = None
    REVIEW_MODELS: Optional[str] = None  # comma-separated extras
    REVIEW_BASE_URL: Optional[str] = None
    REVIEW_MAX_TOKENS: OptionalInt = None

    # 人工检查点（二期真实生效）；AUTO_MODE=true 时全部跳过
    HIL_ENABLED: bool = True
    AUTO_MODE: bool = False

    MAX_CHAT_TURNS: Optional[int] = None
    MAX_RETRIES: Optional[int] = None
    LLM_READ_TIMEOUT: float = 180.0  # seconds; stream idle gap (zen hold)
    E2B_API_KEY: Optional[str] = None
    LOG_LEVEL: str = "DEBUG"
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_MAX_CONNECTIONS: int = 10
    CORS_ALLOW_ORIGINS: Annotated[list[str] | str, BeforeValidator(parse_cors)] = "*"
    SERVER_HOST: str = "http://localhost:8000"
    OPENALEX_EMAIL: Optional[str] = None
    OPENALEX_API_KEY: Optional[str] = None
    EXA_API_KEY: Optional[str] = None

    # 人工检查点开关（沿用上游键名，二期真实生效）
    HIL_TIMEOUT: int = 300  # 审批超时提示时间（秒）；无响应仍永久等待，绝不自动放行
    HIL_CHECKPOINTS: dict = {
        "problem_split": True,   # ① 拆题后
        "model_selection": True, # ② 建模方案后
        "code_review": False,    # ③ 代码方案后（不设人工检查点，由 G2 覆盖）
        "paper_review": True,    # ④ 终稿前
    }

    # A/B 验证开关：质量门（G2 门循环/G3 修复回路/G4 终审）与三期 Agent 契约可整体关闭还原基线
    QUALITY_GATES_ENABLED: bool = True
    AGENT_CONTRACTS_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=".env.dev",
        env_file_encoding="utf-8",
        extra="forbid",  # 拼错/多余的配置键启动即报错，不静默通过
        validate_assignment=True,
    )

    @classmethod
    def from_env(cls, env: str | None = None):
        """根据环境名称加载对应配置。

        Args:
            env: 环境名称（如 dev、prod），默认从 ENV 环境变量获取。
        """
        env = env or os.getenv("ENV", "dev")
        env_file = f".env.{env.lower()}"
        return cls(_env_file=env_file, _env_file_encoding="utf-8")  # type: ignore[call-arg]


settings = Settings()
