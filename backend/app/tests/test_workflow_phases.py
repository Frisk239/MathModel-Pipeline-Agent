"""workflow 编排层拆分后的阶段级单测（Fake 注入，不碰真实 LLM/Redis）。

覆盖：配置预校验、G1 门双分支（AUTO_MODE 降级 / 人工否决）、G2 修复循环
（耗尽降级 / 直接通过）、终审 A/B 基线短路。
"""

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.config.setting import settings
from app.core.quality.contracts import (
    GateName,
    GateReport,
    GateVerdict,
    Obligation,
    RoadmapItem,
    Severity,
)
from app.core.task_state import MAX_REPAIR_ROUNDS, TaskPhase, TaskStateMachine
from app.core.workflow import MathModelWorkFlow, _PipelineContext


async def _noop(*args, **kwargs):
    return None


def _make_wf(tmp_path) -> MathModelWorkFlow:
    wf = MathModelWorkFlow.__new__(MathModelWorkFlow)
    wf.task_id = "wf-test"
    wf.work_dir = str(tmp_path)
    wf.state = TaskStateMachine("wf-test", str(tmp_path))
    wf.gate_reports = []
    wf.g1_leftover_items = []
    wf.g2_leftover_items = []
    wf._g2_fallback_used = set()
    wf._writing_started = False
    wf.cancel_event = None
    wf.questions = {}
    return wf


def _material_report(gate, problems):
    items = [
        RoadmapItem(
            id=f"{gate.value}-i{i}",
            problem=p,
            evidence_anchor="test",
            severity=Severity.CRITICAL,
            obligation=Obligation.MUST_FIX,
            cost_scope="code",
            acceptance_criteria="修复即过",
            target="notebook",
        )
        for i, p in enumerate(problems, 1)
    ]
    return GateReport(
        gate=gate, verdict=GateVerdict.MATERIAL, summary=f"{gate.value} 拦截", items=items
    )


# ---- 配置预校验 ----


def test_validate_llm_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "COORDINATOR_MODEL", "")
    wf = _make_wf(tmp_path)
    with pytest.raises(ValueError, match="配置缺失"):
        wf._validate_llm_config()


# ---- G1 数据门 ----


def _g1_ctx():
    ctx = _PipelineContext(problem=NS(ques_all="数据见附件1"))
    ctx.coordinator_response = NS(required_files=["附件1"])
    return ctx


def test_g1_gate_auto_mode_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_data_completeness",
        lambda req, wd: _material_report(GateName.G1, ["附件1"]),
    )
    monkeypatch.setattr(settings, "AUTO_MODE", True)

    wf = _make_wf(tmp_path)
    wf.state.transition(TaskPhase.SPLITTING)

    asyncio.run(wf._run_g1_gate(_g1_ctx()))

    assert wf.state.phase == TaskPhase.MODELING
    assert len(wf.g1_leftover_items) == 1
    assert any(h["action"] == "auto_degraded" for h in wf.state.override_history)


def test_g1_gate_human_reject_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_data_completeness",
        lambda req, wd: _material_report(GateName.G1, ["附件1"]),
    )
    monkeypatch.setattr(settings, "AUTO_MODE", False)

    async def _reject(*a, **k):
        return NS(action="reject", feedback="补传附件")

    monkeypatch.setattr("app.core.workflow.wait_for_approval", _reject)

    wf = _make_wf(tmp_path)
    wf.state.transition(TaskPhase.SPLITTING)

    with pytest.raises(ValueError, match="G1"):
        asyncio.run(wf._run_g1_gate(_g1_ctx()))

    assert wf.state.phase == TaskPhase.FAILED


# ---- G2 修复循环 ----


def _g2_ctx(tmp_path, coder_agent):
    wf = _make_wf(tmp_path)
    for phase in (
        TaskPhase.SPLITTING,
        TaskPhase.G1_GATE,
        TaskPhase.MODELING,
        TaskPhase.CODING,
    ):
        wf.state.transition(phase)
    ctx = _PipelineContext(problem=NS(ques_all="q", task_id="wf-test"))
    ctx.review_llm = None
    ctx.modeler_response = NS(questions_solution={"ques1": "MILP 方案"})
    ctx.coder_agent = coder_agent
    return wf, ctx


class _FakeCoder:
    def __init__(self):
        self.calls = 0

    async def run(self, prompt, subtask_title):
        self.calls += 1
        return NS(created_images=[], code_response="code")


def test_g2_loop_exhausts_and_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_notebook_artifacts", lambda *a, **k: []
    )
    monkeypatch.setattr("app.core.workflow.run_g2_ai_review", _noop)
    monkeypatch.setattr(
        "app.core.workflow.combine_g2",
        lambda l1, l2: _material_report(GateName.G2_L2, ["结果文件与代码不一致"]),
    )
    monkeypatch.setattr(
        "app.core.workflow.is_plan_fallback_candidate", lambda items: False
    )
    monkeypatch.setattr(
        "app.core.workflow.archive_stale_deliverables", lambda *a, **k: []
    )
    monkeypatch.setattr(settings, "QUALITY_GATES_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_MODE", True)

    coder = _FakeCoder()
    wf, ctx = _g2_ctx(tmp_path, coder)

    resp = asyncio.run(
        wf._run_g2_repair_loop(
            ctx, "ques1", "prompt", 0.0, NS(created_images=[], code_response="c0")
        )
    )

    # 3 轮修复全部耗尽 → AUTO_MODE 降级放行，遗留如实记录
    assert coder.calls == MAX_REPAIR_ROUNDS
    assert wf.g2_leftover_items
    assert any(h["action"] == "auto_degraded" for h in wf.state.override_history)
    assert resp.code_response == "code"


def test_g2_loop_pass_returns_without_repair(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_notebook_artifacts", lambda *a, **k: []
    )
    monkeypatch.setattr("app.core.workflow.run_g2_ai_review", _noop)
    monkeypatch.setattr(
        "app.core.workflow.combine_g2",
        lambda l1, l2: GateReport(
            gate=GateName.G2_L2, verdict=GateVerdict.MINOR, summary="ok", items=[]
        ),
    )
    monkeypatch.setattr(settings, "QUALITY_GATES_ENABLED", True)

    coder = _FakeCoder()
    wf, ctx = _g2_ctx(tmp_path, coder)

    resp = asyncio.run(
        wf._run_g2_repair_loop(
            ctx, "ques1", "prompt", 0.0, NS(created_images=[], code_response="c0")
        )
    )

    assert coder.calls == 0  # 非 material 直接放行，零修复
    assert resp.code_response == "c0"


# ---- 终审 A/B 基线 ----


def test_final_review_ab_baseline_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(settings, "QUALITY_GATES_ENABLED", False)

    wf = _make_wf(tmp_path)
    for phase in (
        TaskPhase.SPLITTING,
        TaskPhase.G1_GATE,
        TaskPhase.MODELING,
        TaskPhase.CODING,
        TaskPhase.WRITING,
    ):
        wf.state.transition(phase)

    ctx = _PipelineContext(problem=NS(ques_all="q", task_id="wf-test"))
    ctx.user_output = NS(save_result=lambda: None, get_res=lambda: "res")

    asyncio.run(wf._run_final_review(ctx))

    assert wf.state.phase == TaskPhase.COMPLETED
    assert (tmp_path / "verify_report.md").exists()
