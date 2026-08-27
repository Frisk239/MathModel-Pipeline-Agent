"""G4 终审：七维 LLM 评审 + 机械裁决 + 数值守恒 + 路线图复核。

裁决纪律（ADR-0001）：分类判据与机械规则，不用数字总分。
- 首审：任一 critical/major must_fix → MATERIAL 退回；仅 minor → MINOR 放行
- 复核（修订后）：只验路线图逐条落实（FULLY/PARTIALLY/NOT_ADDRESSED/MADE_WORSE），
  不重跑全量评审；新发现的"历史遗留"不加重本轮判决（goalpost guard）
- 判官披露：评审与任务模型同族时标注同源盲区
"""

import json
import re

from app.core.llm.llm import LLM
from app.core.quality.contracts import (
    GateName,
    GateReport,
    GateVerdict,
    Obligation,
    RoadmapItem,
    Severity,
)
from app.core.quality.recompute import (
    RecomputeStatus,
    grim_mean_check,
    numeric_conservation,
    pvalue_recompute,
    scan_statistical_claims,
)

DIMENSIONS = (
    "model_soundness 模型合理性",
    "assumption_validity 假设正当性",
    "solution_correctness 求解正确性",
    "reproducibility 代码可复现性",
    "result_validity 结果有效性",
    "writing_norm 写作规范性",
    "sensitivity 充分的敏感性分析",
)
VERDICT_SCALE = "EXCEEDS / MEETS / PARTLY_MEETS / DOES_NOT_MEET / NOT_ASSESSED"

G4_PROMPT = f"""你是数学建模竞赛的终审评委。对以下论文做七维评审，维度固定为：
{'; '.join(DIMENSIONS)}

规则：
1. 每个维度输出五档判据之一：{VERDICT_SCALE}（禁止打分）；
2. 对每个 DOES_NOT_MEET / PARTLY_MEETS 的维度，给出具体问题条目（问题/证据位置/严重度/验收判据）；
3. 严重度定义：critical=单条不修即否决核心结论；major=实质削弱但核心存活；minor=不影响核心；
4. 同时检查：正文数值与图表/结论一致、章节完整、无占位符、结论不超出证据。

输出严格 JSON（无代码块标记）：
{{"dimensions": {{"model_soundness": "MEETS", ...七个维度...}},
 "items": [{{"problem": "...", "evidence": "章节/位置", "severity": "critical|major|minor",
   "acceptance_criteria": "怎么算改好"}}]}}"""

G4_RECHECK_PROMPT = """你是修订核验员。先前终审给出以下问题清单（路线图），作者已提交修订稿。
逐条判定修订状态，判据枚举：FULLY_ADDRESSED / PARTIALLY_ADDRESSED / NOT_ADDRESSED / MADE_WORSE / CANNOT_VERIFY。

规则：
- 先承诺判据再看稿：只有修订稿中可定位的实质修改才算 ADDRESSED；"作者声称已改"不算；
- 新发现的历史遗留问题单独列出（标注 previously_missed），不影响本轮裁决；
- 每条附修订稿中的证据位置。

输出严格 JSON：
{{"judgements": [{{"id": "<路线图条目id>", "status": "...", "evidence": "..."}}],
 "new_issues_previously_missed": [{{"problem": "...", "severity": "..."}}]}}

【路线图】
{roadmap}

【修订稿】
{revised}"""


def run_g4_mechanical_recompute(paper_text: str) -> list[RoadmapItem]:
    """确定性算术验证：GRIM 均值可达性 + t/F/χ² p 值重算（无 LLM 参与）。

    提取不到可重算的统计陈述（数学建模论文常见）时返回空列表——
    不惩罚；mismatch 视为 critical must_fix（数值不可复算是硬伤）。
    """
    items: list[RoadmapItem] = []
    for i, claim in enumerate(scan_statistical_claims(paper_text), 1):
        if claim.kind == "grim_mean":
            result = grim_mean_check(claim.values["mean"], claim.values["n"])
            label = "GRIM 均值可达性"
        elif claim.kind in ("t_test", "f_test", "chi2_test"):
            test = {"t_test": "t", "f_test": "f", "chi2_test": "chi2"}[claim.kind]
            result = pvalue_recompute(
                claim.values["p"], claim.values["stat"], claim.values["df"], test
            )
            label = f"{test} 检验 p 值重算"
        else:
            continue
        if result.status != RecomputeStatus.MISMATCH:
            continue  # consistent/不可算不拦；只有算得出且对不上才是硬伤
        items.append(
            RoadmapItem(
                id=f"g4-recompute-{i}",
                problem=f"〔{label}〕「{claim.snippet}」数值不可复算：{result.note}",
                evidence_anchor=claim.snippet,
                severity=Severity.CRITICAL,
                obligation=Obligation.MUST_FIX,
                cost_scope="section",
                acceptance_criteria="修正报告数值使其与样本量/统计量的重算结果一致，或更正对应的统计陈述",
                target="论文正文",
            )
        )
    return items


