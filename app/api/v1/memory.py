"""申请人记忆治理路由（T138，D055-3/D058）。

- DELETE /api/v1/memory/{user_id}/entries/{case_id}   坐席删除一条核赔档案（审计留痕）

删除语义（D058）：删除即从档案视图消失；scripts/rebuild_memories.py 是显式人工
安全网，重跑会按 cases 表终态重建（非 tombstone 永久删除）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from services.case_store import get_default_recorder
from services.memory.case_memory import delete_case_memory

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class MemoryDeleteRequest(BaseModel):
    """坐席删除操作（审计留痕用）。"""

    agent: str = Field(default="agent", max_length=64)


class MemoryDeleteResponse(BaseModel):
    deleted: bool
    user_id: str
    case_id: str


@router.delete("/{user_id}/entries/{case_id}", response_model=MemoryDeleteResponse)
async def delete_applicant_memory(
    user_id: str, case_id: str, body: MemoryDeleteRequest | None = None
) -> MemoryDeleteResponse:
    """删除一条申请人核赔档案：Store 条目删除 + CaseEvent human 审计留痕（fail-open）。"""
    agent = (body.agent if body is not None else "") or "agent"
    deleted = await delete_case_memory(user_id, case_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="档案条目不存在（或已删除）")
    await get_default_recorder().event(
        case_id,
        "human",
        payload={"action": "memory_deleted", "user_id": user_id, "operator": agent},
    )
    return MemoryDeleteResponse(deleted=True, user_id=user_id, case_id=case_id)
