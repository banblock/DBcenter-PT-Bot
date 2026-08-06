"""대조(align) 판정 엔진 (B-51 ~ B-54).

판정 입력은 3가지다.
1. ``observed_state``  — 비전이 실제로 본 것
2. ``normal_state``    — 설비 마스터가 정의한 평상시 정상값
3. ``expected_state``  — 지금 걸려 있는 작업지시가 요구하는 값 (있으면 2번을 덮어쓴다)

판정 순서
---------
① DB 룰 테이블을 먼저 본다. 매칭되는 룰 중 priority 가 가장 높은 것이 이긴다.
   (룰은 재배포 없이 추가/수정할 수 있어야 하므로 코드보다 우선한다.)
② 매칭 룰이 없으면 기본 규칙으로 떨어진다 — 기대값과 관측값 비교, 수치 범위 검사.

MISMATCH 가 나오면 이벤트를 자동 생성한다 (B-54).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import crud, models
from app.enums import AlignVerdict, EquipmentCheckState, EventType, ObservedState, Severity


def _matches(condition: dict[str, Any], facts: dict[str, Any]) -> bool:
    """condition 의 모든 키가 facts 와 일치해야 매칭 (AND).

    값이 리스트면 '이 중 하나'로 해석한다. facts 에 없는 키를 요구하는 룰은
    매칭 실패로 본다 — 모르는 것을 만족한 것으로 치면 안 된다.
    """
    for key, expected in condition.items():
        if key not in facts:
            return False
        actual = facts[key]
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _range_check(equipment: models.Equipment, value: float | None) -> tuple[bool, str | None]:
    """수치 범위 검사. 기준이 없거나 값이 없으면 '판단 안 함'."""
    if value is None or (equipment.value_min is None and equipment.value_max is None):
        return True, None
    if equipment.value_min is not None and value < equipment.value_min:
        return False, f"측정값 {value}{equipment.unit or ''} 이 최소 기준 {equipment.value_min} 미만"
    if equipment.value_max is not None and value > equipment.value_max:
        return False, f"측정값 {value}{equipment.unit or ''} 이 최대 기준 {equipment.value_max} 초과"
    return True, None


def check(
    db: Session,
    *,
    equipment_id: str,
    observed_state: str,
    value: float | None = None,
    confidence: float = 1.0,
    event_id: str | None = None,
) -> models.AlignResult:
    equipment = crud.equipment.get(db, equipment_id)
    work_order = crud.equipment.active_work_order(db, equipment_id)
    expected_state = work_order.expected_state if work_order else None
    effective_expected = expected_state or equipment.normal_state

    facts: dict[str, Any] = {
        "equipment_type": equipment.type,
        "observed_state": observed_state,
        "normal_state": equipment.normal_state,
        "expected_state": effective_expected,
        "work_order_status": work_order.status if work_order else "NONE",
    }

    verdict: str = AlignVerdict.OK.value
    severity: str = Severity.INFO.value
    rule_id: str | None = None
    reason: str | None = None

    # ① DB 룰
    for rule in crud.equipment.list_rules(db, equipment_type=equipment.type, enabled_only=True):
        if _matches(rule.condition_json or {}, facts):
            verdict, severity, rule_id = rule.verdict, rule.severity, rule.rule_id
            reason = rule.message or f"룰 {rule.rule_id} 매칭"
            break

    # ② 기본 규칙
    if rule_id is None:
        if observed_state == ObservedState.UNKNOWN.value:
            verdict = AlignVerdict.UNVERIFIED.value
            severity = Severity.WARN.value
            reason = "판독 불가 — 관측 상태 UNKNOWN"
        elif effective_expected and observed_state != effective_expected:
            verdict = AlignVerdict.MISMATCH.value
            severity = Severity.WARN.value
            source = "작업지시" if expected_state else "기준값"
            reason = f"{source} {effective_expected} 인데 {observed_state} 로 관측됨"
        else:
            in_range, range_reason = _range_check(equipment, value)
            if not in_range:
                verdict = AlignVerdict.MISMATCH.value
                severity = Severity.WARN.value
                reason = range_reason
            else:
                reason = "기준값과 일치"

    # 저신뢰 관측은 확정 판정으로 올리지 않고 재점검 대상으로 남긴다.
    check_state = EquipmentCheckState.NORMAL.value
    if verdict == AlignVerdict.MISMATCH.value:
        check_state = EquipmentCheckState.MISMATCH.value
    elif verdict == AlignVerdict.UNVERIFIED.value:
        check_state = EquipmentCheckState.RECHECK.value

    result = crud.equipment.add_result(
        db,
        event_id=event_id,
        equipment_id=equipment_id,
        observed_state=observed_state,
        normal_state=equipment.normal_state,
        expected_state=effective_expected,
        value=value,
        verdict=verdict,
        severity=severity,
        rule_id=rule_id,
        reason=reason,
    )

    # ③ MISMATCH 면 이벤트 자동 생성 (B-54)
    if verdict == AlignVerdict.MISMATCH.value:
        auto_event = crud.events.create(
            db,
            source="align",
            type_=_event_type_for(equipment.type),
            confidence=confidence,
            severity=severity,
            zone_id=equipment.zone_id,
            node_id=equipment.node_id,
            memo=reason,
        )
        result.auto_created_event_id = auto_event.event_id

    crud.equipment.record_check(
        db,
        equipment_id,
        observed_state=observed_state,
        value=value,
        verdict=verdict,
        severity=severity,
        check_state=check_state,
    )
    db.flush()
    return result


def _event_type_for(equipment_type: str) -> str:
    return {
        "BREAKER": EventType.BREAKER_ABNORMAL.value,
        "LOCK": EventType.LOCK_ABNORMAL.value,
        "BATTERY_PANEL": EventType.PANEL_OUT_OF_RANGE.value,
        "VALVE": EventType.ALIGN_MISMATCH.value,
    }.get(equipment_type, EventType.ALIGN_MISMATCH.value)
