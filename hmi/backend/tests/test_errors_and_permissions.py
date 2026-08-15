"""에러 응답 규격 · 권한 거부 케이스.

여기서 확인하는 계약:
* 실패 응답도 봉투를 지킨다 — `{"result":"FAIL","code":...,"message":...}`
* HTTP 상태는 400/403/404/409/500 만 쓴다
* 권한이 없으면 **DB 를 건드리기 전에** 403 으로 끊긴다
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import data_of


def assert_fail(response, status: int, code: str | None = None):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["result"] == "FAIL", body
    assert "message" in body and body["message"]
    if code:
        assert body["code"] == code, body
    return body


# ══════════════════════════════════════════════════════════════════════════
# 404 — 대상 없음
# ══════════════════════════════════════════════════════════════════════════
def test_404_returns_domain_code(client: TestClient):
    assert_fail(client.get("/api/map/MAP-NOPE"), 404, "MAP_NOT_FOUND")
    assert_fail(client.get("/api/zones/Z99"), 404, "ZONE_NOT_FOUND")
    assert_fail(client.get("/api/nodes/N-999"), 404, "NODE_NOT_FOUND")
    assert_fail(client.get("/api/routes/R-99"), 404, "ROUTE_NOT_FOUND")
    assert_fail(client.get("/api/robots/amr_nope"), 404, "ROBOT_NOT_FOUND")
    assert_fail(client.get("/api/events/EV-NOPE"), 404, "EVENT_NOT_FOUND")
    assert_fail(client.get("/api/equipment/EQ-NOPE"), 404, "EQUIPMENT_NOT_FOUND")


def test_404_message_names_the_missing_id(client: TestClient):
    body = assert_fail(client.get("/api/routes/R-12345"), 404)
    assert "R-12345" in body["message"]


def test_unknown_path_is_still_enveloped(client: TestClient):
    assert_fail(client.get("/api/definitely-not-a-route"), 404)


# ══════════════════════════════════════════════════════════════════════════
# 400 — 유효성
# ══════════════════════════════════════════════════════════════════════════
def test_validation_error_reports_field(admin_client: TestClient):
    body = assert_fail(
        admin_client.post("/api/zones", json={"name": "", "risk_base": 99}), 400, "VALIDATION_ERROR"
    )
    fields = {e["field"] for e in body["data"]["errors"]}
    assert "risk_base" in fields, body


def test_polygon_must_have_three_points(admin_client: TestClient):
    body = assert_fail(
        admin_client.post("/api/zones", json={"name": "잘못된 구역", "polygon": [[0, 0], [1, 1]]}), 400
    )
    assert "꼭짓점" in body["message"]


def test_value_range_inversion_rejected(admin_client: TestClient):
    assert_fail(
        admin_client.post(
            "/api/equipment",
            json={
                "equipment_id": "EQ-BAD-01", "type": "BREAKER", "name": "역범위",
                "value_min": 30.0, "value_max": 10.0,
            },
        ),
        400,
    )


def test_unsupported_export_format(client: TestClient):
    assert_fail(client.get("/api/events/export", params={"format": "xlsx"}), 400)


def test_unknown_role_header_rejected(seeded, bridge):
    from app.main import app

    with TestClient(app, headers={"X-Role": "SUPERUSER"}) as c:
        assert_fail(c.get("/api/robots"), 400, "UNKNOWN_ROLE")


# ══════════════════════════════════════════════════════════════════════════
# 409 — 상태 충돌
# ══════════════════════════════════════════════════════════════════════════
def test_duplicate_zone_id_conflicts(admin_client: TestClient):
    assert_fail(admin_client.post("/api/zones", json={"zone_id": "Z01", "name": "중복"}), 409, "DUPLICATE_ID")


def test_duplicate_equipment_id_conflicts(admin_client: TestClient):
    assert_fail(
        admin_client.post(
            "/api/equipment", json={"equipment_id": "EQ-BRK-01", "type": "BREAKER", "name": "중복"}
        ),
        409,
        "DUPLICATE_ID",
    )


def test_double_patrol_start_conflicts(client: TestClient):
    payload = {"route_id": "R-01", "robot_ids": ["amr_2"], "mode": "ONCE", "apply_priority": False}
    first = data_of(client.post("/api/patrol/start", json=payload))
    body = assert_fail(client.post("/api/patrol/start", json=payload), 409, "MISSION_ALREADY_RUNNING")
    assert body["data"]["robot_id"] == "amr_2"
    client.post(f"/api/patrol/{first['mission_id']}/cancel", json={"reason": "cleanup"})


def test_resume_from_wrong_state_conflicts(client: TestClient):
    # IDLE 로봇을 resume 하려 하면 409
    assert_fail(client.post("/api/robots/amr_1/resume", json={"mode": "RESUME_CHECKPOINT"}), 409)


def test_suppression_confirm_text_mismatch(admin_client: TestClient):
    assert_fail(
        admin_client.post(
            "/api/suppression/request",
            json={
                "zone_id": "Z01", "actions": ["POWER_CUT"],
                "requested_by": "admin_tester", "confirm_text": "Z02",  # 불일치
            },
        ),
        400,
        "CONFIRM_TEXT_MISMATCH",
    )


def test_resolve_twice_conflicts(client: TestClient):
    created = data_of(
        client.post(
            "/api/events/detect",
            json={"source": "amr", "robot_id": "amr_1", "type": "LEAK", "confidence": 0.7, "zone_id": "Z03"},
        )
    )
    event_id = created["event_id"]
    data_of(client.post(f"/api/events/{event_id}/resolve", json={"operator": "tester"}))
    assert_fail(
        client.post(f"/api/events/{event_id}/resolve", json={"operator": "tester"}),
        409,
        "EVENT_ALREADY_CLOSED",
    )


# ══════════════════════════════════════════════════════════════════════════
# 403 — 권한
# ══════════════════════════════════════════════════════════════════════════
def test_viewer_can_read_everything(viewer_client: TestClient):
    for path in ("/api/robots", "/api/zones", "/api/nodes", "/api/events", "/api/equipment"):
        assert viewer_client.get(path).status_code == 200, path


def test_viewer_cannot_control_robots(viewer_client: TestClient):
    assert_fail(
        viewer_client.post("/api/robots/amr_1/goto", json={"waypoints": [{"x": 1, "y": 1}]}),
        403,
        "FORBIDDEN",
    )
    assert_fail(viewer_client.post("/api/robots/amr_1/dock"), 403)
    assert_fail(
        viewer_client.post("/api/patrol/start", json={"route_id": "R-01", "robot_ids": ["amr_1"]}), 403
    )


def test_viewer_can_still_emergency_stop(viewer_client: TestClient):
    """안전 기능은 권한으로 막지 않는다 — 정책상 의도된 예외."""
    assert viewer_client.post("/api/robots/amr_1/emergency-stop", json={"reason": "VIEWER"}).status_code == 200


def test_operator_cannot_edit_master_data(client: TestClient):
    assert_fail(client.post("/api/zones", json={"name": "운영자 생성 시도"}), 403, "FORBIDDEN")
    assert_fail(
        client.post("/api/equipment", json={"equipment_id": "EQ-X", "type": "VALVE", "name": "X"}), 403
    )
    assert_fail(client.delete("/api/align/rules/RULE-001"), 403)


def test_operator_cannot_approve_suppression_or_change_config(client: TestClient):
    assert_fail(client.put("/api/config/suppression-mode", json={"mode": "AUTO"}), 403)
    assert_fail(client.post("/api/suppression/SUP-0001/approve", json={"approver": "tester"}), 403)


def test_forbidden_response_says_what_was_needed(client: TestClient):
    body = assert_fail(client.post("/api/zones", json={"name": "x"}), 403)
    assert body["data"]["required"] == "MASTER_EDIT"
    assert body["data"]["role"] == "OPERATOR"


def test_permission_check_runs_before_db_work(client: TestClient):
    """권한 거부는 존재하지 않는 대상이어도 403 이어야 한다 (404 보다 먼저)."""
    assert_fail(client.delete("/api/nodes/N-DOES-NOT-EXIST"), 403)


def test_admin_can_do_everything(admin_client: TestClient):
    zone = data_of(admin_client.post("/api/zones", json={"name": "관리자 구역"}))
    assert data_of(admin_client.delete(f"/api/zones/{zone['zone_id']}"))["deleted"]
    assert data_of(admin_client.put("/api/config/suppression-mode", json={"mode": "MANUAL"}))["mode"] == "MANUAL"


def test_missing_role_header_defaults_to_operator(seeded, bridge):
    from app.main import app

    with TestClient(app) as c:  # 헤더 없음
        assert c.get("/api/robots").status_code == 200          # 조회 가능
        assert c.post("/api/zones", json={"name": "x"}).status_code == 403  # 마스터 편집 불가
