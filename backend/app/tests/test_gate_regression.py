"""质量门 seeded-defects 回归集。

纪律（借鉴 ARS reviewer_seeded_defects）：
- 每个种子 = 合成工件 + 唯一锚文本 + 期望判定；
- 干净对照必须零误报（只计"断言了不存在的问题"）；
- 改门前先测基线、改后复测。
"""

from pathlib import Path

import pytest

from app.core.quality.contracts import GateVerdict
from app.core.quality.g1_data_gate import check_data_completeness
from app.core.quality.g3_text_gate import (
    check_citation_integrity,
    check_section_text,
)
from app.core.quality.recompute import grim_mean_check

# ---- 种子定义（manifest 风格：锚文本唯一 + 期望判定）----

G1_SEEDS = [
    {
        "name": "missing_attachment",
        "required": ["附件1", "附件2"],
        "existing": ["附件2 (Attachment 2).csv"],
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "附件1",
    },
    {
        "name": "clean_all_present",
        "required": ["附件1", "附件2"],
        "existing": ["附件1.csv", "附件2.csv"],
        "expect_verdict": GateVerdict.PASS,
        "anchor": None,
    },
]

CLEAN_SECTION = (
    "# 五、模型的建立与求解\n\n针对问题一构建灰色预测模型，"
    "滚动留一验证 MAPE 为 8.6%，优于同星期朴素法的 23.2%。"
)

G3_SEEDS = [
    {
        "name": "leak_error_message",
        "text": CLEAN_SECTION + "\n搜索文献失败: 配置OpenAlex邮箱获取访问文献权利",
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "搜索文献失败",
    },
    {
        "name": "placeholder_todo",
        "text": CLEAN_SECTION + "\nTODO: 补充灵敏度分析",
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "TODO",
    },
    {
        "name": "internal_path_leak",
        "text": CLEAN_SECTION + "\n结果保存在 logs/messages 目录下。",
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "logs/messages",
    },
    {
        "name": "clean_control",
        "text": CLEAN_SECTION,
        "expect_verdict": GateVerdict.PASS,
        "anchor": None,
    },
]

CITATION_SEEDS = [
    {
        "name": "empty_references",
        "text": "如文献{[^1] Zhang 2019}所示。",
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "参考文献列表为空",
    },
    {
        "name": "orphan_citation",
        "text": "如{[^1] A}与{[^2] B}所示。\n\n## 参考文献\n\n[^1]: A",
        "expect_verdict": GateVerdict.MATERIAL,
        "anchor": "孤儿引用",
    },
    {
        "name": "ghost_entries_minor",
        "text": "正文无标记引用。\n\n## 参考文献\n\n[^1]: 未被引用的条目",
        "expect_verdict": GateVerdict.MINOR,
        "anchor": "幽灵条目",
    },
    {
        "name": "clean_control",
        "text": "如{[^1] Zhang 2019}所示。\n\n## 参考文献\n\n[^1]: Zhang 2019",
        "expect_verdict": GateVerdict.PASS,
        "anchor": None,
    },
]


# ---- G1 回归 ----


@pytest.mark.parametrize("seed", G1_SEEDS, ids=[s["name"] for s in G1_SEEDS])
def test_g1_regression(tmp_path: Path, seed):
    for f in seed["existing"]:
        (tmp_path / f).write_text("data", encoding="utf-8")
    report = check_data_completeness(seed["required"], str(tmp_path))
    assert report.verdict == seed["expect_verdict"], seed["name"]
    if seed["anchor"]:
        assert any(
            seed["anchor"] in it.problem for it in report.items
        ), f"锚点未命中: {seed['anchor']}"
    else:
        assert not report.items, "干净对照不应产生问题条目"


# ---- G3 回归 ----


@pytest.mark.parametrize("seed", G3_SEEDS, ids=[s["name"] for s in G3_SEEDS])
def test_g3_section_regression(seed):
    report = check_section_text(seed["text"], "ques1")
    assert report.verdict == seed["expect_verdict"], seed["name"]
    if seed["anchor"]:
        assert any(
            seed["anchor"] in it.problem or seed["anchor"] in it.evidence_anchor
            for it in report.items
        ), f"锚点未命中: {seed['anchor']}"
    else:
        assert not report.items, "干净对照不应产生问题条目"


@pytest.mark.parametrize(
    "seed", CITATION_SEEDS, ids=[s["name"] for s in CITATION_SEEDS]
)
def test_g3_citation_regression(seed):
    report = check_citation_integrity(seed["text"])
    assert report.verdict == seed["expect_verdict"], seed["name"]
    if seed["anchor"]:
        assert any(
            seed["anchor"] in it.problem for it in report.items
        ), f"锚点未命中: {seed['anchor']}"
    else:
        assert not report.items, "干净对照不应产生问题条目"


# ---- G4 数值重算回归（GRIM 封闭数值预言）----


def test_g4_grim_impossible_mean():
    # N=20 个整数观测的均值不可能是 3.33（20*3.33=66.6 非整数和）
    r = grim_mean_check(3.33, 20)
    assert r.status.value == "mismatch"
    assert "最近可达值" in r.note


def test_g4_grim_reachable_mean():
    r = grim_mean_check(3.35, 20)  # sum=67 可达
    assert r.status.value == "consistent"
