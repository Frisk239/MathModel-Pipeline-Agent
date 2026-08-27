"""G4 机械重算的 df↔N 一致性观察项（C-3 接线）单测。"""

from app.core.quality.g4_final_review import run_g4_mechanical_recompute


def _df_n_items(paper: str):
    items = run_g4_mechanical_recompute(paper)
    return [it for it in items if it.id.startswith("g4-df-n")]


def test_df_n_mismatch_recorded_as_minor_observation():
    """t(28) 隐含 N=29/30，与声明 N=40 均不符 → 记 MINOR 观察项，不拦截。"""
    items = _df_n_items("样本总量 N = 40。检验 t(28) = 2.15, p = 0.041。")

    assert len(items) == 1
    assert items[0].severity.value == "minor"
    assert items[0].obligation.value == "consider"
    assert "N=40" in items[0].problem


def test_df_n_consistent_not_recorded():
    """t(29) 隐含 N=30（单样本/配对），与声明一致 → 不记录。"""
    assert _df_n_items("样本总量 N = 30。检验 t(29) = 2.045, p = 0.05。") == []


def test_two_sample_identity_also_accepted():
    """t(58) 隐含 N=60（独立两样本 df=n1+n2-2）→ 不记录。"""
    assert _df_n_items("样本总量 N = 60。检验 t(58) = 2.001, p = 0.05。") == []


def test_multiple_stated_n_values_skip_check():
    """文中多个不同 N 声明（口径并存）→ 无法判定总样本量，跳过。"""
    assert _df_n_items("组A N = 30，组B N = 32。检验 t(28) = 2.15, p = 0.041。") == []


def test_no_stated_n_skips_check():
    assert _df_n_items("检验 t(28) = 2.15, p = 0.041。") == []


def test_grim_mismatch_still_critical():
    """既有行为回归：GRIM mismatch 仍是 critical must_fix（不被观察项逻辑稀释）。"""
    items = run_g4_mechanical_recompute("均值为 3.42（n=35）")
    grim = [it for it in items if it.id.startswith("g4-recompute")]
    assert grim and grim[0].severity.value == "critical"
