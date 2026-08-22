"""人工审批服务：检查点挂起注册表与挂起-决策-恢复的运行时桥。

铁律（docs/quality-gates-plan.md §6）：
- 无响应永久等待（event 无超时），绝不自动放行；
- 恢复必须由决策驱动（POST /approval），禁止用预设值自动推进；
- 全部决策（含意见原文）经状态机 override_history 记录在案。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage
from app.utils.log_util import logger


@dataclass
class PendingApproval:
    checkpoint: str
    payload: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    action: str = ""
    feedback: str = ""


# 运行中任务的挂起注册表（进程内存；状态持久化由状态机 task_state.json 承担）
_pending: dict[str, PendingApproval] = {}


def submit_decision(task_id: str, action: str, feedback: str = "") -> bool:
    """审批 API 调用：写入决策并唤醒挂起的工作流。"""
    p = _pending.get(task_id)
    if p is None:
        return False
    p.action = action
    p.feedback = feedback
    p.event.set()
    return True


def get_pending(task_id: str) -> Optional[PendingApproval]:
    return _pending.get(task_id)


async def wait_for_approval(
    task_id: str,
    state: Any,
    checkpoint: str,
    payload: dict[str, Any],
) -> PendingApproval:
    """挂起工作流等待人工决策（永久等待）。

    Args:
        state: TaskStateMachine，用于挂起/恢复的状态记录。
        checkpoint: 检查点名（split_review / model_review / paper_review）。
        payload: 展示给审批人的材料（摘要/预审意见/选项）。
    """
    p = PendingApproval(checkpoint=checkpoint, payload=payload)
    _pending[task_id] = p
    state.enter_checkpoint(checkpoint, payload)
    await redis_manager.publish_message(
        task_id,
        SystemMessage(content=f"流水线已挂起，等待人工审批：{checkpoint}（无响应将一直等待）", type="warning"),
    )
    try:
        await p.event.wait()  # 永久等待，无超时
    finally:
        _pending.pop(task_id, None)

    state.resolve_checkpoint(p.action, p.feedback)
    warn = state.friction_warning() if p.action == "revise" else None
    if warn:
        await redis_manager.publish_message(task_id, SystemMessage(content=warn, type="warning"))
    logger.info(f"[审批] {checkpoint} 决策: {p.action} 意见长度={len(p.feedback)}")
    return p
