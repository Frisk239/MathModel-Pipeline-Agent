"""LLM Provider 思考参数映射的单元测试。"""

import pytest

from app.core.llm.llm import LLM, LLMConfigError
from app.core.llm.providers.anthropic import EFFORT_TO_BUDGET, AnthropicProvider


@pytest.fixture
def provider():
    return AnthropicProvider()


class TestBuildThinking:
    """Anthropic thinking 参数组装。"""

    def test_explicit_budget_wins_over_effort(self, provider):
        assert provider._build_thinking("low", 32768, 128000) == {
            "type": "enabled",
            "budget_tokens": 32768,
        }

    def test_effort_mapped_to_budget(self, provider):
        assert provider._build_thinking("high", None, 128000) == {
            "type": "enabled",
            "budget_tokens": EFFORT_TO_BUDGET["high"],
        }

    def test_off_disables_even_with_budget(self, provider):
        assert provider._build_thinking("off", 65536, 128000) is None

    def test_empty_config_returns_none(self, provider):
        assert provider._build_thinking(None, None, 4096) is None
        assert provider._build_thinking("", None, 4096) is None

    def test_unknown_effort_returns_none(self, provider):
        # 未收录的自定义档位无法换算，保持不开启
        assert provider._build_thinking("ultra", None, 128000) is None

    def test_budget_clamped_below_max_tokens(self, provider):
        # Anthropic 要求 budget < max_tokens，超出时收敛到 max_tokens - 1024
        result = provider._build_thinking("max", None, 20000)
        assert result == {"type": "enabled", "budget_tokens": 18976}

    def test_budget_floor_is_1024(self, provider):
        result = provider._build_thinking(None, 100, 4096)
        assert result == {"type": "enabled", "budget_tokens": 1024}

    def test_common_efforts_all_mappable(self):
        for effort in ["low", "medium", "high", "max", "minimal", "xhigh"]:
            assert EFFORT_TO_BUDGET[effort] >= 1024


class TestLLMConfig:
    """LLM 封装层配置校验。"""

    def test_missing_model_raises_config_error(self):
        llm = LLM(api_key="k", model=None)
        with pytest.raises(LLMConfigError):
            llm._validate_config("TestAgent")

    def test_missing_api_key_raises_config_error(self):
        llm = LLM(api_key="", model="m")
        with pytest.raises(LLMConfigError):
            llm._validate_config("TestAgent")

    def test_reasoning_fields_stored(self):
        llm = LLM(
            api_key="k",
            model="m",
            reasoning_effort="xhigh",
            thinking_budget=16384,
        )
        assert llm.reasoning_effort == "xhigh"
        assert llm.thinking_budget == 16384

    def test_default_provider_is_openai_chat(self):
        from app.core.llm.providers.openai_chat import OpenAIChatProvider

        llm = LLM(api_type=None)
        assert isinstance(llm.provider, OpenAIChatProvider)
