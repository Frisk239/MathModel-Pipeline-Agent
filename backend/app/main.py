"""MathModelAgent 应用入口，配置 FastAPI 应用和中间件。"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import os
from app.routers import modeling_router, ws_router, common_router, files_router
from app.config.setting import settings
from app.utils.log_util import logger
from fastapi.staticfiles import StaticFiles
from app.utils.cli import get_ascii_banner, center_cli_str


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(get_ascii_banner())
    print(center_cli_str("GitHub:https://github.com/jihe520/MathModelAgent"))
    logger.info("Starting MathModelAgent")

    PROJECT_FOLDER = "./project"
    os.makedirs(PROJECT_FOLDER, exist_ok=True)

    # 启动时扫描非终态任务并标记 stale（进程中断的任务状态不再误导查询）
    from app.core.task_state import TaskPhase, TaskStateMachine

    stale = 0
    work_root = os.path.join(PROJECT_FOLDER, "work_dir")
    if os.path.isdir(work_root):
        for name in os.listdir(work_root):
            state_file = os.path.join(work_root, name, "task_state.json")
            if not os.path.isfile(state_file):
                continue
            sm = TaskStateMachine.load(name, os.path.join(work_root, name))
            if sm and sm.phase not in (
                TaskPhase.COMPLETED,
                TaskPhase.FAILED,
                TaskPhase.CANCELLED,
            ):
                try:
                    sm.fail("进程中断（后端重启时任务不在运行，已标记 stale）")
                    sm.transitions[-1]["note"] = "stale: 进程中断"
                    sm.save()
                    stale += 1
                except Exception as e:
                    logger.warning(f"stale 标记失败 {name}: {e}")
    if stale:
        logger.warning(f"启动扫描：{stale} 个非终态任务已标记为 stale")

    yield
    logger.info("Stopping MathModelAgent")


app = FastAPI(
    title="MathModelAgent",
    description="Agents for MathModel",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(modeling_router.router)
app.include_router(ws_router.router)
app.include_router(common_router.router)
app.include_router(files_router.router)


# 跨域 CORS：来源读配置；"*" 与 allow_credentials=True 互斥（浏览器会拒绝
# 带凭证的通配跨域），配置为通配时不带凭证
_cors_raw = settings.CORS_ALLOW_ORIGINS
_cors_origins = [_cors_raw] if isinstance(_cors_raw, str) else list(_cors_raw)
if "*" in _cors_origins:
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],  # 暴露所有响应头
)

# 静态资源根目录；启动前确保存在（干净 checkout 下 import 即挂载，不能假设目录已在）
_static_root = os.path.join("project", "work_dir")
os.makedirs(_static_root, exist_ok=True)
app.mount(
    "/static",  # 这是访问时的前缀
    StaticFiles(directory=_static_root),  # 这是本地文件夹路径
    name="static",
)
