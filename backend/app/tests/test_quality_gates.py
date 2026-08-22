"""质量门与任务状态机的一期单元测试。

纪律（docs/quality-gates-plan.md 一期 DoD）：
- 状态机禁止转移表每条一个用例；
- 每个门检查项都有"破坏时触发"的用例，且豁免场景不误报。
"""

from pathlib import Path

import pytest

from app.core.quality.checks import SHINGLE_CHARS, shingles
from app.core.quality.contracts import GateVerdict
from app.core.quality.g1_data_gate import (
    check_data_completeness,
    extract_required_from_problem,
)
from app.core.quality.g3_text_gate import check_citation_integrity, check_section_text
from app.core.task_state import (
    MAX_REPAIR_ROUNDS,
    PROHIBITED_TRANSITIONS,
    TaskPhase,
    TaskStateMachine,
    TransitionError,
)


# ---- 状态机：合法路径 ----


def _fresh_sm(tmp_path: Path) -> TaskStateMachine:
    return TaskStateMachine("test-task", str(tmp_path))


def test_happy_path_transitions(tmp_path):
    sm = _fresh_sm(tmp_path)
    for phase in [
        TaskPhase.SPLITTING,
        TaskPhase.G1_GATE,
        TaskPhase.MODELING,
        TaskPhase.CODING,
        TaskPhase.WRITING,
        TaskPhase.ASSEMBLING,
        TaskPhase.COMPLETED,
    ]:
        sm.transition(phase)
    assert sm.phase == TaskPhase.COMPLETED


def test_g1_failure_marks_failed(tmp_path):
    sm = _fresh_sm(tmp_path)
    sm.transition(TaskPhase.SPLITTING)
    sm.transition(TaskPhase.G1_GATE)
    sm.fail("G1 缺少附件1")
    assert sm.phase == TaskPhase.FAILED
    assert "附件1" in sm.fail_reason


# ---- 状态机：禁止转移（每条一个用例）----


def _to_phase(sm: TaskStateMachine, phase: TaskPhase) -> None:
    """直接设置阶段（仅测试用：构造禁止转移的起点状态）。"""
    sm.phase = phase


@pytest.mark.parametrize(
    "desc, src, dst",
    [(d, s, t) for d, (s, t) in PROHIBITED_TRANSITIONS],
    ids=[d for d, _ in PROHIBITED_TRANSITIONS],
)
def test_prohibited_transitions(tmp_path, desc, src, dst):
    sm = _fresh_sm(tmp_path)
    _to_phase(sm, src)
    with pytest.raises(TransitionError, match="非法状态转移"):
        sm.transition(dst)


# ---- 状态机：修复轮次 ----


def test_repair_rounds_capped(tmp_path):
    sm = _fresh_sm(tmp_path)
    for i in range(1, MAX_REPAIR_ROUNDS + 1):
        assert sm.request_repair("g3") == i
    with pytest.raises(TransitionError, match="修复轮次已达上限"):
        sm.request_repair("g3")


def test_persistence_roundtrip(tmp_path):
    sm = _fresh_sm(tmp_path)
    sm.transition(TaskPhase.SPLITTING)
    sm.repair_rounds = {"g3": 2}
    sm.save()

    restored = TaskStateMachine.load("test-task", str(tmp_path))
    assert restored is not None
    assert restored.phase == TaskPhase.SPLITTING
    assert restored.repair_rounds == {"g3": 2}
    assert len(restored.transitions) == 1


def test_load_missing_returns_none(tmp_path):
    assert TaskStateMachine.load("nope", str(tmp_path)) is None


# ---- G1 数据门 ----


def test_g1_missing_attachment(tmp_path):
    (tmp_path / "附件2 (Attachment 2).csv").write_text("x", encoding="utf-8")
    report = check_data_completeness(["附件1", "附件2"], str(tmp_path))
    assert report.verdict == GateVerdict.MATERIAL
    assert "附件1" in report.summary
    assert "附件2" not in report.summary.split("缺少")[1].split("。")[0]


def test_g1_all_present_fuzzy_names(tmp_path):
    # 命名变体也应命中：全角空格、英文名
    (tmp_path / "附件 1.csv").write_text("x", encoding="utf-8")
    (tmp_path / "attachment2.xlsx").write_text("x", encoding="utf-8")
    report = check_data_completeness(["附件1", "附件2"], str(tmp_path))
    assert report.verdict == GateVerdict.PASS


