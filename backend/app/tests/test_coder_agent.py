"""CoderAgent 工具调用约束的回归测试。"""

import asyncio
import json

from app.config.setting import ApiType
from app.core.agents.coder_agent import CoderAgent
from app.core.llm.types import StandardResponse, ToolCall


class _FakeModel:
    api_type = ApiType.ANTHROPIC

    def __init__(self, responses: list[StandardResponse] | None = None) -> None:
        self.calls: list[dict] = []
        self.responses = responses or [
            StandardResponse(content="我先查看数据文件结构。"),
            StandardResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="execute_code",
                        arguments=json.dumps({"code": "print('ok')"}),
                    )
                ],
            ),
            StandardResponse(content="EDA 已完成。"),
        ]

    async def chat(self, **kwargs) -> StandardResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeInterpreter:
    def __init__(self) -> None:
        self.sections: list[str] = []
        self.executed_codes: list[str] = []

    def add_section(self, section_name: str) -> None:
        self.sections.append(section_name)

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        self.executed_codes.append(code)
        return "ok", False, ""

    async def get_created_images(self, section: str) -> list[str]:
        return []


async def _ignore_publish(*args, **kwargs) -> None:
    return None


def test_no_tool_text_before_execution_is_retried(tmp_path, monkeypatch):
    """供应商忽略 required 时，不得把普通正文误判为子任务完成。"""
    monkeypatch.setattr(
        "app.core.agents.coder_agent.redis_manager.publish_message",
        _ignore_publish,
    )
    model = _FakeModel()
    interpreter = _FakeInterpreter()
    agent = CoderAgent(
        task_id="coder-no-tool-regression",
        model=model,  # type: ignore[arg-type]
        work_dir=str(tmp_path),
        code_interpreter=interpreter,  # type: ignore[arg-type]
    )

    result = asyncio.run(agent.run("执行 EDA", "eda"))

    assert interpreter.executed_codes == ["print('ok')"]
    assert [call["tool_choice"] for call in model.calls] == [
        "required",
        "required",
        "auto",
    ]
    assert result.code_response == "EDA 已完成。"


def test_each_subtask_requires_its_own_execution(tmp_path, monkeypatch):
    """同一 CoderAgent 的后一子任务不能继承前一任务的执行资格。"""
    monkeypatch.setattr(
        "app.core.agents.coder_agent.redis_manager.publish_message",
        _ignore_publish,
    )
    tool_response = lambda call_id, code: StandardResponse(  # noqa: E731
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                name="execute_code",
                arguments=json.dumps({"code": code}),
            )
        ],
    )
    model = _FakeModel(
        responses=[
            tool_response("call-1", "print('first')"),
            StandardResponse(content="第一步完成。"),
            StandardResponse(content="第二步先说明思路。"),
            tool_response("call-2", "print('second')"),
            StandardResponse(content="第二步完成。"),
        ]
    )
    interpreter = _FakeInterpreter()
    agent = CoderAgent(
        task_id="coder-subtask-regression",
        model=model,  # type: ignore[arg-type]
        work_dir=str(tmp_path),
        code_interpreter=interpreter,  # type: ignore[arg-type]
    )

    async def run_both():
        first = await agent.run("执行第一步", "first")
        second = await agent.run("执行第二步", "second")
        return first, second

    first, second = asyncio.run(run_both())

    assert interpreter.executed_codes == ["print('first')", "print('second')"]
    assert [call["tool_choice"] for call in model.calls] == [
        "required",
        "auto",
        "required",
        "required",
        "auto",
    ]
    assert first.code_response == "第一步完成。"
    assert second.code_response == "第二步完成。"
