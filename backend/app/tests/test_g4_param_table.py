"""G4 参数表覆盖机械检查（v4 2-2）的回归测试。

对应实战缺陷：08832b52 G4 must_fix「θ/K/增长率等关键参数未在正文给出、
无法复算」。机械检查只认蓝图中「单符号 = 数值」的显式赋值声明，
宁缺毋滥；缺失聚合为一条 MAJOR must_fix。
"""

from app.core.quality.g4_final_review import run_g4_mechanical_recompute
from app.core.quality.recompute import extract_declared_params, param_table_coverage

BLUEPRINT = (
    "ques1: 建立马尔可夫链模型，转移矩阵按行归一化，阻尼系数 θ = 0.95，"
    "里程上限 K = 1201，学习率 α = 0.3~0.5。x_{ij} = 1 表示地块 i 种植作物 j。"
)


def test_extract_only_single_symbols():
    """带下标形态（x_{ij}）不收；拉丁小写单字母（局部变量语义）不收。"""
    params = extract_declared_params(BLUEPRINT + " 另有收敛阈值 k = 3。")
    assert set(params) == {"θ", "K", "α"}


def test_extract_dedup_keeps_first_snippet():
    params = extract_declared_params("K = 1201，后文 K = 2 重新声明。")
    assert list(params) == ["K"]
    assert "1201" in params["K"]


def test_extract_cap_abandons_when_too_many():
    import string

    blueprint = ", ".join(f"{ch} = {i}" for i, ch in enumerate(string.ascii_uppercase))
    assert extract_declared_params(blueprint) == {}


def test_coverage_pass_when_paper_mentions_all():
    paper = "本文取 θ = 0.95，K = 1201，α 在 0.3~0.5 网格搜索，参数见表 3。"
    assert param_table_coverage(BLUEPRINT, paper) == []


def test_coverage_flags_missing_symbol():
    paper = "本文取 θ = 0.95，α 在 0.3~0.5 网格搜索。"
    assert param_table_coverage(BLUEPRINT, paper) == ["K"]


def test_no_blueprint_no_check():
    assert run_g4_mechanical_recompute("任意正文", model_plan_text="") == []


def test_mechanical_item_is_major_must_fix():
    items = run_g4_mechanical_recompute("正文不含任何参数。", model_plan_text=BLUEPRINT)
    assert len(items) == 1
    it = items[0]
    assert it.id == "g4-param-table"
    assert it.severity.value == "major"
    assert it.obligation.value == "must_fix"
    assert "K" in it.problem
    assert it.evidence_anchor == "θ、K、α"


def test_param_item_coexists_with_grim_items():
    """与既有 GRIM 项共存：论文另含一个不可达均值。"""
    paper = "均值为 3.42（n=35），本文取 θ = 0.95、K = 1201，α 在 0.3~0.5 内搜索。"
    items = run_g4_mechanical_recompute(paper, model_plan_text=BLUEPRINT)
    ids = {it.id for it in items}
    assert "g4-param-table" not in ids  # θ/K 均在正文，参数表不拦
    assert any(i.startswith("g4-recompute-") for i in ids)  # GRIM 照常命中