def test_g1_problem_text_extraction():
    text = "请结合附件1与附件 2的数据，并参考 Attachment 3 完成分析。附件1已给出。"
    found = extract_required_from_problem(text)
    assert len(found) == 3  # 附件1/附件2/attachment3 去重


def test_g1_explicit_files_list(tmp_path):
    report = check_data_completeness(
        ["附件1"], str(tmp_path), existing_files=["随便什么.csv"]
    )
    assert report.verdict == GateVerdict.MATERIAL


# ---- G3 节级文本门禁（破坏触发 + 豁免不误报）----


CLEAN_SECTION = (
    "# 五、模型的建立与求解\n\n针对问题一，本文构建灰色预测模型，"
    "通过一次累加生成与最小二乘估计获得参数，滚动验证 MAPE 为 8.6%。"
)


def test_g3_clean_section_passes():
    assert check_section_text(CLEAN_SECTION, "ques1").verdict == GateVerdict.PASS


def test_g3_leak_keyword_triggers():
    bad = CLEAN_SECTION + "\n搜索文献失败: 配置OpenAlex邮箱获取访问文献权利"
    report = check_section_text(bad, "ques1")
    assert report.verdict == GateVerdict.MATERIAL
    assert any("内部信息" in it.problem for it in report.items)


def test_g3_internal_path_triggers():
    bad = CLEAN_SECTION + "\n详见 app/core/workflow.py 与 logs/messages 目录。"
    report = check_section_text(bad, "ques1")
    assert report.verdict == GateVerdict.MATERIAL


def test_g3_placeholder_triggers():
    for placeholder in ["TODO：补充此处", "FIXME", "待补充", "PLACEHOLDER"]:
        report = check_section_text(CLEAN_SECTION + f"\n{placeholder}", "ques1")
        assert report.verdict != GateVerdict.PASS, placeholder


def test_g3_shingle_leak_detected():
    internal = "搜索服务暂时不可用。请基于已有材料继续撰写本节内容，不要在论文正文中提及搜索失败或本条提示。"
    # 逐字复制内部材料（含 ≥12 词连续片段）
    bad = CLEAN_SECTION + "\n" + internal
    report = check_section_text(
        bad, "ques1", internal_sources=[internal], exemptions=[]
    )
    assert report.verdict == GateVerdict.MATERIAL
    assert any("内部材料" in it.problem for it in report.items)


def test_g3_shingle_exemption_no_false_positive():
    # 题面原文（豁免堆）即使与内部材料重叠也不报
    internal = "这句话恰好也出现在题目里 please note this internal fallback text appears verbatim here now"
    report = check_section_text(
        CLEAN_SECTION + "\n" + internal,
        "ques1",
        internal_sources=[internal],
        exemptions=[internal],
    )
    assert report.verdict == GateVerdict.PASS


def test_g3_minor_only_allows_pass_with_minor():
    # 单条 Minor（幽灵条目类不在此出现，这里验证占位符单条 Major 即 MATERIAL）
    report = check_section_text(CLEAN_SECTION + "\n待补充", "ques1")
    assert report.verdict == GateVerdict.MATERIAL


# ---- G3 引用双向完整性 ----


def test_citation_integrity_empty_references():
    text = "如文献{[^1] Zhang et al. 2019}所示。"
    report = check_citation_integrity(text)
    assert report.verdict == GateVerdict.MATERIAL
    assert "参考文献列表为空" in report.items[0].problem


def test_citation_integrity_orphan_reference():
    text = "如文献{[^1] Zhang 2019}与{[^2] Li 2020}所示。\n\n## 参考文献\n\n[^1]: Zhang 2019"
    report = check_citation_integrity(text)
    assert report.verdict == GateVerdict.MATERIAL
    assert "孤儿引用" in report.items[0].problem


def test_citation_integrity_pass():
    text = "如文献{[^1] Zhang 2019: Deep learning}所示。\n\n## 参考文献\n\n[^1]: Zhang 2019. Deep learning"
    report = check_citation_integrity(text)
    assert report.verdict == GateVerdict.PASS


def test_citation_integrity_no_citations_pass():
    assert check_citation_integrity("正文无引用。").verdict == GateVerdict.PASS


# ---- checks 工具 ----


def test_shingles_character_window():
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳" * 2
    sh = shingles(text)
    assert len(sh) >= 1
    assert all(len(s) == SHINGLE_CHARS for s in sh)


def test_shingles_short_text_empty():
    assert shingles("太短了") == set()
