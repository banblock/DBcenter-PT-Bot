"""화재 자동 급파 (단계 D) — dispatch 서비스 + 자동 트리거 조건.

vision_bridge._auto_dispatch 자체는 rclpy/이벤트루프가 필요해 여기서 직접 못 돌리지만,
그것이 호출하는 **순수/서비스 로직**(select_robot, assign_and_goto, should_auto_dispatch)을
여기서 검증한다. REST 급파 엔드포인트도 리팩터 후 이 서비스를 그대로 호출한다(같은 로직).

DB 격리: 함수스코프 `db` 픽스처(teardown 에서 rollback)를 쓰고 flush 만 한다 → 커밋하지
않으므로 로봇 상태/미션 변경이 다른 테스트로 새지 않는다(공유 파일 DB 오염 방지).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app import crud, models
from app.bridge import NullBridge
from app.enums import RobotState
from app.services import dispatch
from app.services.homography import settings


def _seed_min(db: Session) -> None:
    """이 파일은 시드 없이 로봇만 직접 만든다(격리). robot 2대 생성 후 flush."""
    for rid in ("amr_1", "amr_2"):
        if db.get(models.Robot, rid) is None:
            crud.robots.ensure(db, rid, name=rid)
    db.flush()


def _make_eligible_robot(db: Session, robot_id: str, *, x: float = 0.0, y: float = 0.0):
    robot = crud.robots.get(db, robot_id)
    robot.online = True
    robot.status = RobotState.IDLE.value
    robot.battery = 90
    robot.x = x
    robot.y = y
    db.flush()
    return robot


def _make_fire_event(db: Session, *, x: float | None, y: float | None):
    # 격리를 위해 zone/camera FK 를 걸지 않는다(dispatch 로직은 x/y 만 본다).
    event = crud.events.create(
        db, source="cctv", type_="FIRE", confidence=0.95, x=x, y=y,
    )
    db.flush()
    return event


# ── should_auto_dispatch 조건 (순수) ─────────────────────────────────────────

@pytest.mark.parametrize("etype", ["FIRE", "LEAK"])  # 화재·냉각수 누수 = 자동 급파 대상
def test_should_auto_dispatch_with_coords(etype) -> None:
    assert dispatch.should_auto_dispatch(etype, (12.0, 3.0), merged=False) is True


@pytest.mark.parametrize(
    "etype, xy, merged",
    [
        ("SMOKE", (1.0, 2.0), False),   # 연기는 대상 아님 → 자동 급파 안 함
        ("FIRE", None, False),          # 맵 좌표 없음 → GOTO 목표 없음
        ("LEAK", None, False),          # 맵 좌표 없음 → GOTO 목표 없음
        ("FIRE", (1.0, 2.0), True),     # dedup 병합 → 최초 감지 때 이미 급파
    ],
)
def test_should_not_auto_dispatch(etype, xy, merged) -> None:
    assert dispatch.should_auto_dispatch(etype, xy, merged=merged) is False


def test_should_auto_dispatch_respects_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vision_auto_dispatch", False)
    assert dispatch.should_auto_dispatch("FIRE", (1.0, 2.0), merged=False) is False


# ── should_auto_hold 조건 (AMR 자체 감지 제자리 정지, 순수) ────────────────────

@pytest.mark.parametrize("etype", ["FIRE", "LEAK"])  # 화재·냉각수 = 제자리 정지 대상
def test_should_auto_hold_amr_self_detection(etype) -> None:
    # 자체 감지는 맵 좌표가 없어도(로봇이 이미 현장) 정지시켜야 한다.
    assert dispatch.should_auto_hold(etype, "amr", "AMR-01", merged=False) is True


@pytest.mark.parametrize(
    "etype, source, robot_id, merged",
    [
        ("SMOKE", "amr", "AMR-01", False),   # 연기는 대상 아님
        ("FIRE", "cctv", None, False),       # CCTV 는 급파 경로 — 제자리 정지 아님
        ("FIRE", "amr", None, False),        # 세울 로봇 불명
        ("FIRE", "amr", "AMR-01", True),     # dedup 병합 → 최초 감지 때 이미 대응
    ],
)
def test_should_not_auto_hold(etype, source, robot_id, merged) -> None:
    assert dispatch.should_auto_hold(etype, source, robot_id, merged=merged) is False


def test_should_auto_hold_respects_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vision_auto_dispatch", False)
    assert dispatch.should_auto_hold("FIRE", "amr", "AMR-01", merged=False) is False


# ── dispatch 서비스 (자동 급파가 쓰는 그 로직) ───────────────────────────────

def test_select_robot_picks_eligible_and_skips_offline(db: Session) -> None:
    _seed_min(db)
    _make_eligible_robot(db, "amr_1", x=1.0, y=1.0)  # amr_2 는 기본 OFFLINE
    event = _make_fire_event(db, x=1.0, y=1.0)
    robot_id, selection = dispatch.select_robot(db, event)
    assert robot_id == "amr_1"
    assert "distance_m" in selection


def test_select_robot_none_when_all_offline(db: Session) -> None:
    _seed_min(db)
    event = _make_fire_event(db, x=1.0, y=1.0)
    robot_id, _ = dispatch.select_robot(db, event)
    assert robot_id is None


def test_assign_and_goto_publishes_goto_with_map_coords(db: Session, bridge: NullBridge) -> None:
    _seed_min(db)
    _make_eligible_robot(db, "amr_1")
    event = _make_fire_event(db, x=12.5, y=-3.0)

    outcome = dispatch.assign_and_goto(db, event, "amr_1", preempt=True)

    assert event.assigned_robot_id == "amr_1"
    assert event.status == "ASSIGNED"
    assert outcome.mission.mission_type == "ANOMALY"
    robot = crud.robots.get(db, "amr_1")
    assert robot.status == RobotState.DISPATCHING.value
    # 호모그래피로 채운 맵 좌표(event.x/y)가 GOTO waypoint 로 그대로 전달돼야 한다.
    goto = [c for c in bridge.sent if c["command_type"] == "GOTO"]
    assert len(goto) == 1
    wp = goto[0]["payload"]["waypoints"][0]
    assert (wp["x"], wp["y"]) == (12.5, -3.0)
    assert goto[0]["payload"]["event_id"] == event.event_id


def test_assign_and_hold_publishes_hold_without_waypoints(db: Session, bridge: NullBridge) -> None:
    _seed_min(db)
    _make_eligible_robot(db, "amr_1")
    # 자체 감지 이벤트는 좌표가 없다(로봇이 이미 현장에 있음).
    event = crud.events.create(db, source="amr", type_="LEAK", confidence=0.9, x=None, y=None)
    db.flush()

    outcome = dispatch.assign_and_hold(db, event, "amr_1", preempt=True)

    assert event.assigned_robot_id == "amr_1"
    assert event.status == "ASSIGNED"
    assert outcome.mission.mission_type == "ANOMALY"
    robot = crud.robots.get(db, "amr_1")
    assert robot.status == RobotState.INSPECTING.value
    # 좌표 없이 그 로봇을 제자리에 세우는 ANOMALY_HOLD 만 나가야 한다(GOTO waypoint 없음).
    hold = [c for c in bridge.sent if c["command_type"] == "ANOMALY_HOLD"]
    assert len(hold) == 1
    assert hold[0]["payload"]["event_id"] == event.event_id
    assert "waypoints" not in hold[0]["payload"]
    assert not [c for c in bridge.sent if c["command_type"] == "GOTO"]


def _make_anomaly_hold(db: Session, robot_id: str, *, with_preempted_patrol: bool):
    """robot_id 를 '이상 대응 hold(INSPECTING)' 상태로 만든다 - 진행 중 ANOMALY 미션 +
    (옵션) 선점된 PATROL 미션 + 연결 이벤트."""
    event = crud.events.create(db, source="amr", type_="LEAK", confidence=0.9, x=None, y=None)
    db.flush()
    patrol = None
    if with_preempted_patrol:
        patrol = crud.robots.create_mission(
            db, mission_id=crud.ids.next_mission_id(), mission_type="PATROL",
            robot_id=robot_id, status="PREEMPTED",
            node_order_json=["N1", "N2", "N3"], current_node_id="N2",
            resume_context_json={"remaining_nodes": ["N2", "N3"], "current_index": 1,
                                 "reason": "ANOMALY_PREEMPT"},
        )
    anomaly = crud.robots.create_mission(
        db, mission_id=crud.ids.next_mission_id(), mission_type="ANOMALY",
        robot_id=robot_id, event_id=event.event_id, status="RUNNING",
    )
    robot = crud.robots.get(db, robot_id)
    robot.current_mission_id = anomaly.mission_id
    robot.status = RobotState.INSPECTING.value
    db.flush()
    return event, patrol, anomaly


def test_resume_from_anomaly_restores_patrol_and_signals(db: Session, bridge: NullBridge) -> None:
    _seed_min(db)
    _make_eligible_robot(db, "amr_1")
    event, patrol, anomaly = _make_anomaly_hold(db, "amr_1", with_preempted_patrol=True)

    outcome = dispatch.resume_from_anomaly(db, "amr_1")

    # ANOMALY 미션 종료, 선점됐던 PATROL 미션 복원(재개 노드 = 남은 노드 첫 번째)
    assert outcome.closed_anomaly.mission_id == anomaly.mission_id
    assert anomaly.status == "DONE"
    assert outcome.resumed_patrol.mission_id == patrol.mission_id
    assert patrol.status == "RUNNING"
    assert patrol.current_node_id == "N2"
    assert patrol.resume_context_json is None
    robot = crud.robots.get(db, "amr_1")
    assert robot.current_mission_id == patrol.mission_id
    assert robot.status == RobotState.RESUMING.value
    assert event.status == "RESOLVED"
    # 로봇에 hold 해제 신호(ANOMALY_RESUME)가 정확히 한 번 나가야 한다
    resume = [c for c in bridge.sent if c["command_type"] == "ANOMALY_RESUME"]
    assert len(resume) == 1


def test_resume_from_anomaly_without_preempted_patrol_goes_idle(db: Session, bridge: NullBridge) -> None:
    _seed_min(db)
    _make_eligible_robot(db, "amr_1")
    _make_anomaly_hold(db, "amr_1", with_preempted_patrol=False)  # 순찰 중이 아니었던 경우

    outcome = dispatch.resume_from_anomaly(db, "amr_1")

    assert outcome.resumed_patrol is None
    robot = crud.robots.get(db, "amr_1")
    assert robot.status == RobotState.IDLE.value
    assert robot.current_mission_id is None
    assert [c for c in bridge.sent if c["command_type"] == "ANOMALY_RESUME"]
