"""§7 순찰 우선순위 · 통계."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.connection_manager import manager
from app.database import get_db
from app.enums import WsMessageType
from app.responses import ok
from app.schemas import PriorityWeightIn, RecalculateIn
from app.security import ActorDep, Permission, require
from app.services import priority as priority_service

DbDep = Annotated[Session, Depends(get_db)]

priority_router = APIRouter(prefix="/priority", tags=["순찰 우선순위"])
stats_router = APIRouter(prefix="/stats", tags=["통계"])

MasterEdit = Depends(require(Permission.MASTER_EDIT))


@priority_router.get("/nodes", summary="노드별 우선순위 점수 랭킹")
def priority_nodes(db: DbDep, window: Annotated[str, Query()] = "7d"):
    return ok(priority_service.compute(db, window))


@priority_router.put("/nodes/{node_id}", summary="수동 가중치 설정", dependencies=[MasterEdit])
async def set_manual_weight(node_id: str, body: PriorityWeightIn, db: DbDep, actor: ActorDep):
    node = crud.patrol.get_node(db, node_id)
    before = node.priority_score
    node.manual_weight = body.manual_weight
    db.flush()

    # 가중치를 바꿨으면 즉시 재계산해야 화면 값과 실제 순찰 순서가 어긋나지 않는다.
    results = priority_service.recalculate(db, operator=body.operator or actor.name)
    after = next((r["score"] for r in results if r["node_id"] == node_id), node.priority_score)
    crud.system.log_priority_change(
        db,
        node_id=node_id,
        before=before,
        after=after,
        reason=body.memo or "수동 가중치 조정",
        operator=body.operator or actor.name,
    )
    db.commit()
    await manager.publish_async(
        WsMessageType.PRIORITY_UPDATED.value,
        [{"node_id": r["node_id"], "score": r["score"], "rank": r["rank"],
          "visit_multiplier": r["visit_multiplier"]} for r in results],
    )
    return ok({"node_id": node_id, "before_score": before, "after_score": after})


@priority_router.post("/recalculate", summary="우선순위 전체 재계산", dependencies=[MasterEdit])
async def recalculate(body: RecalculateIn, db: DbDep, actor: ActorDep):
    results = priority_service.recalculate(db, body.window, operator=actor.name)
    db.commit()
    payload = [
        {"node_id": r["node_id"], "score": r["score"], "rank": r["rank"],
         "visit_multiplier": r["visit_multiplier"]}
        for r in results
    ]
    await manager.publish_async(WsMessageType.PRIORITY_UPDATED.value, payload)
    return ok({"updated": len(results), "top": payload[:5]})


@priority_router.get("/log", summary="우선순위 변경 이력")
def priority_log(
    db: DbDep,
    node_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    rows = crud.system.list_priority_log(db, node_id=node_id, limit=limit)
    return ok(
        [
            {
                "log_id": r.log_id,
                "node_id": r.node_id,
                "before_score": r.before_score,
                "after_score": r.after_score,
                "reason": r.reason,
                "operator": r.operator,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    )


@stats_router.get("/events", summary="이벤트 통계")
def event_stats(
    db: DbDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    group_by: Annotated[str, Query(pattern="^(zone|type|day)$")] = "zone",
):
    summary = crud.events.response_stats(db, from_=from_, to=to)
    summary["groups"] = crud.events.count_by(db, group_by, from_=from_, to=to)
    summary["group_by"] = group_by
    return ok(summary)


@stats_router.get("/overview", summary="대시보드 상단 통계 카드")
def overview(db: DbDep):
    """이미지 우측 '이상 감지 현황' 카드용 — 오늘 누적 집계."""
    from datetime import time, timezone as tz

    today = datetime.now(tz.utc).date()
    start = datetime.combine(today, time.min, tzinfo=tz.utc)
    rows, total = crud.events.query(db, from_=start, page=1, size=500)

    def count(*types: str) -> int:
        return sum(1 for e in rows if e.type in types)

    unresolved = sum(1 for e in rows if e.status not in crud.events.CLOSED_STATUSES)
    return ok(
        {
            "date": today.isoformat(),
            "total_today": total,
            "fire_smoke": count("FIRE", "SMOKE"),
            "leak": count("LEAK"),
            "breaker_mismatch": count("BREAKER_ABNORMAL", "ALIGN_MISMATCH"),
            "unresolved": unresolved,
        }
    )
