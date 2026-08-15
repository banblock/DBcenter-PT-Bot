"""순찰 우선순위 서비스 — 반복 이상 → 순찰빈도 상향 & 스케줄러 재배분 연동 검증.

점수 산식·재계산·visit_multiplier·reorder 로직은 이미 구현되어 있으나(priority.py),
'반복 이상 발생 → 순찰 빈도 상향'과 '우선순위 → 스케줄러 재배분 연동'을 끝까지
확인하는 테스트가 없어 이 파일에서 보강한다.

체크리스트 대응:
  · B-56/57 카운팅·가중치 산정   → test_repeated_anomaly_raises_visit_frequency
  · B-59 고위험 노드 중복 삽입    → reorder 검증
  · B-61 재계산 반영·이력         → test_recalculate_persists_and_logs
  · 우선순위 ↔ 스케줄러 재배분    → test_priority_feeds_scheduler_reordering
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import crud
from app.services import priority

from .conftest import data_of


def _pile_events(db: Session, node_id: str, zone_id: str, count: int, type_: str) -> None:
    """한 노드에 이상 이벤트를 쌓는다 (dedup 을 안 타는 CRUD 직접 생성)."""
    for _ in range(count):
        crud.events.create(
            db, source="cctv", type_=type_, confidence=0.95, zone_id=zone_id, node_id=node_id
        )
    db.flush()


def _temp_node(db: Session, node_id: str, x: float) -> None:
    """이 테스트 전용 새 노드. 다른 테스트가 쌓아 둔 이벤트와 완전히 격리된다."""
    crud.patrol.create_node(db, node_id=node_id, map_id="MAP-DEMO-1F", zone_id="Z01", name=node_id, x=x, y=x)


# ══════════════════════════════════════════════════════════════════════════
# 반복 이상 → 순찰 빈도 상향 (B-56/57/59)   ── seeded/rollback 로 완전 격리
# ══════════════════════════════════════════════════════════════════════════
def test_repeated_anomaly_raises_visit_frequency(seeded: Session):
    """같은 노드에 이상이 반복되면 점수·순찰 빈도(visit_multiplier)가 함께 오른다.

    다른 테스트(대량 이벤트)가 시드 노드를 포화시켜도 영향받지 않도록, 이 테스트만의
    새 노드 두 개(HOT/COLD)를 만들어 그 위에서 before/after 를 비교한다.
    """
    _temp_node(seeded, "N-PRIO-HOT", 1.0)
    _temp_node(seeded, "N-PRIO-COLD", 2.0)
    seeded.flush()

    before = {r["node_id"]: r for r in priority.compute(seeded, "7d")}
    _pile_events(seeded, "N-PRIO-HOT", "Z01", count=8, type_="FIRE")  # 반복 이상(고심각도)
    after = {r["node_id"]: r for r in priority.compute(seeded, "7d")}

    hot_before, hot_after = before["N-PRIO-HOT"], after["N-PRIO-HOT"]
    assert hot_after["event_count"] >= 8
    assert hot_after["score"] > hot_before["score"]
    assert hot_after["visit_multiplier"] >= hot_before["visit_multiplier"]
    assert hot_after["visit_multiplier"] >= 2  # 고위험이 되면 1주기당 방문이 늘어난다
    assert hot_after["score"] > after["N-PRIO-COLD"]["score"]  # 이상 쌓인 쪽이 더 높다

    # B-59: 재정렬 시 고위험 노드가 경로에 중복 삽입돼 감시 밀도가 실제로 올라간다.
    scores = {nid: row["score"] for nid, row in after.items()}
    reordered = priority.reorder_nodes(["N-PRIO-COLD", "N-PRIO-HOT"], scores)
    assert reordered[0] == "N-PRIO-HOT"
    assert reordered.count("N-PRIO-HOT") >= 2
    assert reordered.count("N-PRIO-HOT") > reordered.count("N-PRIO-COLD")


def test_recalculate_persists_and_logs(seeded: Session):
    """재계산이 노드 점수·visit_multiplier 를 DB 에 반영하고 변경 이력을 남긴다 (B-61)."""
    _pile_events(seeded, "N-004", "Z01", count=6, type_="BREAKER_ABNORMAL")

    results = priority.recalculate(seeded, "7d", operator="tester")
    persisted = crud.patrol.get_node(seeded, "N-004")
    expected = next(r for r in results if r["node_id"] == "N-004")
    assert persisted.priority_score == expected["score"]
    assert persisted.visit_multiplier == expected["visit_multiplier"]

    logs = crud.system.list_priority_log(seeded, node_id="N-004", limit=10)
    assert logs, "점수 변경이 이력에 남아야 한다 (설명 가능성)"


# ══════════════════════════════════════════════════════════════════════════
# 우선순위 ↔ 스케줄러 재배분 연동 (통합)
# ══════════════════════════════════════════════════════════════════════════
def test_priority_score_feeds_scheduler_reordering(seeded: Session, admin_client: TestClient):
    """우선순위 점수가 높은 노드를 apply_priority 순찰이 경로 맨 앞에서 먼저 돈다.

    스케줄러(start_patrol)는 노드의 저장된 priority_score 를 읽어 정렬하므로, 점수를
    결정적으로 세팅해 '우선순위 → 재배분' 연동만 정확히 검증한다(재계산 노이즈 배제).
    """
    node = crud.patrol.get_node(seeded, "N-004")
    original = node.priority_score
    node.priority_score = 1.0  # 최고 점수로 지정
    seeded.commit()
    try:
        started = data_of(
            admin_client.post(
                "/api/patrol/start",
                json={"route_id": "R-01", "robot_ids": ["amr_1"], "mode": "ONCE", "apply_priority": True},
            )
        )
        try:
            nodes = started["assigned"][0]["nodes"]
            assert nodes[0] == "N-004", f"고우선 노드가 경로 맨 앞에 와야 한다: {nodes}"
        finally:
            for mid in started["mission_ids"]:
                admin_client.post(f"/api/patrol/{mid}/cancel", json={"reason": "cleanup"})
    finally:
        node.priority_score = original  # 상태 원복
        seeded.commit()


def test_manual_weight_raises_score_via_api(admin_client: TestClient):
    """수동 가중치를 올리면 재계산이 즉시 반영돼 노드 점수가 오른다 (B-60/61)."""
    result = data_of(
        admin_client.put("/api/priority/nodes/N-004", json={"manual_weight": 1.0, "operator": "tester"})
    )
    try:
        assert result["node_id"] == "N-004"
        assert result["after_score"] >= result["before_score"]  # 가중치가 점수에 반영된다
    finally:
        admin_client.put("/api/priority/nodes/N-004", json={"manual_weight": 0.0, "operator": "tester"})
