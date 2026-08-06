"""대량 이벤트 삽입 · 조회 성능 확인.

목적은 '빠른가'가 아니라 **'인덱스가 실제로 걸려 있고 스케일이 무너지지 않는가'** 다.
CI 머신 성능에 따라 절대 시간은 흔들리므로 임계값은 넉넉하게 잡되,
전체 스캔이 되면 반드시 실패할 수준으로 둔다.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import crud, models

BULK_COUNT = 10_000


@pytest.fixture(scope="module")
def bulk_db():
    """이 모듈 전용 세션 — 1만 건을 넣으므로 다른 테스트와 섞지 않는다."""
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="module")
def bulk_events(bulk_db: Session):
    """1만 건 일괄 삽입. ORM 객체 생성 없이 Core bulk insert 를 쓴다."""
    now = datetime.now(timezone.utc)
    zones = ["Z01", "Z02", "Z03"]
    types = ["FIRE", "SMOKE", "LEAK", "PERSON", "BREAKER_ABNORMAL"]
    severities = ["INFO", "WARN", "CRITICAL"]
    statuses = ["QUEUED", "ASSIGNED", "CONFIRMED", "RESOLVED", "FALSE_POSITIVE"]

    rows = [
        {
            "event_id": f"EV-PERF-{i:06d}",
            "source": "cctv",
            "camera_id": f"CAM-{i % 4:02d}",
            "type": types[i % len(types)],
            "severity": severities[i % len(severities)],
            "status": statuses[i % len(statuses)],
            "zone_id": zones[i % len(zones)],
            "node_id": f"N-{(i % 8) + 1:03d}",
            "confidence": round(0.5 + (i % 50) / 100.0, 2),
            "hit_count": 1,
            "detected_at": now - timedelta(seconds=i * 3),
        }
        for i in range(BULK_COUNT)
    ]

    started = time.perf_counter()
    bulk_db.execute(models.Event.__table__.insert(), rows)
    bulk_db.commit()
    elapsed = time.perf_counter() - started

    print(f"\n  [bulk insert] {BULK_COUNT:,}건 / {elapsed:.2f}s ({BULK_COUNT / elapsed:,.0f} rows/s)")
    # 1만 건에 10초가 걸리면 뭔가 근본적으로 잘못된 것이다 (건별 커밋 등)
    assert elapsed < 10.0, f"대량 삽입이 너무 느립니다: {elapsed:.2f}s"

    yield bulk_db

    bulk_db.execute(text("DELETE FROM tb_events WHERE event_id LIKE 'EV-PERF-%'"))
    bulk_db.commit()


def test_bulk_insert_all_rows_present(bulk_events: Session):
    count = bulk_events.execute(
        text("SELECT COUNT(*) FROM tb_events WHERE event_id LIKE 'EV-PERF-%'")
    ).scalar_one()
    assert count == BULK_COUNT


def test_paginated_query_is_fast(bulk_events: Session):
    """최신순 50건 조회 — detected_at 인덱스를 타야 한다."""
    started = time.perf_counter()
    rows, total = crud.events.query(bulk_events, page=1, size=50)
    elapsed = time.perf_counter() - started

    print(f"  [page query] {elapsed * 1000:.1f}ms (total={total:,})")
    assert len(rows) == 50
    assert total >= BULK_COUNT
    assert elapsed < 1.0, f"페이지 조회가 너무 느립니다: {elapsed:.3f}s"


def test_deep_pagination_does_not_degrade(bulk_events: Session):
    """마지막 페이지가 첫 페이지보다 극단적으로 느려지면 안 된다."""
    started = time.perf_counter()
    crud.events.query(bulk_events, page=1, size=50)
    first_page = time.perf_counter() - started

    started = time.perf_counter()
    rows, _ = crud.events.query(bulk_events, page=BULK_COUNT // 50, size=50)
    last_page = time.perf_counter() - started

    print(f"  [deep page] first={first_page * 1000:.1f}ms last={last_page * 1000:.1f}ms")
    assert rows
    assert last_page < 2.0


def test_filtered_query_uses_composite_index(bulk_events: Session):
    """zone+type+status 복합 인덱스가 쿼리 플랜에 나타나는지 직접 확인한다."""
    plan = bulk_events.execute(
        text(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM tb_events WHERE zone_id='Z01' AND type='FIRE' AND status='QUEUED'"
        )
    ).all()
    plan_text = " ".join(str(row) for row in plan)
    print(f"  [query plan] {plan_text}")
    assert "USING INDEX" in plan_text.upper(), f"인덱스를 타지 않습니다: {plan_text}"
    assert "SCAN tb_events" not in plan_text, f"전체 스캔이 발생합니다: {plan_text}"


def test_filtered_query_is_fast(bulk_events: Session):
    started = time.perf_counter()
    rows, total = crud.events.query(
        bulk_events, zone_id="Z01", type_="FIRE", status="QUEUED", page=1, size=50
    )
    elapsed = time.perf_counter() - started
    print(f"  [filtered] {elapsed * 1000:.1f}ms (matched={total:,})")
    assert elapsed < 1.0


def test_aggregation_over_bulk_data(bulk_events: Session):
    started = time.perf_counter()
    groups = crud.events.count_by(bulk_events, "zone")
    stats = crud.events.response_stats(bulk_events)
    elapsed = time.perf_counter() - started

    print(f"  [aggregate] {elapsed * 1000:.1f}ms")
    assert len(groups) >= 3
    assert stats["total"] >= BULK_COUNT
    assert 0.0 <= stats["false_positive_rate"] <= 1.0
    assert elapsed < 3.0


def test_observation_lookup_by_event_is_indexed(bulk_events: Session):
    plan = bulk_events.execute(
        text("EXPLAIN QUERY PLAN SELECT * FROM tb_observations WHERE event_id='EV-PERF-000001'")
    ).all()
    plan_text = " ".join(str(row) for row in plan).upper()
    assert "USING INDEX" in plan_text, plan_text
