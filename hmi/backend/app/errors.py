"""에러 코드 체계 + 도메인 예외.

규칙
----
* HTTP 상태는 명세서의 4종만 쓴다 — 400 유효성 / 404 대상 없음 / 409 상태 충돌 / 500 내부.
  (권한 거부는 403 을 추가로 쓴다 — 명세서에 없던 항목이라 아래 주석에 명시.)
* 코드는 ``DOMAIN_REASON`` 형태의 대문자 스네이크. 프론트는 이 코드로 분기하고,
  `message` 는 사람이 읽는 용도로만 쓴다 (문구가 바뀌어도 프론트가 깨지지 않게).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorCatalog:
    code: str
    status: int
    message: str


class E:
    """에러 카탈로그. 새 에러는 반드시 여기에 먼저 등록하고 쓴다."""

    # ── 공통 (명세서 규정) ────────────────────────────────────────────────
    VALIDATION = ErrorCatalog("VALIDATION_ERROR", 400, "요청 값이 올바르지 않습니다")
    NOT_FOUND = ErrorCatalog("NOT_FOUND", 404, "대상을 찾을 수 없습니다")
    CONFLICT = ErrorCatalog("CONFLICT", 409, "현재 상태에서는 처리할 수 없습니다")
    INTERNAL = ErrorCatalog("INTERNAL_ERROR", 500, "서버 내부 오류가 발생했습니다")

    # ── 권한 (명세서 미규정 — 본 구현에서 추가) ──────────────────────────
    FORBIDDEN = ErrorCatalog("FORBIDDEN", 403, "이 작업을 수행할 권한이 없습니다")
    UNKNOWN_ROLE = ErrorCatalog("UNKNOWN_ROLE", 400, "알 수 없는 역할입니다")

    # ── 맵 / 노드 / 경로 ──────────────────────────────────────────────────
    MAP_NOT_FOUND = ErrorCatalog("MAP_NOT_FOUND", 404, "맵을 찾을 수 없습니다")
    ZONE_NOT_FOUND = ErrorCatalog("ZONE_NOT_FOUND", 404, "구역을 찾을 수 없습니다")
    NODE_NOT_FOUND = ErrorCatalog("NODE_NOT_FOUND", 404, "순찰 노드를 찾을 수 없습니다")
    ROUTE_NOT_FOUND = ErrorCatalog("ROUTE_NOT_FOUND", 404, "순찰 경로를 찾을 수 없습니다")
    ROUTE_EMPTY = ErrorCatalog("ROUTE_EMPTY", 400, "경로에 노드가 하나도 없습니다")
    DUPLICATE_ID = ErrorCatalog("DUPLICATE_ID", 409, "이미 존재하는 식별자입니다")

    # ── 로봇 / 미션 ───────────────────────────────────────────────────────
    ROBOT_NOT_FOUND = ErrorCatalog("ROBOT_NOT_FOUND", 404, "로봇을 찾을 수 없습니다")
    ROBOT_OFFLINE = ErrorCatalog("ROBOT_OFFLINE", 409, "로봇이 오프라인입니다")
    MISSION_NOT_FOUND = ErrorCatalog("MISSION_NOT_FOUND", 404, "미션을 찾을 수 없습니다")
    MISSION_ALREADY_RUNNING = ErrorCatalog(
        "MISSION_ALREADY_RUNNING", 409, "이미 실행 중인 미션이 있습니다"
    )
    MISSION_NOT_RUNNING = ErrorCatalog("MISSION_NOT_RUNNING", 409, "실행 중인 미션이 아닙니다")
    NO_AVAILABLE_ROBOT = ErrorCatalog("NO_AVAILABLE_ROBOT", 409, "가용한 로봇이 없습니다")
    NODE_LOCKED = ErrorCatalog("NODE_LOCKED", 409, "다른 로봇이 노드를 점유 중입니다")

    # ── 이벤트 / 설비 / align ─────────────────────────────────────────────
    EVENT_NOT_FOUND = ErrorCatalog("EVENT_NOT_FOUND", 404, "이벤트를 찾을 수 없습니다")
    EVENT_ALREADY_CLOSED = ErrorCatalog("EVENT_ALREADY_CLOSED", 409, "이미 종결된 이벤트입니다")
    EQUIPMENT_NOT_FOUND = ErrorCatalog("EQUIPMENT_NOT_FOUND", 404, "설비를 찾을 수 없습니다")
    WORK_ORDER_NOT_FOUND = ErrorCatalog("WORK_ORDER_NOT_FOUND", 404, "작업지시를 찾을 수 없습니다")
    RULE_NOT_FOUND = ErrorCatalog("RULE_NOT_FOUND", 404, "대조 룰을 찾을 수 없습니다")

    # ── 진압 ──────────────────────────────────────────────────────────────
    SUPPRESSION_NOT_FOUND = ErrorCatalog("SUPPRESSION_NOT_FOUND", 404, "진압 요청을 찾을 수 없습니다")
    CONFIRM_TEXT_MISMATCH = ErrorCatalog(
        "CONFIRM_TEXT_MISMATCH", 400, "확인 문구가 구역 ID와 일치하지 않습니다"
    )
    INTERLOCK_BLOCKED = ErrorCatalog("INTERLOCK_BLOCKED", 409, "안전 인터락에 걸려 있습니다")


class ApiError(Exception):
    """라우터/서비스에서 던지는 표준 예외. 핸들러가 FAIL 봉투로 변환한다."""

    def __init__(
        self,
        catalog: ErrorCatalog,
        message: str | None = None,
        data: Any = None,
    ) -> None:
        self.catalog = catalog
        self.message = message or catalog.message
        self.data = data
        super().__init__(self.message)

    @property
    def code(self) -> str:
        return self.catalog.code

    @property
    def status(self) -> int:
        return self.catalog.status


def not_found(catalog: ErrorCatalog, identifier: str) -> ApiError:
    """`ROUTE_NOT_FOUND: R-99` 처럼 무엇이 없는지 메시지에 실어준다."""
    return ApiError(catalog, f"{catalog.message}: {identifier}")
