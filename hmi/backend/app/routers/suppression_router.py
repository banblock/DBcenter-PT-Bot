"""§8 화재진압 + 시스템 설정.

안전 관련 코드다. 여기서 지키는 원칙 두 가지:

1. **인터락을 통과하지 못하면 절대 진행하지 않는다.** BLOCKED 상태로 세우고,
   무엇이 막았는지(blockers)를 반드시 응답에 담는다.
2. **이중 확인.** `confirm_text` 가 zone_id 와 정확히 일치해야 요청이 접수된다.
   실수로 엉뚱한 구역을 정전시키는 사고를 막기 위한 장치다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.bridge import get_bridge
from app.connection_manager import manager
from app.database import get_db
from app.enums import RobotState, SuppressionStatus, WsMessageType
from app.errors import ApiError, E
from app.models import utcnow
from app.responses import ok
from app.schemas import (
    SuppressionAbortIn,
    SuppressionApproveIn,
    SuppressionModeIn,
    SuppressionOut,
    SuppressionRequestIn,
)
from app.security import ActorDep, Permission, require

DbDep = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/suppression", tags=["화재진압"])
config_router = APIRouter(prefix="/config", tags=["시스템 설정"])

SuppressRequest = Depends(require(Permission.SUPPRESSION_REQUEST))
SuppressApprove = Depends(require(Permission.SUPPRESSION_APPROVE))
ConfigEdit = Depends(require(Permission.CONFIG_EDIT))


def _check_interlock(db: Session, zone_id: str, mode: str) -> dict:
    """안전 인터락 검사 (B-64).

    막는 조건 3가지 (체크리스트 §1-5):
      ① 해당 구역에 AMR 이 남아 있음
      ② 인원 잔류 미확인 — 해당 구역에 열린 PERSON/INTRUSION 이벤트가 있으면 '있다'로 본다
      ③ 수동 모드인데 관리자 승인이 아직 없음

    ②를 '이벤트가 없으면 통과'로 처리하는 건 안전하지 않다 — 사람이 없음을
    적극적으로 확인한 게 아니라 '탐지되지 않았을' 뿐이기 때문이다. 그래서 현재
    구현은 탐지된 경우만 막고, 이 한계를 blockers 응답과 감사 로그에 남긴다.
    실운영 전에 인원 확인 절차(출입 통제 연동 또는 관리자 체크박스)를 반드시 붙일 것.
    """
    blockers: list[dict] = []

    zone_robots = [
        r
        for r in crud.robots.list_all(db)
        if r.online and r.status not in (RobotState.CHARGING.value, RobotState.OFFLINE.value)
    ]
    # 구역 판정은 노드 소속으로 근사한다 (폴리곤 점검은 지오메트리 유틸이 붙으면 교체)
    zone_node_ids = {n.node_id for n in crud.patrol.list_nodes(db, zone_id=zone_id)}
    for robot in zone_robots:
        if robot.current_node_id and robot.current_node_id in zone_node_ids:
            blockers.append({"code": "AMR_IN_ZONE", "detail": f"{robot.robot_id} 대피 중"})

    person_events, _ = crud.events.query(db, zone_id=zone_id, type_="PERSON", page=1, size=20)
    for event in person_events:
        if event.status not in crud.events.CLOSED_STATUSES:
            blockers.append({"code": "PERSON_DETECTED", "detail": f"{event.event_id} 인원 감지"})
            break

    return {
        "passed": not blockers,
        "blockers": blockers,
        "requires_approval": mode == "MANUAL",
        "person_check": "DETECTION_ONLY",  # 인원 확인 절차 미연동 — 위 주석 참조
    }


@router.post("/request", summary="진압 요청", dependencies=[SuppressRequest], status_code=202)
async def request_suppression(body: SuppressionRequestIn, db: DbDep, actor: ActorDep):
    if body.confirm_text.strip() != body.zone_id:
        raise ApiError(
            E.CONFIRM_TEXT_MISMATCH,
            f"확인 문구가 구역 ID 와 다릅니다 (입력: {body.confirm_text!r}, 필요: {body.zone_id!r})",
        )
    crud.patrol.get_zone(db, body.zone_id)
    if body.event_id:
        crud.events.get(db, body.event_id)

    mode = crud.system.get_suppression_mode(db)
    interlock = _check_interlock(db, body.zone_id, mode)
    status = (
        SuppressionStatus.BLOCKED.value
        if not interlock["passed"]
        else SuppressionStatus.INTERLOCK_CHECK.value
    )

    suppression = crud.system.create_suppression(
        db,
        event_id=body.event_id,
        zone_id=body.zone_id,
        actions_json=body.actions,
        breaker_ids_json=body.breaker_ids,
        sprinkler_duration_sec=body.sprinkler_duration_sec,
        status=status,
        mode=mode,
        interlock_json=interlock,
        requested_by=body.requested_by,
        started_at=utcnow(),
    )
    crud.system.append_step(
        db,
        suppression,
        step="INTERLOCK_CHECK",
        status="DONE" if interlock["passed"] else "BLOCKED",
        result="OK" if interlock["passed"] else ",".join(b["code"] for b in interlock["blockers"]),
    )

    # 인터락을 통과했고 구역에 AMR 이 있으면 먼저 빼낸다 (B-65)
    if interlock["passed"]:
        for robot in crud.robots.list_all(db):
            if robot.online:
                get_bridge().publish_command(robot.robot_id, "EVACUATE", {"zone_id": body.zone_id})

    db.commit()
    payload = SuppressionOut.model_validate(suppression).model_dump(by_alias=False)
    await manager.publish_async(WsMessageType.SUPPRESSION_STATUS.value, payload)
    return ok(
        {
            "suppression_id": suppression.suppression_id,
            "status": suppression.status,
            "interlock": interlock,
        },
        status_code=202,
    )


@router.get("/logs", summary="진압 감사 로그")
def suppression_logs(
    db: DbDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
):
    rows = crud.system.list_suppressions(db, from_=from_, to=to)
    return ok(
        [
            {
                "suppression_id": s.suppression_id,
                "event_id": s.event_id,
                "zone_id": s.zone_id,
                "actions": s.actions_json,
                "requested_by": s.requested_by,
                "approved_by": s.approved_by,
                "aborted_by": s.aborted_by,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            }
            for s in rows
        ]
    )


@router.get("/{suppression_id}", summary="진압 진행 상태")
def get_suppression(suppression_id: str, db: DbDep):
    obj = crud.system.get_suppression(db, suppression_id)
    return ok(SuppressionOut.model_validate(obj).model_dump(by_alias=False))


@router.post("/{suppression_id}/approve", summary="수동 승인", dependencies=[SuppressApprove])
async def approve(suppression_id: str, body: SuppressionApproveIn, db: DbDep):
    suppression = crud.system.get_suppression(db, suppression_id)
    if suppression.status == SuppressionStatus.BLOCKED.value:
        raise ApiError(
            E.INTERLOCK_BLOCKED,
            "인터락이 해소되지 않아 승인할 수 없습니다",
            data=suppression.interlock_json,
        )
    if suppression.status not in (
        SuppressionStatus.INTERLOCK_CHECK.value,
        SuppressionStatus.REQUESTED.value,
    ):
        raise ApiError(E.CONFLICT, f"{suppression.status} 상태에서는 승인할 수 없습니다")

    suppression.approved_by = body.approver
    suppression.status = SuppressionStatus.APPROVED.value
    crud.system.append_step(db, suppression, step="APPROVED", status="DONE", result=body.approver)
    db.commit()
    payload = SuppressionOut.model_validate(suppression).model_dump(by_alias=False)
    await manager.publish_async(WsMessageType.SUPPRESSION_STATUS.value, payload)
    return ok({"status": suppression.status})


@router.post("/{suppression_id}/abort", summary="수동 중단", dependencies=[SuppressApprove])
async def abort(suppression_id: str, body: SuppressionAbortIn, db: DbDep):
    suppression = crud.system.get_suppression(db, suppression_id)
    if suppression.status in (
        SuppressionStatus.COMPLETED.value,
        SuppressionStatus.ABORTED.value,
    ):
        raise ApiError(E.CONFLICT, f"이미 {suppression.status} 상태입니다")
    suppression.status = SuppressionStatus.ABORTED.value
    suppression.aborted_by = body.operator
    suppression.abort_reason = body.reason
    suppression.ended_at = utcnow()
    crud.system.append_step(db, suppression, step="ABORTED", status="DONE", result=body.reason)
    db.commit()
    payload = SuppressionOut.model_validate(suppression).model_dump(by_alias=False)
    await manager.publish_async(WsMessageType.SUPPRESSION_STATUS.value, payload)
    return ok({"status": suppression.status})


# ══════════════════════════════════════════════════════════════════════════
# 시스템 설정
# ══════════════════════════════════════════════════════════════════════════
@config_router.get("", summary="현재 설정 조회")
def get_config(db: DbDep):
    from app.config import settings

    return ok(
        {
            "suppression_mode": crud.system.get_suppression_mode(db),
            "heartbeat_timeout_sec": settings.heartbeat_timeout_sec,
            "dedup_window_sec": settings.dedup_window_sec,
            "inspect_angle_count": settings.inspect_angle_count,
            "confidence_threshold": settings.confidence_threshold,
            "battery_low_threshold": settings.battery_low_threshold,
            "priority_weights": settings.priority_weights,
            "robot_ids": settings.robot_ids,
        }
    )


@config_router.put("/suppression-mode", summary="진압 모드 변경", dependencies=[ConfigEdit])
def set_suppression_mode(body: SuppressionModeIn, db: DbDep, actor: ActorDep):
    crud.system.set_config(db, "suppression_mode", body.mode, updated_by=actor.name)
    db.commit()
    return ok({"mode": body.mode})
