"""执行环境能力探测：生成供 Modeler/Coder 任务级 prompt 注入的能力清单。

背景（优化路线 v3 / F2 求解器幻觉）：Modeler 曾在方案中承诺 Gurobi/CPLEX，
而执行环境实际只有 PuLP+CBC，导致 G2 无论怎么修都判 inconsistency。
方案承诺必须限制在实测能力清单内。
"""

import importlib.metadata
import importlib.util
from typing import Literal

# 展示名 -> 导入名；探测顺序即清单展示顺序
_PROBE_PACKAGES: list[tuple[str, str]] = [
    ("NumPy", "numpy"),
    ("Pandas", "pandas"),
    ("SciPy", "scipy"),
    ("PuLP", "pulp"),
    ("HiGHS", "highspy"),
    ("Matplotlib", "matplotlib"),
    ("Seaborn", "seaborn"),
    ("Statsmodels", "statsmodels"),
    ("scikit-learn", "sklearn"),
    ("NetworkX", "networkx"),
    ("OpenPyXL", "openpyxl"),
]

# 商业求解器：默认无许可证，仅在实测可用时才允许出现在方案里
_COMMERCIAL_SOLVERS: list[tuple[str, str]] = [
    ("Gurobi", "gurobipy"),
    ("CPLEX", "cplex"),
    ("Mosek", "mosek"),
]

_SOLVER_RULES = (
    "【求解器纪律】\n"
    "- 建模方案承诺的求解器与第三方库必须且只能在上述清单内\n"
    "- 混合整数规划（MILP/LP）使用 PuLP(内置CBC) 或 scipy.optimize.milp(内置HiGHS)\n"
    "- 未经清单确认，禁止假设 Gurobi/CPLEX/Mosek 等商业求解器可用\n"
    "- 方案中必须写明求解器选择及理由；若首选库不可用，需同时给出清单内的降级替代"
)

# E2B 沙箱无法在建模阶段（解释器尚未创建）实测，给保守描述，宁可低估不可诱导幻觉
_REMOTE_DESCRIPTION = (
    "【代码执行环境能力清单（E2B 沙箱，保守口径）】\n"
    "- 预装常见科学计算栈：numpy / pandas / scipy / matplotlib / scikit-learn\n"
    "- 商业求解器（Gurobi/CPLEX/Mosek）无许可证，一律不可用\n"
    "- 不确定是否安装的库必须在方案中注明清单内降级替代\n"
)


def _probe(display: str, module: str) -> tuple[str, str] | None:
    """探测单个包，返回 (展示名, 版本) 或 None（未安装）。"""
    if importlib.util.find_spec(module) is None:
        return None
    try:
        version = importlib.metadata.version(module)
    except importlib.metadata.PackageNotFoundError:
        version = "?"
    return display, version


def detect_local_capability() -> str:
    """探测当前进程环境（local 解释器与后端同环境），生成中文能力清单文本。"""
    available: list[str] = []
    for display, module in _PROBE_PACKAGES:
        found = _probe(display, module)
        if found:
            available.append(f"{found[0]} {found[1]}" if found[1] != "?" else found[0])

    commercial: list[str] = []
    for display, module in _COMMERCIAL_SOLVERS:
        if importlib.util.find_spec(module) is not None:
            found = _probe(display, module)
            if found:
                commercial.append(f"{found[0]} {found[1]}" if found[1] != "?" else found[0])

    lines = ["【代码执行环境能力清单（本地实测）】"]
    lines.append(f"- 可用：{'、'.join(available) if available else '（基础 Python 标准库）'}")
    if commercial:
        lines.append(f"- 商业求解器（可用，含许可证）：{'、'.join(commercial)}")
    else:
        lines.append("- 不可用：Gurobi、CPLEX、Mosek（无商业求解器许可证）")
    lines.append(_SOLVER_RULES)
    return "\n".join(lines)


def get_capability_description(kind: Literal["local", "remote"]) -> str:
    """按解释器类型返回能力清单文本（local 实测 / remote 保守描述）。"""
    if kind == "remote":
        return _REMOTE_DESCRIPTION + _SOLVER_RULES
    return detect_local_capability()
