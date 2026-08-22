"""G3 文本门禁：论文节级文本检查（泄露/占位符/结构）与组装级引用双向完整性。

节级检查在 Writer 每节交付后运行；引用双向完整性在论文组装后运行。
检查规则常量统一来自 checks.py（单一来源）。
"""


from app.core.quality.checks import (
    CITATION_MARK_RE,
    FOOTNOTE_ENTRY_RE,
    LEAK_KEYWORDS,
    LEAK_PATH_PATTERNS,
    PLACEHOLDER_PATTERNS,
    SHINGLE_CHARS,
    shingles,
)
from app.core.quality.contracts import (
    GateName,
    GateReport,
    GateVerdict,
    Obligation,
    RoadmapItem,
    Severity,
)


def _item(i: int, problem: str, anchor: str, criteria: str) -> RoadmapItem:
    return RoadmapItem(
        id=f"g3-{i}",
        problem=problem,
        evidence_anchor=anchor,
        severity=Severity.MAJOR,
        obligation=Obligation.MUST_FIX,
        cost_scope="sentence",
        acceptance_criteria=criteria,
        target=anchor,
    )


def check_section_text(
    section_text: str,
    section_key: str = "",
    internal_sources: list[str] | None = None,
    exemptions: list[str] | None = None,
) -> GateReport:
    """节级文本检查：泄露（关键词+路径+shingle）、占位符、章节标题结构。

    Args:
        section_text: 该节正文。
        section_key: 章节标识（如 ques1），用于锚点。
        internal_sources: 内部材料原文（shingle 检测源，如系统消息模板）。
        exemptions: 豁免文本（如题面原文，论文引用题面合法）。
    """
    items: list[RoadmapItem] = []

    # 1) 泄露：内部错误文案关键词
    for kw in LEAK_KEYWORDS:
        if kw in section_text:
            items.append(
                _item(
                    len(items) + 1,
                    f"论文正文出现内部信息「{kw}」",
                    f"{section_key}",
                    f"删除或改写含「{kw}」的句子，正文不得出现系统/工具文案",
                )
            )

    # 2) 泄露：内部路径模式
    for pat in LEAK_PATH_PATTERNS:
        m = pat.search(section_text)
        if m:
            items.append(
                _item(
                    len(items) + 1,
                    f"论文正文出现内部路径「{m.group(0)}」",
                    f"{section_key}",
                    "删除内部路径引用",
                )
            )

    # 3) 泄露：shingle 指纹（连续字符来自内部材料，且不在豁免堆）
    if internal_sources:
        exemption_shingles: set[str] = set()
        for ex in exemptions or []:
            exemption_shingles |= shingles(ex)
        for src in internal_sources:
            for sh in shingles(src):
                if sh in shingles(section_text) and sh not in exemption_shingles:
                    frag = sh[:80]
                    items.append(
                        _item(
                            len(items) + 1,
                            f"论文正文包含与内部材料逐字相同的连续 {SHINGLE_CHARS} 字符片段「{frag}…」",
                            f"{section_key}",
                            "改写该片段，正文不得逐字复制内部过程材料",
                        )
                    )
                    break  # 每个来源报一条即可

    # 4) 占位符
    for pat in PLACEHOLDER_PATTERNS:
        m = pat.search(section_text)
        if m:
            items.append(
                _item(
                    len(items) + 1,
                    f"论文正文存在占位符「{m.group(0)}」",
                    f"{section_key}",
                    "补全该处内容，删除占位符",
                )
            )

    minor_count = sum(1 for it in items if it.severity == Severity.MINOR)
    major_count = len(items) - minor_count

    if not items:
        return GateReport(
            gate=GateName.G3,
            verdict=GateVerdict.PASS,
            summary=f"章节 {section_key} 文本门禁通过",
        )
    # ≤3 条且全为 Minor → MINOR 放行；否则 MATERIAL
    if major_count == 0 and minor_count <= 3:
        return GateReport(
            gate=GateName.G3,
            verdict=GateVerdict.MINOR,
            items=items,
            summary=f"章节 {section_key} 存在 {minor_count} 条轻微问题，放行并提示",
        )
    return GateReport(
        gate=GateName.G3,
        verdict=GateVerdict.MATERIAL,
        items=items,
        summary=f"章节 {section_key} 文本门禁拦截：{len(items)} 条问题（{major_count} 条 Major+）",
    )


def check_citation_integrity(full_text: str) -> GateReport:
    """组装级引用双向完整性：正文引用标记 ↔ 参考文献条目互相对照。

    - 孤儿引用：正文有 {[^N]...} 标记但终稿无 [^N]: 条目；
    - 幽灵条目：终稿有条目但正文从未引用（提示级）；
    - 文献空白：正文含引用标记但参考文献节为空。
    """
    marks = CITATION_MARK_RE.findall(full_text)
    cited_nums = {n for n, _ in marks}
    entry_nums = {m.group(1) for m in FOOTNOTE_ENTRY_RE.finditer(full_text)}

    items: list[RoadmapItem] = []
    orphans = sorted(cited_nums - entry_nums, key=int)
    ghosts = sorted(entry_nums - cited_nums, key=int)

    if cited_nums and not entry_nums:
        items.append(
            _item(
                1,
                f"正文含 {len(cited_nums)} 处引用标记但参考文献列表为空",
                "参考文献节",
                "组装环节应将全部引用生成为 [^N]: 条目",
            )
        )
    elif orphans:
        items.append(
            _item(
                1,
                f"孤儿引用 [^{', ^'.join(orphans)}]：正文引用了但参考文献无对应条目",
                "参考文献节",
                "为每个被引编号补齐条目，或修正编号",
            )
        )
    if ghosts:
        items.append(
            RoadmapItem(
                id="g3-ghost",
                problem=f"幽灵条目 [^{', ^'.join(ghosts)}]：参考文献有条目但正文未引用",
                evidence_anchor="参考文献节",
                severity=Severity.MINOR,
                obligation=Obligation.SHOULD_FIX,
                cost_scope="sentence",
                acceptance_criteria="删除未引用条目或在正文补引用",
                target="参考文献节",
            )
        )

    if not items:
        return GateReport(
            gate=GateName.G3,
            verdict=GateVerdict.PASS,
            summary="引用双向完整性通过",
        )
    has_major = any(it.severity != Severity.MINOR for it in items)
    return GateReport(
        gate=GateName.G3,
        verdict=GateVerdict.MATERIAL if has_major else GateVerdict.MINOR,
        items=items,
        summary=f"引用完整性：{len(items)} 条问题（孤儿={len(orphans)} 幽灵={len(ghosts)}）",
    )
