"""식별자 채번.

사람이 로그에서 눈으로 읽어야 하는 ID 라 UUID 를 쓰지 않는다.
* 순번형(`Z01`, `N-001`, `R-01`) — 마스터 데이터. 개수가 적고 순서가 의미 있다.
* 날짜형(`EV-20260806-0012`, `MSN-20260806-3c9f`) — 이력 데이터. 언제 것인지가 중요하다.

동시 요청에서 같은 번호가 나오지 않도록 채번은 항상 같은 트랜잭션 안에서
`SELECT MAX` 후 INSERT 하고, PK 충돌은 호출부가 409 로 되돌린다.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models


def _next_seq(db: Session, column, pattern: str) -> int:
    """`pattern` 에 매칭되는 기존 ID 들에서 최대 순번 + 1 을 구한다."""
    rows = db.execute(select(column)).scalars().all()
    rx = re.compile(pattern)
    best = 0
    for value in rows:
        m = rx.fullmatch(str(value))
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def next_zone_id(db: Session) -> str:
    seq = _next_seq(db, models.Zone.zone_id, r"Z(\d+)")
    return f"Z{seq:02d}"


def next_node_id(db: Session) -> str:
    seq = _next_seq(db, models.Node.node_id, r"N-(\d+)")
    return f"N-{seq:03d}"


def next_route_id(db: Session) -> str:
    seq = _next_seq(db, models.Route.route_id, r"R-(\d+)")
    return f"R-{seq:02d}"


def next_wo_id(db: Session) -> str:
    seq = _next_seq(db, models.WorkOrder.wo_id, r"WO-(\d+)")
    return f"WO-{seq:04d}"


def next_map_id() -> str:
    return f"MAP-{today_str()}-{uuid.uuid4().hex[:4]}"


def next_mission_id() -> str:
    return f"MSN-{today_str()}-{uuid.uuid4().hex[:4]}"


def next_command_id() -> str:
    return f"CMD-{today_str()}-{uuid.uuid4().hex[:4]}"


def next_event_id(db: Session) -> str:
    """`EV-YYYYMMDD-NNNN` — 하루 단위로 순번이 리셋된다."""
    prefix = f"EV-{today_str()}-"
    count = db.execute(
        select(func.count()).select_from(models.Event).where(models.Event.event_id.startswith(prefix))
    ).scalar_one()
    return f"{prefix}{count + 1:04d}"


def next_suppression_id(db: Session) -> str:
    seq = _next_seq(db, models.Suppression.suppression_id, r"SUP-(\d+)")
    return f"SUP-{seq:04d}"
