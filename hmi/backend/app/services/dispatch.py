"""이벤트 급파 공용 로직 — 로봇 선정 + ANOMALY 미션 + GOTO 발행.

REST(`POST /events/{id}/dispatch`, 운영자 수동)와 비전 자동 급파(`vision_bridge`, 화재
감지 즉시)가 **같은 로직**을 쓰도록 여기 모은다. 두 경로가 각자 미션을 만들면 상태 전이·
선점 규칙이 갈라진다(detection.ingest 와 같은 이유).

경계: 이 함수들은 DB 변경만 한다. **commit/broadcast 는 호출부**가 한다
(REST 는 async 핸들러, 비전은 컨슈머 코루틴 — 둘 다 이벤트 루프에서 publish_async).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import crud, models
from app.bridge import get_bridge
from app.config import settings
from app.enums import EventStatus, EventType, RobotState
from app.logging_config import get_logger
from app.models import utcnow

log = get_logger("dispatch")


def select_robot(db: Session, event: models.Event) -> tuple[str | None, dict]:
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


@dataclass
class DispatchOutcome:
    mission: models.Mission
    preempted_mission_id: str | None


def assign_and_goto(
    db: Session, event: models.Event, robot_id: str, *, preempt: bool = True
) -> DispatchOutcome:
    """robot_id 를 event 에 배정 → ANOMALY 미션 생성 → GOTO(event.x/y) 발행.

    commit/broadcast 는 하지 않는다(호출부). preempt=True 면 순찰(PATROL) 중인 로봇을
    선점하고 재개 컨텍스트를 남긴다. GOTO 좌표는 event.x/event.y(호모그래피로 채운 맵 좌표).
    """
    preempted_mission_id = None
    if preempt:
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
        event_id=event.event_id,
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
        db, event.event_id, EventStatus.ASSIGNED.value, actor="dispatcher", detail=f"{robot_id} 선정"
    )

    get_bridge().publish_command(
        robot_id,
        "GOTO",
        {
            "waypoints": [{"x": event.x or 0.0, "y": event.y or 0.0, "theta": 0.0}],
            "event_id": event.event_id,
        },
    )
    return DispatchOutcome(mission=mission, preempted_mission_id=preempted_mission_id)


def should_auto_dispatch(event_type: str, map_xy: tuple | None, merged: bool) -> bool:
    """비전 자동 급파 트리거 조건 (2026-08-09 정책: 화재 감지 즉시 자동 급파).

    · 화재(FIRE)만 — 연기/누수는 자동 급파 안 함(오탐 시 순찰 낭비 방지).
    · 맵 좌표가 있어야 함 — 호모그래피로 좌표를 못 구하면 GOTO 목표가 없어 급파 무의미.
    · 신규 이벤트만(merged=False) — dedup 병합은 최초 감지 때 이미 급파됐으므로 재급파 안 함.
    · settings.vision_auto_dispatch 로 전체 on/off.
    """
    return (
        getattr(settings, "vision_auto_dispatch", True)
        and event_type == EventType.FIRE.value
        and map_xy is not None
        and not merged
    )
