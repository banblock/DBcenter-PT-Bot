"""§6 설비 마스터 · 작업지시 · align 대조."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.connection_manager import manager
from app.database import get_db
from app.enums import WsMessageType
from app.responses import ok
from app.schemas import (
    AlignCheckIn,
    AlignResultOut,
    AlignRuleIn,
    AlignRuleOut,
    AlignRuleUpdate,
    EquipmentIn,
    EquipmentOut,
    EquipmentUpdate,
    WorkOrderIn,
    WorkOrderOut,
)
from app.security import Permission, require
from app.services import align_engine

DbDep = Annotated[Session, Depends(get_db)]
MasterEdit = Depends(require(Permission.MASTER_EDIT))

equipment_router = APIRouter(prefix="/equipment", tags=["설비 마스터"])
work_order_router = APIRouter(prefix="/work-orders", tags=["작업지시"])
align_router = APIRouter(prefix="/align", tags=["대조 판정"])


# ══════════════════════════════════════════════════════════════════════════
# 설비 마스터
# ══════════════════════════════════════════════════════════════════════════
@equipment_router.post("", summary="설비 마스터 등록", dependencies=[MasterEdit], status_code=201)
def create_equipment(body: EquipmentIn, db: DbDep):
    obj = crud.equipment.create(
        db,
        equipment_id=body.equipment_id,
        type_=body.type.value,
        name=body.name,
        node_id=body.node_id,
        zone_id=body.zone_id,
        normal_state=body.normal_state.value if body.normal_state else None,
        value_min=body.value_min,
        value_max=body.value_max,
        unit=body.unit,
    )
    db.commit()
    return ok({"equipment_id": obj.equipment_id}, status_code=201)


@equipment_router.get("", summary="설비 목록")
def list_equipment(
    db: DbDep,
    zone_id: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,  # noqa: A002 - 명세서 파라미터명
):
    rows = crud.equipment.list_all(db, zone_id=zone_id, type_=type)
    return ok([EquipmentOut.model_validate(e).model_dump() for e in rows])


@equipment_router.get("/{equipment_id}", summary="설비 상세")
def get_equipment(equipment_id: str, db: DbDep):
    return ok(EquipmentOut.model_validate(crud.equipment.get(db, equipment_id)).model_dump())


@equipment_router.put("/{equipment_id}", summary="설비 수정", dependencies=[MasterEdit])
def update_equipment(equipment_id: str, body: EquipmentUpdate, db: DbDep):
    fields = body.model_dump(exclude_unset=True)
    if fields.get("normal_state") is not None:
        fields["normal_state"] = fields["normal_state"].value
    obj = crud.equipment.update(db, equipment_id, **fields)
    db.commit()
    return ok({"equipment_id": obj.equipment_id})


@equipment_router.delete("/{equipment_id}", summary="설비 삭제", dependencies=[MasterEdit])
def delete_equipment(equipment_id: str, db: DbDep):
    crud.equipment.delete(db, equipment_id)
    db.commit()
    return ok({"equipment_id": equipment_id, "deleted": True})


# ══════════════════════════════════════════════════════════════════════════
# 작업지시
# ══════════════════════════════════════════════════════════════════════════
@work_order_router.post("", summary="작업지시 등록", dependencies=[MasterEdit], status_code=201)
def create_work_order(body: WorkOrderIn, db: DbDep):
    obj = crud.equipment.create_work_order(
        db,
        wo_id=body.wo_id,
        equipment_id=body.equipment_id,
        work_type=body.work_type,
        expected_state=body.expected_state.value if body.expected_state else None,
        status=body.status,
        start_ts=body.start_ts,
        end_ts=body.end_ts,
        requested_by=body.requested_by,
    )
    db.commit()
    return ok({"wo_id": obj.wo_id, "status": obj.status}, status_code=201)


@work_order_router.get("", summary="작업지시 목록")
def list_work_orders(
    db: DbDep,
    equipment_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
):
    rows = crud.equipment.list_work_orders(db, equipment_id=equipment_id, status=status)
    return ok([WorkOrderOut.model_validate(w).model_dump() for w in rows])


@work_order_router.put("/{wo_id}", summary="작업지시 수정", dependencies=[MasterEdit])
def update_work_order(wo_id: str, body: WorkOrderIn, db: DbDep):
    fields = body.model_dump(exclude_unset=True, exclude={"wo_id", "equipment_id"})
    if fields.get("expected_state") is not None:
        fields["expected_state"] = fields["expected_state"].value
    obj = crud.equipment.update_work_order(db, wo_id, **fields)
    db.commit()
    return ok({"wo_id": obj.wo_id, "status": obj.status})


# ══════════════════════════════════════════════════════════════════════════
# 대조 판정
# ══════════════════════════════════════════════════════════════════════════
@align_router.post("/check", summary="대조 판정 실행")
async def align_check(body: AlignCheckIn, db: DbDep):
    result = align_engine.check(
        db,
        equipment_id=body.equipment_id,
        observed_state=body.observed_state.value,
        value=body.value,
        confidence=body.confidence,
        event_id=body.event_id,
    )
    work_order = crud.equipment.active_work_order(db, body.equipment_id)
    db.commit()

    payload = AlignResultOut.model_validate(result).model_dump()
    payload["work_order_expected_state"] = work_order.expected_state if work_order else None
    await manager.publish_async(WsMessageType.ALIGN_RESULT.value, payload)
    return ok(payload)


@align_router.get("/results", summary="대조 결과 조회")
def align_results(
    db: DbDep,
    event_id: Annotated[str | None, Query()] = None,
    equipment_id: Annotated[str | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
):
    rows = crud.equipment.list_results(
        db, event_id=event_id, equipment_id=equipment_id, from_=from_, to=to
    )
    return ok([AlignResultOut.model_validate(r).model_dump() for r in rows])


@align_router.post("/rules", summary="대조 룰 등록", dependencies=[MasterEdit], status_code=201)
def create_rule(body: AlignRuleIn, db: DbDep):
    rule = crud.equipment.create_rule(
        db,
        rule_id=body.rule_id,
        equipment_type=body.equipment_type.value,
        condition=body.condition,
        verdict=body.verdict.value,
        severity=body.severity.value,
        message=body.message,
        priority=body.priority,
        enabled=body.enabled,
    )
    db.commit()
    return ok({"rule_id": rule.rule_id}, status_code=201)


@align_router.get("/rules", summary="대조 룰 목록")
def list_rules(db: DbDep, equipment_type: Annotated[str | None, Query()] = None):
    rows = crud.equipment.list_rules(db, equipment_type=equipment_type)
    return ok([AlignRuleOut.model_validate(r).model_dump(by_alias=False) for r in rows])


@align_router.put("/rules/{rule_id}", summary="대조 룰 수정", dependencies=[MasterEdit])
def update_rule(rule_id: str, body: AlignRuleUpdate, db: DbDep):
    fields = body.model_dump(exclude_unset=True)
    for key in ("verdict", "severity"):
        if fields.get(key) is not None:
            fields[key] = fields[key].value
    rule = crud.equipment.update_rule(db, rule_id, **fields)
    db.commit()
    return ok({"rule_id": rule.rule_id})


@align_router.delete("/rules/{rule_id}", summary="대조 룰 삭제", dependencies=[MasterEdit])
def delete_rule(rule_id: str, db: DbDep):
    crud.equipment.delete_rule(db, rule_id)
    db.commit()
    return ok({"rule_id": rule_id, "deleted": True})
