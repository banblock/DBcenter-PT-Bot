"""설비 마스터 · 작업지시 · 대조(align) 룰/결과 CRUD."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.crud import ids
from app.enums import EquipmentCheckState
from app.errors import ApiError, E, not_found


# ══════════════════════════════════════════════════════════════════════════
# 설비 마스터
# ══════════════════════════════════════════════════════════════════════════
def create(db: Session, *, equipment_id: str, type_: str, name: str, **kwargs) -> models.Equipment:
    if db.get(models.Equipment, equipment_id) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 설비 ID 입니다: {equipment_id}")
    obj = models.Equipment(equipment_id=equipment_id, type=type_, name=name, **kwargs)
    db.add(obj)
    db.flush()
    return obj


def upsert(db: Session, *, equipment_id: str, type_: str, name: str, **kwargs) -> models.Equipment:
    """시드/일괄 등록용 — 있으면 갱신, 없으면 생성."""
    obj = db.get(models.Equipment, equipment_id)
    if obj is None:
        return create(db, equipment_id=equipment_id, type_=type_, name=name, **kwargs)
    obj.type, obj.name = type_, name
    for key, value in kwargs.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


def get(db: Session, equipment_id: str) -> models.Equipment:
    obj = db.get(models.Equipment, equipment_id)
    if obj is None:
        raise not_found(E.EQUIPMENT_NOT_FOUND, equipment_id)
    return obj


def list_all(
    db: Session, *, zone_id: str | None = None, type_: str | None = None
) -> list[models.Equipment]:
    stmt = select(models.Equipment)
    if zone_id:
        stmt = stmt.where(models.Equipment.zone_id == zone_id)
    if type_:
        stmt = stmt.where(models.Equipment.type == type_)
    return list(db.execute(stmt.order_by(models.Equipment.equipment_id)).scalars())


def update(db: Session, equipment_id: str, **fields) -> models.Equipment:
    obj = get(db, equipment_id)
    for key, value in fields.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


def delete(db: Session, equipment_id: str) -> None:
    db.delete(get(db, equipment_id))
    db.flush()


def record_check(
    db: Session,
    equipment_id: str,
    *,
    observed_state: str | None,
    value: float | None,
    verdict: str,
    severity: str,
    check_state: str = EquipmentCheckState.NORMAL.value,
) -> models.Equipment:
    """점검 결과 캐시 갱신 — 설비 점검 화면(F-63)이 조인 없이 읽는 값."""
    obj = get(db, equipment_id)
    obj.last_observed_state = observed_state
    obj.last_value = value
    obj.last_verdict = verdict
    obj.last_severity = severity
    obj.check_state = check_state
    obj.last_checked_at = datetime.now(timezone.utc)
    db.flush()
    return obj


# ══════════════════════════════════════════════════════════════════════════
# 작업지시
# ══════════════════════════════════════════════════════════════════════════
def create_work_order(db: Session, *, equipment_id: str, work_type: str, **kwargs) -> models.WorkOrder:
    get(db, equipment_id)  # FK 선검사
    wo_id = kwargs.pop("wo_id", None) or ids.next_wo_id(db)
    if db.get(models.WorkOrder, wo_id) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 작업지시 ID 입니다: {wo_id}")
    obj = models.WorkOrder(wo_id=wo_id, equipment_id=equipment_id, work_type=work_type, **kwargs)
    db.add(obj)
    db.flush()
    return obj


def get_work_order(db: Session, wo_id: str) -> models.WorkOrder:
    obj = db.get(models.WorkOrder, wo_id)
    if obj is None:
        raise not_found(E.WORK_ORDER_NOT_FOUND, wo_id)
    return obj


def list_work_orders(
    db: Session, *, equipment_id: str | None = None, status: str | None = None
) -> list[models.WorkOrder]:
    stmt = select(models.WorkOrder)
    if equipment_id:
        stmt = stmt.where(models.WorkOrder.equipment_id == equipment_id)
    if status:
        stmt = stmt.where(models.WorkOrder.status == status)
    return list(db.execute(stmt.order_by(models.WorkOrder.created_at.desc())).scalars())


def active_work_order(db: Session, equipment_id: str, at: datetime | None = None) -> models.WorkOrder | None:
    """지금 이 설비에 걸려 있는 진행 중 작업지시.

    align 판정의 3번째 입력(work_order_expected_state)이 여기서 나온다.
    시각 범위가 비어 있는 작업지시는 status 만으로 판단한다.
    """
    now = at or datetime.now(timezone.utc)
    candidates = list_work_orders(db, equipment_id=equipment_id, status="IN_PROGRESS")
    for wo in candidates:
        start, end = wo.start_ts, wo.end_ts
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if (start is None or start <= now) and (end is None or now <= end):
            return wo
    return None


def update_work_order(db: Session, wo_id: str, **fields) -> models.WorkOrder:
    obj = get_work_order(db, wo_id)
    for key, value in fields.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


# ══════════════════════════════════════════════════════════════════════════
# 대조 룰 / 결과
# ══════════════════════════════════════════════════════════════════════════
def create_rule(db: Session, *, rule_id: str, equipment_type: str, **kwargs) -> models.AlignRule:
    if db.get(models.AlignRule, rule_id) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 룰 ID 입니다: {rule_id}")
    condition = kwargs.pop("condition", None)
    obj = models.AlignRule(
        rule_id=rule_id,
        equipment_type=equipment_type,
        condition_json=condition or {},
        **kwargs,
    )
    db.add(obj)
    db.flush()
    return obj


def upsert_rule(db: Session, *, rule_id: str, equipment_type: str, **kwargs) -> models.AlignRule:
    obj = db.get(models.AlignRule, rule_id)
    if obj is None:
        return create_rule(db, rule_id=rule_id, equipment_type=equipment_type, **kwargs)
    condition = kwargs.pop("condition", None)
    if condition is not None:
        obj.condition_json = condition
    obj.equipment_type = equipment_type
    for key, value in kwargs.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


def get_rule(db: Session, rule_id: str) -> models.AlignRule:
    obj = db.get(models.AlignRule, rule_id)
    if obj is None:
        raise not_found(E.RULE_NOT_FOUND, rule_id)
    return obj


def list_rules(db: Session, *, equipment_type: str | None = None, enabled_only: bool = False) -> list[models.AlignRule]:
    stmt = select(models.AlignRule)
    if equipment_type:
        stmt = stmt.where(models.AlignRule.equipment_type == equipment_type)
    if enabled_only:
        stmt = stmt.where(models.AlignRule.enabled.is_(True))
    # priority 내림차순 — 여러 룰이 매칭되면 숫자가 큰 쪽이 이긴다.
    return list(
        db.execute(stmt.order_by(models.AlignRule.priority.desc(), models.AlignRule.rule_id)).scalars()
    )


def update_rule(db: Session, rule_id: str, **fields) -> models.AlignRule:
    obj = get_rule(db, rule_id)
    condition = fields.pop("condition", None)
    if condition is not None:
        obj.condition_json = condition
    for key, value in fields.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


def delete_rule(db: Session, rule_id: str) -> None:
    db.delete(get_rule(db, rule_id))
    db.flush()


def add_result(db: Session, **kwargs) -> models.AlignResult:
    obj = models.AlignResult(**kwargs)
    db.add(obj)
    db.flush()
    return obj


def list_results(
    db: Session,
    *,
    event_id: str | None = None,
    equipment_id: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 200,
) -> list[models.AlignResult]:
    stmt = select(models.AlignResult)
    if event_id:
        stmt = stmt.where(models.AlignResult.event_id == event_id)
    if equipment_id:
        stmt = stmt.where(models.AlignResult.equipment_id == equipment_id)
    if from_:
        stmt = stmt.where(models.AlignResult.created_at >= from_)
    if to:
        stmt = stmt.where(models.AlignResult.created_at <= to)
    return list(db.execute(stmt.order_by(models.AlignResult.created_at.desc()).limit(limit)).scalars())
