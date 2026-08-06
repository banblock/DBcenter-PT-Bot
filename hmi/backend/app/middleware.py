"""공통 미들웨어 — 요청 ID 부여 + 요청/응답 1줄 로깅 + 최종 예외 그물."""

from __future__ import annotations

import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import settings
from app.logging_config import get_logger, request_id_var
from app.responses import fail, fail_from
from app.errors import E

log = get_logger("request")

#: 헬스체크·문서 요청까지 로그를 남기면 실제 트래픽이 묻힌다.
_QUIET_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}


def _validate_role_header(request: Request) -> Response | None:
    """X-Role 이 알려진 역할인지 확인. 문제 없으면 None."""
    raw = request.headers.get("X-Role")
    if raw is None:
        return None
    from app.security import Role

    try:
        Role(raw.strip().upper())
    except ValueError:
        return fail(E.UNKNOWN_ROLE.code, f"알 수 없는 역할입니다: {raw}", E.UNKNOWN_ROLE.status)
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """모든 요청에 X-Request-ID 를 붙이고 처리 시간을 기록한다."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:8]
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        # 역할 헤더는 여기서 한 번에 검증한다. 권한 가드가 붙은 엔드포인트에서만
        # 검사하면, 오타 난 역할이 조회에서는 조용히 통과하고 쓰기에서만 터져
        # 원인을 찾기 어려워진다.
        role_error = _validate_role_header(request)
        if role_error is not None:
            log.warning("%s %s → 400 알 수 없는 역할 헤더", request.method, request.url.path)
            role_error.headers["X-Request-ID"] = request_id
            request_id_var.reset(token)
            return role_error

        try:
            response = await call_next(request)
        except Exception:
            # 라우터 밖(다른 미들웨어 등)에서 터진 예외까지 봉투 형태로 돌려준다.
            elapsed = (time.perf_counter() - started) * 1000
            log.exception("%s %s → 500 (%.1fms) 처리되지 않은 예외", request.method, request.url.path, elapsed)
            response = fail_from(E.INTERNAL)
            response.headers["X-Request-ID"] = request_id
            request_id_var.reset(token)
            return response

        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed:.1f}"

        if request.url.path not in _QUIET_PATHS:
            level = log.warning if response.status_code >= 400 else log.info
            level(
                "%s %s → %d (%.1fms) role=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
                request.headers.get("X-Role", "-"),
            )
            if settings.log_request_body and request.method in ("POST", "PUT", "PATCH"):
                body = await request.body()
                if body:
                    log.debug("  body: %s", body[:2000].decode("utf-8", "replace"))

        request_id_var.reset(token)
        return response
