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


def _assign_anomaly_mission(
    db: Session, event: models.Event, robot_id: str, *, preempt: bool, status: str
) -> tuple[models.Mission, str | None]:
    """급파(GOTO)와 제자리 정지(HOLD)가 공유하는 배정 로직 — 로봇 명령만 빼고 동일.

    순찰 선점(preempt) → ANOMALY 미션 생성 → 로봇/이벤트 배정까지 한다. 실제 로봇
    명령(GOTO/ANOMALY_HOLD)은 호출부가 이 뒤에 발행한다. commit 은 하지 않는다.
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
    robot.status = status
    robot.progress_step = 2

    event.assigned_robot_id = robot_id
    crud.events.set_status(
        db, event.event_id, EventStatus.ASSIGNED.value, actor="dispatcher", detail=f"{robot_id} 선정"
    )
    return mission, preempted_mission_id


def assign_and_goto(
    db: Session, event: models.Event, robot_id: str, *, preempt: bool = True
) -> DispatchOutcome:
    """robot_id 를 event 에 배정 → ANOMALY 미션 생성 → GOTO(event.x/y) 발행.

    commit/broadcast 는 하지 않는다(호출부). preempt=True 면 순찰(PATROL) 중인 로봇을
    선점하고 재개 컨텍스트를 남긴다. GOTO 좌표는 event.x/event.y(호모그래피로 채운 맵 좌표).
    """
    mission, preempted_mission_id = _assign_anomaly_mission(
        db, event, robot_id, preempt=preempt, status=RobotState.DISPATCHING.value
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


def assign_and_hold(
    db: Session, event: models.Event, robot_id: str, *, preempt: bool = True
) -> DispatchOutcome:
    """자체 카메라로 이상을 감지한 robot_id 를 그 자리에 정지(HOLD)시킨다.

    급파(assign_and_goto)와 달리 이동 목표(좌표)가 없다 — 로봇이 이미 이상 지점에
    있기 때문이다. fleet 의 자체 감지 경로(/fleet/anomaly_trigger {"robot": ns})가 그
    로봇의 현재 위치(amcl_pose)를 이상 위치로 써서 제자리 정지로 동작한다.
    commit/broadcast 는 하지 않는다(호출부).
    """
    mission, preempted_mission_id = _assign_anomaly_mission(
        db, event, robot_id, preempt=preempt, status=RobotState.INSPECTING.value
    )
    get_bridge().publish_command(robot_id, "ANOMALY_HOLD", {"event_id": event.event_id})
    return DispatchOutcome(mission=mission, preempted_mission_id=preempted_mission_id)


#: 자동 급파를 발동시키는 이상 유형. 화재(FIRE)는 2026-08-09부터, 냉각수 누수
#: (LEAK)는 화재와 동일 대응으로 추가(2026-08-11). 연기(SMOKE)는 오탐이 잦아 제외.
AUTO_DISPATCH_TYPES = frozenset({EventType.FIRE.value, EventType.LEAK.value})


def should_auto_dispatch(event_type: str, map_xy: tuple | None, merged: bool) -> bool:
    """비전 자동 급파 트리거 조건 (화재·냉각수 누수 감지 시 즉시 자동 급파).

    · AUTO_DISPATCH_TYPES(화재/냉각수 누수)만 — 연기는 자동 급파 안 함(오탐 시 순찰 낭비 방지).
    · 맵 좌표가 있어야 함 — 호모그래피로 좌표를 못 구하면 GOTO 목표가 없어 급파 무의미.
    · 신규 이벤트만(merged=False) — dedup 병합은 최초 감지 때 이미 급파됐으므로 재급파 안 함.
    · settings.vision_auto_dispatch 로 전체 on/off.
    """
    return (
        getattr(settings, "vision_auto_dispatch", True)
        and event_type in AUTO_DISPATCH_TYPES
        and map_xy is not None
        and not merged
    )


def should_auto_hold(
    event_type: str, source: str | None, robot_id: str | None, merged: bool
) -> bool:
    """자체 감지 제자리 정지 트리거 조건 (AMR 자체 카메라가 이상 감지 → 그 로봇 정지).

    급파(should_auto_dispatch)와의 차이:
    · 맵 좌표가 필요 없다 — fleet 이 로봇의 현재 위치를 이상 위치로 쓴다(제자리 정지).
    · 감지 출처가 AMR 자체 카메라여야 한다(source="amr") — 어느 로봇을 세울지 그 로봇
      자신으로 정해지므로 robot_id 가 있어야 한다.
    유형/병합/전체 on-off 규칙은 급파와 동일하다.
    """
    return (
        getattr(settings, "vision_auto_dispatch", True)
        and event_type in AUTO_DISPATCH_TYPES
        and source == "amr"
        and robot_id is not None
        and not merged
    )
