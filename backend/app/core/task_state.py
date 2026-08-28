"""任务状态机：显式阶段枚举、转移表、禁止转移、修复轮次计数与持久化。

设计约束（docs/quality-gates-plan.md §3）：
- 循环上限由状态机强制：G2/G3 修复 ≤3 轮，超限的 gate→agent 回退被拒绝；
- 终态不可逆：completed/failed/cancelled 不再转出；
- 持久化到 work_dir/task_state.json，后端重启后任务状态可查询。
"""

import json
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from app.utils.log_util import logger

MAX_REPAIR_ROUNDS = 3


class TaskPhase(str, Enum):
    """任务阶段（含挂起态与三个终态）。"""

    CREATED = "created"
    SPLITTING = "splitting"            # 拆题
    G1_GATE = "g1_gate"                # 数据完备性门
    MODELING = "modeling"              # 建模
    CODING = "coding"                  # 编码（含 G2 循环）
    WRITING = "writing"                # 写作（含 G3 循环）
    ASSEMBLING = "assembling"          # 论文组装
    FINAL_REVIEW = "final_review"      # 终审（二期）
    AWAITING_DECISION = "awaiting_decision"  # 人工检查点挂起（二期）
    COMPLETED = "completed"            # 终态
    FAILED = "failed"                  # 终态
    CANCELLED = "cancelled"            # 终态


