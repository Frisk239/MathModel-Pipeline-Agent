"""模型能力缓存服务：将思考档位等探测结果落盘，避免重复试探请求。

缓存文件为 backend/data/model_capabilities.json（运行时数据，不进 git），
key 形如 "{api_type}::{base_url}::{model_id}"。
"""

import json
import time
from pathlib import Path
from typing import Optional

CAPABILITY_DB_PATH = Path("data") / "model_capabilities.json"


def _load_db() -> dict:
    if not CAPABILITY_DB_PATH.exists():
        return {}
    try:
        return json.loads(CAPABILITY_DB_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_db(db: dict) -> None:
    CAPABILITY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPABILITY_DB_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def capability_key(api_type: str, base_url: str, model_id: str) -> str:
    return f"{api_type}::{base_url}::{model_id}"


def get_capability(
    api_type: str, base_url: str, model_id: str
) -> Optional[dict]:
    """读取缓存的能力数据，返回 {"supported": [...], "probed_at": ts} 或 None。"""
    return _load_db().get(capability_key(api_type, base_url, model_id))


def save_capability(
    api_type: str, base_url: str, model_id: str, supported: list[str]
) -> None:
    """写入能力数据（读-改-写全量文件，单机单进程场景足够）。"""
    db = _load_db()
    db[capability_key(api_type, base_url, model_id)] = {
        "supported": supported,
        "probed_at": int(time.time()),
    }
    _save_db(db)
