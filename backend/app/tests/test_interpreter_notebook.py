"""代码解释器写入 notebook 的回归测试。"""

import asyncio

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
