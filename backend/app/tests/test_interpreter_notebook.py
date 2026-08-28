"""代码解释器写入 notebook 的回归测试。"""

import asyncio
import queue
import time

import nbformat

from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer


async def _ignore_async(*args, **kwargs) -> None:
    return None


def test_failed_execution_is_not_kept_in_notebook(tmp_path, monkeypatch):
    """失败尝试保留在日志/UI，但不得污染交付用 notebook。"""
    serializer = NotebookSerializer(work_dir=str(tmp_path))
    interpreter = LocalCodeInterpreter(
        task_id="failed-cell-regression",
        work_dir=str(tmp_path),
        notebook_serializer=serializer,
    )
    monkeypatch.setattr(
        interpreter,
        "execute_code_",
        lambda code: [("stdout", "partial"), ("error", "Traceback: boom")],
    )
    monkeypatch.setattr(interpreter, "_push_to_websocket", _ignore_async)
    monkeypatch.setattr(
        "app.tools.local_interpreter.redis_manager.publish_message",
        _ignore_async,
    )

    _, error_occurred, _ = asyncio.run(interpreter.execute_code("raise Boom"))

    saved = nbformat.read(tmp_path / "notebook.ipynb", as_version=4)
    assert error_occurred is True
    assert serializer.nb.cells == []
    assert saved.cells == []


class TestExecuteTimeoutRecovery:
    """20260827 活锁事故回归：kernel 死锁/崩溃必须报错并恢复，不得无限等待。"""

    def _make_interpreter(self, tmp_path, monkeypatch, km, kc):
        serializer = NotebookSerializer(work_dir=str(tmp_path))
        interp = LocalCodeInterpreter(
            task_id="timeout-regression",
            work_dir=str(tmp_path),
            notebook_serializer=serializer,
        )
        interp.km, interp.kc = km, kc
        monkeypatch.setattr(
            "app.tools.local_interpreter.jupyter_client.manager.start_new_kernel",
            lambda **kwargs: (km, kc),
        )
        monkeypatch.setattr(interp, "_pre_execute_code", lambda: (None, "info"))
        return interp

    def test_deadlock_times_out_and_restarts(self, tmp_path, monkeypatch):
        """kernel 存活但不回消息（死锁）：先 interrupt，宽限后重启并返回错误。"""

        class FakeKM:
            interrupted = 0

            def is_alive(self):
                return True

            def interrupt_kernel(self):
                FakeKM.interrupted += 1

            def shutdown_kernel(self):
                pass

        class FakeKC:
            def execute(self, code):
                pass

            def get_iopub_msg(self, timeout=None):
                time.sleep(0.05)
                raise queue.Empty()  # 永远无消息：模拟死锁

            def shutdown(self):
                pass

        interp = self._make_interpreter(tmp_path, monkeypatch, FakeKM(), FakeKC())

        out = interp.execute_code_("x=1", total_timeout=0.3, interrupt_grace=0.3)

        assert out and out[0][0] == "error"
        assert "超时" in out[0][1]
        assert FakeKM.interrupted >= 1  # 判死前先尝试 interrupt

    def test_dead_kernel_reported_immediately(self, tmp_path, monkeypatch):
        """kernel 进程已死：立即报错恢复，不进入收消息循环。"""

        class FakeKM:
            def is_alive(self):
                return False

            def interrupt_kernel(self):
                raise AssertionError("不应 interrupt 已死的内核")

            def shutdown_kernel(self):
                pass

        class FakeKC:
            def execute(self, code):
                pass

            def get_iopub_msg(self, timeout=None):
                raise AssertionError("内核已死不应继续收消息")

            def shutdown(self):
                pass

        interp = self._make_interpreter(tmp_path, monkeypatch, FakeKM(), FakeKC())

        out = interp.execute_code_("x=1", total_timeout=60)

        assert out and out[0][0] == "error"
        assert "崩溃" in out[0][1]
