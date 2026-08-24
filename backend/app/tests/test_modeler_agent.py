"""ModelerAgent 结构化输出重试的回归测试。"""

import asyncio

from app.core.agents.modeler_agent import ModelerAgent
from app.core.llm.types import StandardResponse
from app.schemas.A2A import CoordinatorToModeler


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0
        self.responses = [
            StandardResponse(content=None),
            StandardResponse(content='{"eda": "已生成"}'),
        ]

    async def chat(self, **kwargs) -> StandardResponse:
        self.calls += 1
        return self.responses.pop(0)


def test_empty_response_retries_like_invalid_json():
    """供应商偶发空正文时应走既有 JSON 重试，而不是立即终止任务。"""
    model = _FakeModel()
    agent = ModelerAgent(task_id="modeler-empty-retry", model=model)  # type: ignore[arg-type]
    request = CoordinatorToModeler(
        questions={"ques_count": 1, "ques1": "测试问题"},
        ques_count=1,
    )

    result = asyncio.run(agent.run(request))

    assert model.calls == 2
    assert result.questions_solution == {"eda": "已生成"}
    assert agent.chat_history[-1]["role"] == "user"
    assert "为空" in agent.chat_history[-1]["content"]
