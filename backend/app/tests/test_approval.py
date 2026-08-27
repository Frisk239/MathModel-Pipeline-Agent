"""人工审批服务单测：三分支决策、无挂起提交、超时提醒（Fake 注入，不碰 Redis）。"""

import asyncio
from types import SimpleNamespace

from app.config.setting import settings
from app.services import approval


class _FakeState:
    """替身 TaskStateMachine：只记录检查点挂起/恢复调用。"""

    def __init__(self, friction: str | None = None):
        self.calls: list[tuple] = []
        self._friction = friction

    def enter_checkpoint(self, checkpoint, payload):
        self.calls.append(("enter", checkpoint))

    def resolve_checkpoint(self, action, feedback):
        self.calls.append(("resolve", action, feedback))

    def friction_warning(self):
        return self._friction


def _patch_publish(monkeypatch) -> list:
    """把 redis_manager 换成消息收集器，返回收集到的 SystemMessage 列表。"""
    published = []

    async def _publish(task_id, msg):
        published.append(msg)

    monkeypatch.setattr(
        "app.services.approval.redis_manager",
        SimpleNamespace(publish_message=_publish),
    )
    return published


def _run_wait_and_decide(task_id, state, action, feedback=""):
    async def _scenario():
        task = asyncio.create_task(
            approval.wait_for_approval(task_id, state, "split_review", {"a": 1})
        )
        await asyncio.sleep(0.02)
        assert approval.submit_decision(task_id, action, feedback)
        return await task

    return asyncio.run(_scenario())


def test_approve_branch_resolves_and_clears_registry(monkeypatch):
    published = _patch_publish(monkeypatch)
    state = _FakeState()

    p = _run_wait_and_decide("t-approve", state, "approve")

    assert p.action == "approve"
    assert state.calls == [("enter", "split_review"), ("resolve", "approve", "")]
    assert approval.get_pending("t-approve") is None  # 决策后注册表必须清空
    assert published[0].type == "warning"  # 挂起提示已发出


def test_revise_branch_carries_feedback_and_friction_warning(monkeypatch):
    published = _patch_publish(monkeypatch)
    state = _FakeState(friction="⚠️ 修复轮预算告警")

    p = _run_wait_and_decide("t-revise", state, "revise", "请补敏感性分析")

    assert (p.action, p.feedback) == ("revise", "请补敏感性分析")
    assert state.calls[-1] == ("resolve", "revise", "请补敏感性分析")
    assert len(published) == 2 and "告警" in published[1].content


def test_reject_branch_resolves(monkeypatch):
    _patch_publish(monkeypatch)
    state = _FakeState()

    p = _run_wait_and_decide("t-reject", state, "reject", "方向错误，重做")

    assert p.action == "reject"
    assert state.calls[-1] == ("resolve", "reject", "方向错误，重做")


def test_submit_without_pending_returns_false():
    assert approval.submit_decision("no-such-task", "approve") is False


def test_timeout_reminds_but_never_auto_releases(monkeypatch):
    """HIL_TIMEOUT 只触发提醒：超时后仍保持挂起，等待决策才恢复。"""
    published = _patch_publish(monkeypatch)
    monkeypatch.setattr(settings, "HIL_TIMEOUT", 0)  # max(...,1) → 1 秒后提醒
    state = _FakeState()

    async def _scenario():
        task = asyncio.create_task(
            approval.wait_for_approval("t-timeout", state, "model_review", {})
        )
        await asyncio.sleep(1.3)  # 跨过一个提醒周期
        assert approval.get_pending("t-timeout") is not None  # 仍挂起
        approval.submit_decision("t-timeout", "approve")
        return await task

    p = asyncio.run(_scenario())

    assert p.action == "approve"
    reminders = [m for m in published if "仍无响应" in m.content]
    assert len(reminders) >= 1
    assert "绝不自动放行" in reminders[0].content
