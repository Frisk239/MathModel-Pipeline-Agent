"""磁盘保留策略：logs/messages 按天数、work_dir 按任务数滚动清理。

启动时执行一次（main.py lifespan），阈值来自配置。默认值保守
（60 个任务目录 > 当前存量），需用户显式调小才会真正删除。
"""

import os
import shutil
import time

from app.config.setting import settings
from app.utils.common_utils import TASK_ID_PATTERN
from app.utils.log_util import logger


def prune_message_logs(days: int, logs_dir: str = "logs/messages") -> int:
    """删除 logs/messages 下 mtime 超过 days 天的 *.json。

    Args:
        days: 保留天数；<=0 表示不清理。
        logs_dir: 消息目录路径（测试注入用）。

    Returns:
        删除的文件数。
    """
    if days <= 0 or not os.path.isdir(logs_dir):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(logs_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(logs_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as e:
            logger.warning(f"清理消息日志失败 {path}: {e}")
    return removed


def prune_task_workdirs(keep: int, work_root: str = "project/work_dir") -> int:
    """按 mtime 只保留最近 keep 个任务目录。

    只删名字形如任务 ID 的目录（TASK_ID_PATTERN），非任务目录（手工放置的
    文件等）一律不动；keep<=0 表示不清理。

    Args:
        keep: 保留的目录数。
        work_root: 任务根目录路径（测试注入用）。

    Returns:
        删除的目录数。
    """
    if keep <= 0 or not os.path.isdir(work_root):
        return 0
    entries = []
    for name in os.listdir(work_root):
        if not TASK_ID_PATTERN.fullmatch(name):
            continue
        path = os.path.join(work_root, name)
        if os.path.isdir(path):
            try:
                entries.append((os.path.getmtime(path), name, path))
            except OSError:
                continue
    entries.sort(reverse=True)  # 新→旧
    removed = 0
    for _, name, path in entries[keep:]:
        try:
            shutil.rmtree(path)
            removed += 1
            logger.info(f"保留策略清理任务目录: {name}")
        except OSError as e:
            logger.warning(f"清理任务目录失败 {path}: {e}")
    return removed


def run_startup_retention() -> None:
    """启动期执行一次滚动清理；任何异常只告警不阻断启动。"""
    try:
        logs_removed = prune_message_logs(settings.LOG_RETENTION_DAYS)
        dirs_removed = prune_task_workdirs(settings.TASK_DIR_MAX_COUNT)
        if logs_removed or dirs_removed:
            logger.info(
                f"磁盘保留清理：消息日志 {logs_removed} 个文件、"
                f"任务目录 {dirs_removed} 个"
            )
    except Exception as e:  # noqa: BLE001 清理失败不应阻断服务启动
        logger.warning(f"磁盘保留清理执行失败: {e}")