async def run_g4_final_review(
    review_llm: LLM,
    paper_text: str,
    same_family_as_task_model: bool,
) -> GateReport:
    """首审：七维评审 + 机械裁决 + 判官披露 + 数值守恒（有旧版时）。"""
    disclosure = (
        "⚠️ 同模型族评审：终审模型与任务模型同族，存在同源盲区（如实披露）"
        if same_family_as_task_model
        else "评审模型与任务模型不同族"
    )
    prompt = f"{G4_PROMPT}\n\n【论文全文】\n{paper_text[:60000]}"

    try:
        response = await review_llm.chat(
            history=[{"role": "user", "content": prompt}], agent_name="G4FinalReview"
        )
        raw = (response.content or "").replace("```json", "").replace("```", "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return GateReport(
            gate=GateName.G4,
            verdict=GateVerdict.MINOR,
            not_checked=[f"终审调用失败（如实记录）：{str(e)[:80]}"],
            summary=f"G4 终审未执行；判官披露：{disclosure}",
        )

    dims = data.get("dimensions", {})
    dims_str = "; ".join(f"{k}={v}" for k, v in dims.items()) or "(未返回维度判据)"

    items = []
    for i, it in enumerate(data.get("items", []), 1):
        sev_raw = it.get("severity", "major")
        sev = (
            Severity.CRITICAL
            if sev_raw == "critical"
            else Severity.MINOR if sev_raw == "minor" else Severity.MAJOR
        )
        items.append(
            RoadmapItem(
                id=f"g4-{i}",
                problem=str(it.get("problem", ""))[:400],
                evidence_anchor=str(it.get("evidence", ""))[:200],
                severity=sev,
                obligation=Obligation.MUST_FIX if sev != Severity.MINOR else Obligation.SHOULD_FIX,
                cost_scope="section",
                acceptance_criteria=str(it.get("acceptance_criteria", ""))[:300],
                target=str(it.get("evidence", ""))[:100],
            )
        )

    # 机械重算（GRIM/p 值）与 LLM 评审互补：确定性验证不依赖判官
    mech_items = run_g4_mechanical_recompute(paper_text)
    items.extend(mech_items)

    blocking = [it for it in items if it.obligation == Obligation.MUST_FIX]
    mech_note = f"；机械重算：{len(mech_items)} 条不可复算" if mech_items else ""
    if not blocking:
        verdict = GateVerdict.MINOR if items else GateVerdict.PASS
        summary = f"终审放行；维度：{dims_str}{mech_note}；{disclosure}"
    else:
        verdict = GateVerdict.MATERIAL
        summary = (
            f"终审退回：{len(blocking)} 条 must_fix"
            f"（{sum(1 for i in blocking if i.severity == Severity.CRITICAL)} 条 critical）"
            f"{mech_note}；{disclosure}"
        )
    return GateReport(gate=GateName.G4, verdict=verdict, items=items, summary=summary)


async def run_g4_recheck(
    review_llm: LLM,
    roadmap_items: list[RoadmapItem],
    revised_text: str,
    old_text: str = "",
) -> GateReport:
    """复核：只验路线图逐条落实，机械规则裁决（B 规则简化版）。"""
    if not roadmap_items:
        return GateReport(
            gate=GateName.G4, verdict=GateVerdict.PASS, summary="复核：无待验条目"
        )

    conservation_note = ""
    if old_text:
        delta = numeric_conservation(old_text, revised_text)
        lost = [t for t in delta["removed"] if t not in delta["added"]]
        if lost:
            conservation_note = f"；数值守恒提示：{len(lost)} 个数值在修订中消失（advisory）"

    roadmap_str = json.dumps(
        [
            {"id": it.id, "problem": it.problem, "acceptance": it.acceptance_criteria}
            for it in roadmap_items
        ],
        ensure_ascii=False,
        indent=1,
    )
    prompt = G4_RECHECK_PROMPT.format(roadmap=roadmap_str, revised=revised_text[:60000])

    try:
        response = await review_llm.chat(
            history=[{"role": "user", "content": prompt}], agent_name="G4Recheck"
        )
        raw = (response.content or "").replace("```json", "").replace("```", "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return GateReport(
            gate=GateName.G4,
            verdict=GateVerdict.MINOR,
            not_checked=[f"复核调用失败（如实记录）：{str(e)[:80]}"],
            summary="G4 复核未执行",
        )

    status_by_id = {j.get("id"): j.get("status", "CANNOT_VERIFY") for j in data.get("judgements", [])}
    lines = []
    unresolved_must = 0
    made_worse = 0
    for it in roadmap_items:
        st = status_by_id.get(it.id, "CANNOT_VERIFY")
        lines.append(f"[{it.id}] {st}（{it.problem[:40]}…）")
        if it.obligation == Obligation.MUST_FIX and st not in ("FULLY_ADDRESSED",):
            unresolved_must += 1
        if st == "MADE_WORSE":
            made_worse += 1

    # 机械规则（first-match）
    if made_worse and any(
        it.severity == Severity.CRITICAL
        for it in roadmap_items
        if status_by_id.get(it.id) == "MADE_WORSE"
    ):
        verdict, decision = GateVerdict.MATERIAL, "B1：critical 条目修订后恶化 → 退回"
    elif unresolved_must:
        verdict, decision = (
            GateVerdict.MATERIAL,
            f"B3：{unresolved_must} 条 must_fix 未完全解决 → 强制人工检查点",
        )
    else:
        verdict, decision = GateVerdict.PASS, "B6：must_fix 全部 FULLY_ADDRESSED → 通过"

    # goalpost guard：新发现的历史遗留仅记录，不影响裁决
    new_missed = data.get("new_issues_previously_missed", [])
    note = f"；历史遗留新发现 {len(new_missed)} 条（不加重本轮判决）" if new_missed else ""

    return GateReport(
        gate=GateName.G4,
        verdict=verdict,
        items=roadmap_items if verdict == GateVerdict.MATERIAL else [],
        summary=f"复核：{decision}；{' | '.join(lines)}{conservation_note}{note}",
    )
