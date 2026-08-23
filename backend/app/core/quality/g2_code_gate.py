"""G2 代码质量门：L1 脚本层（notebook 产物检查）+ L2 AI 评审（固定 checklist）。

L2 四条定向审查（来自实测病灶，冻结清单，不让评审自由发挥）：
1. 数据泄漏：是否使用目标日之后/当天完整数据做训练或标定；
2. 方法误用：指标/检验/采样与问题性质不匹配；
3. 验证缺失：无 holdout/交叉验证即报指标；
4. 方案一致性：代码实现与建模方案、总结叙事一致。
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

# notebook/代码产物中的演示数据标记（L1）
DEMO_MARKERS: tuple[str, ...] = (
    "demo_data",
    "synthetic",
    "示范数据",
    "示例数据（",
    "mock_data",
    "placeholder_data",
    "受数据获取条件限制",
)

def extract_notebook_outputs(notebook: dict, max_chars: int = 12000) -> str:
    """提取 notebook 全部执行输出的纯文本（display_data 的 ansi2html 行列表 → 纯文本）。

    供 G2-L2 评审对照"报告的数值是否有执行输出支撑"。
    """
    import re as _re

    parts: list[str] = []
    for idx, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error":
                continue  # 报错由 L1 负责
            data = out.get("data", {})
            html = data.get("text/html")
            if isinstance(html, list):
                text = "".join(html)
            elif isinstance(html, str):
                text = html
            else:
                text = str(data.get("text/plain", ""))
            # 剥样式块与 HTML 标签留文本
            text = _re.sub(r"<style.*?</style>", "", text, flags=_re.S)
            text = _re.sub(r"<[^>]+>", "", text)
            text = text.strip()
            if text:
                parts.append(f"[cell {idx}] {text[:2000]}")
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n".join(parts)[:max_chars]


G2_L2_CHECKLIST = """你是一名严格的数据科学代码评审。对以下数学建模任务的代码产物做定向审查，只查这四类问题，每类逐条给出结论：

1. data_leakage（数据泄漏）：是否使用预测目标日之后/当天完整数据做训练、标定或特征构造（如用 7月22日全天数据预测 7月22日）；
2. method_misuse（方法误用）：指标/检验/采样与问题性质不匹配（不平衡数据用 accuracy 当基线、回归问题用分类指标、忽略时序顺序随机划分等）；
3. missing_validation（验证缺失）：报告了性能指标但无 holdout/交叉验证/滚动验证支撑；
4. inconsistency（方案不一致）：代码实际实现与建模方案或执行总结的叙述不一致（如总结称用模型A，代码只实现了模型B）。

输出严格 JSON（不要代码块标记）：
{"items": [{"category": "data_leakage|method_misuse|missing_validation|inconsistency",
  "problem": "具体问题描述", "evidence": "代码或总结中的证据片段",
  "severity": "critical|major|minor",
  "acceptance_criteria": "怎么算改好"}], "clean": true/false}
