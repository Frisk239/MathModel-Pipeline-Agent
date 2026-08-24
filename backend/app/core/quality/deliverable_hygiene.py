"""交付物卫生（v3/P2-3，灭 F3 无代码支撑的结论）。

实证（20260823-233814）：上轮失败遗留的磁盘 result*.xlsx 被新一轮在首个 cell
读取并冒充本轮求解产出，执行总结报告了 notebook 中不存在的代码的数字。
"失败 cell 不入交付 notebook"只管 cell，磁盘产物需要独立的归档隔离。
"""

import glob
import os
import shutil

# 本问交付物/解档案模式：修复轮开始时归档，防新一轮读取遗留产物冒充产出。
# 图表不归档（由代码同名覆盖重生成）；EDA 中间表（clean_*）不归档（每轮重建）。
DELIVERABLE_PATTERNS: tuple[str, ...] = ("result*.xlsx", "result*.csv", "sol*.pkl")

# notebook 源码中视为"写出文件"的调用特征（与文件名共同构成溯源证据）
WRITE_CALL_MARKERS: tuple[str, ...] = (
    "to_excel",
    "to_csv",
    "to_pickle",
    "pickle.dump",
    "savefig",
    "ExcelWriter",
)


def archive_stale_deliverables(work_dir: str, round_no: int, since: float) -> list[str]:
    """G2 修复轮开始时，把本问产生的交付物移入 _retry_archive/round{N}/。

    只归档 mtime >= since（本问开始时间）的匹配文件：早于本问的文件是
    此前问次的合法产物，绝不能动。归档不删除，保留审计轨迹。

    Returns:
        归档的文件名列表（空列表表示无可归档项）。
    """
    archive_dir = os.path.join(work_dir, "_retry_archive", f"round{round_no}")
    archived: list[str] = []
    for pattern in DELIVERABLE_PATTERNS:
        for path in glob.glob(os.path.join(work_dir, pattern)):
            try:
                if os.path.getmtime(path) < since:
                    continue
            except OSError:
                continue
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(path, os.path.join(archive_dir, os.path.basename(path)))
            archived.append(os.path.basename(path))
    return archived
