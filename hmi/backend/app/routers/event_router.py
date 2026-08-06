"""§5 이상 이벤트 — 탐지 수신 · dedup · 급파 · 검증 · 종결 · 이력."""

from __future__ import annotations

import base64
import csv
import io
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import crud
from app.bridge import get_bridge
from app.config import settings
from app.connection_manager import manager
from app.database import get_db
from app.enums import EventStatus, EventVerdict, RobotState, Severity, WsMessageType
from app.errors import ApiError, E
from app.models import utcnow
from app.responses import ok, paginated
from app.schemas import (
    AckIn,
    DetectIn,
    DispatchIn,
    MediaOut,
    ObservationIn,
    ObservationOut,
    ReinspectIn,
    ResolveIn,
    ReviewIn,
    TimelineOut,
    VerdictIn,
)
from app.security import ActorDep, Permission, require

router = APIRouter(prefix="/events", tags=["이상 이벤트"])
DbDep = Annotated[Session, Depends(get_db)]

EventHandle = Depends(require(Permission.EVENT_HANDLE))
EventReview = Depends(require(Permission.EVENT_REVIEW))


def _save_b64_image(event_id: str, image_b64: str, angle_idx: int | None = None) -> str:
    """base64 이미지를 media_root 에 파일로 떨구고 경로만 돌려준다 (B-44).

    DB 에 base64 를 그대로 넣으면 이벤트 테이블이 순식간에 GB 단위가 되고
    목록 조회까지 느려진다. 파일은 파일시스템에, DB 에는 경로만.
    """
    target_dir = Path(settings.media_root) / "events"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{angle_idx}" if angle_idx is not None else ""
    filename = f"{event_id}{suffix}-{uuid.uuid4().hex[:6]}.jpg"
    payload = image_b64.split(",", 1)[-1]  # data URI 접두어 제거
    (target_dir / filename).write_bytes(base64.b64decode(payload))
    return f"/media/events/{filename}"


@router.post("/detect", summary="탐지 결과 수신 (CCTV/AMR → 백엔드)", status_code=201)
async def detect(body: DetectIn, db: DbDep):
    """dedup 창 안의 같은 zone+type 이면 기존 이벤트에 병합한다 (B-31, B-32)."""
    existing = crud.events.find_dedup_target(db, zone_id=body.zone_id, type_=body.type.value)
    if existing is not None:
        merged = crud.events.merge_into(db, existing, body.confidence)
        crud.events.add_timeline(
            db, merged.event_id, stage="MERGED", actor=body.camera_id or body.robot_id,
            detail=f"중복 탐지 병합 (hit={merged.hit_count})",
        )
        db.commit()
        await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(merged))
        return ok(
            {
                "event_id": merged.event_id,
                "status": EventStatus.MERGED.value,
                "merged_into": merged.event_id,
                "severity": merged.severity,
                "hit_count": merged.hit_count,
            },
            status_code=201,
        )

    event = crud.events.create(
        db,
        source=body.source,
        type_=body.type.value,
        confidence=body.confidence,
        camera_id=body.camera_id,
        robot_id=body.robot_id,
        zone_id=body.zone_id,
        node_id=body.node_id,
        x=body.x,
        y=body.y,
        bbox_json=body.bbox,
        detected_at=body.detected_at,
    )
    if body.image_b64:
        uri = _save_b64_image(event.event_id, body.image_b64)
        event.thumbnail_url = uri
        crud.events.add_media(db, event.event_id, uri=uri)
    elif body.image_uri:
        event.thumbnail_url = body.image_uri
        crud.events.add_media(db, event.event_id, uri=body.image_uri)

    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    return ok(
        {
            "event_id": event.event_id,
            "status": event.status,
            "merged_into": None,
            "severity": event.severity,
            "target_node_id": event.node_id,
        },
        status_code=201,
    )


