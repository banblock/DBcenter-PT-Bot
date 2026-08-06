"""FastAPI 애플리케이션 엔트리포인트."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.connection_manager import manager
from app.database import SessionLocal, init_db
from app.enums import WsMessageType
from app.errors import ApiError, E
from app.logging_config import get_logger, setup_logging
from app.middleware import RequestContextMiddleware
from app.responses import fail, fail_from
from app.routers import ALL_ROUTERS
from app.ws import router as ws_router

setup_logging()
log = get_logger("main")


async def _heartbeat_watchdog() -> None:
    """heartbeat 감시 (B-08). 3초 넘게 소식 없는 로봇을 OFFLINE 으로 내리고 알린다."""
    while True:
        try:
            await asyncio.sleep(1.0)
            db = SessionLocal()
            try:
                changed = crud_mark_offline(db)
                db.commit()
            finally:
                db.close()
            for robot_id, last_seen in changed:
                await manager.publish_async(
                    WsMessageType.ROBOT_OFFLINE.value,
                    {"robot_id": robot_id, "last_seen": last_seen},
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 감시 태스크는 무슨 일이 있어도 살아 있어야 한다
            log.exception("heartbeat 감시 중 예외 — 계속 진행합니다")


def crud_mark_offline(db) -> list[tuple[str, str | None]]:
    from app import crud

    return [
        (r.robot_id, r.last_seen.isoformat() if r.last_seen else None)
        for r in crud.robots.mark_offline_stale(db)
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 70)
    log.info("%s v%s 기동", settings.app_name, settings.app_version)
    init_db()

    from app import crud

    db = SessionLocal()
    try:
        # 설정에 정의된 로봇을 DB 에 보장 — 화면이 빈 상태로 뜨지 않게
        for robot_id in settings.robot_ids:
            crud.robots.ensure(db, robot_id, name=f"로봇 {robot_id}")
        db.commit()
    finally:
        db.close()

    consumer = asyncio.create_task(manager.consumer_loop(), name="broadcast-consumer")
    watchdog = asyncio.create_task(_heartbeat_watchdog(), name="heartbeat-watchdog")
    log.info("REST %d개 라우터 · WS /ws/monitor 준비 완료", len(ALL_ROUTERS))
    log.info("=" * 70)

    try:
        yield
    finally:
        for task in (consumer, watchdog):
            task.cancel()
        await asyncio.gather(consumer, watchdog, return_exceptions=True)
        log.info("서버 종료")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AMR 순찰·이상감지 관제 시스템 REST/WebSocket API.\n\n"
        "모든 응답은 `{result, data, message}` 봉투로 감싸진다.\n"
        "권한은 `X-Role` 헤더(VIEWER/OPERATOR/ADMIN)로 전달한다 — 로그인은 사용하지 않는다."
    ),
    lifespan=lifespan,
)

# ── 미들웨어 (등록 역순으로 실행된다 — RequestContext 가 가장 바깥) ──────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
)
app.add_middleware(RequestContextMiddleware)


# ── 예외 핸들러 — 어떤 오류든 동일한 FAIL 봉투로 나간다 ──────────────────
@app.exception_handler(ApiError)
async def handle_api_error(_: Request, exc: ApiError):
    return fail(exc.code, exc.message, exc.status, exc.data)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError):
    """Pydantic 검증 실패 → 400 + 어떤 필드가 왜 틀렸는지."""
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    first = details[0] if details else None
    message = f"{first['field']}: {first['message']}" if first else E.VALIDATION.message
    return fail(E.VALIDATION.code, message, 400, {"errors": details})


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_: Request, exc: StarletteHTTPException):
    catalog = {404: E.NOT_FOUND, 403: E.FORBIDDEN, 409: E.CONFLICT}.get(exc.status_code)
    if catalog is not None:
        return fail(catalog.code, str(exc.detail) or catalog.message, exc.status_code)
    return fail("HTTP_ERROR", str(exc.detail), exc.status_code)


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception):
    log.exception("처리되지 않은 예외: %s", exc)
    return fail_from(E.INTERNAL)


# ── 라우터 ────────────────────────────────────────────────────────────────
for router in ALL_ROUTERS:
    app.include_router(router, prefix=settings.api_prefix)
app.include_router(ws_router)

Path(settings.media_root).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_root), name="media")


@app.get("/health", tags=["시스템"], summary="헬스체크")
async def health():
    from app.bridge import get_bridge

    return {
        "result": "SUCCESS",
        "data": {
            "status": "ok",
            "version": settings.app_version,
            "ws_clients": manager.client_count,
            "ros_bridge": type(get_bridge()).__name__,
            "ros_connected": get_bridge().connected,
        },
    }


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
