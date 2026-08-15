"""방재 제어 서비스 — 승인 없이 미실행 / 승인 후 실행 / 인터락 / E2E 검증.

요청 생성·인터락·승인·중단·이력은 이미 구현돼 있었고, 이번에 '승인 후 액추에이터 실행
시퀀스(POWER_CUTTING → SPRINKLER_ON → COMPLETED)'를 services/suppression 로 채웠다.
액추에이터는 로그 스텁(실장비/시뮬레이터 시임)이라 하드웨어 없이도 끝까지 검증된다.

체크리스트 대응:
  · B-66/67 액추에이터 발행·실행 이력   → test_execute_runs_actuator_sequence_to_completed
  · 승인 없이는 미실행                   → test_execute_refuses_without_approval / test_blocked_cannot_be_approved
  · B-68 재시도 → 실패 확정             → test_execute_escalates_to_failed_on_actuator_failure
  · 승인 콘솔 ↔ 방재 제어 ↔ 액추에이터  → test_approve_executes_to_completion_e2e
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import crud
from app.enums import SuppressionStatus
from app.errors import ApiError
from app.services import suppression as suppression_service

from .conftest import data_of


def _assert_fail(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["result"] == "FAIL", body
    assert body["code"] == code, body


def _approved_suppression(db: Session, actions: list[str]) -> object:
    return crud.system.create_suppression(
        db,
        zone_id="Z01",
        actions_json=actions,
        breaker_ids_json=["EQ-BRK-01"],
        sprinkler_duration_sec=30,
        status=SuppressionStatus.APPROVED.value,
    )


# ══════════════════════════════════════════════════════════════════════════
# 실행 시퀀스 (services/suppression) — seeded/rollback 로 격리
# ══════════════════════════════════════════════════════════════════════════
def test_execute_runs_actuator_sequence_to_completed(seeded: Session):
    """승인된 요청이 전력차단 → 스프링클러 → 완료로 진행하고 단계가 이력에 남는다."""
    dispatched: list[tuple[str, str]] = []

    def spy(action, zone_id, **params):
        dispatched.append((action, zone_id))

    sup = _approved_suppression(seeded, ["POWER_CUT", "SPRINKLER"])
    result = suppression_service.execute(seeded, sup, actuator=spy)

    assert result.status == SuppressionStatus.COMPLETED.value
    assert result.ended_at is not None
    assert dispatched == [("POWER_CUT", "Z01"), ("SPRINKLER", "Z01")]  # 액추에이터 명령 발행됨
    steps = [s["step"] for s in sup.steps_json]
    assert "POWER_CUTTING" in steps
    assert "SPRINKLER_ON" in steps
    assert steps[-1] == "COMPLETED"


def test_execute_refuses_without_approval(seeded: Session):
    """APPROVED 가 아니면 실행 자체를 거부한다 — 승인 없이는 절대 미실행."""
    sup = crud.system.create_suppression(
        seeded, zone_id="Z01", actions_json=["POWER_CUT"], status=SuppressionStatus.INTERLOCK_CHECK.value
    )
    with pytest.raises(ApiError):
        suppression_service.execute(seeded, sup)
    assert sup.status == SuppressionStatus.INTERLOCK_CHECK.value  # 상태 불변


def test_execute_escalates_to_failed_on_actuator_failure(seeded: Session):
    """액추에이터가 계속 실패하면 재시도 소진 후 FAILED 로 확정한다 (B-68)."""
    calls = {"n": 0}

    def always_fail(action, zone_id, **params):
        calls["n"] += 1
        raise suppression_service.ActuatorError("relay stuck")

    sup = _approved_suppression(seeded, ["POWER_CUT", "SPRINKLER"])
    result = suppression_service.execute(seeded, sup, actuator=always_fail)

    assert result.status == SuppressionStatus.FAILED.value
    assert calls["n"] == suppression_service.MAX_RETRIES  # 재시도 후 중단, 다음 액션은 진행 안 함
    assert sup.retry_count >= suppression_service.MAX_RETRIES
    assert sup.result_json["ok"] is False
    assert any(s["status"] == "FAILED" for s in sup.steps_json)


# ══════════════════════════════════════════════════════════════════════════
# 인터락 — 승인 없이 미실행 (HTTP)
# ══════════════════════════════════════════════════════════════════════════
def test_blocked_request_cannot_be_approved(admin_client: TestClient, seeded: Session):
    """인터락(인원 감지)에 걸린 요청은 BLOCKED 로 서고 승인이 거부된다 → 실행되지 않는다."""
    admin_client.post("/api/zones", json={"zone_id": "Z-BLK", "name": "인터락 테스트 구역"})
    # 해당 구역에 열린 PERSON 이벤트 → 인원 잔류 미확인으로 인터락이 막는다.
    crud.events.create(seeded, source="cctv", type_="PERSON", confidence=0.8, zone_id="Z-BLK")
    seeded.commit()
    try:
        requested = data_of(
            admin_client.post(
                "/api/suppression/request",
                json={
                    "zone_id": "Z-BLK", "actions": ["POWER_CUT"], "breaker_ids": ["EQ-BRK-01"],
                    "requested_by": "admin_tester", "confirm_text": "Z-BLK",
                },
            )
        )
        assert requested["status"] == "BLOCKED"
        assert any(b["code"] == "PERSON_DETECTED" for b in requested["interlock"]["blockers"])
        # 승인 시도 → 409 INTERLOCK_BLOCKED (실행으로 넘어가지 않는다)
        _assert_fail(
            admin_client.post(
                f"/api/suppression/{requested['suppression_id']}/approve", json={"approver": "manager"}
            ),
            409,
            "INTERLOCK_BLOCKED",
        )
        detail = data_of(admin_client.get(f"/api/suppression/{requested['suppression_id']}"))
        assert detail["status"] == "BLOCKED"  # 여전히 미실행
    finally:
        admin_client.delete("/api/zones/Z-BLK")


# ══════════════════════════════════════════════════════════════════════════
# 통합 — 승인 콘솔 ↔ 방재 제어 ↔ 액추에이터 E2E
# ══════════════════════════════════════════════════════════════════════════
def test_approve_executes_to_completion_e2e(admin_client: TestClient):
    """인터락 통과 → 요청 → 승인 → 액추에이터 실행 → COMPLETED 까지 한 흐름으로 검증."""
    admin_client.post("/api/zones", json={"zone_id": "Z-SAFE", "name": "E2E 안전 구역"})
    try:
        requested = data_of(
            admin_client.post(
                "/api/suppression/request",
                json={
                    "zone_id": "Z-SAFE", "actions": ["POWER_CUT", "SPRINKLER"],
                    "breaker_ids": ["EQ-BRK-01"], "sprinkler_duration_sec": 30,
                    "requested_by": "admin_tester", "confirm_text": "Z-SAFE",
                },
            )
        )
        assert requested["status"] == "INTERLOCK_CHECK", requested["interlock"]

        approved = data_of(
            admin_client.post(
                f"/api/suppression/{requested['suppression_id']}/approve", json={"approver": "manager_lee"}
            )
        )
        assert approved["status"] == "COMPLETED"  # 승인이 실행까지 이어진다

        detail = data_of(admin_client.get(f"/api/suppression/{requested['suppression_id']}"))
        steps = [s["step"] for s in detail["steps"]]
        assert steps.count("POWER_CUTTING") >= 1
        assert steps.count("SPRINKLER_ON") >= 1
        assert steps[-1] == "COMPLETED"

        # 감사 로그에 완료 이력이 남는다.
        logs = data_of(admin_client.get("/api/suppression/logs"))
        assert any(s["suppression_id"] == requested["suppression_id"] for s in logs)
    finally:
        admin_client.delete("/api/zones/Z-SAFE")