def _select_robot(db: Session, event) -> tuple[str | None, dict]:
    """급파 대상 선정 (B-34).

    점수 = 거리(가까울수록↑) 0.6 + 배터리 0.4. 오프라인·긴급정지·충전 중인 로봇은 후보에서 제외.
    실제 주행거리 대신 직선거리를 쓴다 — 경로 탐색까지 하려면 Nav2 가 필요하다.
    """
    candidates = []
    for robot in crud.robots.list_all(db):
        if not robot.online or robot.status in (
            RobotState.OFFLINE.value,
            RobotState.EMERGENCY_STOP.value,
            RobotState.ERROR.value,
            RobotState.CHARGING.value,
        ):
            continue
        if robot.battery < settings.battery_low_threshold:
            continue
        dx = (event.x or 0.0) - robot.x
        dy = (event.y or 0.0) - robot.y
        distance = (dx * dx + dy * dy) ** 0.5
        score = 0.6 * (1.0 / (1.0 + distance)) + 0.4 * (robot.battery / 100.0)
        candidates.append((score, distance, robot))

    if not candidates:
        return None, {}
    score, distance, robot = max(candidates, key=lambda c: c[0])
    return robot.robot_id, {
        "distance_m": round(distance, 2),
        "battery": robot.battery,
        "score": round(score, 2),
    }


@router.post("/{event_id}/dispatch", summary="AMR 급파 (순찰 선점)", dependencies=[EventHandle])
async def dispatch(event_id: str, body: DispatchIn, db: DbDep, actor: ActorDep):
    event = crud.events.get(db, event_id)
    if event.status in crud.events.CLOSED_STATUSES:
        raise ApiError(E.EVENT_ALREADY_CLOSED, f"{event.status} 상태의 이벤트는 급파할 수 없습니다")

    if body.robot_id:
        crud.robots.get(db, body.robot_id)
        robot_id, selection = body.robot_id, {}
    else:
        robot_id, selection = _select_robot(db, event)

    if robot_id is None:
        # 가용 로봇 없음 (B-38) — UNASSIGNED 로 남겨 재시도 대상이 되게 한다
        crud.events.set_status(db, event_id, EventStatus.UNASSIGNED.value, actor="dispatcher")
        db.commit()
        raise ApiError(E.NO_AVAILABLE_ROBOT, data={"event_id": event_id})

    preempted_mission_id = None
    if body.preempt:
        running = crud.robots.active_mission_for(db, robot_id)
        if running is not None and running.mission_type == "PATROL":
            order = list(running.node_order_json or [])
            index = order.index(running.current_node_id) if running.current_node_id in order else 0
            running.resume_context_json = {
                "remaining_nodes": order[index:],
                "current_index": index,
                "reason": "ANOMALY_PREEMPT",
            }
            running.status = "PREEMPTED"
            preempted_mission_id = running.mission_id

    mission = crud.robots.create_mission(
        db,
        mission_id=crud.ids.next_mission_id(),
        mission_type="ANOMALY",
        robot_id=robot_id,
        event_id=event_id,
        status="RUNNING",
        node_order_json=[event.node_id] if event.node_id else [],
        current_node_id=event.node_id,
        start_time=utcnow(),
    )
    robot = crud.robots.get(db, robot_id)
    robot.current_mission_id = mission.mission_id
    robot.status = RobotState.DISPATCHING.value
    robot.progress_step = 2

    event.assigned_robot_id = robot_id
    crud.events.set_status(
        db, event_id, EventStatus.ASSIGNED.value, actor="dispatcher", detail=f"{robot_id} 선정"
    )

    get_bridge().publish_command(
        robot_id,
        "GOTO",
        {"waypoints": [{"x": event.x or 0.0, "y": event.y or 0.0, "theta": 0.0}], "event_id": event_id},
    )
    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    await manager.publish_async(
        WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)
    )
    return ok(
        {
            "event_id": event_id,
            "status": EventStatus.ASSIGNED.value,
            "assigned_robot_id": robot_id,
            "eta_sec": None,
            "preempted_mission_id": preempted_mission_id,
            "selection": selection,
        }
    )