若四类均无问题，输出 {"items": [], "clean": true}。"""


def check_notebook_artifacts(
    notebook_path: str,
    work_dir: str,
    created_images: list[str] | None = None,
) -> list[RoadmapItem]:
    """L1 脚本层：notebook 无报错 cell、无演示数据标记、产物完整。"""
    items: list[RoadmapItem] = []

    def _add(problem: str, criteria: str, severity=Severity.MAJOR):
        items.append(
            RoadmapItem(
                id=f"g2l1-{len(items) + 1}",
                problem=problem,
                evidence_anchor=notebook_path,
                severity=severity,
                obligation=Obligation.MUST_FIX,
                cost_scope="section",
                acceptance_criteria=criteria,
                target="notebook",
            )
        )

    try:
        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _add(f"notebook 不可读或损坏：{e}", "恢复可解析的 notebook.ipynb", Severity.CRITICAL)
        return items

    src_all = ""
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", []))
        src_all += src
        # 报错 cell：输出含 error 类型或 traceback
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error" or "traceback" in out:
                _add(
                    f"notebook cell #{i} 存在未处理的报错输出（{out.get('ename', 'Error')}）",
                    "修复该 cell 使其无错误执行完成",
                    Severity.CRITICAL,
                )
                break

    for marker in DEMO_MARKERS:
        if marker in src_all:
            _add(
                f"代码中含演示/合成数据标记「{marker}」",
                "改用真实附件数据实现，删除演示数据分支",
                Severity.CRITICAL,
            )

    if not any(
        c.get("cell_type") == "code" and "".join(c.get("source", [])).strip()
        for c in nb.get("cells", [])
    ):
        _add("notebook 无任何有效代码 cell", "实现并执行建模代码", Severity.CRITICAL)
    else:
        # 有代码但全部无输出：可能是执行后序列化丢失（上游缺陷），也可能是未执行——
        # 无法区分时如实降为警告级（should_fix），不阻断
        has_any_output = any(
            c.get("outputs") for c in nb["cells"] if c.get("cell_type") == "code"
        )
        if not has_any_output:
            items.append(
                RoadmapItem(
                    id=f"g2l1-{len(items) + 1}",
                    problem="notebook 有代码 cell 但全部无执行输出（无法验证数值可复现性；可能为序列化丢失）",
                    evidence_anchor=notebook_path,
                    severity=Severity.MINOR,
                    obligation=Obligation.SHOULD_FIX,
                    cost_scope="section",
                    acceptance_criteria="确保执行输出保留在 notebook 中，或注明输出迁移位置",
                    target="notebook",
                )
            )

    if created_images is not None:
        import os

        missing_imgs = [img for img in created_images if not os.path.exists(os.path.join(work_dir, img))]
        if missing_imgs:
            _add(
                f"声称生成的图表缺失：{', '.join(missing_imgs[:3])}",
                "确保所有声明图表真实落盘",
            )

    return items


async def run_g2_ai_review(
    review_llm: LLM,
    notebook_path: str,
    model_plan: str,
    coder_summary: str,
    problem_text: str,
    prior_items: list[str] | None = None,
) -> GateReport:
    """L2 AI 评审：固定 checklist 四条，产出结构化路线图条目。

    Args:
        prior_items: 上一轮已报问题清单（goalpost guard：修复轮只验上轮问题
            是否解决 + 新增的 critical 数据泄漏类，不再全量重扫，防评审漂移循环）。
    """
    try:
        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        code_src = "\n\n".join(
            "".join(c.get("source", []))
            for c in nb.get("cells", [])
            if c.get("cell_type") == "code"
        )[:20000]
        outputs_text = extract_notebook_outputs(nb)
    except (OSError, json.JSONDecodeError):
        code_src = "(notebook 不可读)"
        outputs_text = ""

    focus_note = ""
    if prior_items:
        focus_note = (
            "\n\n【本轮为修复复核】上一轮已报问题如下，只验证它们是否已解决，"
            "并新报 critical 级的数据泄漏类问题；上轮问题已解决且无新 critical 即 "
            "clean=true，不要重扫出新的一般性问题（防止标准漂移）：\n"
            + "\n".join(f"- {p}" for p in prior_items)
        )

    prompt = (
        f"{G2_L2_CHECKLIST}{focus_note}\n\n【题目】\n{problem_text[:2000]}\n\n"
        f"【建模方案】\n{model_plan[:3000]}\n\n"
        f"【代码手执行总结】\n{coder_summary[:3000]}\n\n"
        f"【notebook 源码】\n{code_src}\n\n"
        f"【notebook 执行输出】\n{outputs_text or '(无输出)'}"
    )

    try:
        response = await review_llm.chat(
            history=[{"role": "user", "content": prompt}],
            agent_name="G2Review",
        )
        raw = (response.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {"items": [], "clean": False}
    except Exception as e:
        return GateReport(
            gate=GateName.G2_L2,
            verdict=GateVerdict.MINOR,
            not_checked=["AI 评审调用失败，本轮跳过（如实记录）"],
            summary=f"G2-L2 评审未执行：{str(e)[:80]}",
        )

    items = []
    for i, it in enumerate(data.get("items", []), 1):
        sev = Severity.CRITICAL if it.get("severity") == "critical" else (
            Severity.MINOR if it.get("severity") == "minor" else Severity.MAJOR
        )
        items.append(
            RoadmapItem(
                id=f"g2l2-{i}",
                problem=f"[{it.get('category', '?')}] {it.get('problem', '')}",
                evidence_anchor=str(it.get("evidence", ""))[:200],
                severity=sev,
                obligation=Obligation.MUST_FIX if sev != Severity.MINOR else Obligation.SHOULD_FIX,
                cost_scope="re_analysis",
                acceptance_criteria=str(it.get("acceptance_criteria", ""))[:300],
                target="notebook",
            )
        )

    critical = [it for it in items if it.severity == Severity.CRITICAL]
    if not items:
        return GateReport(
            gate=GateName.G2_L2,
            verdict=GateVerdict.PASS,
            summary="AI 代码评审通过（四类定向审查均无问题）",
        )
    return GateReport(
        gate=GateName.G2_L2,
        verdict=GateVerdict.MATERIAL,
        items=items,
        summary=f"AI 代码评审拦截：{len(items)} 条问题（{len(critical)} 条 critical）",
    )


def combine_g2(l1_items: list[RoadmapItem], l2_report: GateReport | None) -> GateReport:
    """合并 L1/L2 为单一门判定。"""
    items = list(l1_items)
    if l2_report is not None:
        items.extend(l2_report.items)
    if not items:
        summary = "代码质量门通过"
        if l2_report is not None:
            summary = f"L1 通过；{l2_report.summary}"
        return GateReport(gate=GateName.G2_L2, verdict=GateVerdict.PASS, summary=summary)
    has_critical = any(it.severity == Severity.CRITICAL for it in items)
    return GateReport(
        gate=GateName.G2_L2,
        verdict=GateVerdict.MATERIAL,
        items=items,
        summary=f"代码质量门拦截：{len(items)} 条问题"
        f"（{'含 critical' if has_critical else '无 critical'}）",
    )
