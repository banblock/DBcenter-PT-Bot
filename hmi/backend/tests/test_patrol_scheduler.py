"""순찰 스케줄러 — 다중 AMR 경로 중복 방지(node_lock) 검증.

체크리스트 대응:
  · B-24 노드 분배 (라운드로빈)      → test_single_call_multi_amr_partitions_without_overlap
  · B-25 노드 점유 락 (claim/release) → test_node_lock_*
  · B-28 경로 겹침 사전 검사          → test_full_overlap_*, test_partial_overlap_*
  · 순찰 취소 → 긴급 이동 전환        → test_cancel_frees_nodes_*, test_cancel_then_emergency_goto
  · 스케줄러 ↔ AMR Bridge 연동        → test_bridge_receives_start_patrol_per_robot

주의: API 로 시작한 미션은 커밋되므로 테스트 사이에 점유가 새지 않도록 반드시 취소한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import crud
from app.crud import ids
from app.enums import MissionStatus
from app.services import node_lock

from .conftest import data_of


# ══════════════════════════════════════════════════════════════════════════
# 단위 — node_lock 서비스 (B-25)
# ══════════════════════════════════════════════════════════════════════════
def _make_mission(db: Session, robot_id: str, nodes: list[str], status: str) -> str:
    mission = crud.robots.create_mission(
        db,
        mission_id=ids.next_mission_id(),
        mission_type="PATROL",
        robot_id=robot_id,
        status=status,
        node_order_json=nodes,
    )
    return mission.mission_id


def test_node_lock_derives_holders_from_active_missions(seeded: Session):
    """활성 미션의 담당 노드가 그대로 점유 노드가 된다 — 별도 락 테이블 없이 유도."""
    _make_mission(seeded, "amr_1", ["N-001", "N-002"], MissionStatus.RUNNING.value)
    _make_mission(seeded, "amr_2", ["N-005"], MissionStatus.PREEMPTED.value)  # 급파로 멈춰도 점유

    held = node_lock.held_nodes(seeded)
    assert held["N-001"] == "amr_1"
    assert held["N-002"] == "amr_1"
    assert held["N-005"] == "amr_2"  # PREEMPTED 도 점유로 본다
    assert "N-003" not in held  # 아무도 안 물고 있는 노드


def test_node_lock_ignores_finished_missions(seeded: Session):
    """취소/완료된 미션의 노드는 자동으로 풀린다 (release 호출 불필요)."""
    _make_mission(seeded, "amr_1", ["N-001"], MissionStatus.CANCELED.value)
    _make_mission(seeded, "amr_2", ["N-002"], MissionStatus.DONE.value)
    assert node_lock.held_nodes(seeded) == {}


def test_claim_excludes_held_and_preserves_order(seeded: Session):
    """다른 로봇이 점유한 노드만 충돌로 걸러내고, granted 순서는 입력 순서를 지킨다."""
    _make_mission(seeded, "amr_1", ["N-002"], MissionStatus.RUNNING.value)

    result = node_lock.claim(seeded, ["amr_2"], ["N-001", "N-002", "N-003"])
    assert result.granted == ["N-001", "N-003"]
    assert result.has_conflict
    assert result.conflicts_as_dicts() == [{"node_id": "N-002", "holder": "amr_1"}]


def test_claim_allows_self_reoccupation(seeded: Session):
    """자기 자신이 물고 있던 노드는 충돌이 아니다 (재점유 허용)."""
    _make_mission(seeded, "amr_1", ["N-001"], MissionStatus.RUNNING.value)
    result = node_lock.claim(seeded, ["amr_1"], ["N-001", "N-002"])
    assert result.granted == ["N-001", "N-002"]
    assert not result.has_conflict


# ══════════════════════════════════════════════════════════════════════════
# 통합 — /api/patrol/start 의 중복 방지 (B-28)
# ══════════════════════════════════════════════════════════════════════════
def test_single_call_multi_amr_partitions_without_overlap(client: TestClient):
    """한 번의 호출로 2대에 배분하면 담당 노드가 서로 겹치지 않아야 한다 (B-24)."""
    started = data_of(
        client.post(
            "/api/patrol/start",
            json={
                "route_id": "R-01",
                "robot_ids": ["amr_1", "amr_2"],
                "mode": "ONCE",
                "apply_priority": False,
            },
        )
    )
    try:
        assigned = {a["robot_id"]: a["nodes"] for a in started["assigned"]}
        assert set(assigned) == {"amr_1", "amr_2"}
        a1, a2 = set(assigned["amr_1"]), set(assigned["amr_2"])
        assert a1.isdisjoint(a2), f"두 로봇의 노드가 겹친다: {a1 & a2}"
        assert a1 | a2  # 최소한 나눠 가졌다
        assert started["conflicts"] == []
    finally:
        for mid in started["mission_ids"]:
            client.post(f"/api/patrol/{mid}/cancel", json={"reason": "cleanup"})


def test_full_overlap_rejected_with_node_locked(client: TestClient):
    """이미 한 로봇이 경로 전체를 물고 있으면, 같은 경로 재시작은 409 NODE_LOCKED."""
    first = data_of(
        client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": False},
        )
    )
    try:
        res = client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_2"], "mode": "ONCE", "apply_priority": False},
        )
        assert res.status_code == 409
        body = res.json()
        assert body["result"] == "FAIL"
        assert body["code"] == "NODE_LOCKED"
        holders = {c["holder"] for c in body["data"]["conflicts"]}
        assert holders == {"amr_1"}
    finally:
        client.post(f"/api/patrol/{first['mission_id']}/cancel", json={"reason": "cleanup"})


def test_partial_overlap_assigns_free_and_reports_conflicts(admin_client: TestClient):
    """일부만 겹치면 자유 노드는 배분하고, 겹친 노드는 conflicts 로 보고한다."""
    map_id = data_of(admin_client.get("/api/map"))[0]["map_id"]
    route_a = data_of(
        admin_client.post(
            "/api/routes",
            json={"name": "겹침A", "map_id": map_id, "node_order": ["N-001", "N-002"], "loop": False},
        )
    )["route_id"]
    route_b = data_of(
        admin_client.post(
            "/api/routes",
            json={"name": "겹침B", "map_id": map_id, "node_order": ["N-002", "N-003"], "loop": False},
        )
    )["route_id"]

    first = data_of(
        admin_client.post(
            "/api/patrol/start",
            json={"route_id": route_a, "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": False},
        )
    )
    try:
        second = data_of(
            admin_client.post(
                "/api/patrol/start",
                json={"route_id": route_b, "robot_ids": ["amr_2"], "mode": "ONCE", "apply_priority": False},
            )
        )
        # N-002 는 amr_1 이 점유 → 빠지고, N-003 만 amr_2 에 배분된다.
        assigned = {a["robot_id"]: a["nodes"] for a in second["assigned"]}
        assert assigned == {"amr_2": ["N-003"]}
        assert second["conflicts"] == [{"node_id": "N-002", "holder": "amr_1"}]
        for mid in second["mission_ids"]:
            admin_client.post(f"/api/patrol/{mid}/cancel", json={"reason": "cleanup"})
    finally:
        admin_client.post(f"/api/patrol/{first['mission_id']}/cancel", json={"reason": "cleanup"})
        admin_client.delete(f"/api/routes/{route_a}")
        admin_client.delete(f"/api/routes/{route_b}")


# ══════════════════════════════════════════════════════════════════════════
# 순찰 취소 → 긴급 이동 전환
# ══════════════════════════════════════════════════════════════════════════
def test_cancel_frees_nodes_for_reassignment(client: TestClient):
    """순찰을 취소하면 그 노드가 즉시 풀려 다른 로봇이 같은 경로를 잡을 수 있어야 한다."""
    first = data_of(
        client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": False},
        )
    )
    data_of(client.post(f"/api/patrol/{first['mission_id']}/cancel", json={"reason": "긴급 상황"}))

    # 취소 직후 — 같은 경로를 amr_2 가 충돌 없이 잡는다.
    second = data_of(
        client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_2"], "mode": "ONCE", "apply_priority": False},
        )
    )
    try:
        assert second["conflicts"] == []
        assert second["assigned"], "취소로 풀린 노드가 amr_2 에 배분돼야 한다"
    finally:
        for mid in second["mission_ids"]:
            client.post(f"/api/patrol/{mid}/cancel", json={"reason": "cleanup"})


def test_cancel_then_emergency_goto(client: TestClient, bridge):
    """순찰 취소 후 로봇이 자유로워져 긴급 이동(goto)이 하달된다."""
    first = data_of(
        client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": False},
        )
    )
    data_of(client.post(f"/api/patrol/{first['mission_id']}/cancel", json={"reason": "이상지점 급파"}))

    moved = data_of(
        client.post("/api/robots/amr_1/goto", json={"waypoints": [{"x": 5.0, "y": 3.0, "theta": 0.0}]})
    )
    assert moved["accepted"] is True
    assert any(c["command_type"] == "GOTO" and c["robot_id"] == "amr_1" for c in bridge.sent)


# ══════════════════════════════════════════════════════════════════════════
# 스케줄러 ↔ AMR Bridge 연동
# ══════════════════════════════════════════════════════════════════════════
def test_bridge_receives_start_patrol_per_robot(client: TestClient, bridge):
    """배분 결과가 로봇별 START_PATROL 명령으로 정확히 하달된다 (§10-2)."""
    started = data_of(
        client.post(
            "/api/patrol/start",
            json={
                "route_id": "R-01",
                "robot_ids": ["amr_1", "amr_2"],
                "mode": "LOOP",
                "apply_priority": False,
            },
        )
    )
    try:
        assigned = {a["robot_id"]: a["nodes"] for a in started["assigned"]}
        sent_by_robot = {
            c["robot_id"]: c
            for c in bridge.sent
            if c["command_type"] == "START_PATROL"
        }
        assert set(sent_by_robot) == set(assigned)
        for robot_id, cmd in sent_by_robot.items():
            node_ids = [n["node_id"] for n in cmd["payload"]["nodes"]]
            assert node_ids == assigned[robot_id]
            assert cmd["payload"]["loop"] is True
    finally:
        for mid in started["mission_ids"]:
            client.post(f"/api/patrol/{mid}/cancel", json={"reason": "cleanup"})