@router.post("/{event_id}/observation", summary="다각도 관측 결과 업로드", dependencies=[EventHandle])
async def add_observation(event_id: str, body: ObservationIn, db: DbDep):
    event = crud.events.get(db, event_id)
    image_uri = body.image_uri
    if body.image_b64:
        image_uri = _save_b64_image(event_id, body.image_b64, body.angle_idx)
        crud.events.add_media(db, event_id, uri=image_uri, angle_idx=body.angle_idx)

    obs = crud.events.add_observation(
        db,
        event_id,
        robot_id=body.robot_id,
        equipment_id=body.equipment_id,
        angle_idx=body.angle_idx,
        observed_state=body.observed_state.value,
        value=body.value,
        unit=body.unit,
        confidence=body.confidence,
        image_uri=image_uri,
        sensor_json=body.sensor,
    )
    if event.status not in (EventStatus.VERIFYING.value, *crud.events.CLOSED_STATUSES):
        crud.events.set_status(db, event_id, EventStatus.VERIFYING.value, actor=body.robot_id)
    collected = len(event.observations)
    db.commit()
    return ok(
        {
            "observation_id": obs.obs_id,
            "collected": collected,
            "required": settings.inspect_angle_count,
        }
    )


@router.post("/{event_id}/verdict", summary="검증 판정 확정", dependencies=[EventHandle])
async def verdict(event_id: str, body: VerdictIn, db: DbDep, actor: ActorDep):
    """다수결 판정 (B-42).

    확정 조건 (체크리스트 §판정 규칙): conf ≥ 0.75 인 관측이 2개 이상이고 그 라벨이 같을 것.
    미충족이면 UNVERIFIED — 여기서 임의로 CONFIRMED 를 만들지 않는다.
    """
    event = crud.events.get_detail(db, event_id)
    observations = list(event.observations)

    if body.auto:
        threshold = settings.verdict_confidence_threshold
        strong = [o for o in observations if o.confidence >= threshold]
        buckets: dict[str, list[float]] = {}
        for obs in strong:
            buckets.setdefault(obs.observed_state, []).append(obs.confidence)

        winner, confs = None, []
        for state, values in buckets.items():
            if len(values) >= 2 and len(values) > len(confs):
                winner, confs = state, values

        if winner is None:
            result = EventVerdict.UNVERIFIED.value
            final_conf = max((o.confidence for o in observations), default=0.0)
        else:
            result = (
                EventVerdict.FALSE_POSITIVE.value
                if winner == "NORMAL"
                else EventVerdict.CONFIRMED.value
            )
            final_conf = round(sum(confs) / len(confs), 3)
    else:
        if body.verdict is None:
            raise ApiError(E.VALIDATION, "auto=false 이면 verdict 를 함께 보내야 합니다")
        result = body.verdict
        final_conf = max((o.confidence for o in observations), default=0.0)

    event.verdict = result
    event.final_confidence = final_conf
    status = {
        EventVerdict.CONFIRMED.value: EventStatus.CONFIRMED.value,
        EventVerdict.FALSE_POSITIVE.value: EventStatus.FALSE_POSITIVE.value,
        EventVerdict.UNVERIFIED.value: EventStatus.VERIFYING.value,
    }[result]
    crud.events.set_status(db, event_id, status, actor="align_engine", detail=f"{len(observations)}건 관측")
    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    return ok(
        {
            "event_id": event_id,
            "status": status,
            "verdict": result,
            "final_confidence": final_conf,
            "votes": [
                {"angle_idx": o.angle_idx, "state": o.observed_state, "conf": o.confidence}
                for o in observations
            ],
        }
    )


@router.post("/{event_id}/reinspect", summary="재검증 요청", dependencies=[EventHandle])
def reinspect(event_id: str, body: ReinspectIn, db: DbDep):
    event = crud.events.get(db, event_id)
    if not event.assigned_robot_id:
        raise ApiError(E.CONFLICT, "배정된 로봇이 없어 재검증을 요청할 수 없습니다")
    crud.events.set_status(
        db, event_id, EventStatus.VERIFYING.value, actor="operator", detail=body.reason
    )
    get_bridge().publish_command(
        event.assigned_robot_id,
        "INSPECT",
        {"event_id": event_id, "angle_count": body.extra_angles, "reason": body.reason},
    )
    db.commit()
    return ok({"status": EventStatus.VERIFYING.value, "extra_angles": body.extra_angles})


