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


#: CheckGate.error_state → 사람이 읽는 사유.
_GATE_ERROR_REASON = {
    0: "일치(비전 판정)",
    1: "차단기 상태 불일치(비전 판정)",
    2: "차단기 표시등을 찾지 못함",
    3: "로봇 카메라 연결/응답 없음",
}


def record_gate_check(
    db: Session,
    *,
    equipment_id: str,
    gate_state_equal: bool,
    error_state: int,
    effective_expected: str | None = None,
    robot_id: str | None = None,
) -> models.AlignResult:
    """비전 CheckGate 결과(equal/error)를 align 결과로 기록한다 (Phase 2-2, 인수인계서 결정2).

    비전이 이미 DB 기준(백엔드가 넘긴 effective_expected)과 비교해 equal 을 돌려주므로, 여기선
    그 판정을 신뢰해 verdict 를 정하고 MISMATCH 면 이벤트를 자동 생성한다. (관측 상태 자체가
    필요한 룰 기반 세분화는 CheckGate.srv 에 observed_state 필드를 추가하는 Phase 2 후속 과제.)
    """
    equipment = crud.equipment.get(db, equipment_id)

    # error_state 가 판정의 단일 기준(0=일치, 1=불일치, 2/3=관측 실패). gate_state_equal 은
    # error_state 와 일관(0↔True, 1↔False)이므로 error_state 로만 분기한다.
    if error_state == 0:
        verdict, severity, check_state = (
            AlignVerdict.OK.value, Severity.INFO.value, EquipmentCheckState.NORMAL.value,
        )
    elif error_state == 1:
        # 차단기 상태 불일치는 안전 직결 → CRITICAL.
        verdict, severity, check_state = (
            AlignVerdict.MISMATCH.value, Severity.CRITICAL.value, EquipmentCheckState.MISMATCH.value,
        )
    else:  # 2=못 찾음, 3=cam 연결/응답 없음 → 확정 못 함(재점검)
        verdict, severity, check_state = (
            AlignVerdict.UNVERIFIED.value, Severity.WARN.value, EquipmentCheckState.RECHECK.value,
        )
    reason = _GATE_ERROR_REASON.get(error_state, f"error_state={error_state}")

    result = crud.equipment.add_result(
        db,
        equipment_id=equipment_id,
        observed_state=None,  # CheckGate 는 관측 상태를 안 줌(equal 만). Phase 2 후속에 채움.
        normal_state=equipment.normal_state,
        expected_state=effective_expected,
        verdict=verdict,
        severity=severity,
        rule_id=None,
        reason=reason,
    )

    if verdict == AlignVerdict.MISMATCH.value:
        auto_event = crud.events.create(
            db,
            source="align",
            type_=_event_type_for(equipment.type),
            confidence=1.0,
            severity=severity,
            zone_id=equipment.zone_id,
            node_id=equipment.node_id,
            robot_id=robot_id,
            memo=reason,
        )
        result.auto_created_event_id = auto_event.event_id

    crud.equipment.record_check(
        db,
        equipment_id,
        observed_state=None,
        value=None,
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
