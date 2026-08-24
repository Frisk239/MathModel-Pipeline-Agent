"""交付物卫生（v3/P2-3，灭 F3 无代码支撑的结论）单测。"""

import json
import os
import time

from app.core.quality.deliverable_hygiene import archive_stale_deliverables
from app.core.quality.g2_code_gate import check_notebook_artifacts


def _write_nb(path: str, src: str) -> None:
    nb = {
        "cells": [
            {"cell_type": "code", "source": [src], "outputs": []},
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False)


def _touch(path: str, mtime: float | None = None) -> None:
    with open(path, "wb") as f:
        f.write(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_archive_only_current_subtask_deliverables(tmp_path):
    since = time.time() - 60
    # 本问产物（新）→ 应归档；此前问次的合法产物（旧）→ 不动；不匹配文件 → 不动
    _touch(tmp_path / "result2.xlsx", mtime=since + 30)
    _touch(tmp_path / "result1_1.xlsx", mtime=since - 30)
    _touch(tmp_path / "附件1.xlsx", mtime=since + 30)

    archived = archive_stale_deliverables(str(tmp_path), round_no=1, since=since)

    assert archived == ["result2.xlsx"]
    assert os.path.exists(tmp_path / "_retry_archive" / "round1" / "result2.xlsx")
    assert not os.path.exists(tmp_path / "result2.xlsx")
    assert os.path.exists(tmp_path / "result1_1.xlsx")
    assert os.path.exists(tmp_path / "附件1.xlsx")


def test_l1_flags_deliverable_without_code_provenance(tmp_path):
    since = time.time() - 60
    _write_nb(tmp_path / "notebook.ipynb", "print('只有打印，没有写盘代码')")
    _touch(tmp_path / "result1_1.xlsx", mtime=since + 10)

    items = check_notebook_artifacts(
        str(tmp_path / "notebook.ipynb"), str(tmp_path), deliverable_since=since
    )
    provenance = [it for it in items if "非本问代码生成" in it.problem]
    assert len(provenance) == 1
    assert provenance[0].severity.value == "critical"


def test_l1_passes_deliverable_with_code_provenance(tmp_path):
    since = time.time() - 60
    _write_nb(
        tmp_path / "notebook.ipynb",
        "df.to_excel('result1_1.xlsx')",
    )
    _touch(tmp_path / "result1_1.xlsx", mtime=since + 10)

    items = check_notebook_artifacts(
        str(tmp_path / "notebook.ipynb"), str(tmp_path), deliverable_since=since
    )
    assert not any("非本问代码生成" in it.problem for it in items)


def test_l1_ignores_stale_deliverable_from_previous_subtask(tmp_path):
    since = time.time() - 60
    _write_nb(tmp_path / "notebook.ipynb", "print('无写盘')")
    # mtime 早于本问开始：上一问的合法产物，不属本问溯源范围
    _touch(tmp_path / "result1_1.xlsx", mtime=since - 30)

    items = check_notebook_artifacts(
        str(tmp_path / "notebook.ipynb"), str(tmp_path), deliverable_since=since
    )
    assert not any("非本问代码生成" in it.problem for it in items)
