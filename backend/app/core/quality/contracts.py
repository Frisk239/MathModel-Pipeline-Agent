"""质量门契约：门判定、修订路线图、检查结果的 pydantic 模型。

判定纪律（见 docs/quality-gates-plan.md §2.1）：
- 检查项三值结果 fail / not_checked，永不把"查不了"伪装成"通过"；
- 门判定三档 pass / minor / material；
- 严重度与义务级别互不推导，must_fix 只能来自规则或人。
"""

from enum import Enum
from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    """单项检查结果（三值，无 fail 伪装）。"""

    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"


class Severity(str, Enum):
    """问题严重度，按"决策影响测试"定义。"""

    CRITICAL = "critical"  # 单条不修即否决核心结论
    MAJOR = "major"        # 实质削弱核心结论但核心存活
    MINOR = "minor"        # 不影响核心结论


class Obligation(str, Enum):
    """修订义务级别（与严重度独立，不得互相推导）。"""

    MUST_FIX = "must_fix"
    SHOULD_FIX = "should_fix"
    CONSIDER = "consider"


class GateVerdict(str, Enum):
    """门判定三档。"""

    PASS = "pass"
    MINOR = "minor"        # ≤3 条 Minor 问题，放行并提示
    MATERIAL = "material"  # 阻断，进入修复回路


class GateName(str, Enum):
    G1 = "g1"
    G2_L1 = "g2_l1"
    G2_L2 = "g2_l2"
    G3 = "g3"
    G4 = "g4"


class RoadmapItem(BaseModel):
    """修订路线图条目：问题与"怎么算改好"绑定交付。"""

    id: str
    problem: str
    evidence_anchor: str = ""   # 证据位置（章节/notebook cell/文件）
    severity: Severity
    obligation: Obligation
    cost_scope: str = "section"  # sentence / section / re_analysis
    acceptance_criteria: str = ""  # 预先声明的验收判据
    target: str = ""             # 目标位置


class GateReport(BaseModel):
    """一次门运行的完整报告。"""

    gate: GateName
    verdict: GateVerdict
    items: list[RoadmapItem] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)  # 无法核查的项，如实记录
    round_no: int = 0
    summary: str = ""
