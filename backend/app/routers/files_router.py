"""文件管理路由模块，提供文件下载、列表和目录打开等接口。"""

import os
import subprocess

from fastapi import APIRouter, HTTPException

from app.config.setting import settings
from app.utils.common_utils import (
    ensure_safe_task_id,
    get_current_files,
    get_work_dir,
)

router = APIRouter()


def _require_safe_task_id(task_id: str) -> str:
    """验证并返回安全的任务 ID。

    Args:
        task_id: 待验证的任务 ID。

    Returns:
        验证通过的任务 ID。

    Raises:
        HTTPException: 任务 ID 非法时返回 400。
    """
    try:
        return ensure_safe_task_id(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法任务ID") from exc


def _require_work_dir(task_id: str) -> str:
    """验证任务 ID 并返回其工作目录，不存在时 404。"""
    safe_task_id = _require_safe_task_id(task_id)
    try:
        return get_work_dir(safe_task_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"任务不存在: {task_id}"
        ) from exc


@router.get("/download_url")
async def get_download_url(task_id: str, filename: str):
    safe_task_id = _require_safe_task_id(task_id)
    safe_filename = os.path.basename((filename or "").replace("\\", "/"))
    return {"download_url": f"{settings.SERVER_HOST}/static/{safe_task_id}/{safe_filename}"}


@router.get("/download_all_url")
async def get_download_all_url(task_id: str):
    safe_task_id = _require_safe_task_id(task_id)
    return {"download_url": f"{settings.SERVER_HOST}/static/{safe_task_id}/all.zip"}


@router.get("/files")
async def get_files(task_id: str):
    work_dir = _require_work_dir(task_id)
    files = get_current_files(work_dir, "all")
    file_all = []

    for i in files:
        file_type = i.split(".")[-1]
        file_all.append({"filename": i, "file_type": file_type})

    return file_all


@router.get("/open_folder")
async def open_folder(task_id: str):
    # 打开工作目录
    work_dir = _require_work_dir(task_id)

    # 打开工作目录
    if os.name == "nt":
        subprocess.run(["explorer", work_dir])
    elif os.name == "posix":
        subprocess.run(["open", work_dir])
    else:
        raise HTTPException(status_code=500, detail=f"不支持的操作系统: {os.name}")

    return {"message": "打开工作目录成功", "work_dir": work_dir}
