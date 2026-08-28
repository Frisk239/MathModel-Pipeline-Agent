"""Coordinator/Writer Agent 响应解析单测（FakeModel 注入，不碰真实 LLM）。"""

import asyncio

import pytest

from app.core.agents.coordinator_agent import MAX_JSON_RETRIES, CoordinatorAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.types import StandardResponse


class _FakeLLM:
    def __init__(self, contents=None, raise_on_call=False):
        self.contents = list(contents or [])
        self.raise_on_call = raise_on_call
        self.calls = 0

    async def chat(self, **kwargs) -> StandardResponse:
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("供应商超时")
        return StandardResponse(content=self.contents.pop(0))


class TestCoordinatorParsing:
    def test_parses_fenced_json_with_required_files(self):
        model = _FakeLLM(
            [
                '```json\n{"ques_count": 2, "ques1": "问一", "ques2": "问二",'
                ' "required_files": ["a.csv", "b.xlsx"]}\n```'
            ]
        )
        agent = CoordinatorAgent(task_id="coord-1", model=model)  # type: ignore[arg-type]

        result = asyncio.run(agent.run("2024 年某竞赛题面"))

        assert result.ques_count == 2
        assert result.required_files == ["a.csv", "b.xlsx"]

    def test_scalar_required_files_wrapped_into_list(self):
        model = _FakeLLM(
            ['{"ques_count": 1, "ques1": "问", "required_files": "only.csv"}']
        )
        agent = CoordinatorAgent(task_id="coord-2", model=model)  # type: ignore[arg-type]

        result = asyncio.run(agent.run("题面"))

        assert result.required_files == ["only.csv"]

    def test_control_characters_do_not_break_json(self):
        model = _FakeLLM(
            ['{"ques_count": 1, "ques1": "问\\u0000题", "required_files": []}\x00']
        )
        agent = CoordinatorAgent(task_id="coord-3", model=model)  # type: ignore[arg-type]

        result = asyncio.run(agent.run("题面"))

        assert result.ques_count == 1

    def test_retries_then_raises_after_max_attempts(self):
        model = _FakeLLM(["not json"] * MAX_JSON_RETRIES)
        agent = CoordinatorAgent(task_id="coord-4", model=model)  # type: ignore[arg-type]

        with pytest.raises(ValueError):
            asyncio.run(agent.run("题面"))

        assert model.calls == MAX_JSON_RETRIES
        # 每次失败都追加了错误反馈提示，供下一轮修正
        feedbacks = [
            m for m in agent.chat_history if m.get("role") == "system" and "格式错误" in m.get("content", "")
        ]
        assert len(feedbacks) == MAX_JSON_RETRIES


class TestWriterSummarize:
    def test_returns_summary_content(self):
        model = _FakeLLM(["已完成 EDA、建模与论文初稿"])
        agent = WriterAgent(task_id="writer-1", model=model)  # type: ignore[arg-type]

        summary = asyncio.run(agent.summarize())

        assert summary == "已完成 EDA、建模与论文初稿"
        assert model.calls == 1

    def test_falls_back_to_placeholder_on_provider_error(self):
        model = _FakeLLM(raise_on_call=True)
        agent = WriterAgent(task_id="writer-2", model=model)  # type: ignore[arg-type]

        summary = asyncio.run(agent.summarize())

        assert "无法生成详细总结" in summary
