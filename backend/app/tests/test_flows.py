"""Flows 任务级 prompt 注入（v3/P2-1、P2-2）单测。"""

from app.core.flows import DATA_DISCIPLINE, Flows


def _flows(env: str | None) -> Flows:
    questions = {"background": "bg", "ques_count": 1, "ques1": "问一"}
    return Flows(questions, env_capability=env)


def test_solution_prompts_contain_data_discipline_with_or_without_env():
    from app.core.agents.modeler_agent import ModelerToCoder

    for env in (None, "【代码执行环境能力清单（本地实测）】可用：PuLP"):
        flows = _flows(env)
        sol = flows.get_solution_flows(
            flows.questions, ModelerToCoder(questions_solution={})
        )
        assert set(sol) == {"eda", "ques1", "sensitivity_analysis"}
        for cfg in sol.values():
            prompt = cfg["coder_prompt"]
            assert "数据与主链路纪律" in prompt
            assert "合法标识符" in prompt
            assert "先落盘" in prompt
            assert "主链路优先级" in prompt


def test_solution_prompts_contain_env_capability_when_provided():
    from app.core.agents.modeler_agent import ModelerToCoder

    flows = _flows("【代码执行环境能力清单（本地实测）】可用：PuLP 3.3.2")
    sol = flows.get_solution_flows(
        flows.questions, ModelerToCoder(questions_solution={})
    )
    for cfg in sol.values():
        assert "PuLP 3.3.2" in cfg["coder_prompt"]


def test_data_discipline_covers_f1_failure_modes():
    # F1 实证三类执行错误的针对性条款
    assert "KeyError" in DATA_DISCIPLINE
    assert "回头修改清洗逻辑" in DATA_DISCIPLINE
    assert "顺序不得颠倒" in DATA_DISCIPLINE
