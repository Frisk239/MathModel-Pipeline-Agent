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
            stop_reason="end_turn",
        )

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get_final_message(self):
                return raw_response

        class FakeMessages:
            def stream(self, **kwargs):
                return FakeStream()

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


class TestAdaptiveMaxTokens:
    """思考挤占输出预算的自适应放大（max_tokens 截断时 ×2 重试一次）。"""

    def _make_llm(self, responses):
        calls = []

        class FakeProvider:
            async def call(self, **kwargs):
                calls.append(kwargs["max_tokens"])
                return responses[len(calls) - 1]

        llm = LLM(api_key="k", model="m", max_tokens=8192)
        llm.provider = FakeProvider()
        return llm, calls

    def test_widens_once_on_truncation(self):
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                StandardResponse(content=None, stop_reason="max_tokens"),
                StandardResponse(content="ok", stop_reason="end_turn"),
            ]
        )
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [8192, 16384]  # 放大一次后成功
        assert resp.content == "ok"

    def test_widens_only_once_even_if_still_truncated(self):
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                StandardResponse(content=None, stop_reason="max_tokens"),
                StandardResponse(content=None, stop_reason="max_tokens"),
            ]
        )
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [8192, 16384]  # 只放大一次，之后交给上层重试
        assert resp.content is None

    def test_ceiling_caps_widening(self):
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                StandardResponse(content=None, stop_reason="max_tokens"),
            ]
        )
        llm.max_tokens = 128000  # 已在 GLM-5.3 输出上限，不放大
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [128000]
        assert resp.stop_reason == "max_tokens"

    def test_no_widen_with_tool_calls(self):
        from app.core.llm.types import ToolCall
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                StandardResponse(
                    tool_calls=[ToolCall(id="1", name="t", arguments="{}")],
                    stop_reason="max_tokens",
                )
            ]
        )
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [8192]  # 工具调用截断交给反思回路，不放大
        assert len(resp.tool_calls) == 1


class TestAnthropicStreamDelta:
    """真流式：on_delta 逐事件上抛 thinking/text 增量，thinking 采集进 reasoning_content。"""

    def test_on_delta_and_thinking_capture(self, provider, monkeypatch):
        raw_response = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="思前"),
                SimpleNamespace(type="text", text="答"),
            ],
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            stop_reason="end_turn",
        )
        events = [
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="thinking_delta", thinking="思前"),
            ),
            SimpleNamespace(type="content_block_start"),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="答"),
            ),
        ]

        class FakeStream:
            def __aiter__(self):
                self._iter = iter(events)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get_final_message(self):
                return raw_response

        class FakeMessages:
            def stream(self, **kwargs):
                return FakeStream()

        class FakeClient:
            def __init__(self, **kwargs):
                self.messages = FakeMessages()

        monkeypatch.setattr(
            "app.core.llm.providers.anthropic.AsyncAnthropic", FakeClient
        )
        received: list[tuple[str, str]] = []

        async def on_delta(kind: str, text: str) -> None:
            received.append((kind, text))

        response = asyncio.run(
            provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="glm-5.3",
                api_key="k",
                on_delta=on_delta,
            )
        )
        assert received == [("thinking", "思前"), ("text", "答")]
        assert response.content == "答"
        assert response.reasoning_content == "思前"
        assert response.stop_reason == "end_turn"
