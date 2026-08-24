"""G2 修复指令分级聚焦（v3/P2-4）单测。"""

from app.core.quality.contracts import Obligation, RoadmapItem, Severity
from app.core.quality.g2_code_gate import format_repair_roadmap


def _item(i: int, sev: Severity) -> RoadmapItem:
    return RoadmapItem(
        id=f"t-{i}",
        problem=f"问题{i}",
        evidence_anchor="",
        severity=sev,
        obligation=Obligation.MUST_FIX if sev != Severity.MINOR else Obligation.SHOULD_FIX,
        cost_scope="section",
        acceptance_criteria=f"验收{i}",
        target="notebook",
    )


def test_roadmap_orders_by_severity_and_groups_minor():
    text = format_repair_roadmap(
        [_item(1, Severity.MINOR), _item(2, Severity.CRITICAL), _item(3, Severity.MAJOR)]
    )
    # critical/major 块在前并带级别标注，minor 单独成块且不强制
    assert text.index("问题2") < text.index("问题3") < text.index("问题1")
    assert "【本轮必须解决（critical/major）】" in text
    assert "[critical] 问题2（验收：验收2）" in text
    assert "【可延后（minor" in text
    assert "【修复优先级】" in text
    assert "主链路" in text


def test_roadmap_all_minor_still_has_priority_note():
    text = format_repair_roadmap([_item(1, Severity.MINOR)])
    assert "【可延后（minor" in text
    assert "【修复优先级】" in text
    assert "【本轮必须解决" not in text
