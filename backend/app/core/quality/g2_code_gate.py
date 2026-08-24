"""G2 代码质量门：L1 脚本层（notebook 产物检查）+ L2 AI 评审（固定 checklist）。

L2 四条定向审查（来自实测病灶，冻结清单，不让评审自由发挥）：
1. 数据泄漏：是否使用目标日之后/当天完整数据做训练或标定；
2. 方法误用：指标/检验/采样与问题性质不匹配；
3. 验证缺失：无 holdout/交叉验证即报指标；
4. 方案一致性：代码实现与建模方案、总结叙事一致。
"""

import json
import os
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
from app.core.quality.deliverable_hygiene import (
    DELIVERABLE_PATTERNS,
    WRITE_CALL_MARKERS,
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
    deliverable_since: float | None = None,
) -> list[RoadmapItem]:
    """L1 脚本层：notebook 无报错 cell、无演示数据标记、产物完整。

    Args:
        deliverable_since: 本问开始时间戳（v3/P2-3）。提供时执行交付物溯源检查：
            本问产生的交付物（mtime >= since）必须能在 notebook 源码中找到
            写盘证据（文件名 + 写出调用），否则判"非本轮代码生成"。
    """
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
        missing_imgs = [img for img in created_images if not os.path.exists(os.path.join(work_dir, img))]
        if missing_imgs:
            _add(
                f"声称生成的图表缺失：{', '.join(missing_imgs[:3])}",
                "确保所有声明图表真实落盘",
            )

    # v3/P2-3 交付物溯源：本问交付物必须能对应到 notebook 中的写盘代码
    if deliverable_since is not None:
        import glob

        has_write_call = any(marker in src_all for marker in WRITE_CALL_MARKERS)
        for pattern in DELIVERABLE_PATTERNS:
            for path in glob.glob(os.path.join(work_dir, pattern)):
                try:
                    if os.path.getmtime(path) < deliverable_since:
                        continue
                except OSError:
                    continue
                name = os.path.basename(path)
                if name in src_all and has_write_call:
                    continue
                _add(
                    f"交付物 {name} 疑似非本问代码生成"
                    f"（notebook 源码中无该文件的写出代码，可能读取了遗留产物）",
                    f"在本问 notebook 中用代码端到端生成 {name}"
                    f"（含 to_excel/to_csv/pickle 等写出调用并真实执行）",
                    Severity.CRITICAL,
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


def format_repair_roadmap(items: list[RoadmapItem]) -> str:
    """v3/P2-4：把门条目组装成分级聚焦的修复指令。

    实证（F1/F4）：四类问题平铺给 Coder，模型顺序修并被 minor/清洗类
    缠住，修复预算（≤3 轮）被稀释。改为 critical→major→minor 排序 +
    显式标注必须解决范围，minor 明示可延后。
    """
    sev_rank = {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2}
    ordered = sorted(items, key=lambda it: sev_rank.get(it.severity, 3))
    must = [it for it in ordered if it.severity != Severity.MINOR]
    minor = [it for it in ordered if it.severity == Severity.MINOR]

    blocks: list[str] = []
    if must:
        blocks.append(
            "【本轮必须解决（critical/major）】\n"
            + "\n".join(
                f"- [{it.severity.value}] {it.problem}（验收：{it.acceptance_criteria}）"
                for it in must
            )
        )
    if minor:
        blocks.append(
            "【可延后（minor，主链路跑通后有余力再处理）】\n"
            + "\n".join(f"- {it.problem}" for it in minor)
        )
    blocks.append(
        "【修复优先级】先解决上述 critical/major 条目并跑通主链路"
        "（建模→求解→结果文件写出）；minor 条目不得挤占主链路修复预算。"
    )
    return "\n\n".join(blocks)


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
