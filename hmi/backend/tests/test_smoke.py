"""전체 엔드포인트 스모크 테스트.

'모든 라우터가 등록되어 있고, 정상 입력에 200/201 로 답하며, 응답이 공통 봉투를
지킨다'까지만 확인한다. 도메인 로직의 정확성은 다른 테스트 파일이 맡는다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import data_of


def test_health(client: TestClient):
    body = client.get("/health").json()
    assert body["result"] == "SUCCESS"
    assert body["data"]["status"] == "ok"
    # ROS 미연결 상태가 정직하게 드러나야 한다
    assert body["data"]["ros_connected"] is False


def test_openapi_covers_all_routers(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/map/{map_id}",
        "/api/zones",
        "/api/nodes",
        "/api/routes",
        "/api/patrol/start",
        "/api/robots",
        "/api/events",
        "/api/equipment",
        "/api/work-orders",
        "/api/align/check",
        "/api/priority/nodes",
        "/api/stats/events",
        "/api/suppression/request",
        "/api/config/suppression-mode",
    ):
        assert expected in paths, f"{expected} 라우트가 등록되지 않았습니다"


def test_envelope_shape_on_success(client: TestClient):
    body = client.get("/api/robots").json()
    assert set(body) >= {"result", "data"}
    assert body["result"] == "SUCCESS"
    assert isinstance(body["data"], list)


# ── §1 맵 ─────────────────────────────────────────────────────────────────
def test_map_endpoints(client: TestClient):
    maps = data_of(client.get("/api/map"))
    assert maps, "시드 맵이 있어야 한다"
    map_id = maps[0]["map_id"]

    detail = data_of(client.get(f"/api/map/{map_id}"))
    assert detail["resolution"] == 0.05
    assert len(detail["origin"]) == 3

    assert data_of(client.get("/api/map/active"))["map_id"] == map_id
    assert data_of(client.get(f"/api/map/{map_id}/aruco")) == []


def test_map_coordinate_transform_roundtrips(client: TestClient):
    """map→pixel→map 왕복이 원래 좌표로 돌아와야 한다 (B-13)."""
    map_id = data_of(client.get("/api/map"))[0]["map_id"]
    result = data_of(client.get(f"/api/map/{map_id}/transform", params={"x": 3.2, "y": -1.5}))
    assert abs(result["roundtrip"]["x"] - 3.2) < 1e-6
    assert abs(result["roundtrip"]["y"] - (-1.5)) < 1e-6


# ── §2 구역·노드·경로 ─────────────────────────────────────────────────────
def test_zone_node_route_flow(admin_client: TestClient):
    zone = data_of(admin_client.post("/api/zones", json={"name": "스모크 구역", "risk_base": 2}))
    zone_id = zone["zone_id"]

    map_id = data_of(admin_client.get("/api/map"))[0]["map_id"]
    node = data_of(
        admin_client.post(
            "/api/nodes",
            json={"map_id": map_id, "zone_id": zone_id, "name": "스모크 노드", "x": 1.0, "y": 1.0},
        )
    )
    node_id = node["node_id"]

    route = data_of(
        admin_client.post(
            "/api/routes",
            json={"name": "스모크 경로", "map_id": map_id, "node_order": [node_id], "loop": False},
        )
    )
    validation = data_of(admin_client.post(f"/api/routes/{route['route_id']}/validate"))
    assert validation["valid"] is True, validation

    assert data_of(admin_client.delete(f"/api/routes/{route['route_id']}"))["deleted"]
    assert data_of(admin_client.delete(f"/api/nodes/{node_id}"))["deleted"]
    assert data_of(admin_client.delete(f"/api/zones/{zone_id}"))["deleted"]


def test_node_list_filters(client: TestClient):
    nodes = data_of(client.get("/api/nodes", params={"zone_id": "Z01"}))
    assert nodes and all(n["zone_id"] == "Z01" for n in nodes)


# ── §3 순찰 미션 ──────────────────────────────────────────────────────────
def test_patrol_lifecycle(client: TestClient, bridge):
    started = data_of(
        client.post(
            "/api/patrol/start",
            json={"route_id": "R-01", "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": False},
        )
    )
    mission_id = started["mission_id"]
    assert started["status"] == "RUNNING"
    assert started["assigned"][0]["robot_id"] == "amr_1"
    assert any(c["command_type"] == "START_PATROL" for c in bridge.sent)

    assert data_of(client.post(f"/api/patrol/{mission_id}/pause", json={"reason": "MANUAL"}))["status"] == "PREEMPTED"
    assert data_of(client.post(f"/api/patrol/{mission_id}/resume"))["status"] == "RUNNING"
    assert data_of(client.post(f"/api/patrol/{mission_id}/cancel", json={"reason": "TEST"}))["status"] == "CANCELED"

    queue = data_of(client.get("/api/patrol/missions", params={"limit": 10}))
    assert any(m["mission_id"] == mission_id for m in queue)


# ── §4 로봇 ───────────────────────────────────────────────────────────────
def test_robot_endpoints(client: TestClient, bridge):
    robots = data_of(client.get("/api/robots"))
    assert {r["robot_id"] for r in robots} >= {"amr_1", "amr_2"}
    assert "state_ko" in robots[0]

    stop = data_of(client.post("/api/robots/amr_2/emergency-stop", json={"reason": "TEST"}))
    assert stop["state"] == "EMERGENCY_STOP"
    assert stop["checkpoint"]["interrupted_state"]

    resumed = data_of(client.post("/api/robots/amr_2/resume", json={"mode": "RESUME_CHECKPOINT"}))
    assert resumed["state"] != "EMERGENCY_STOP"
    assert any(c["command_type"] == "ESTOP" for c in bridge.sent)


def test_emergency_stop_all(client: TestClient):
    result = data_of(client.post("/api/robots/emergency-stop-all"))
    assert set(result["stopped"]) >= {"amr_1", "amr_2"}
    assert result["failed"] == []


# ── §5 이벤트 ─────────────────────────────────────────────────────────────
def test_event_detect_and_history(client: TestClient):
    created = data_of(
        client.post(
            "/api/events/detect",
            json={
                "source": "cctv", "camera_id": "CAM-01", "type": "FIRE",
                "confidence": 0.87, "zone_id": "Z01", "node_id": "N-004", "x": 3.2, "y": -1.5,
            },
        )
    )
    event_id = created["event_id"]
    assert created["severity"] == "CRITICAL"  # FIRE 기본 심각도

    detail = data_of(client.get(f"/api/events/{event_id}"))
    assert detail["event"]["event_id"] == event_id
    assert detail["timeline"][0]["stage"] == "DETECTED"

    listing = client.get("/api/events", params={"zone_id": "Z01", "size": 5}).json()
    assert listing["result"] == "SUCCESS"
    assert {"count", "page", "size"} <= set(listing)

    assert data_of(client.post(f"/api/events/{event_id}/ack", json={"operator": "tester"}))["acknowledged_at"]
    assert data_of(client.post(f"/api/events/{event_id}/resolve", json={"operator": "tester"}))["status"] == "RESOLVED"


def test_event_csv_export(client: TestClient):
    response = client.get("/api/events/export", params={"format": "csv"})
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "event_id" in response.text


# ── §6 설비 · align ───────────────────────────────────────────────────────
def test_equipment_and_align(admin_client: TestClient):
    equipment = data_of(admin_client.get("/api/equipment", params={"zone_id": "Z01"}))
    assert any(e["equipment_id"] == "EQ-BRK-01" for e in equipment)

    result = data_of(
        admin_client.post(
            "/api/align/check",
            json={"equipment_id": "EQ-BRK-01", "observed_state": "ON", "value": 24.1},
        )
    )
    assert result["verdict"] == "OK", result  # 작업지시 없음 + 기준값 일치
    assert data_of(admin_client.get("/api/align/results", params={"equipment_id": "EQ-BRK-01"}))

    rules = data_of(admin_client.get("/api/align/rules"))
    assert {r["rule_id"] for r in rules} >= {"RULE-001", "RULE-002", "RULE-003"}


def test_work_order_crud(admin_client: TestClient):
    wo = data_of(
        admin_client.post(
            "/api/work-orders",
            json={
                "equipment_id": "EQ-BRK-01", "work_type": "MAINTENANCE",
                "expected_state": "OFF", "status": "IN_PROGRESS",
            },
        )
    )
    assert wo["status"] == "IN_PROGRESS"
    listed = data_of(admin_client.get("/api/work-orders", params={"equipment_id": "EQ-BRK-01"}))
    assert any(w["wo_id"] == wo["wo_id"] for w in listed)
    data_of(admin_client.put(f"/api/work-orders/{wo['wo_id']}", json={
        "equipment_id": "EQ-BRK-01", "work_type": "MAINTENANCE", "status": "DONE"
    }))


# ── §7 우선순위 · 통계 ────────────────────────────────────────────────────
def test_priority_and_stats(admin_client: TestClient):
    ranked = data_of(admin_client.get("/api/priority/nodes", params={"window": "7d"}))
    assert ranked and ranked[0]["rank"] == 1
    assert all(0.0 <= n["score"] <= 1.0 for n in ranked)

    recalculated = data_of(admin_client.post("/api/priority/recalculate", json={"window": "7d"}))
    assert recalculated["updated"] == len(ranked)

    weighted = data_of(
        admin_client.put("/api/priority/nodes/N-004", json={"manual_weight": 0.8, "memo": "야간 집중"})
    )
    assert weighted["after_score"] >= 0.0
    assert data_of(admin_client.get("/api/priority/log", params={"node_id": "N-004"}))

    stats = data_of(admin_client.get("/api/stats/events", params={"group_by": "zone"}))
    assert {"total", "false_positive_rate", "avg_response_sec", "groups"} <= set(stats)
    assert "unresolved" in data_of(admin_client.get("/api/stats/overview"))


# ── §8 진압 · 설정 ────────────────────────────────────────────────────────
def test_suppression_and_config(admin_client: TestClient):
    assert data_of(admin_client.get("/api/config"))["suppression_mode"] in ("MANUAL", "AUTO")
    assert data_of(admin_client.put("/api/config/suppression-mode", json={"mode": "MANUAL"}))["mode"] == "MANUAL"

    requested = data_of(
        admin_client.post(
            "/api/suppression/request",
            json={
                "zone_id": "Z01", "actions": ["POWER_CUT"], "breaker_ids": ["EQ-BRK-01"],
                "requested_by": "admin_tester", "confirm_text": "Z01",
            },
        )
    )
    suppression_id = requested["suppression_id"]
    assert requested["status"] in ("INTERLOCK_CHECK", "BLOCKED")

    detail = data_of(admin_client.get(f"/api/suppression/{suppression_id}"))
    assert detail["steps"][0]["step"] == "INTERLOCK_CHECK"

    aborted = data_of(
        admin_client.post(
            f"/api/suppression/{suppression_id}/abort",
            json={"operator": "admin_tester", "reason": "스모크 테스트"},
        )
    )
    assert aborted["status"] == "ABORTED"
    assert data_of(admin_client.get("/api/suppression/logs"))
