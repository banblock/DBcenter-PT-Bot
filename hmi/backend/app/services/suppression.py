"""화재진압 실행 시퀀스 — 승인된 요청을 액추에이터로 실행 (B-66~B-68 · B-70/71).

설계 메모
--------
실제 액추에이터(차단기 릴레이·스프링클러 밸브)는 이 저장소에 없다. ROS 명령이
``NullBridge`` 로 시작하듯, 여기서도 ``dispatch_actuator`` 를 '로그만 남기는 스텁'으로
두고 실기/시뮬레이터 연동 시 이 함수(또는 주입 인자)만 교체한다 — 체크리스트 B-66 이
'실장비 or 시뮬레이터'를 명시한 그대로다. 라우터 코드는 바뀌지 않는다.

지켜야 할 안전 규칙
  · 실행은 오직 ``APPROVED`` 상태에서만 시작한다 (승인 없이는 절대 미실행).
  · 액추에이터가 실패하면 최대 ``MAX_RETRIES`` 회 재시도하고, 그래도 실패하면 ``FAILED``
    로 확정한다 (B-68 재시도 → 에스컬레이션). 스프링클러를 어정쩡한 상태로 두지 않는다.
  · 모든 단계 전이를 ``steps_json`` 에 한 줄씩 남긴다 (B-70 감사 로그: who/when/what/result).
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from app import crud, models
from app.enums import SuppressionStatus
from app.errors import ApiError, E
from app.logging_config import get_logger
from app.models import utcnow

log = get_logger("suppression")

#: 액추에이터 실패 시 재시도 횟수 (B-68).
MAX_RETRIES = 3

#: actions_json 항목 → 진행 상태(SuppressionStatus).
_ACTION_STEP: dict[str, str] = {
    "POWER_CUT": SuppressionStatus.POWER_CUTTING.value,
    "SPRINKLER": SuppressionStatus.SPRINKLER_ON.value,
}


class ActuatorError(RuntimeError):
    """액추에이터(차단기/스프링클러) 명령 실패 — 재시도/에스컬레이션의 트리거."""


#: 액추에이터 명령 발행 시그니처. 테스트/실기에서 주입 교체할 수 있게 타입만 고정한다.
Actuator = Callable[..., None]


def dispatch_actuator(action: str, zone_id: str, **params) -> None:
    """액추에이터 명령 발행 **스텁** — 실제 하드웨어 없이 로그만 남긴다 (B-66).

    실기 연동 시 이 함수를 차단기 릴레이/스프링클러 밸브 드라이버로 교체하거나,
    ``execute(..., actuator=...)`` 로 시뮬레이터를 주입한다.
    """
    log.info("[actuator] %s zone=%s %s", action, zone_id, params)


def execute(
    db: Session, suppression: models.Suppression, *, actuator: Actuator = dispatch_actuator
) -> models.Suppression:
    """승인된 진압을 실행한다: 전력 차단 → 스프링클러 → 완료.

    호출부는 ``APPROVED`` 상태에서만 부른다. 방어적으로 여기서도 상태를 확인해
    '승인 없이는 미실행' 불변식을 코드로 강제한다. 커밋은 호출부 몫이다.
    """
    if suppression.status != SuppressionStatus.APPROVED.value:
        raise ApiError(
            E.CONFLICT,
            f"승인(APPROVED)된 요청만 실행할 수 있습니다: 현재 {suppression.status}",
            data={"suppression_id": suppression.suppression_id, "status": suppression.status},
        )

    for action in suppression.actions_json or []:
        step = _ACTION_STEP.get(action)
        if step is None:
            continue  # 알 수 없는 액션은 건너뛴다 (스키마가 이미 걸러주지만 방어적으로)
        suppression.status = step
        crud.system.append_step(db, suppression, step=step, status="RUNNING")
        if not _dispatch_with_retry(db, suppression, action, step, actuator):
            return suppression  # 재시도 소진 → 이미 FAILED 로 확정됨
        crud.system.append_step(db, suppression, step=step, status="DONE", result="OK")

    suppression.status = SuppressionStatus.COMPLETED.value
    suppression.ended_at = utcnow()
    suppression.result_json = {"ok": True, "actions": list(suppression.actions_json or [])}
    crud.system.append_step(db, suppression, step=SuppressionStatus.COMPLETED.value, status="DONE")
    log.info("진압 완료(COMPLETED) — %s zone=%s", suppression.suppression_id, suppression.zone_id)
    return suppression


def _dispatch_with_retry(
    db: Session, suppression: models.Suppression, action: str, step: str, actuator: Actuator
) -> bool:
    """한 액추에이터 명령을 최대 MAX_RETRIES 회 시도한다. 실패 확정 시 FAILED 로 내린다."""
    params = (
        {"breaker_ids": list(suppression.breaker_ids_json or [])}
        if action == "POWER_CUT"
        else {"duration_sec": suppression.sprinkler_duration_sec}
    )
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            actuator(action, suppression.zone_id, **params)
            return True
        except ActuatorError as exc:
            last_error = exc
            suppression.retry_count += 1
            crud.system.append_step(
                db, suppression, step=step, status="RETRY", result=f"{attempt}회 실패: {exc}"
            )

    suppression.status = SuppressionStatus.FAILED.value
    suppression.ended_at = utcnow()
    suppression.result_json = {"ok": False, "failed_action": action, "error": str(last_error)}
    crud.system.append_step(
        db, suppression, step=step, status="FAILED", result=f"{MAX_RETRIES}회 재시도 실패"
    )
    log.error(
        "진압 실행 실패(FAILED) — %s %s: %s", suppression.suppression_id, action, last_error
    )
    return False
