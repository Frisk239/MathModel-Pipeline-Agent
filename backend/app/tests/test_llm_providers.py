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

    def test_injects_budget_when_unconfigured_and_empty(self):
        # hy3 空响应事故形态：max_tokens 未配置（端点默认预算过小），
        # content=None + stop_reason=None 连发，旧逻辑无物可放大直接放行
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                StandardResponse(content=None, stop_reason=None),
                StandardResponse(content="ok", stop_reason="end_turn"),
            ]
        )
        llm.max_tokens = None
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [None, 16384]  # 未配置时注入下限预算重试一次
        assert resp.content == "ok"

    def test_no_widen_on_normal_empty_end_turn(self):
        # 正常结束（end_turn）但内容为空：模型行为问题，不是预算问题，不重试
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [StandardResponse(content=None, stop_reason="end_turn")]
        )
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == [8192]
        assert resp.content is None


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


class TestOpenAIChatStream:
    """openai-chat 流式聚合：delta 上抛 + tool_calls 分帧拼接 + usage/finish 采集。"""

    def test_stream_aggregation_and_on_delta(self, monkeypatch):
        from types import SimpleNamespace as NS

        def chunk(delta=None, finish=None, usage=None):
            choices = [NS(delta=delta, finish_reason=finish)] if (delta or finish) else []
            return NS(choices=choices, usage=usage)

        chunks = [
            chunk(delta=NS(content=None, reasoning_content="思前", tool_calls=None)),
            chunk(delta=NS(content="答", reasoning_content=None, tool_calls=None)),
            chunk(delta=NS(
                content=None, reasoning_content=None,
                tool_calls=[NS(index=0, id="t1", function=NS(name="execute_code", arguments='{"code": '))],
            )),
            chunk(delta=NS(
                content=None, reasoning_content=None,
                tool_calls=[NS(index=0, id=None, function=NS(name=None, arguments='"print(1)"}'))],
            )),
            chunk(finish="tool_calls", usage=NS(prompt_tokens=5, completion_tokens=9)),
        ]

        class FakeCompletions:
            async def create(self, **kwargs):
                assert kwargs.get("stream") is True

                async def _gen():
                    for c in chunks:
                        yield c

                return _gen()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = NS(completions=FakeCompletions())

        monkeypatch.setattr(
            "app.core.llm.providers.openai_chat.AsyncOpenAI", FakeClient
        )
        from app.core.llm.providers.openai_chat import OpenAIChatProvider

        received: list[tuple[str, str]] = []

        async def on_delta(kind: str, text: str) -> None:
            received.append((kind, text))

        resp = asyncio.run(
            OpenAIChatProvider().call(
                messages=[{"role": "user", "content": "hi"}],
                model="hy3",
                api_key="k",
                on_delta=on_delta,
            )
        )
        assert received == [("thinking", "思前"), ("text", "答")]
        assert resp.content == "答"
        assert resp.reasoning_content == "思前"
        # openai finish_reason 已归一化为 anthropic 词表
        assert resp.stop_reason == "tool_use"
        assert resp.usage.prompt_tokens == 5 and resp.usage.completion_tokens == 9

    def test_finish_reason_length_normalized(self, monkeypatch):
        """openai 的 length 截断必须归一化为 max_tokens，否则 llm 层放大永不触发。"""
        from types import SimpleNamespace as NS

        def chunk(delta=None, finish=None, usage=None):
            choices = [NS(delta=delta, finish_reason=finish)] if (delta or finish) else []
            return NS(choices=choices, usage=usage)

        chunks = [
            chunk(delta=NS(content="部分", reasoning_content=None, tool_calls=None)),
            chunk(finish="length"),
        ]

        class FakeCompletions:
            async def create(self, **kwargs):
                async def _gen():
                    for c in chunks:
                        yield c

                return _gen()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = NS(completions=FakeCompletions())

        monkeypatch.setattr(
            "app.core.llm.providers.openai_chat.AsyncOpenAI", FakeClient
        )
        from app.core.llm.providers.openai_chat import OpenAIChatProvider

        resp = asyncio.run(
            OpenAIChatProvider().call(
                messages=[{"role": "user", "content": "hi"}],
                model="hy3",
                api_key="k",
            )
        )
        assert resp.stop_reason == "max_tokens"


