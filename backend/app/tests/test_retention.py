"""磁盘保留策略单测：造 N+1 个假文件/目录，断言只留 N。"""

import os
import time

from app.services.retention import prune_message_logs, prune_task_workdirs


def _touch(path: str, age_days: float = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_prune_message_logs_keeps_recent_deletes_old(tmp_path):
    logs = tmp_path / "messages"
    _touch(str(logs / "recent.json"), age_days=1)
    _touch(str(logs / "old.json"), age_days=40)
    _touch(str(logs / "keep.txt"), age_days=40)  # 非 json 不动

    removed = prune_message_logs(30, str(logs))

    assert removed == 1
    assert (logs / "recent.json").exists()
    assert not (logs / "old.json").exists()
    assert (logs / "keep.txt").exists()


def test_prune_message_logs_zero_days_disables(tmp_path):
    logs = tmp_path / "messages"
    _touch(str(logs / "old.json"), age_days=999)
    assert prune_message_logs(0, str(logs)) == 0
    assert (logs / "old.json").exists()


def test_prune_workdirs_keeps_newest_n(tmp_path):
    root = tmp_path / "work_dir"
    names = []
    for i, tid in enumerate(["20240101-0000000a", "20240102-0000000b", "20240103-0000000c"]):
        path = root / tid
        path.mkdir(parents=True)
        _touch(str(path / "task_state.json"), age_days=10 - i)  # 越后越新
        names.append(tid)

    removed = prune_task_workdirs(2, str(root))

    assert removed == 1
    assert not (root / names[0]).exists()  # 最旧的被删
    assert (root / names[1]).exists()
    assert (root / names[2]).exists()


def test_prune_workdirs_skips_non_task_dirs(tmp_path):
    root = tmp_path / "work_dir"
    (root / "not-a-task").mkdir(parents=True)  # 不匹配任务 ID 模式
    (root / "20240101-0000000a").mkdir(parents=True)

    removed = prune_task_workdirs(0, str(root))  # keep=0 不清理

    assert removed == 0
    assert (root / "not-a-task").exists()
    assert (root / "20240101-0000000a").exists()
