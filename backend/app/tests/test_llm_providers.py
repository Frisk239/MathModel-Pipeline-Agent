"""LLM Provider 思考参数与响应归一化的单元测试。"""

import asyncio
import json
from types import SimpleNamespace

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


class TestAnthropicTextToolCallFallback:
    """兼容供应商把工具调用放进 text block 的非标准响应。"""

    def test_native_anthropic_tool_schema_is_not_dropped(self, provider):
        tool = {
            "name": "execute_code",
            "description": "执行 Python",
            "input_schema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        }

        assert provider._convert_tools([tool]) == [tool]

    @pytest.mark.parametrize(
        "raw_text",
        [
            (
                '<tool_call>\n{"name":"execute_code","arguments":'
                '{"code":"print(1)"}}\n</tool_call>'
            ),
            (
                "<tool_call>\n<name>execute_code</name>\n"
                "<code>\nprint(1)\n</code>\n</tool_call>"
            ),
        ],
    )
    def test_text_tool_call_is_normalized(
        self, provider, monkeypatch, raw_text
    ):
        raw_response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=raw_text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )

        class FakeMessages:
            async def create(self, **kwargs):
                return raw_response

        class FakeClient:
            def __init__(self, **kwargs):
                self.messages = FakeMessages()

        monkeypatch.setattr(
            "app.core.llm.providers.anthropic.AsyncAnthropic", FakeClient
        )
        response = asyncio.run(
            provider.call(
                messages=[{"role": "user", "content": "执行代码"}],
                model="glm-5.3",
                api_key="test-key",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "execute_code",
                            "description": "执行 Python",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                tool_choice="required",
            )
        )

        assert response.content is None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "execute_code"
        assert json.loads(response.tool_calls[0].arguments) == {
            "code": "print(1)"
        }


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