# 合法转移表（一期流水线；二期接入审批后扩展 model_review/paper_review）
ALLOWED_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.CREATED: {TaskPhase.SPLITTING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.SPLITTING: {TaskPhase.G1_GATE, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.G1_GATE: {TaskPhase.MODELING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.MODELING: {TaskPhase.CODING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.CODING: {TaskPhase.WRITING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.WRITING: {TaskPhase.ASSEMBLING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.ASSEMBLING: {TaskPhase.FINAL_REVIEW, TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.FINAL_REVIEW: {TaskPhase.COMPLETED, TaskPhase.WRITING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.AWAITING_DECISION: {TaskPhase.FAILED, TaskPhase.CANCELLED},  # 挂起态仅允许终止类转移（恢复走 resolve_checkpoint 专用通道）
    TaskPhase.COMPLETED: set(),
    TaskPhase.FAILED: set(),
    TaskPhase.CANCELLED: set(),
}

# 禁止转移的显式清单（写死，配单测；格式：描述 -> (from, to)）
PROHIBITED_TRANSITIONS: tuple[tuple[str, tuple[TaskPhase, TaskPhase]], ...] = (
    ("禁止绕过 G1 数据门", (TaskPhase.SPLITTING, TaskPhase.MODELING)),
    ("禁止跳过写作直接组装", (TaskPhase.CODING, TaskPhase.ASSEMBLING)),
    ("禁止终修后回审（G4 语义，一期预留）", (TaskPhase.FINAL_REVIEW, TaskPhase.FINAL_REVIEW)),
    ("completed 不可回 in-flight", (TaskPhase.COMPLETED, TaskPhase.WRITING)),
    ("failed 不可复活为 completed", (TaskPhase.FAILED, TaskPhase.COMPLETED)),
    ("cancelled 不可复活", (TaskPhase.CANCELLED, TaskPhase.SPLITTING)),
    ("未决策不得自动离开挂起态", (TaskPhase.AWAITING_DECISION, TaskPhase.WRITING)),
)


class TransitionError(RuntimeError):
    """非法状态转移（含修复轮次超限）。"""


class TaskStateMachine:
    """单任务状态机：转移校验、修复轮次计数、审批挂起/恢复、持久化。"""

    def __init__(self, task_id: str, work_dir: str):
        self.task_id = task_id
        self.work_dir = work_dir
        self.phase = TaskPhase.CREATED
        self.fail_reason: str = ""
        self.repair_rounds: dict[str, int] = {}   # gate 名 -> 已用修复轮次
        self.transitions: list[dict] = []
        self.pending_checkpoint: dict | None = None  # 挂起的审批请求（含原阶段）
        self.override_history: list[dict] = []       # 人工推翻/决策记录（含摩擦升级）
        self._state_path = Path(work_dir) / "task_state.json"

    # ---- 转移 ----

    def transition(self, to: TaskPhase, note: str = "") -> None:
        """执行状态转移；非法转移抛 TransitionError（绝不静默放行）。"""
        allowed = ALLOWED_TRANSITIONS[self.phase]
        if to not in allowed:
            raise TransitionError(
                f"非法状态转移 {self.phase.value} -> {to.value}"
                f"{'（' + note + '）' if note else ''}；合法目标: "
                f"{sorted(p.value for p in allowed) or ['<无（终态/挂起态）>']}"
            )
        record = {
            "from": self.phase.value,
            "to": to.value,
            "note": note,
            "at": int(time.time()),
        }
        self.phase = to
        self.transitions.append(record)
        logger.info(f"[状态机] {record['from']} -> {record['to']} {note}")
        self.save()

    # ---- 人工检查点（挂起/恢复）----

    def enter_checkpoint(self, checkpoint: str, payload: dict) -> None:
        """进入人工检查点：挂起并记录原阶段与审批材料。"""
        if self.phase == TaskPhase.AWAITING_DECISION:
            raise TransitionError("已有挂起的检查点，不可重复挂起")
        original = self.phase
        record = {
            "from": self.phase.value,
            "to": TaskPhase.AWAITING_DECISION.value,
            "note": f"人工检查点 {checkpoint}",
            "at": int(time.time()),
        }
        self.phase = TaskPhase.AWAITING_DECISION
        self.pending_checkpoint = {
            "checkpoint": checkpoint,
            "original_phase": original.value,
            "payload": payload,
        }
        self.transitions.append(record)
        logger.info(f"[状态机] 挂起等待审批: {checkpoint}")
        self.save()

    def resolve_checkpoint(self, action: str, feedback: str = "") -> dict:
        """审批决策：记录决策（含摩擦规则）并恢复原阶段；返回决策供 workflow 分支。

        铁律：恢复必须经此方法（决策驱动），禁止自动离开挂起态。
        """
        if self.phase != TaskPhase.AWAITING_DECISION or not self.pending_checkpoint:
            raise TransitionError("当前无挂起的检查点")
        if action not in ("approve", "revise", "reject"):
            raise TransitionError(f"未知审批动作: {action}")

        # 摩擦升级：连续 revise 且不带意见 → 警告；决策全部记录在案
        record = {
            "checkpoint": self.pending_checkpoint["checkpoint"],
            "action": action,
            "feedback": feedback,
            "at": int(time.time()),
        }
        self.override_history.append(record)

        original = TaskPhase(self.pending_checkpoint["original_phase"])
        self.pending_checkpoint = None
        self.transitions.append(
            {
                "from": TaskPhase.AWAITING_DECISION.value,
                "to": original.value,
                "note": f"审批决策: {action}"
                f"{'（附意见）' if feedback else ''}",
                "at": int(time.time()),
            }
        )
        self.phase = original
        self.save()
        return record

    def record_auto_degrade(self, gate: str, note: str) -> None:
        """AUTO_MODE 下门耗尽的自动降级记录（与人工决策区分审计）。"""
        self.override_history.append(
            {
                "checkpoint": f"{gate}_exhausted",
                "action": "auto_degraded",
                "feedback": note[:300],
                "at": int(time.time()),
            }
        )
        self.save()

    def friction_warning(self) -> str | None:
        """摩擦升级提示：同一检查点第 2 次 revise 无意见时给出警告文案。"""
        cp = self.pending_checkpoint["checkpoint"] if self.pending_checkpoint else ""
        recent = [r for r in self.override_history if r["checkpoint"] == cp]
        if len(recent) >= 2 and recent[-1]["action"] == "revise" and not recent[-1]["feedback"]:
            return f"检查点 {cp} 已连续 {len(recent)} 次返工且未附意见，请说明具体修改方向"
        return None

    def fail(self, reason: str) -> None:
        """标记任务失败（任何非终态可达；幂等）。"""
        if self.phase in (TaskPhase.COMPLETED, TaskPhase.CANCELLED):
            return
        self.fail_reason = reason
        try:
            self.transition(TaskPhase.FAILED, note=reason[:200])
        except TransitionError:
            # 已是 failed 等情形，仅补记原因
            self.save()

    # ---- 修复轮次 ----

    def request_repair(self, gate: str, cap: int | None = None) -> int:
        """申请一轮修复；返回当前轮次编号，超限抛 TransitionError。

        cap 允许调用方按门覆盖默认上限（如 G2 的可配置轮次）。
        """
        limit = cap if cap and cap > 0 else MAX_REPAIR_ROUNDS
        used = self.repair_rounds.get(gate, 0)
        if used >= limit:
            raise TransitionError(
                f"门 {gate} 修复轮次已达上限 {limit}，必须升级人工决策"
            )
        used += 1
        self.repair_rounds[gate] = used
        self.save()
        return used

    def repair_used(self, gate: str) -> int:
        return self.repair_rounds.get(gate, 0)

    # ---- 持久化 ----

    def save(self) -> None:
        """落盘到 work_dir/task_state.json（原子写）。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": self.task_id,
            "phase": self.phase.value,
            "fail_reason": self.fail_reason,
            "repair_rounds": self.repair_rounds,
            "transitions": self.transitions,
            "pending_checkpoint": self.pending_checkpoint,
            "override_history": self.override_history,
        }
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self._state_path)

    @classmethod
    def load(cls, task_id: str, work_dir: str) -> Optional["TaskStateMachine"]:
        """从磁盘恢复状态机（后端重启后任务状态可查询）。"""
        path = Path(work_dir) / "task_state.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"任务状态文件损坏 {path}: {e}")
            return None
        sm = cls(task_id, work_dir)
        sm.phase = TaskPhase(payload.get("phase", "created"))
        sm.fail_reason = payload.get("fail_reason", "")
        sm.repair_rounds = payload.get("repair_rounds", {})
        sm.transitions = payload.get("transitions", [])
        sm.pending_checkpoint = payload.get("pending_checkpoint")
        sm.override_history = payload.get("override_history", [])
        return sm

    def snapshot(self) -> dict:
        """对外只读快照（/status 查询用）。"""
        return {
            "task_id": self.task_id,
            "phase": self.phase.value,
            "is_terminal": self.phase
            in (TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED),
            "fail_reason": self.fail_reason,
            "repair_rounds": self.repair_rounds,
            "pending_checkpoint": self.pending_checkpoint,
            "override_history": self.override_history,
            "transition_count": len(self.transitions),
            "last_transition": self.transitions[-1] if self.transitions else None,
        }
