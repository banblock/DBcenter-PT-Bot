"""CRUD 정상 동작 · DB 제약조건 검증.

라우터를 거치지 않고 CRUD 레이어를 직접 두드린다. HTTP 계층이 잡아주는 검증에
가려져 DB 제약이 실제로는 안 걸려 있는 상황을 잡기 위해서다.
(SQLite 는 `PRAGMA foreign_keys=ON` 이 없으면 FK 를 통째로 무시한다.)
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, models
from app.enums import AlignVerdict, EquipmentCheckState, ObservedState, RobotState
from app.errors import ApiError
from app.services import align_engine


# ══════════════════════════════════════════════════════════════════════════
# 제약조건
# ══════════════════════════════════════════════════════════════════════════
def test_foreign_keys_are_enforced(seeded: Session):
    """FK 가 실제로 켜져 있는지 — SQLite 기본값은 OFF 라 반드시 확인해야 한다."""
    seeded.add(models.Node(node_id="N-ORPHAN", map_id="MAP-NOPE", name="고아", x=0, y=0))
    with pytest.raises(IntegrityError):
        seeded.flush()
    seeded.rollback()


def test_check_constraint_rejects_bad_enum(seeded: Session):
    seeded.add(models.Robot(robot_id="amr_bad", name="잘못된 상태", status="FLYING"))
    with pytest.raises(IntegrityError):
        seeded.flush()
    seeded.rollback()


def test_check_constraint_rejects_out_of_range_battery(seeded: Session):
    seeded.add(models.Robot(robot_id="amr_batt", name="배터리 초과", status="IDLE", battery=150))
    with pytest.raises(IntegrityError):
        seeded.flush()
    seeded.rollback()


def test_check_constraint_rejects_bad_confidence(seeded: Session):
    seeded.add(
        models.Event(event_id="EV-BAD", source="cctv", type="FIRE", severity="INFO",
                     status="QUEUED", confidence=1.5)
    )
    with pytest.raises(IntegrityError):
        seeded.flush()
    seeded.rollback()


def test_unique_observation_angle_per_event(seeded: Session):
    event = crud.events.create(seeded, source="amr", type_="SMOKE", confidence=0.5, zone_id="Z01")
    crud.events.add_observation(seeded, event.event_id, angle_idx=1, observed_state="ON")
    with pytest.raises(IntegrityError):
        crud.events.add_observation(seeded, event.event_id, angle_idx=1, observed_state="OFF")
    seeded.rollback()


def test_cascade_delete_removes_children(seeded: Session):
    event = crud.events.create(seeded, source="amr", type_="LEAK", confidence=0.6, zone_id="Z03")
    event_id = event.event_id
    crud.events.add_media(seeded, event_id, uri="/media/x.jpg")
    crud.events.add_observation(seeded, event_id, angle_idx=1, observed_state="NORMAL")
    seeded.commit()

    seeded.delete(seeded.get(models.Event, event_id))
    seeded.commit()

    assert seeded.query(models.EventMedia).filter_by(event_id=event_id).count() == 0
    assert seeded.query(models.Observation).filter_by(event_id=event_id).count() == 0
    assert seeded.query(models.EventTimeline).filter_by(event_id=event_id).count() == 0


# ══════════════════════════════════════════════════════════════════════════
# 채번
# ══════════════════════════════════════════════════════════════════════════
def test_event_ids_increment_within_a_day(seeded: Session):
    first = crud.events.create(seeded, source="cctv", type_="FIRE", confidence=0.8, zone_id="Z01")
    second = crud.events.create(seeded, source="cctv", type_="FIRE", confidence=0.8, zone_id="Z02")
    assert first.event_id < second.event_id
    assert first.event_id.startswith("EV-")


def test_node_id_autonumber_skips_existing(seeded: Session):
    node = crud.patrol.create_node(seeded, map_id="MAP-DEMO-1F", name="자동채번", x=0, y=0)
    assert node.node_id == "N-009"  # 시드가 N-008 까지 쓴다
    seeded.rollback()


# ══════════════════════════════════════════════════════════════════════════
# 404 / 409 를 CRUD 가 직접 던지는가
# ══════════════════════════════════════════════════════════════════════════
def test_crud_raises_api_error_for_missing(seeded: Session):
    with pytest.raises(ApiError) as exc:
        crud.patrol.get_node(seeded, "N-NOPE")
    assert exc.value.status == 404
    assert exc.value.code == "NODE_NOT_FOUND"


def test_duplicate_raises_conflict(seeded: Session):
    with pytest.raises(ApiError) as exc:
        crud.patrol.create_zone(seeded, zone_id="Z01", name="중복")
    assert exc.value.status == 409
    seeded.rollback()


# ══════════════════════════════════════════════════════════════════════════
# 경로 검증
# ══════════════════════════════════════════════════════════════════════════
def test_route_validation_flags_missing_and_duplicate(seeded: Session):
    route = crud.patrol.create_route(
        seeded, route_id="R-TEST", name="검증용", map_id="MAP-DEMO-1F",
        node_order=["N-001", "N-001", "N-NOPE"],
    )
    result = crud.patrol.validate_route(seeded, route.route_id)
    assert result.valid is False
    reasons = {e.reason for e in result.errors}
    assert "DUPLICATED" in reasons and "NOT_FOUND" in reasons
    seeded.rollback()


def test_deleting_node_removes_it_from_routes(seeded: Session):
    node = crud.patrol.create_node(seeded, map_id="MAP-DEMO-1F", name="곧 삭제", x=9, y=9)
    route = crud.patrol.create_route(
        seeded, route_id="R-DEL", name="삭제 검증", map_id="MAP-DEMO-1F",
        node_order=["N-001", node.node_id],
    )
    crud.patrol.delete_node(seeded, node.node_id)
    assert node.node_id not in crud.patrol.get_route(seeded, route.route_id).node_order_json
    seeded.rollback()


# ══════════════════════════════════════════════════════════════════════════
# dedup
# ══════════════════════════════════════════════════════════════════════════
def test_dedup_finds_recent_same_zone_type(seeded: Session):
    created = crud.events.create(seeded, source="cctv", type_="SMOKE", confidence=0.7, zone_id="Z02")
    target = crud.events.find_dedup_target(seeded, zone_id="Z02", type_="SMOKE")
    assert target is not None and target.event_id == created.event_id


def test_dedup_does_not_cross_zones(seeded: Session):
    crud.events.create(seeded, source="cctv", type_="SMOKE", confidence=0.7, zone_id="Z02")
    assert crud.events.find_dedup_target(seeded, zone_id="Z03", type_="SMOKE") is None


def test_dedup_skips_when_zone_unknown(seeded: Session):
    """구역을 모르면 병합하지 않는다 — 다른 장소 사건을 뭉치면 안 된다."""
    crud.events.create(seeded, source="sensor", type_="LEAK", confidence=0.7, zone_id=None)
    assert crud.events.find_dedup_target(seeded, zone_id=None, type_="LEAK") is None


def test_merge_keeps_highest_confidence(seeded: Session):
    event = crud.events.create(seeded, source="cctv", type_="FIRE", confidence=0.6, zone_id="Z01")
    crud.events.merge_into(seeded, event, 0.4)
    assert event.hit_count == 2
    assert event.confidence == 0.6  # 낮은 값으로 덮이지 않는다


# ══════════════════════════════════════════════════════════════════════════
# align 엔진
# ══════════════════════════════════════════════════════════════════════════
def test_align_ok_when_matching_normal_state(seeded: Session):
    result = align_engine.check(
        seeded, equipment_id="EQ-BRK-01", observed_state=ObservedState.ON.value, value=24.0
    )
    assert result.verdict == AlignVerdict.OK.value
    assert result.auto_created_event_id is None


def test_align_mismatch_creates_event(seeded: Session):
    result = align_engine.check(
        seeded, equipment_id="EQ-LOCK-02", observed_state=ObservedState.UNLOCKED.value
    )
    assert result.verdict == AlignVerdict.MISMATCH.value
    assert result.auto_created_event_id is not None
    auto = seeded.get(models.Event, result.auto_created_event_id)
    assert auto.type == "LOCK_ABNORMAL"
    assert auto.source == "align"


def test_align_work_order_overrides_normal_state(seeded: Session):
    """대표 케이스 ①: 작업 중인데 차단기가 ON → CRITICAL MISMATCH (RULE-001)."""
    crud.equipment.create_work_order(
        seeded, equipment_id="EQ-BRK-02", work_type="MAINTENANCE",
        expected_state="OFF", status="IN_PROGRESS",
    )
    result = align_engine.check(
        seeded, equipment_id="EQ-BRK-02", observed_state=ObservedState.ON.value
    )
    assert result.verdict == AlignVerdict.MISMATCH.value
    assert result.severity == "CRITICAL"
    assert result.rule_id == "RULE-001"
    assert result.expected_state == "OFF"
    seeded.rollback()


def test_align_out_of_range_value_is_mismatch(seeded: Session):
    """대표 케이스 ③: 수치 범위 이탈."""
    result = align_engine.check(
        seeded, equipment_id="EQ-PANEL-01", observed_state=ObservedState.NORMAL.value, value=99.0
    )
    assert result.verdict == AlignVerdict.MISMATCH.value
    assert "초과" in result.reason


def test_align_unknown_state_is_unverified_not_ok(seeded: Session):
    """판독 불가를 '정상'으로 처리하면 안 된다."""
    result = align_engine.check(
        seeded, equipment_id="EQ-BRK-01", observed_state=ObservedState.UNKNOWN.value
    )
    assert result.verdict == AlignVerdict.UNVERIFIED.value
    equipment = crud.equipment.get(seeded, "EQ-BRK-01")
    assert equipment.check_state == EquipmentCheckState.RECHECK.value


def test_align_updates_equipment_cache(seeded: Session):
    align_engine.check(seeded, equipment_id="EQ-BRK-01", observed_state="ON", value=23.5)
    equipment = crud.equipment.get(seeded, "EQ-BRK-01")
    assert equipment.last_observed_state == "ON"
    assert equipment.last_value == 23.5
    assert equipment.last_checked_at is not None


# ══════════════════════════════════════════════════════════════════════════
# 우선순위
# ══════════════════════════════════════════════════════════════════════════
def test_priority_scores_are_bounded_and_ranked(seeded: Session):
    from app.services import priority

    results = priority.compute(seeded, "7d")
    assert results
    assert all(0.0 <= r["score"] <= 1.0 for r in results)
    assert [r["rank"] for r in results] == sorted(r["rank"] for r in results)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_priority_reflects_event_count(seeded: Session):
    from app.services import priority

    for _ in range(5):
        crud.events.create(
            seeded, source="cctv", type_="FIRE", confidence=0.9, zone_id="Z01", node_id="N-004"
        )
    seeded.flush()
    results = {r["node_id"]: r for r in priority.compute(seeded, "7d")}
    assert results["N-004"]["event_count"] >= 5
    assert results["N-004"]["score"] > results["N-002"]["score"]


def test_visit_multiplier_increases_with_score():
    from app.services.priority import visit_multiplier_for

    assert visit_multiplier_for(0.1) == 1
    assert visit_multiplier_for(0.5) == 2
    assert visit_multiplier_for(0.9) == 3


def test_progress_never_divides_by_zero():
    assert crud.robots.compute_progress(0, 0) == 0.0
    assert crud.robots.compute_progress(3, 4) == 0.75
    assert crud.robots.compute_progress(9, 4) == 1.0  # 상한 고정


# ══════════════════════════════════════════════════════════════════════════
# 설정 KV
# ══════════════════════════════════════════════════════════════════════════
def test_suppression_mode_persists_in_db(seeded: Session):
    crud.system.set_config(seeded, "suppression_mode", "AUTO", updated_by="tester")
    seeded.commit()
    assert crud.system.get_suppression_mode(seeded) == "AUTO"
    crud.system.set_config(seeded, "suppression_mode", "MANUAL", updated_by="tester")
    seeded.commit()


# ══════════════════════════════════════════════════════════════════════════
# 시각 직렬화 (회귀 테스트)
# ══════════════════════════════════════════════════════════════════════════
def test_datetimes_round_trip_with_timezone(seeded: Session):
    """DB 왕복 후에도 timezone 이 살아 있어야 한다.

    naive datetime 이 돌아오면 isoformat() 에 오프셋이 빠지고, 프론트가 이를
    로컬 시각으로 해석해 KST 기준 9시간이 어긋난다 (실제로 발생했던 버그).
    """
    from datetime import timezone as tz

    event = crud.events.create(seeded, source="cctv", type_="FIRE", confidence=0.9, zone_id="Z01")
    seeded.commit()
    seeded.expire_all()  # 캐시가 아니라 DB 에서 다시 읽게 강제

    reloaded = crud.events.get(seeded, event.event_id)
    assert reloaded.detected_at.tzinfo is not None
    assert reloaded.detected_at.utcoffset() == tz.utc.utcoffset(None)

    serialized = crud.events.to_dict(reloaded)["detected_at"]
    assert serialized.endswith("+00:00"), f"오프셋이 빠졌습니다: {serialized}"


def test_naive_datetime_input_is_treated_as_utc(seeded: Session):
    from datetime import datetime

    naive = datetime(2026, 8, 6, 12, 0, 0)
    event = crud.events.create(
        seeded, source="cctv", type_="SMOKE", confidence=0.8, zone_id="Z02", detected_at=naive
    )
    seeded.commit()
    seeded.expire_all()
    assert crud.events.get(seeded, event.event_id).detected_at.tzinfo is not None


def test_aware_datetime_comparison_does_not_raise(seeded: Session):
    """aware/naive 혼용으로 인한 TypeError 가 나지 않아야 한다."""
    from datetime import datetime, timedelta, timezone as tz

    crud.events.create(seeded, source="amr", type_="LEAK", confidence=0.6, zone_id="Z03")
    seeded.commit()
    since = datetime.now(tz.utc) - timedelta(hours=1)
    rows, _ = crud.events.query(seeded, from_=since)
    assert rows


# ══════════════════════════════════════════════════════════════════════════
# apply_telemetry — 이상 대응 hold(INSPECTING+ANOMALY) 상태 보호
#   /control/<ns>_State 를 Fleet(4-상태 폴링)과 Control(세부 상태)이 공유해, hold 중
#   Control 의 INSPECTING 을 Fleet 의 PATROLLING/DISPATCHING 이 덮어써 '작업 복귀' 버튼이
#   사라지던 문제를 백엔드에서 막는다. (mission_type=ANOMALY + status=INSPECTING 일 때만)
# ══════════════════════════════════════════════════════════════════════════
def _put_in_anomaly_hold(db: Session, robot_id: str) -> None:
    robot = crud.robots.ensure(db, robot_id)
    robot.status = RobotState.INSPECTING.value
    crud.robots.create_mission(
        db, mission_id=crud.ids.next_mission_id(), mission_type="ANOMALY",
        robot_id=robot_id, status="RUNNING",
    )
    db.flush()


def test_apply_telemetry_keeps_anomaly_hold_over_patrol_states(db: Session):
    _put_in_anomaly_hold(db, "amr_1")
    for state in ("PATROLLING", "DISPATCHING", "IDLE"):
        robot = crud.robots.apply_telemetry(db, "amr_1", status=state)
        assert robot.status == RobotState.INSPECTING.value  # hold 유지


def test_apply_telemetry_allows_safety_state_during_hold(db: Session):
    _put_in_anomaly_hold(db, "amr_1")
    robot = crud.robots.apply_telemetry(db, "amr_1", status="EMERGENCY_STOP")
    assert robot.status == RobotState.EMERGENCY_STOP.value  # 안전/종단 상태는 통과


def test_apply_telemetry_pose_passes_through_during_hold(db: Session):
    _put_in_anomaly_hold(db, "amr_1")
    robot = crud.robots.apply_telemetry(db, "amr_1", x=1.5, y=2.5)
    assert robot.status == RobotState.INSPECTING.value  # 상태 유지
    assert robot.x == 1.5 and robot.y == 2.5            # pose 는 반영


def test_apply_telemetry_not_guarded_for_patrol_inspecting(db: Session):
    # 순찰 중 INSPECTING(차단기 점검 등, mission_type=PATROL)은 보호 대상 아님 → 정상 반영
    robot = crud.robots.ensure(db, "amr_1")
    robot.status = RobotState.INSPECTING.value
    crud.robots.create_mission(
        db, mission_id=crud.ids.next_mission_id(), mission_type="PATROL",
        robot_id="amr_1", status="RUNNING",
    )
    db.flush()
    robot = crud.robots.apply_telemetry(db, "amr_1", status="PATROLLING")
    assert robot.status == RobotState.PATROLLING.value


def test_apply_telemetry_normal_when_not_in_hold(db: Session):
    crud.robots.ensure(db, "amr_1")  # hold 아님(기본 상태)
    robot = crud.robots.apply_telemetry(db, "amr_1", status="PATROLLING")
    assert robot.status == RobotState.PATROLLING.value
