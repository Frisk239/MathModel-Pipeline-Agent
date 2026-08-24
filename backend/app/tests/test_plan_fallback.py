"""方案复杂度预算 + 候选回退（v3/P2-5，灭 F4 方案过重）单测。"""

from app.core.prompts.modeler import _contract_block
from app.core.quality.contracts import Obligation, RoadmapItem, Severity
from app.core.quality.g2_code_gate import is_plan_fallback_candidate
from app.config.setting import settings


def _item(problem: str, sev: Severity = Severity.CRITICAL) -> RoadmapItem:
    return RoadmapItem(
        id="t",
        problem=problem,
        evidence_anchor="",
        severity=sev,
        obligation=Obligation.MUST_FIX,
        cost_scope="re_analysis",
        acceptance_criteria="",
        target="notebook",
    )


def test_fallback_triggered_by_unimplemented_plan_critical():
    items = [
        _item("[inconsistency] 方案承诺的多周期MILP优化模型在代码中仍未实现"),
        _item("[minor] 叙述与实现不符", Severity.MINOR),
    ]
    assert is_plan_fallback_candidate(items) is True


def test_fallback_not_triggered_without_critical():
    # 全是 major/minor 的实现瑕疵：不是"方案未实现"，普通降级即可
    items = [
        _item("[inconsistency] 列名不统一导致 KeyError", Severity.MAJOR),
        _item("[method_misuse] 混池 IQR", Severity.MINOR),
    ]
    assert is_plan_fallback_candidate(items) is False


def test_fallback_not_triggered_by_unrelated_critical():
    items = [
        _item("[data_leakage] 使用了预测目标日的完整数据做训练"),
    ]
    assert is_plan_fallback_candidate(items) is False


def test_contract_block_contains_implementation_budget():
    if not settings.AGENT_CONTRACTS_ENABLED:
        return  # A/B 关闭态保持基线纯净
    block = _contract_block()
    assert "实现预算（Implementation Budget" in block
    assert "3 轮修复内可完整交付" in block
    assert "宁可完整实现一个较简单模型" in block
