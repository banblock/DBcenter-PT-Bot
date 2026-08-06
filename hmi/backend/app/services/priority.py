"""순찰 우선순위 점수 (B-56 ~ B-62).

점수 산식 (체크리스트 §2-8)::

    score = w1·norm(최근발생빈도) + w2·norm(최대심각도)
          + w3·norm(마지막순찰경과시간) + w4·manual_weight
    기본값: w1=0.4, w2=0.3, w3=0.2, w4=0.1

정규화는 '해당 창(window) 안 노드들 중 최대값 대비 비율'로 한다. 절대 기준을 두면
현장마다 스케일이 달라 튜닝이 불가능해진다. 모든 노드가 0이면 그 항은 0으로 둔다.

시간 감쇠(B-58): 오래된 이벤트일수록 가중치를 낮춘다 — ``exp(-λ·days)``.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings
from app.enums import Severity

#: 심각도 → 점수. CRITICAL 이 WARN 의 2배 이상이어야 위험 노드가 확실히 앞으로 온다.
SEVERITY_SCORE = {Severity.INFO.value: 1.0, Severity.WARN.value: 3.0, Severity.CRITICAL.value: 7.0}

#: 점수 구간 → 1회 순찰에서 방문할 횟수 (B-59 의 '고위험 노드 중복 삽입')
def visit_multiplier_for(score: float) -> int:
    if score >= 0.75:
        return 3
    if score >= 0.45:
        return 2
    return 1


def _normalize(value: float, maximum: float) -> float:
    return value / maximum if maximum > 0 else 0.0


def compute(db: Session, window: str = "7d") -> list[dict]:
    """전 노드 점수를 계산해 rank 순으로 돌려준다. DB 는 아직 건드리지 않는다."""
    since = crud.system.window_start(window)
    now = datetime.now(timezone.utc)
    weights = settings.priority_weights
    nodes = crud.patrol.list_nodes(db)
    if not nodes:
        return []

    raw: list[dict] = []
    for node in nodes:
        events = list(
            db.execute(
                select(models.Event).where(
                    models.Event.node_id == node.node_id, models.Event.detected_at >= since
                )
            ).scalars()
        )

        # 빈도 — 시간 감쇠 적용 (최근 것이 더 무겁다)
        frequency = 0.0
        for event in events:
            detected = event.detected_at
            if detected.tzinfo is None:
                detected = detected.replace(tzinfo=timezone.utc)
            days = max((now - detected).total_seconds() / 86400.0, 0.0)
            frequency += math.exp(-settings.priority_decay_lambda * days)

        max_severity = max(
            (SEVERITY_SCORE.get(e.severity, 0.0) for e in events), default=0.0
        )
        max_severity_label = None
        if events:
            max_severity_label = max(events, key=lambda e: SEVERITY_SCORE.get(e.severity, 0.0)).severity

        last = node.last_visited_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        # 한 번도 안 간 노드는 '가장 오래된' 것으로 취급해야 순번이 돌아온다.
        hours_since = (now - last).total_seconds() / 3600.0 if last else None

        raw.append(
            {
                "node": node,
                "frequency": frequency,
                "severity": max_severity,
                "severity_label": max_severity_label,
                "hours_since": hours_since,
                "event_count": len(events),
            }
        )

    max_freq = max((r["frequency"] for r in raw), default=0.0)
    max_sev = max((r["severity"] for r in raw), default=0.0)
    observed_hours = [r["hours_since"] for r in raw if r["hours_since"] is not None]
    max_hours = max(observed_hours, default=0.0)

    results: list[dict] = []
    for row in raw:
        node: models.Node = row["node"]
        hours = row["hours_since"]
        # 미방문 노드는 정규화 항을 1.0(최대)으로 준다.
        hours_norm = 1.0 if hours is None else _normalize(hours, max_hours)
        score = (
            weights.get("w1", 0.4) * _normalize(row["frequency"], max_freq)
            + weights.get("w2", 0.3) * _normalize(row["severity"], max_sev)
            + weights.get("w3", 0.2) * hours_norm
            + weights.get("w4", 0.1) * node.manual_weight
        )
        score = round(min(score, 1.0), 4)
        results.append(
            {
                "node_id": node.node_id,
                "name": node.name,
                "zone_id": node.zone_id,
                "event_count": row["event_count"],
                "max_severity": row["severity_label"],
                "hours_since_last_patrol": round(hours, 2) if hours is not None else None,
                "manual_weight": node.manual_weight,
                "score": score,
                "visit_multiplier": visit_multiplier_for(score),
            }
        )

    results.sort(key=lambda r: -r["score"])
    for index, row in enumerate(results, start=1):
        row["rank"] = index
    return results


def recalculate(db: Session, window: str = "7d", operator: str | None = None) -> list[dict]:
    """점수를 계산해 DB 에 반영하고 변경 이력을 남긴다 (B-61)."""
    results = compute(db, window)
    for row in results:
        node = crud.patrol.get_node(db, row["node_id"])
        before = node.priority_score
        after = row["score"]
        if abs(before - after) > 1e-6:
            crud.system.log_priority_change(
                db,
                node_id=node.node_id,
                before=before,
                after=after,
                reason=f"자동 재계산 (window={window})",
                operator=operator,
            )
        node.priority_score = after
        node.visit_multiplier = row["visit_multiplier"]
    db.flush()
    return results


def reorder_nodes(node_order: list[str], scores: dict[str, float]) -> list[str]:
    """점수 기반 순찰 노드 재정렬 (B-59).

    고위험 노드는 `visit_multiplier` 만큼 경로에 중복 삽입한다. 단순히 앞으로
    당기기만 하면 '한 번 더 본다'가 아니라 '먼저 본다'가 되어, 순찰 1주기 동안의
    감시 밀도는 그대로다.
    """
    reordered: list[str] = []
    for node_id in sorted(node_order, key=lambda n: -scores.get(n, 0.0)):
        reordered.extend([node_id] * visit_multiplier_for(scores.get(node_id, 0.0)))
    return reordered
