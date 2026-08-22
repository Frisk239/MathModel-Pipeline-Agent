"""G3 文本门禁的检查规则常量（单一来源：门与单测共享，防漂移）。"""

import re

# ---- 泄露检测 ----

# 内部错误文案/系统消息片段：出现在论文正文即违规（关键词级）
LEAK_KEYWORDS: tuple[str, ...] = (
    "搜索文献失败",
    "搜索服务暂时不可用",
    "配置OpenAlex邮箱",
    "EXA_API_KEY",
    "OPENALEX_EMAIL",
    "调用工具",
    "工具调用",
    "（本节生成失败）",
    "(本节生成失败)",
    "论文手调用",
    "写作手调用",
)

# 内部路径模式：backend 内部目录/文件结构泄露
LEAK_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"app[/\\](core|routers|tools|services|utils)[/\\]",
        r"logs[/\\]messages",
        r"chat_history",
        r"\.venv[/\\]",
    )
)

# shingle 泄露检测窗口：字符级 n-gram（中英文统一——中文无空格分词，词级窗口失效）
SHINGLE_CHARS = 24  # 连续字符数，24 字符连续相同基本可断定逐字复制


def normalize_ws(text: str) -> str:
    """空白折叠归一（换行不敏感比对的前置）。"""
    return re.sub(r"\s+", " ", text).strip()


def shingles(text: str, window: int = SHINGLE_CHARS) -> set[str]:
    """文本的字符级 n-gram 指纹集合（去空白后切窗）。"""
    compact = re.sub(r"\s+", "", text)
    if len(compact) < window:
        return set()
    return {compact[i : i + window] for i in range(len(compact) - window + 1)}


# ---- 占位符检测 ----

PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"TODO[:：]?",
        r"FIXME",
        r"PLACEHOLDER",
        r"待补充",
        r"待续写",
        r"示例数据（仅",
        r"XXX{4,}",
        r"…{3,}在这里填写",
    )
)

# ---- 引用标记与参考文献 ----

# 引用标记：{[^N]: content} 与 {[^N] content} 两种格式（冒号可选）
CITATION_MARK_RE = re.compile(r"\{\[\^(\d+)\]:?\s*([^}]*)\}")
# 参考文献条目行：[^N]: content（组装后由 UserOutput 生成）
FOOTNOTE_ENTRY_RE = re.compile(r"^\[\^(\d+)\]:\s*\S", re.MULTILINE)
