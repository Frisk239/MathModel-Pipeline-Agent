"""API 层 smoke 测试（TestClient）。

只验证路由层的安全边界与基本可用性：非法 task_id 被拒、越界/超限
上传被拒、/config 可取。不碰真实 LLM 与 Redis（后台任务与 redis_manager
均以桩替换），合法上传用例的落盘目录重定向到 tmp_path。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_config_endpoint():
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "environment" in body
    assert "hil" in body


@pytest.mark.parametrize(
    "path",
    ["/files", "/open_folder", "/download_url", "/download_all_url"],
)
def test_task_id_traversal_rejected(path):
    """含路径成分的 task_id 一律 400，不触达文件系统/子进程。"""
    resp = client.get(path, params={"task_id": "../../etc", "filename": "a.md"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "非法任务ID"


def test_files_unknown_task_returns_404():
    resp = client.get("/files", params={"task_id": "20990101-00000000"})
    assert resp.status_code == 404


def _post_modeling(files):
    return client.post(
        "/modeling",
        data={
            "ques_all": "一道测试题面",
            "comp_template": "CHINA",
            "format_output": "Markdown",
        },
        files=files,
    )


@pytest.fixture
def isolated_upload_dir(tmp_path, monkeypatch):
    """把工作目录重定向到 tmp_path，避免污染真实 project/work_dir。"""
    monkeypatch.setattr(
        "app.routers.modeling_router.create_work_dir", lambda task_id: str(tmp_path)
    )
    return tmp_path


def test_modeling_rejects_traversal_filename(isolated_upload_dir):
    resp = _post_modeling([("files", ("../evil.csv", b"a,b\n1,2", "text/csv"))])
    assert resp.status_code == 400
    assert "非法文件名" in resp.json()["detail"]
    assert not any(isolated_upload_dir.iterdir())


def test_modeling_rejects_windows_traversal_filename(isolated_upload_dir):
    resp = _post_modeling(
        [("files", ("..\\..\\evil.csv", b"a,b\n1,2", "text/csv"))]
    )
    assert resp.status_code == 400


def test_modeling_rejects_disallowed_ext(isolated_upload_dir):
    resp = _post_modeling(
        [("files", ("evil.exe", b"MZ", "application/octet-stream"))]
    )
    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["detail"]


def test_modeling_rejects_oversize(isolated_upload_dir, monkeypatch):
    monkeypatch.setattr("app.routers.modeling_router.UPLOAD_MAX_BYTES", 4)
    resp = _post_modeling([("files", ("big.csv", b"a" * 16, "text/csv"))])
    assert resp.status_code == 400
    assert "大小上限" in resp.json()["detail"]


def test_modeling_accepts_whitelisted_upload(isolated_upload_dir, monkeypatch):
    class _FakeRedis:
        async def set(self, *args, **kwargs):
            return None

    async def _noop_task(*args, **kwargs):
        return None

    monkeypatch.setattr("app.routers.modeling_router.redis_manager", _FakeRedis())
    monkeypatch.setattr(
        "app.routers.modeling_router.run_modeling_task_async", _noop_task
    )

    resp = _post_modeling([("files", ("data.csv", b"a,b\n1,2", "text/csv"))])
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    assert (isolated_upload_dir / "data.csv").read_bytes() == b"a,b\n1,2"
