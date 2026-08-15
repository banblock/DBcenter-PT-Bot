"""이상감지 판정 서비스 — 검출 수신 → 다수결 판정 → 대조/등급 분류 E2E 검증.

detect/dedup/observation/verdict/align 로직 자체는 이미 구현되어 있으나(라우터·엔진),
'등급별 판정 정확도 / 오탐·미탐 케이스 / Detection ↔ 판정 서비스 연동'을 HTTP 경로에서
끝까지 확인하는 통합 테스트가 없어 이 파일에서 보강한다.

체크리스트 대응:
  · B-31/32 검출 수신·dedup 병합   → test_detect_*, test_dedup_*
  · B-42 다수결 종합 판정          → test_verdict_confirmed / _false_positive / _unverified
  · B-51~54 대조·등급 분류         → test_align_check_*
  · Detection ↔ 판정 서비스 연동   → test_pipeline_detect_to_confirmed_verdict
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import data_of


def _detect(client: TestClient, type_: str, zone_id: str, confidence: float = 0.82) -> dict:
    """탐지 1건 등록. 테스트마다 (type, zone) 을 고유하게 줘서 dedup 병합을 피한다.

    이 파일은 알파벳순으로 test_smoke(FIRE·Z01)·test_errors(LEAK·Z03) 보다 먼저 돌므로,
    그 두 조합만 피하면 뒤 테스트의 dedup 대상이 되지 않는다.
    """
    return data_of(
        client.post(
            "/api/events/detect",
            json={
                "source": "cctv",
                "camera_id": "CAM-02",
                "type": type_,
                "confidence": confidence,
                "zone_id": zone_id,
                "node_id": "N-005",
                "x": 11.0,
                "y": 2.0,
            },
        )
    )


def _observe(client: TestClient, event_id: str, angle: int, state: str, conf: float) -> None:
    data_of(
        client.post(
            f"/api/events/{event_id}/observation",
            json={"robot_id": "amr_2", "angle_idx": angle, "observed_state": state, "confidence": conf},
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 검출 수신 · dedup (B-31 · B-32)
# ══════════════════════════════════════════════════════════════════════════
def test_detect_creates_queued_event_with_severity(client: TestClient):
    result = _detect(client, "SMOKE", "Z01", confidence=0.9)
    assert result["event_id"].startswith("EV-")
    assert result["merged_into"] is None
    assert result["severity"]  # 타입 기반 기본 심각도가 매겨진다


def test_dedup_merges_same_zone_type_within_window(client: TestClient):
    first = _detect(client, "PERSON", "Z03", confidence=0.7)
    second = _detect(client, "PERSON", "Z03", confidence=0.9)
    assert second["merged_into"] == first["event_id"]
    assert second["event_id"] == first["event_id"]
    assert second["hit_count"] >= 2


# ══════════════════════════════════════════════════════════════════════════
# 다수결 종합 판정 — 등급별 정확도 & 오탐/미탐 (B-42)
# ══════════════════════════════════════════════════════════════════════════
def test_verdict_confirmed_when_strong_majority_agrees(client: TestClient):
    """conf ≥ 임계 관측이 2개 이상 같은 라벨(NORMAL 아님) → CONFIRMED (실경보)."""
    event_id = _detect(client, "FIRE", "Z02", confidence=0.9)["event_id"]
    _observe(client, event_id, 1, "ABNORMAL", 0.86)
    _observe(client, event_id, 2, "ABNORMAL", 0.81)
    _observe(client, event_id, 3, "UNKNOWN", 0.20)

    result = data_of(client.post(f"/api/events/{event_id}/verdict", json={"auto": True}))
    assert result["verdict"] == "CONFIRMED"
    assert result["status"] == "CONFIRMED"
    assert result["final_confidence"] >= 0.75
    assert len(result["votes"]) == 3


def test_verdict_false_positive_when_majority_is_normal(client: TestClient):
    """다수 관측이 NORMAL 로 강하게 일치 → FALSE_POSITIVE (오탐)."""
    event_id = _detect(client, "SMOKE", "Z02", confidence=0.8)["event_id"]
    _observe(client, event_id, 1, "NORMAL", 0.88)
    _observe(client, event_id, 2, "NORMAL", 0.9)

    result = data_of(client.post(f"/api/events/{event_id}/verdict", json={"auto": True}))
    assert result["verdict"] == "FALSE_POSITIVE"
    assert result["status"] == "FALSE_POSITIVE"


def test_verdict_unverified_when_no_strong_consensus(client: TestClient):
    """강한 합의가 없으면(저신뢰 or 분열) 임의로 확정하지 않고 UNVERIFIED (미확정)."""
    event_id = _detect(client, "PERSON", "Z02", confidence=0.6)["event_id"]
    _observe(client, event_id, 1, "ABNORMAL", 0.40)  # 임계 미만
    _observe(client, event_id, 2, "NORMAL", 0.85)  # 강하지만 단독

    result = data_of(client.post(f"/api/events/{event_id}/verdict", json={"auto": True}))
    assert result["verdict"] == "UNVERIFIED"
    assert result["status"] == "VERIFYING"  # 종결하지 않고 검증 상태로 남긴다


def test_manual_verdict_overrides_auto(client: TestClient):
    """auto=false 면 사람이 준 판정을 그대로 확정한다 (검수 오버라이드)."""
    event_id = _detect(client, "INTRUSION", "Z01", confidence=0.9)["event_id"]
    result = data_of(
        client.post(f"/api/events/{event_id}/verdict", json={"auto": False, "verdict": "FALSE_POSITIVE"})
    )
    assert result["verdict"] == "FALSE_POSITIVE"


# ══════════════════════════════════════════════════════════════════════════
# 설비 기준값 대조 · 등급 분류 (B-51 ~ B-54)
# ══════════════════════════════════════════════════════════════════════════
def test_align_check_mismatch_grades_critical_and_creates_event(client: TestClient):
    """관측이 기준을 벗어나면 MISMATCH + 룰에 따른 등급 + 이벤트 자동 생성 (B-54)."""
    result = data_of(
        client.post(
            "/api/align/check",
            json={"equipment_id": "EQ-PANEL-01", "observed_state": "ABNORMAL", "confidence": 0.9},
        )
    )
    assert result["verdict"] == "MISMATCH"
    assert result["severity"] == "CRITICAL"  # RULE-003
    assert result["auto_created_event_id"], "MISMATCH 는 이벤트를 자동 생성해야 한다"

    # 자동 생성된 이벤트가 실제로 이력에 조회돼야 한다 (연동 확인).
    detail = data_of(client.get(f"/api/events/{result['auto_created_event_id']}"))
    assert detail["event"]["source"] == "align"


def test_align_check_ok_when_matches_baseline(client: TestClient):
    """기준값과 일치하면 OK, 이벤트를 만들지 않는다."""
    result = data_of(
        client.post(
            "/api/align/check",
            json={"equipment_id": "EQ-BRK-01", "observed_state": "ON", "value": 23.5},
        )
    )
    assert result["verdict"] == "OK"
    assert result["auto_created_event_id"] is None


# ══════════════════════════════════════════════════════════════════════════
# 통합 — Detection 노드 ↔ 판정 서비스
# ══════════════════════════════════════════════════════════════════════════
def test_pipeline_detect_to_confirmed_verdict(client: TestClient):
    """검출 수신 → 다각도 관측 → 다수결 확정까지 상태가 일관되게 흐른다."""
    event_id = _detect(client, "FIRE", "Z03", confidence=0.88)["event_id"]

    # 관측 업로드가 이벤트를 VERIFYING 으로 올린다.
    _observe(client, event_id, 1, "ABNORMAL", 0.9)
    mid = data_of(client.get(f"/api/events/{event_id}"))
    assert mid["event"]["status"] == "VERIFYING"

    _observe(client, event_id, 2, "ABNORMAL", 0.82)
    data_of(client.post(f"/api/events/{event_id}/verdict", json={"auto": True}))

    final = data_of(client.get(f"/api/events/{event_id}"))
    assert final["event"]["status"] == "CONFIRMED"
    assert len(final["observations"]) == 2
    stages = [t["stage"] for t in final["timeline"]]
    assert "CONFIRMED" in stages  # 판정 단계가 타임라인에 기록된다
