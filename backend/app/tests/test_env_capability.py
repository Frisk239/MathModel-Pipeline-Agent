"""环境能力清单探测（v3/P2-1）单测。"""

from app.tools.env_capability import (
    detect_local_capability,
    get_capability_description,
)


def test_local_capability_lists_core_stack_and_solvers_rules():
    text = detect_local_capability()
    # 本 venv 必装的核心栈应出现在可用清单里
    assert "NumPy" in text
    assert "Pandas" in text
    assert "Matplotlib" in text
    # 无商业许可证时必须显式声明不可用，防止方案幻觉 Gurobi/CPLEX
    assert "Gurobi" in text
    # 求解器纪律四条
    assert "求解器纪律" in text
    assert "scipy.optimize.milp" in text
    assert "降级替代" in text


def test_remote_capability_is_conservative():
    text = get_capability_description("remote")
    assert "E2B" in text
    # 保守口径：商业求解器一律不可用
    assert "Gurobi" in text
    assert "不可用" in text or "无许可证" in text


def test_local_kind_matches_detect():
    assert get_capability_description("local") == detect_local_capability()
