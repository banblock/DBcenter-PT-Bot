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

def test_should_auto_dispatch_fire_with_coords() -> None:
    assert dispatch.should_auto_dispatch("FIRE", (12.0, 3.0), merged=False) is True


@pytest.mark.parametrize(
    "etype, xy, merged",
    [
        ("SMOKE", (1.0, 2.0), False),   # 화재 아님 → 자동 급파 안 함
        ("LEAK", (1.0, 2.0), False),
        ("FIRE", None, False),          # 맵 좌표 없음 → GOTO 목표 없음
        ("FIRE", (1.0, 2.0), True),     # dedup 병합 → 최초 감지 때 이미 급파
    ],
)
def test_should_not_auto_dispatch(etype, xy, merged) -> None:
    assert dispatch.should_auto_dispatch(etype, xy, merged=merged) is False


def test_should_auto_dispatch_respects_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vision_auto_dispatch", False)
    assert dispatch.should_auto_dispatch("FIRE", (1.0, 2.0), merged=False) is False


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
