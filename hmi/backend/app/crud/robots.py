"""로봇 상태 · 미션 CRUD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.enums import ROBOT_STATE_KO, MissionStatus, RobotState
from app.errors import E, not_found
from app.models import utcnow


# ══════════════════════════════════════════════════════════════════════════
# 로봇
# ══════════════════════════════════════════════════════════════════════════
def ensure(db: Session, robot_id: str, name: str | None = None) -> models.Robot:
    """설정에 있는 로봇을 DB 에 보장한다. 기동 시 시드 대신 쓰인다."""
    obj = db.get(models.Robot, robot_id)
    if obj is None:
        obj = models.Robot(robot_id=robot_id, name=name or robot_id)
        db.add(obj)
        db.flush()
    return obj


def get(db: Session, robot_id: str) -> models.Robot:
    obj = db.get(models.Robot, robot_id)
    if obj is None:
        raise not_found(E.ROBOT_NOT_FOUND, robot_id)
    return obj


def list_all(db: Session) -> list[models.Robot]:
    return list(db.execute(select(models.Robot).order_by(models.Robot.robot_id)).scalars())


def to_dict(r: models.Robot) -> dict:
    """WS/REST 공용 직렬화. 프론트 `Robot` 타입과 필드명이 일치해야 한다."""
    return {
        "robot_id": r.robot_id,
        "name": r.name,
        "state": r.status,
        "state_ko": ROBOT_STATE_KO.get(r.status, r.status),
        "progress_step": r.progress_step,
        "battery": r.battery,
        "charging": r.charging,
        "pose": {"x": r.x, "y": r.y, "theta": r.theta},
        "mission_id": r.current_mission_id,
        "current_node_id": r.current_node_id,
        "online": r.online,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
    }


def apply_telemetry(db: Session, robot_id: str, **fields) -> models.Robot:
    """ROS 브리지가 올려주는 텔레메트리 반영. 항상 last_seen/online 을 갱신한다."""
    obj = ensure(db, robot_id)
    for key, value in fields.items():
        if value is not None and hasattr(obj, key):
            setattr(obj, key, value)
    obj.last_seen = utcnow()
    obj.online = True
    db.flush()
    return obj


def set_state(db: Session, robot_id: str, state: str) -> models.Robot:
    obj = get(db, robot_id)
    obj.status = RobotState(state).value
    db.flush()
    return obj


def mark_offline_stale(db: Session) -> list[models.Robot]:
    """heartbeat 타임아웃(기본 3초)을 넘긴 로봇을 OFFLINE 으로 내린다 (B-08).

    이미 OFFLINE 인 로봇은 건드리지 않는다 — 매 틱마다 상태 전이 브로드캐스트가
    쏟아지는 걸 막기 위해, 실제로 바뀐 것만 반환한다.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.heartbeat_timeout_sec)
    changed: list[models.Robot] = []
    for robot in list_all(db):
        if robot.status == RobotState.OFFLINE.value:
            continue
        last = robot.last_seen
        if last is None or (last.tzinfo and last < cutoff) or (not last.tzinfo and last.replace(tzinfo=timezone.utc) < cutoff):
            robot.status = RobotState.OFFLINE.value
            robot.online = False
            changed.append(robot)
    if changed:
        db.flush()
    return changed


# ══════════════════════════════════════════════════════════════════════════
# 미션
# ══════════════════════════════════════════════════════════════════════════
def create_mission(db: Session, *, mission_id: str, **kwargs) -> models.Mission:
    obj = models.Mission(mission_id=mission_id, **kwargs)
    db.add(obj)
    db.flush()
    return obj


def get_mission(db: Session, mission_id: str) -> models.Mission:
    obj = db.get(models.Mission, mission_id)
    if obj is None:
        raise not_found(E.MISSION_NOT_FOUND, mission_id)
    return obj


def list_missions(
    db: Session,
    *,
    status: str | None = None,
    robot_id: str | None = None,
    limit: int = 50,
) -> list[models.Mission]:
    stmt = select(models.Mission)
    if status:
        stmt = stmt.where(models.Mission.status == status)
    if robot_id:
        stmt = stmt.where(models.Mission.robot_id == robot_id)
    stmt = stmt.order_by(
        models.Mission.queue_order.asc(), models.Mission.created_at.desc()
    ).limit(limit)
    return list(db.execute(stmt).scalars())


def active_mission_for(db: Session, robot_id: str) -> models.Mission | None:
    """이 로봇이 지금 물고 있는 미션 (RUNNING 또는 PENDING)."""
    return (
        db.execute(
            select(models.Mission)
            .where(
                models.Mission.robot_id == robot_id,
                models.Mission.status.in_(
                    [MissionStatus.RUNNING.value, MissionStatus.PENDING.value]
                ),
            )
            .order_by(models.Mission.created_at.desc())
        )
        .scalars()
        .first()
    )


def mission_to_dict(db: Session, m: models.Mission) -> dict:
    route = db.get(models.Route, m.route_id) if m.route_id else None
    return {
        "mission_id": m.mission_id,
        "mission_type": m.mission_type,
        "robot_id": m.robot_id,
        "route_id": m.route_id,
        "route_name": route.name if route else None,
        "status": m.status,
        "progress": m.progress,
        "current_node_id": m.current_node_id,
        "next_node_id": m.next_node_id,
        "start_time": m.start_time.isoformat() if m.start_time else None,
        "end_time": m.end_time.isoformat() if m.end_time else None,
    }


def compute_progress(done_count: int, total: int) -> float:
    """미션 진행률 (B-30). 노드가 0개면 0.0 — ZeroDivision 방지."""
    if total <= 0:
        return 0.0
    return round(min(max(done_count / total, 0.0), 1.0), 4)


def reorder_queue(db: Session, order: list[str]) -> int:
    """작업 큐 수동 재정렬 (3-6). 목록에 없는 미션은 뒤로 밀린다."""
    applied = 0
    for index, mission_id in enumerate(order):
        mission = db.get(models.Mission, mission_id)
        if mission is not None:
            mission.queue_order = index
            applied += 1
    db.flush()
    return applied


def log_error(
    db: Session, *, robot_id: str | None, error_code: str, error_msg: str, mission_id: str | None = None
) -> models.ErrorLog:
    obj = models.ErrorLog(
        robot_id=robot_id, mission_id=mission_id, error_code=error_code, error_msg=error_msg
    )
    db.add(obj)
    db.flush()
    return obj
