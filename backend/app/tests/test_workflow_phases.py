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
    # 含 critical 场景的耗尽降级：固定 3 轮使断言不受默认值调整影响
    monkeypatch.setattr(settings, "G2_MAX_REPAIR_ROUNDS", MAX_REPAIR_ROUNDS)

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


# ---- G2 分级放行与轮次上限（2026-08-28 A/B 后的策略调整） ----


def test_g2_loop_exhausts_without_critical_tiered_release(tmp_path, monkeypatch):
    """轮次耗尽且剩余无 critical → 分级放行：遗留记录但不记 auto_degraded。"""
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_notebook_artifacts", lambda *a, **k: []
    )
    monkeypatch.setattr("app.core.workflow.run_g2_ai_review", _noop)
    monkeypatch.setattr(settings, "G2_MAX_REPAIR_ROUNDS", 2)
    monkeypatch.setattr(
        "app.core.workflow.combine_g2",
        lambda l1, l2: GateReport(
            gate=GateName.G2_L2,
            verdict=GateVerdict.MATERIAL,
            summary="仅 minor 拦截",
            items=[
                RoadmapItem(
                    id="g2-minor-1",
                    problem="图表标题不完整",
                    evidence_anchor="t",
                    severity=Severity.MINOR,
                    obligation=Obligation.CONSIDER,
                    cost_scope="code",
                    acceptance_criteria="补标题",
                    target="notebook",
                )
            ],
        ),
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

    assert coder.calls == 2  # 修复到轮次上限
    assert resp.code_response == "code"
    assert wf.g2_leftover_items  # 遗留照记（局限性章节）
    # 分级放行：不算降级审计
    assert not any(
        h["action"] == "auto_degraded" for h in wf.state.override_history
    )


def test_g2_round_cap_is_configurable(tmp_path, monkeypatch):
    """G2 修复轮上限走 settings.G2_MAX_REPAIR_ROUNDS，不再写死 3。"""
    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(
        "app.core.workflow.check_notebook_artifacts", lambda *a, **k: []
    )
    monkeypatch.setattr("app.core.workflow.run_g2_ai_review", _noop)
    monkeypatch.setattr(
        "app.core.workflow.combine_g2",
        lambda l1, l2: _material_report(GateName.G2_L2, ["含 critical 的问题"]),
    )
    monkeypatch.setattr(
        "app.core.workflow.is_plan_fallback_candidate", lambda items: False
    )
    monkeypatch.setattr(
        "app.core.workflow.archive_stale_deliverables", lambda *a, **k: []
    )
    monkeypatch.setattr(settings, "QUALITY_GATES_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_MODE", True)
    monkeypatch.setattr(settings, "G2_MAX_REPAIR_ROUNDS", 4)

    coder = _FakeCoder()
    wf, ctx = _g2_ctx(tmp_path, coder)

    asyncio.run(
        wf._run_g2_repair_loop(
            ctx, "ques1", "prompt", 0.0, NS(created_images=[], code_response="c0")
        )
    )

    assert coder.calls == 4


def test_request_repair_cap_override(tmp_path):
    from app.core.task_state import TransitionError

    sm = TaskStateMachine("cap-test", str(tmp_path))
    assert sm.request_repair("g3", cap=1) == 1
    with pytest.raises(TransitionError, match="上限 1"):
        sm.request_repair("g3", cap=1)
    # cap 缺省回退模块默认
    assert sm.request_repair("g3") == 2


# ---- 门报告非空断言（防评审静默失效：stream_agent_type 回归的教训） ----


def test_final_review_g4_actually_runs_and_reports(tmp_path, monkeypatch):
    """质量门开启时 G4 终审必须真实执行并产出报告——评审调用静默失败
    （如 LLM 层回归导致全部 not_checked）时本测试必须变红。"""
    import json as _json

    from app.core.llm.types import StandardResponse

    class _FakeReviewLLM:
        async def chat(self, **kwargs):
            return StandardResponse(
                content=_json.dumps(
                    {
                        "dimensions": {
                            "model_soundness": "MEETS",
                            "assumption_validity": "MEETS",
                            "solution_correctness": "MEETS",
                            "reproducibility": "MEETS",
                            "result_validity": "MEETS",
                            "writing_norm": "MEETS",
                            "sensitivity": "MEETS",
                        },
                        "items": [],
                    }
                )
            )

    monkeypatch.setattr("app.core.workflow.redis_manager", NS(publish_message=_noop))
    monkeypatch.setattr(settings, "QUALITY_GATES_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_MODE", True)

    wf = _make_wf(tmp_path)
    for phase in (
        TaskPhase.SPLITTING,
        TaskPhase.G1_GATE,
        TaskPhase.MODELING,
        TaskPhase.CODING,
        TaskPhase.WRITING,
    ):
        wf.state.transition(phase)

    (tmp_path / "res.md").write_text("# 论文\n正文", encoding="utf-8")

    ctx = _PipelineContext(problem=NS(ques_all="q", task_id="wf-test"))
    ctx.user_output = NS(save_result=lambda: None, get_res=lambda: "res")
    ctx.review_llm = _FakeReviewLLM()

    asyncio.run(wf._run_final_review(ctx))

    g4 = [r for r in wf.gate_reports if r.gate == GateName.G4]
    assert g4, "G4 报告缺失：终审未执行"
    assert "未执行" not in g4[0].summary, f"终审被静默跳过: {g4[0].summary}"
    assert g4[0].verdict.value != "material"
    assert wf.state.phase == TaskPhase.COMPLETED
