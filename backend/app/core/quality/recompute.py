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
    df: int | tuple[int, int],
    test: str,
    tail: str = "two",
) -> RecomputeResult:
    """由检验统计量与自由度重算 p 值，与报告值对照。

    Args:
        test: t / f / chi2（F 与卡方强制上尾）。
        df: 单自由度；F 检验传 (dfn, dfd) 二元组。
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
            dfn, dfd = df if isinstance(df, tuple) else (df, df)
            p = stats.f.sf(statistic, dfn, dfd)
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


# ---- 参数表覆盖检查（v4 2-2：蓝图中显式赋值的关键参数必须在正文可查） ----

# 只认「单符号 = 数值/区间」的显式赋值声明：θ = 0.95、K = 1201、α = 0.3~0.5。
# 带下标/多字母符号（x_{ij}、w_i）与中文命名参数（增长率等）不收——误报率高，
# 交给 Writer 参数表任务指令与 G4 LLM 评审兜底（宁缺毋滥，同模块纪律）。
# 拉丁字母只收大写单字母（小写单字母在公式里多为局部变量）。
_PARAM_DECL_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩA-Z])"
    r"\s*=\s*"
    r"-?\d+(?:\.\d+)?(?:%)?(?:\s*[~～至]\s*-?\d+(?:\.\d+)?(?:%)?)?"
)

# 提取结果超过此数视为蓝图口径存疑，整体放弃（与 _stated_n 多值放弃纪律一致）
_MAX_PARAM_SYMBOLS = 15


def extract_declared_params(blueprint_text: str) -> dict[str, str]:
    """从建模蓝图提取显式赋值的参数符号 → 首个声明片段（保序去重）。"""
    found: dict[str, str] = {}
    for m in _PARAM_DECL_RE.finditer(blueprint_text):
        symbol = m.group(1)
        if symbol not in found:
            found[symbol] = m.group(0)[:60]
    if len(found) > _MAX_PARAM_SYMBOLS:
        return {}
    return found


def param_table_coverage(blueprint_text: str, paper_text: str) -> list[str]:
    """返回蓝图中声明赋值、但论文正文从未出现的参数符号（保序）。"""
    declared = extract_declared_params(blueprint_text)
    return [sym for sym in declared if sym not in paper_text]


# ---- 文本统计陈述提取（G4 机械重算的输入层） ----


class StatisticalClaim:
    """论文文本中可重算的统计陈述（保守提取：只收精确数值形式）。"""

    def __init__(self, kind: str, snippet: str, **values):
        self.kind = kind  # grim_mean / t_test / f_test / chi2_test
        self.snippet = snippet  # 原文片段（作 evidence_anchor）
        self.values = values


# 均值+N：中文「均值为 3.42（n=35）」/ 英文 "mean = 3.42, N = 35"
_MEAN_N_RE = re.compile(
    r"(?:均值|平均值|mean)\s*(?:为|=|：|:)?\s*(\d+\.\d+)\s*[(（,，)）]?\s*(?:n|N)\s*=\s*(\d+)"
)
# t(28) = 2.15, p = 0.041（p < 0.05 形式无精确值，不收）
_T_P_RE = re.compile(
    r"t\s*\(\s*(\d+)\s*\)\s*=\s*(-?\d+(?:\.\d+)?)\s*[,，]?\s*p\s*=\s*(0?\.\d+)",
    re.IGNORECASE,
)
# F(2, 57) = 5.30, p = 0.002
_F_P_RE = re.compile(
    r"F\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*(\d+(?:\.\d+)?)\s*[,，]?\s*p\s*=\s*(0?\.\d+)",
    re.IGNORECASE,
)
# χ²(3) = 8.20, p = 0.042（兼容 χ2/x2 写法；上标/数字必选防 x(3) 变量误报）
_CHI2_P_RE = re.compile(
    r"[χx][²2]\s*\(\s*(\d+)\s*\)\s*=\s*(\d+(?:\.\d+)?)\s*[,，]?\s*p\s*=\s*(0?\.\d+)"
)


def scan_statistical_claims(text: str) -> list[StatisticalClaim]:
    """从论文文本提取可确定性重算的统计陈述。

    设计纪律（与模块头一致）：宁缺毋滥——p < X、无 N 的均值、非标准
    写法一律不收，提取不到就不验证（NOT_APPLICABLE），绝不猜测解析。
    """
    claims: list[StatisticalClaim] = []
    for m in _MEAN_N_RE.finditer(text):
        mean, n = float(m.group(1)), int(m.group(2))
        if n <= 0:
            continue
        claims.append(
            StatisticalClaim("grim_mean", m.group(0)[:80], mean=mean, n=n)
        )
    for m in _T_P_RE.finditer(text):
        df, stat, p = int(m.group(1)), float(m.group(2)), float(m.group(3))
        claims.append(
            StatisticalClaim("t_test", m.group(0)[:80], df=df, stat=stat, p=p)
        )
    for m in _F_P_RE.finditer(text):
        dfn, dfd = int(m.group(1)), int(m.group(2))
        stat, p = float(m.group(3)), float(m.group(4))
        claims.append(
            StatisticalClaim(
                "f_test", m.group(0)[:80], df=(dfn, dfd), stat=stat, p=p
            )
        )
    for m in _CHI2_P_RE.finditer(text):
        df, stat, p = int(m.group(1)), float(m.group(2)), float(m.group(3))
        claims.append(
            StatisticalClaim("chi2_test", m.group(0)[:80], df=df, stat=stat, p=p)
        )
    return claims