class TestModelFailover:
    """过载/超时立即切备用模型；非连接类错误不切。"""

    @pytest.fixture(autouse=True)
    def _mute_redis(self, monkeypatch):
        published = []

        async def fake_publish(task_id, message):
            published.append(message)

        monkeypatch.setattr(
            "app.services.redis_manager.redis_manager.publish_message",
            fake_publish,
        )
        self.published = published

    def _make_llm(self, side_effects, **llm_kwargs):
        from app.core.llm.types import StandardResponse

        calls = []

        class FakeProvider:
            async def call(self, **kwargs):
                calls.append(kwargs["model"])
                effect = side_effects[len(calls) - 1]
                if isinstance(effect, Exception):
                    raise effect
                if isinstance(effect, StandardResponse):
                    return effect
                return StandardResponse(content=str(effect), stop_reason="end_turn")

        llm = LLM(api_key="k", **llm_kwargs)
        llm.provider = FakeProvider()
        return llm, calls

    def test_503_switches_to_next_model(self):
        from app.core.llm.types import StandardResponse

        llm, calls = self._make_llm(
            [
                RuntimeError("Error code: 503 - overloaded"),
                StandardResponse(content="ok", stop_reason="end_turn"),
            ],
            model="hy3",
            fallback_models="ox-alpha-free, glm-4.6",
        )
        resp = asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        assert calls == ["hy3", "ox-alpha-free"]
        assert resp.content == "ok"
        assert llm.model == "ox-alpha-free"

    def test_single_model_conn_error_keeps_retry_budget(self, monkeypatch):
        async def no_sleep(_delay):
            return None

        monkeypatch.setattr("app.core.llm.llm.asyncio.sleep", no_sleep)
        llm, calls = self._make_llm(
            [
                RuntimeError("Error code: 503 - overloaded"),
                RuntimeError("Error code: 503 - overloaded"),
                RuntimeError("Error code: 503 - overloaded"),
            ],
            model="hy3",
        )
        with pytest.raises(RuntimeError, match="503"):
            asyncio.run(
                llm.chat(
                    history=[{"role": "user", "content": "hi"}],
                    max_retries=3,
                    retry_delay=0,
                )
            )
        assert calls == ["hy3", "hy3", "hy3"]
        assert llm.model == "hy3"

    def test_non_conn_error_does_not_switch(self):
        llm, calls = self._make_llm(
            [
                RuntimeError("400 invalid"),
                RuntimeError("400 invalid"),
            ],
            model="hy3",
            fallback_models="ox-alpha-free",
        )
        with pytest.raises(RuntimeError, match="400 invalid"):
            asyncio.run(
                llm.chat(
                    history=[{"role": "user", "content": "hi"}],
                    max_retries=2,
                    retry_delay=0,
                )
            )
        assert calls == ["hy3", "hy3"]
        assert llm.model == "hy3"

    def test_resolve_model_chain_primary_then_extras(self):
        from app.config.setting import resolve_model_chain

        assert resolve_model_chain("hy3", "ox-alpha-free, glm-4.6") == [
            "hy3",
            "ox-alpha-free",
            "glm-4.6",
        ]

    def test_resolve_model_chain_dedupes_primary(self):
        from app.config.setting import resolve_model_chain

        assert resolve_model_chain("hy3", "hy3, ox-alpha-free") == [
            "hy3",
            "ox-alpha-free",
        ]
        assert resolve_model_chain("hy3", "ox-alpha-free, hy3") == [
            "hy3",
            "ox-alpha-free",
        ]

    def test_resolve_model_chain_empty_extras(self):
        from app.config.setting import resolve_model_chain

        assert resolve_model_chain("hy3", None) == ["hy3"]
        assert resolve_model_chain("hy3", "") == ["hy3"]
        assert resolve_model_chain("hy3", "  ,  ") == ["hy3"]

    def test_503_switch_publishes_info_system_message(self):
        from app.core.llm.types import StandardResponse
        from app.schemas.response import SystemMessage

        llm, calls = self._make_llm(
            [
                RuntimeError("Error code: 503"),
                StandardResponse(content="ok", stop_reason="end_turn"),
            ],
            model="hy3",
            fallback_models="ox-alpha-free",
            task_id="t-failover",
        )
        asyncio.run(llm.chat(history=[{"role": "user", "content": "hi"}]))
        infos = [
            m
            for m in self.published
            if isinstance(m, SystemMessage) and m.type == "info"
        ]
        assert len(infos) >= 1
        content = infos[0].content or ""
        assert "hy3" in content and "ox-alpha-free" in content
        assert calls == ["hy3", "ox-alpha-free"]

    def test_openai_chat_client_gets_httpx_timeout(self, monkeypatch):
        import httpx
        from app.core.llm.providers.openai_chat import OpenAIChatProvider

        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                raise RuntimeError("short-circuit")

        monkeypatch.setattr(
            "app.core.llm.providers.openai_chat.AsyncOpenAI", FakeClient
        )
        with pytest.raises(RuntimeError, match="short-circuit"):
            asyncio.run(
                OpenAIChatProvider().call(
                    messages=[{"role": "user", "content": "hi"}],
                    model="hy3",
                    api_key="k",
                )
            )
        timeout = captured.get("timeout")
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 15.0
        assert timeout.read == 180.0
