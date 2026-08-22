"""G4 数值重算层：对论文/报告中的统计数值做确定性算术验证。

借鉴 ARS recompute_receipts（GRIM 系），判定四态：
consistent / mismatch / not_computable(reason) / not_applicable

设计纪律：验证不了就报 not_computable，绝不猜测（灰区=FAIL 的对称面：
查不了的如实说查不了，不伪装成一致）。
"""

import re
from enum import Enum
from fractions import Fraction
from typing import Optional


class RecomputeStatus(str, Enum):
    CONSISTENT = "consistent"
    MISMATCH = "mismatch"
    NOT_COMPUTABLE = "not_computable"
    NOT_APPLICABLE = "not_applicable"


class RecomputeResult:
    """重算结果（status + 说明 + 最近可达值等附注）。"""

    def __init__(self, status: RecomputeStatus, note: str = ""):
        self.status = status
        self.note = note

    def __repr__(self) -> str:
        return f"RecomputeResult({self.status.value}, {self.note!r})"


# ---- GRIM：整数观测均值的可达性 ----


def _rounds_to(value: Fraction, target: float, places: int, rule: str) -> bool:
    """判断 value 按指定舍入规则是否舍入到 target（保留 places 位小数）。"""
    scale = 10**places
    if rule == "half_up":
        rounded = (value.numerator * 2 * scale + value.denominator) // (2 * value.denominator)
    elif rule == "half_even":  # 简化实现：先 half_up 再对恰好 .5 判偶
        scaled_num = value.numerator * scale
        q, r = divmod(scaled_num, value.denominator)
        if 2 * r < value.denominator:
            rounded = q
        elif 2 * r > value.denominator:
            rounded = q + 1
        else:
            rounded = q if q % 2 == 0 else q + 1
    elif rule == "truncation":
        scaled = value.numerator * scale
        rounded = scaled // value.denominator if scaled >= 0 else -((-scaled) // value.denominator)
    else:
        raise ValueError(f"未知舍入规则: {rule}")
    return float(rounded) / scale == round(target, places)


def grim_mean_check(
    reported_mean: float,
    n: int,
    places: Optional[int] = None,
    rules: tuple[str, ...] = ("half_up", "half_even"),
) -> RecomputeResult:
    """GRIM：N 个观测的均值必须落在 k/N 的可达网格上。

    Args:
        reported_mean: 论文报告的均值。
        n: 样本量（正整数）。
        places: 报告值的小数位数；缺省自动从报告值推断。
        rules: 候选舍入规则（任一规则可达即 consistent；全部不可达为 mismatch）。
    """
    if n <= 0:
        return RecomputeResult(RecomputeStatus.NOT_COMPUTABLE, f"样本量非法: {n}")
    if places is None:
        s = repr(float(reported_mean))
        places = len(s.split(".")[1]) if "." in s and "e" not in s.lower() else 0

    step = 10.0**-places
    center = reported_mean * n
    lo = int(center - step * n - 1)
    hi = int(center + step * n + 1)

    for total in range(lo, hi + 1):
        if any(
            _rounds_to(Fraction(total, n), reported_mean, places, rule)
            for rule in rules
        ):
            return RecomputeResult(
                RecomputeStatus.CONSISTENT, f"可达：sum={total}, mean={total}/{n}"
            )

    nearest = round(center) / n
    return RecomputeResult(
        RecomputeStatus.MISMATCH,
        f"报告均值 {reported_mean} 不在 1/{n} 网格上，最近可达值 {nearest:.{places + 2}f}",
    )


# ---- p 值重算 ----


def pvalue_recompute(
    reported_p: float,
    statistic: float,
    df: int,
    test: str,
    tail: str = "two",
) -> RecomputeResult:
    """由检验统计量与自由度重算 p 值，与报告值对照。

    Args:
        test: t / f / chi2（F 与卡方强制上尾）。
        tail: two / upper / lower（t 检验有效）。
    """
    try:
        from scipy import stats
    except ImportError:
        return RecomputeResult(RecomputeStatus.NOT_COMPUTABLE, "scipy 不可用")

    test = test.lower().strip()
    try:
        if test in ("t", "ttest"):
            if tail == "two":
                p = 2 * stats.t.sf(abs(statistic), df)
            elif tail == "lower":
                p = stats.t.cdf(statistic, df)
            else:
                p = stats.t.sf(abs(statistic), df)
        elif test in ("f", "anova"):
            p = stats.f.sf(statistic, df)
        elif test in ("chi2", "chisquare", "x2"):
            p = stats.chi2.sf(statistic, df)
        else:
            return RecomputeResult(RecomputeStatus.NOT_APPLICABLE, f"未知检验类型 {test}")
    except Exception as e:
        return RecomputeResult(RecomputeStatus.NOT_COMPUTABLE, f"计算失败: {e}")

    # 舍入区间开区间判定；1e-9 边界守卫
    if abs(p - reported_p) < 1e-9:
        return RecomputeResult(RecomputeStatus.NOT_COMPUTABLE, "落在舍入边界 1e-9 内，无法判定")
    if abs(p - reported_p) / max(abs(reported_p), 1e-12) <= 0.05:
        return RecomputeResult(RecomputeStatus.CONSISTENT, f"重算 p={p:.6g}")
    return RecomputeResult(
        RecomputeStatus.MISMATCH, f"重算 p={p:.6g}，报告 p={reported_p}"
    )


# ---- df 与 N 一致性 ----


def df_n_consistency(df: int, stated_n: int, identity: str) -> RecomputeResult:
    """由自由度反推隐含样本量，与声明 N 对照。

    identity: n_minus_1（单样本/配对）、n1_plus_n2_minus_2（独立两样本）。
    """
    offsets = {"n_minus_1": 1, "n1_plus_n2_minus_2": 2}
    if identity not in offsets:
        return RecomputeResult(RecomputeStatus.NOT_APPLICABLE, f"未知 identity {identity}")
    implied = df + offsets[identity]
    if implied == stated_n:
        return RecomputeResult(RecomputeStatus.CONSISTENT, f"implied N={implied}")
    return RecomputeResult(
        RecomputeStatus.MISMATCH,
        f"df={df} 隐含 N={implied}，但正文声明 N={stated_n}",
    )


# ---- 数值 token 守恒（advisory） ----

_NUMBER_RE = re.compile(
    r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?%(?!)"
    r"|(?<![\w.])-?\d+\.\d+(?![\w.])"
)


def numeric_tokens(text: str) -> list[str]:
    """提取数值 token（千分位归一、字母邻接排除）。"""
    out = []
    for m in _NUMBER_RE.finditer(text):
        tok = m.group(0).replace(",", "")
        # 排除版本号/编号（前后紧邻字母数字的点分形态已在负向断言处理大半）
        out.append(tok)
    return out


def numeric_conservation(old_text: str, new_text: str) -> dict[str, list[str]]:
    """修订前后数值多重集对比（advisory：防改一节丢数值）。"""
    from collections import Counter

    old_c = Counter(numeric_tokens(old_text))
    new_c = Counter(numeric_tokens(new_text))
    return {
        "removed": sorted((old_c - new_c).elements()),
        "added": sorted((new_c - old_c).elements()),
    }
