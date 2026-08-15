"""런타임 설정 KV + 우선순위/진압 레코드 CRUD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.crud import ids
from app.errors import E, not_found

SUPPRESSION_MODE_KEY = "suppression_mode"

#: 우선순위 재계산 창 문자열 → timedelta
WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


# ══════════════════════════════════════════════════════════════════════════
# 설정 KV
# ══════════════════════════════════════════════════════════════════════════
def get_config(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(models.SystemConfig, key)
    return row.value if row else default


def set_config(db: Session, key: str, value: str, *, updated_by: str | None = None) -> models.SystemConfig:
    row = db.get(models.SystemConfig, key)
    if row is None:
        row = models.SystemConfig(key=key, value=value, updated_by=updated_by)
        db.add(row)
    else:
        row.value = value
        row.updated_by = updated_by
    db.flush()
    return row


def get_suppression_mode(db: Session) -> str:
    """DB 값 우선, 없으면 .env 기본값. DB 를 우선하는 이유는 운영 중 토글이
    재기동을 넘어 유지되어야 하기 때문이다."""
    return get_config(db, SUPPRESSION_MODE_KEY, settings.suppression_mode) or "MANUAL"


# ══════════════════════════════════════════════════════════════════════════
# 우선순위
# ══════════════════════════════════════════════════════════════════════════
def window_start(window: str) -> datetime:
    return datetime.now(timezone.utc) - WINDOWS.get(window, WINDOWS["7d"])


def log_priority_change(
    db: Session,
    *,
    node_id: str,
    before: float,
    after: float,
    reason: str,
    operator: str | None = None,
) -> models.PriorityLog:
    row = models.PriorityLog(
        node_id=node_id, before_score=before, after_score=after, reason=reason, operator=operator
    )
    db.add(row)
    db.flush()
    return row


def list_priority_log(db: Session, *, node_id: str | None = None, limit: int = 50) -> list[models.PriorityLog]:
    stmt = select(models.PriorityLog)
    if node_id:
        stmt = stmt.where(models.PriorityLog.node_id == node_id)
    return list(db.execute(stmt.order_by(models.PriorityLog.created_at.desc()).limit(limit)).scalars())


# ══════════════════════════════════════════════════════════════════════════
# 진압
# ══════════════════════════════════════════════════════════════════════════
def create_suppression(db: Session, **kwargs) -> models.Suppression:
    obj = models.Suppression(suppression_id=ids.next_suppression_id(db), **kwargs)
    db.add(obj)
    db.flush()
    return obj


def get_suppression(db: Session, suppression_id: str) -> models.Suppression:
    obj = db.get(models.Suppression, suppression_id)
    if obj is None:
        raise not_found(E.SUPPRESSION_NOT_FOUND, suppression_id)
    return obj


def list_suppressions(
    db: Session, *, from_: datetime | None = None, to: datetime | None = None, limit: int = 200
) -> list[models.Suppression]:
    stmt = select(models.Suppression)
    if from_:
        stmt = stmt.where(models.Suppression.created_at >= from_)
    if to:
        stmt = stmt.where(models.Suppression.created_at <= to)
    return list(db.execute(stmt.order_by(models.Suppression.created_at.desc()).limit(limit)).scalars())


def append_step(
    db: Session, suppression: models.Suppression, *, step: str, status: str, result: str | None = None
) -> models.Suppression:
    """진행 단계 1줄 추가. JSON 컬럼은 새 리스트를 할당해야 변경이 감지된다."""
    steps = list(suppression.steps_json or [])
    steps.append(
        {
            "step": step,
            "status": status,
            "at": datetime.now(timezone.utc).isoformat(),
            **({"result": result} if result else {}),
        }
    )
    suppression.steps_json = steps
    db.flush()
    return suppression