@router.post("/{event_id}/review", summary="사람 검수", dependencies=[EventReview])
async def review(event_id: str, body: ReviewIn, db: DbDep):
    event = crud.events.get(db, event_id)
    event.reviewer = body.reviewer
    event.memo = body.memo
    event.verdict = body.verdict
    status = (
        EventStatus.FALSE_POSITIVE.value
        if body.verdict == EventVerdict.FALSE_POSITIVE.value
        else EventStatus.CONFIRMED.value
        if body.verdict == EventVerdict.CONFIRMED.value
        else event.status
    )
    crud.events.set_status(db, event_id, status, actor=body.reviewer, detail=body.memo)
    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    return ok({"status": status})


@router.post("/{event_id}/ack", summary="알림 확인", dependencies=[EventHandle])
async def ack(event_id: str, body: AckIn, db: DbDep):
    event = crud.events.get(db, event_id)
    event.acknowledged_at = utcnow()
    event.acknowledged_by = body.operator
    crud.events.add_timeline(db, event_id, stage="ACKNOWLEDGED", actor=body.operator)
    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    return ok({"acknowledged_at": event.acknowledged_at})


@router.post("/{event_id}/resolve", summary="종결", dependencies=[EventHandle])
async def resolve(event_id: str, body: ResolveIn, db: DbDep):
    event = crud.events.get(db, event_id)
    if event.status == EventStatus.RESOLVED.value:
        raise ApiError(E.EVENT_ALREADY_CLOSED, f"이미 종결된 이벤트입니다: {event_id}")
    event.memo = body.memo or event.memo
    crud.events.set_status(
        db, event_id, EventStatus.RESOLVED.value, actor=body.operator, detail=body.memo
    )
    db.commit()
    await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
    return ok({"status": EventStatus.RESOLVED.value})


@router.get("", summary="이력 조회")
def list_events(
    db: DbDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    zone_id: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,  # noqa: A002 - 명세서 파라미터명
    severity: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=500)] = 50,
):
    rows, total = crud.events.query(
        db,
        from_=from_,
        to=to,
        zone_id=zone_id,
        type_=type,
        severity=severity,
        status=status,
        source=source,
        page=page,
        size=size,
    )
    return paginated([crud.events.to_dict(e) for e in rows], total, page, size)


@router.get("/export", summary="이력 내보내기 (CSV)")
def export_events(
    db: DbDep,
    format: Annotated[str, Query()] = "csv",  # noqa: A002 - 명세서 파라미터명
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    zone_id: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,  # noqa: A002
    status: Annotated[str | None, Query()] = None,
):
    if format != "csv":
        raise ApiError(E.VALIDATION, f"지원하지 않는 형식입니다: {format} (csv 만 지원)")
    rows, _ = crud.events.query(
        db, from_=from_, to=to, zone_id=zone_id, type_=type, status=status, page=1, size=10000
    )
    buffer = io.StringIO()
    columns = [
        "event_id", "detected_at", "source", "type", "severity", "status",
        "zone_id", "node_id", "confidence", "hit_count", "assigned_robot_id",
        "verdict", "resolved_at",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for event in rows:
        writer.writerow({k: v for k, v in crud.events.to_dict(event).items() if k in columns})
    buffer.seek(0)
    # Excel 한글 깨짐 방지용 BOM
    content = "﻿" + buffer.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="events.csv"'},
    )


@router.get("/{event_id}", summary="이벤트 상세 (타임라인 포함)")
def get_event(event_id: str, db: DbDep):
    event = crud.events.get_detail(db, event_id)
    return ok(
        {
            "event": crud.events.to_dict(event),
            "timeline": [TimelineOut.model_validate(t).model_dump() for t in event.timeline],
            "media": [MediaOut.model_validate(m).model_dump() for m in event.media],
            "observations": [
                ObservationOut.model_validate(o).model_dump() for o in event.observations
            ],
        }
    )
