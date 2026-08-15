"""인프로세스 데모 로봇 시뮬레이터 — 실장비/ROS2 없이 로봇을 실제로 '구동'한다.

배경
----
loopback/null 브리지는 명령을 받기만 하고 텔레메트리를 되돌려줄 로봇이 없어서, 순찰을
시작하면 로봇이 UNDOCKING/RUNNING 에서 멈추고 미션이 영원히 안 끝난다(→ 재시작 시
"이미 미션 중" 실패). 이 시뮬레이터는 그 로봇단 절반을 백엔드 프로세스 안에서 흉내낸다:

* RUNNING 미션이 있는 로봇을 노드 좌표를 따라 조금씩 이동시키고(state=PATROLLING),
  매 틱 ROBOT_STATUS 를 브로드캐스트한다(화면에서 마커가 움직인다).
* 노드에 도착하면 다음 노드로 넘기고, 마지막이면 LOOP 는 처음으로 순환 / 단발은 DONE.
* 도킹(DOCKING) 요청을 받으면 미션을 종료하고 로봇을 풀어준다(CHARGING) — 그래야 다시
  순찰을 시작할 수 있다.
* 모든 살아있는 로봇의 last_seen 을 매 틱 갱신해 heartbeat 워치독이 OFFLINE 으로
  내리지 않게 한다(그래서 AMR_HEARTBEAT_TIMEOUT_SEC 를 크게 잡을 필요가 없다).

`AMR_DEMO_SIM=1` 일 때만 main.py 가 이 루프를 백그라운드 태스크로 띄운다.
"""

from __future__ import annotations

import asyncio
import math

from app import crud, models
from app.connection_manager import manager
from app.database import SessionLocal
from app.enums import MissionStatus, RobotState, WsMessageType
from app.logging_config import get_logger

log = get_logger("demo_sim")

TICK_SEC = 0.6  # 시뮬레이션 주기
STEP_M = 0.35  # 한 틱 이동 거리(m)
ARRIVE_M = 0.25  # 이 거리 안이면 노드 도착으로 본다

# 맵 크기 대비 도킹 스테이션 픽셀 비율 — 프론트 dashboard.ts 와 동일.
DOCK_POSITION_RATIO = {
    "AMR-01": (0.15, 0.22),  # 좌상단 안쪽 도킹1
    "AMR-02": (0.90, 0.82),  # 우하단 D2
}

# 시뮬레이터가 능동적으로 다루는 상태 (그 외 IDLE/EMERGENCY_STOP/PATROL_PAUSED 등은 대기)
_MOVING_TRANSIENT = {
    RobotState.UNDOCKING.value,
    RobotState.RESUMING.value,
    RobotState.PATROLLING.value,
}


async def demo_sim_loop() -> None:
    """0.6초마다 로봇 상태를 한 스텝 전진시키고 변경분을 브로드캐스트한다."""
    log.info("데모 시뮬레이터 시작 (TICK=%.1fs, STEP=%.2fm)", TICK_SEC, STEP_M)
    while True:
        try:
            await asyncio.sleep(TICK_SEC)
            for msg_type, payload in _tick():
                await manager.publish_async(msg_type, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 시뮬레이터는 무슨 일이 있어도 살아 있어야 한다
            log.exception("demo_sim 틱 실패 — 계속 진행합니다")


def _tick() -> list[tuple[str, dict]]:
    """한 스텝 진행. (WS 메시지타입, payload) 목록을 반환한다."""
    out: list[tuple[str, dict]] = []
    db = SessionLocal()
    try:
        for robot in crud.robots.list_all(db):
            _advance_robot(db, robot, out)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    finally:
        db.close()
    return out


def _advance_robot(db, robot: models.Robot, out: list[tuple[str, dict]]) -> None:
    from app.models import utcnow

    if robot.status == RobotState.OFFLINE.value:
        return

    # heartbeat keepalive — 살아있는 로봇은 매 틱 갱신(워치독이 OFFLINE 처리 못 하게)
    robot.online = True
    robot.last_seen = utcnow()

    mission = crud.robots.active_mission_for(db, robot.robot_id)

    # 도킹 요청 → 미션 종료 + 로봇 해제 (그래야 재시작 가능)
    if robot.status == RobotState.DOCKING.value:
        active_map = crud.maps.get_active(db)
        ratio = DOCK_POSITION_RATIO.get(robot.robot_id)
        if active_map is not None and ratio is not None:
            robot.x, robot.y = crud.maps.pixel_to_map(
                active_map,
                active_map.width * ratio[0],
                active_map.height * ratio[1],
            )
        robot.status = RobotState.CHARGING.value
        robot.progress_step = 5
        if mission is not None:
            mission.status = MissionStatus.DONE.value
            mission.progress = 1.0
            mission.end_time = utcnow()
            out.append((WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)))
        robot.current_mission_id = None
        out.append((WsMessageType.ROBOT_STATUS.value, crud.robots.to_dict(robot)))
        return

    # 출발/복귀 준비 → 주행 시작
    if robot.status in (RobotState.UNDOCKING.value, RobotState.RESUMING.value):
        robot.status = RobotState.PATROLLING.value
        robot.progress_step = 2

    if robot.status != RobotState.PATROLLING.value or mission is None:
        return

    nodes: list[str] = list(mission.node_order_json or [])
    if not nodes:
        return
    target_id = mission.current_node_id or nodes[0]
    node = db.get(models.Node, target_id)
    if node is None:
        return

    dx, dy = node.x - robot.x, node.y - robot.y
    dist = math.hypot(dx, dy)

    if dist <= ARRIVE_M:
        # 노드 도착 → 다음 노드로
        robot.x, robot.y = node.x, node.y
        robot.current_node_id = target_id
        idx = nodes.index(target_id) if target_id in nodes else 0
        nxt = idx + 1
        if nxt >= len(nodes):
            if mission.loop:
                nxt = 0  # 순환
            else:
                mission.status = MissionStatus.DONE.value
                mission.progress = 1.0
                mission.end_time = utcnow()
                robot.current_mission_id = None
                robot.status = RobotState.DOCKING.value  # 완료 후 도킹으로
                out.append((WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)))
                out.append((WsMessageType.ROBOT_STATUS.value, crud.robots.to_dict(robot)))
                return
        mission.current_node_id = nodes[nxt]
        mission.next_node_id = nodes[(nxt + 1) % len(nodes)] if len(nodes) > 1 else None
        mission.progress = round((nxt % len(nodes)) / len(nodes), 3)
        out.append((WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)))
    else:
        step = min(STEP_M, dist)
        robot.x += dx / dist * step
        robot.y += dy / dist * step
        robot.theta = math.atan2(dy, dx)

    out.append((WsMessageType.ROBOT_STATUS.value, crud.robots.to_dict(robot)))
