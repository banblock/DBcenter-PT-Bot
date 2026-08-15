"""이상 이벤트 CRUD — 등록·dedup·타임라인·조회·통계."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.config import settings
from app.crud import ids
from app.enums import EventStatus, EventType, Severity
from app.errors import E, not_found
from app.models import utcnow

#: 이벤트 타입 → 기본 심각도. 확정 판정 전 1차 라우팅에 쓴다 (B-72).
DEFAULT_SEVERITY: dict[str, str] = {
    EventType.FIRE.value: Severity.CRITICAL.value,
    EventType.SMOKE.value: Severity.CRITICAL.value,
    EventType.LEAK.value: Severity.WARN.value,
    EventType.INTRUSION.value: Severity.WARN.value,
    EventType.PERSON.value: Severity.INFO.value,
    EventType.BREAKER_ABNORMAL.value: Severity.CRITICAL.value,
    EventType.LOCK_ABNORMAL.value: Severity.WARN.value,
    EventType.PANEL_OUT_OF_RANGE.value: Severity.WARN.value,
    EventType.ALIGN_MISMATCH.value: Severity.WARN.value,
}

#: 여기 있는 상태는 '끝난 이벤트'. 재배정·ACK 대상에서 제외된다.
CLOSED_STATUSES = {
    EventStatus.RESOLVED.value,
    EventStatus.FALSE_POSITIVE.value,
    EventStatus.MERGED.value,
}


def default_severity(event_type: str) -> str:
    return DEFAULT_SEVERITY.get(event_type, Severity.WARN.value)


def find_dedup_target(db: Session, *, zone_id: str | None, type_: str) -> models.Event | None:
    """dedup 창(기본 10초) 안의 같은 zone+type 열린 이벤트를 찾는다 (B-32).

    zone_id 가 없으면 위치를 특정할 수 없어 병합하지 않는다 — 서로 다른 곳의
    사건을 하나로 뭉치는 쪽이 놓치는 것보다 위험하다.
    """
    if zone_id is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.dedup_window_sec)
    return (
        db.execute(
            select(models.Event)
            .where(
                models.Event.zone_id == zone_id,
                models.Event.type == type_,
                models.Event.detected_at >= cutoff,
                models.Event.status.notin_(list(CLOSED_STATUSES)),
            )
            .order_by(models.Event.detected_at.desc())
        )
        .scalars()
        .first()
    )


def create(
    db: Session,
    *,
    source: str,
    type_: str,
    confidence: float,
    severity: str | None = None,
    status: str = EventStatus.QUEUED.value,
    **kwargs,
) -> models.Event:
    event = models.Event(
        event_id=ids.next_event_id(db),
        source=source,
        type=type_,
        confidence=confidence,
        severity=severity or default_severity(type_),
        status=status,
        detected_at=kwargs.pop("detected_at", None) or utcnow(),
        **kwargs,
    )
    db.add(event)
    db.flush()
    add_timeline(db, event.event_id, stage="DETECTED", actor=kwargs.get("camera_id") or kwargs.get("robot_id") or source)
    return event


def merge_into(db: Session, target: models.Event, confidence: float) -> models.Event:
    """중복 탐지를 기존 이벤트에 흡수. hit_count 를 올리고 신뢰도는 최대값을 유지한다."""
    target.hit_count += 1
    target.confidence = max(target.confidence, confidence)
    db.flush()
    return target


def get(db: Session, event_id: str) -> models.Event:
    obj = db.get(models.Event, event_id)
    if obj is None:
        raise not_found(E.EVENT_NOT_FOUND, event_id)
    return obj


def get_detail(db: Session, event_id: str) -> models.Event:
    obj = (
        db.execute(
            select(models.Event)
            .options(
                selectinload(models.Event.media),
                selectinload(models.Event.observations),
                selectinload(models.Event.timeline),
            )
            .where(models.Event.event_id == event_id)
        )
        .scalars()
        .first()
    )
    if obj is None:
        raise not_found(E.EVENT_NOT_FOUND, event_id)
    return obj


def add_timeline(
    db: Session, event_id: str, *, stage: str, actor: str | None = None, detail: str | None = None
) -> models.EventTimeline:
    row = models.EventTimeline(event_id=event_id, stage=stage, actor=actor, detail=detail)
    db.add(row)
    db.flush()
    return row


def add_media(
    db: Session, event_id: str, *, uri: str, kind: str = "IMAGE", angle_idx: int | None = None
) -> models.EventMedia:
    row = models.EventMedia(event_id=event_id, uri=uri, kind=kind, angle_idx=angle_idx)
    db.add(row)
    db.flush()
    return row


def add_observation(db: Session, event_id: str, **kwargs) -> models.Observation:
    row = models.Observation(event_id=event_id, **kwargs)
    db.add(row)
    db.flush()
    return row


def set_status(
    db: Session,
    event_id: str,
    status: str,
    *,
    actor: str | None = None,
    detail: str | None = None,
) -> models.Event:
    """상태 전이 + 타임라인 1줄. 시각 컬럼도 함께 찍는다."""
    event = get(db, event_id)
    event.status = EventStatus(status).value
    now = utcnow()
    if status == EventStatus.ASSIGNED.value:
        event.assigned_at = now
    elif status == EventStatus.CONFIRMED.value:
        event.confirmed_at = now
    elif status in (EventStatus.RESOLVED.value, EventStatus.FALSE_POSITIVE.value):
        event.resolved_at = now
    db.flush()
    add_timeline(db, event_id, stage=status, actor=actor, detail=detail)
    return event


def to_dict(e: models.Event) -> dict:
    return {
        "event_id": e.event_id,
        "source": e.source,
        "camera_id": e.camera_id,
        "robot_id": e.robot_id,
        "type": e.type,
        "severity": e.severity,
        "status": e.status,
        "zone_id": e.zone_id,
        "node_id": e.node_id,
        "x": e.x,
        "y": e.y,
        "confidence": e.confidence,
        "final_confidence": e.final_confidence,
        "hit_count": e.hit_count,
        "merged_into": e.merged_into,
        "thumbnail_url": e.thumbnail_url,
        "assigned_robot_id": e.assigned_robot_id,
        "verdict": e.verdict,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        "acknowledged_at": e.acknowledged_at.isoformat() if e.acknowledged_at else None,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    }


def query(
    db: Session,
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    zone_id: str | None = None,
    type_: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    source: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[models.Event], int]:
    """필터 + 페이지네이션. 정렬은 항상 최신순(detected_at DESC)."""
    stmt = select(models.Event)
    conditions = []
    if from_:
        conditions.append(models.Event.detected_at >= from_)
    if to:
        conditions.append(models.Event.detected_at <= to)
    if zone_id:
        conditions.append(models.Event.zone_id == zone_id)
    if type_:
        conditions.append(models.Event.type == type_)
    if severity:
        conditions.append(models.Event.severity == severity)
    if status:
        conditions.append(models.Event.status == status)
    if source:
        conditions.append(models.Event.source == source)
    if conditions:
        stmt = stmt.where(*conditions)

    total = db.execute(
        select(func.count()).select_from(models.Event).where(*conditions)
        if conditions
        else select(func.count()).select_from(models.Event)
    ).scalar_one()

    rows = list(
        db.execute(
            stmt.order_by(models.Event.detected_at.desc()).offset((page - 1) * size).limit(size)
        ).scalars()
    )
    return rows, total


def count_by(
    db: Session, group_by: str, *, from_: datetime | None = None, to: datetime | None = None
) -> list[dict]:
    """구역/타입/일자별 집계 (7-5)."""
    column = {
        "zone": models.Event.zone_id,
        "type": models.Event.type,
        "day": func.date(models.Event.detected_at),
    }.get(group_by, models.Event.zone_id)

    conditions = []
    if from_:
        conditions.append(models.Event.detected_at >= from_)
    if to:
        conditions.append(models.Event.detected_at <= to)

    stmt = select(column, func.count()).group_by(column).order_by(func.count().desc())
    if conditions:
        stmt = stmt.where(*conditions)
    return [{"key": key, "count": count} for key, count in db.execute(stmt).all()]


def response_stats(
    db: Session, *, from_: datetime | None = None, to: datetime | None = None
) -> dict:
    """평균 대응시간 · 오탐률 (F-70, F-71).

    NULL 시각(도착/확정 전 이벤트)은 평균에서 제외한다 — 0 으로 채우면
    아직 진행 중인 건이 평균을 끌어내려 지표가 거짓으로 좋아 보인다.
    """
    conditions = []
    if from_:
        conditions.append(models.Event.detected_at >= from_)
    if to:
        conditions.append(models.Event.detected_at <= to)

    def _scalar(stmt):
        return db.execute(stmt.where(*conditions) if conditions else stmt).scalar() or 0

    total = _scalar(select(func.count()).select_from(models.Event))
    false_positive = _scalar(
        select(func.count())
        .select_from(models.Event)
        .where(models.Event.status == EventStatus.FALSE_POSITIVE.value)
    )

    def _avg_seconds(end_col) -> float:
        rows = db.execute(
            select(models.Event.detected_at, end_col).where(
                end_col.is_not(None), *conditions
            )
        ).all()
        if not rows:
            return 0.0
        deltas = [(end - start).total_seconds() for start, end in rows if end and start]
        return round(sum(deltas) / len(deltas), 1) if deltas else 0.0

    return {
        "total": total,
        "false_positive_rate": round(false_positive / total, 3) if total else 0.0,
        "avg_response_sec": {
            "detect_to_arrive": _avg_seconds(models.Event.arrived_at),
            "detect_to_confirm": _avg_seconds(models.Event.confirmed_at),
        },
    }


def count_since(db: Session, node_id: str, since: datetime) -> int:
    return db.execute(
        select(func.count())
        .select_from(models.Event)
        .where(models.Event.node_id == node_id, models.Event.detected_at >= since)
    ).scalar_one()
