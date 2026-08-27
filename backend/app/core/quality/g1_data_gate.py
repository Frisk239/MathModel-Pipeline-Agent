"""G1 数据完备性门：题面所需附件 vs 工作目录实存文件。

唯一一票否决的门——数据缺失非 Agent 可修复，失败即任务终止。
"""

import os
import re
import unicodedata

from app.core.quality.contracts import (
    GateName,
    GateReport,
    GateVerdict,
    Obligation,
    RoadmapItem,
    Severity,
)


def _normalize_for_match(name: str) -> str:
    """附件名归一：全角转半角、去空白、小写，供模糊匹配。"""
    name = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", name).lower()


def extract_required_from_problem(ques_all: str) -> list[str]:
    """从题面文本提取附件引用（正则兜底，与 Coordinator 声明取并集）。

    匹配 "附件1/附件 1/附件一/Attachment 1/appendix1" 等变体。
    """
    normalized = unicodedata.normalize("NFKC", ques_all)
    # 模板类附件（须提交结果的模板文件）是可选输入，不列入必需清单：
    # 匹配附件名后 20 字符的上下文，含模板特征词则跳过
    _TEMPLATE_CTX = ("result", "模板", "template", "提交结果")
    pat = re.compile(
        r"((?:附件|attachment|appendix)\s*[0-9０-９一二三四五六七八九十]+)", re.IGNORECASE
    )
    seen: list[str] = []
    _SENT_SEP = "。；;！!？?\n"
    for m in pat.finditer(normalized):
        name = m.group(1)
        key = _normalize_for_match(name)
        if key in seen:
            continue
        # 模板词既可能在附件名之后（"附件 3 须提交结果的模板文件"），
        # 也可能在之前（2024-C 官方题面："模板文件见附件 3"）。
        # 判定限定在附件名所在句子内，防止跨句窗口误吞相邻句的 result 字样
        sent_start = max(normalized.rfind(ch, 0, m.start()) for ch in _SENT_SEP) + 1
        ends = [pos for pos in (normalized.find(ch, m.end()) for ch in _SENT_SEP) if pos != -1]
        sent_end = min(ends) if ends else len(normalized)
        sentence = normalized[sent_start:sent_end].lower()
        if any(t in sentence for t in _TEMPLATE_CTX):
            continue
        seen.append(key)
    return seen


def check_data_completeness(
    required_files: list[str],
    work_dir: str,
    existing_files: list[str] | None = None,
) -> GateReport:
    """核对所需附件是否都在工作目录中（模糊匹配附件命名变化）。

    Args:
        required_files: 题面声明的附件清单（Coordinator 输出 + 正则兜底并集）。
        work_dir: 工作目录。
        existing_files: 显式传入实存文件（测试用）；缺省时扫描 work_dir。

    Returns:
        GateReport：缺失即 MATERIAL（任务级失败由调用方处理）。
    """
    if existing_files is None:
        existing_files = os.listdir(work_dir) if os.path.isdir(work_dir) else []

    existing_norm = {_normalize_for_match(f) for f in existing_files}
    # 附件编号集合：中英变体（附件N/attachmentN/appendixN）统一折算为数字 N
    existing_numbers: set[str] = set()
    existing_tokens: set[str] = set()
    for f in existing_files:
        norm = _normalize_for_match(f)
        existing_tokens.add(norm)
        for m in re.finditer(r"(?:附件|attachment|appendix)(\d+)", norm):
            existing_tokens.add(m.group(0))
            existing_numbers.add(m.group(1))

    def _required_numbers(norm_name: str) -> set[str]:
        return {
            m.group(1)
            for m in re.finditer(r"(?:附件|attachment|appendix)(\d+)", norm_name)
        }

    # 模板类附件（结果模板/提交模板）是可选输入，缺失不阻断——只记录提示
    TEMPLATE_MARKERS = ("result", "模板", "template", "提交结果")

    def _is_template(name: str) -> bool:
        n = _normalize_for_match(name)
        return any(m in n for m in TEMPLATE_MARKERS)

    missing: list[str] = []
    template_missing: list[str] = []
    for required in required_files:
        norm = _normalize_for_match(required)
        req_nums = _required_numbers(norm)
        hit = bool(req_nums & existing_numbers) or any(
            norm in e or e in norm for e in existing_norm if abs(len(e) - len(norm)) < 40
        )
        if not hit:
            if _is_template(required):
                template_missing.append(required)
            else:
                missing.append(required)

    if not missing:
        return GateReport(
            gate=GateName.G1,
            verdict=GateVerdict.PASS,
            summary=f"题面声明的 {len(required_files)} 项附件全部就位"
            + (f"（模板类 {len(template_missing)} 项可选未传）" if template_missing else ""),
        )

    items = [
        RoadmapItem(
            id=f"g1-missing-{i}",
            problem=f"题面要求的附件「{name}」在工作目录中未找到",
            evidence_anchor=f"工作目录: {os.path.basename(work_dir)}",
            severity=Severity.CRITICAL,
            obligation=Obligation.MUST_FIX,
            cost_scope="re_analysis",
            acceptance_criteria="用户补充对应附件文件后重新提交任务",
            target="工作目录",
        )
        for i, name in enumerate(missing, 1)
    ]
    return GateReport(
        gate=GateName.G1,
        verdict=GateVerdict.MATERIAL,
        items=items,
        summary=f"数据完备性检查失败：缺少 {len(missing)} 项附件（{', '.join(missing)}）。"
        "数据缺失非 Agent 可修复，任务终止。",
    )
