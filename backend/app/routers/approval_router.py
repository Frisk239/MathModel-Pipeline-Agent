"""人工审批路由：检查点挂起材料查询与三分支决策提交。"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import approval as approval_service

router = APIRouter()


class ApprovalDecisionRequest(BaseModel):
    action: str  # approve / revise / reject
    feedback: str = ""


@router.get("/approval/{task_id}")
async def get_pending_approval(task_id: str):
    """获取当前挂起的审批材料（前端弹窗消费）。"""
    p = approval_service.get_pending(task_id)
    if p is None:
        return {"pending": False}
    return {
        "pending": True,
        "checkpoint": p.checkpoint,
        "payload": p.payload,
    }


@router.post("/approval/{task_id}")
async def submit_approval(task_id: str, request: ApprovalDecisionRequest):
    """提交审批决策（三分支：批准/带意见返工/否决）。"""
    ok = approval_service.submit_decision(task_id, request.action, request.feedback)
    if not ok:
        return {"success": False, "message": "该任务当前无挂起的审批"}
    return {"success": True, "message": f"决策已提交: {request.action}"}
