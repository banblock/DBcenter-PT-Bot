"""FastAPI 애플리케이션 엔트리포인트."""
# ★앱 본체(시작·종료·라우터 등록)

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


# [공부 메모] 여기서 config의 스위치(AMR_BRIDGE_BACKEND)를 보고 어떤 브리지를 끼울지 결정.
#   null(기본)=NullBridge(로그만) / loopback=RobotBridge(발행 로그) / ros2=Ros2Bridge(진짜).
#   set_bridge()로 갈아끼우는 거라 라우터는 뭘 끼우든 모름(= bridge.py의 의존성 역전 활용).
#   ros2는 상행 컨슈머가 이벤트 루프를 필요로 해서 loop를 넘기고, 종료 시 정리하려고 mgr 반환.
def _install_bridge(loop=None):
    """설정에 따라 robot_bridge(§10)를 끼운다. 기본 null 은 NullBridge 유지 → 동작 불변.

    ros2 모드에서는 상행 프로듀서-컨슈머가 이벤트 루프를 필요로 하므로 `loop` 를 받아
    넘기고, 종료 시 정리할 수 있게 관리자(Ros2Bridge)를 반환한다.
    """
    backend = settings.bridge_backend
    if backend == "null":
        return None
    from app.bridge import set_bridge
    from app.bridge_backend import BackendSink
    from app.robot_bridge import RobotBridge, build_ros2_bridge

    if backend == "loopback":
        # ROS2 없이 §10 발행 규격만 검증하는 배선 — 발행은 로그로만 남긴다.
        def _log_publisher(topic: str, payload: str) -> None:
            log.info("[loopback] %s ← %s", topic, payload)

        set_bridge(RobotBridge(settings.robot_ids, publisher=_log_publisher, sink=BackendSink()))
        return None
    elif backend == "ros2":
        mgr = build_ros2_bridge(settings.robot_ids, BackendSink(), loop)
        set_bridge(mgr.bridge)
        return mgr
    else:
        log.warning("알 수 없는 bridge_backend=%s — NullBridge 유지", backend)
        return None


def _install_vision(loop):
    """비전 브리지(vision_bridge)를 설치한다. ros2 모드 + vision_enabled 일 때만.

    반환: 종료 시 정리할 VisionBridge (없으면 None). rclpy/patrol_interfaces 가 없으면
    (colcon build/source 안 됨) 경고만 남기고 백엔드는 그대로 뜬다.
    """
    if not settings.vision_enabled or settings.bridge_backend != "ros2":
        return None
    try:
        from app.vision_bridge import build_vision_bridge

        return build_vision_bridge(loop)
    except Exception as exc:  # noqa: BLE001 - 비전 없이도 백엔드는 떠야 한다
        log.warning("vision_bridge 설치 실패(비전 없이 계속): %s", exc)
        return None


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

    bridge_mgr = _install_bridge(asyncio.get_running_loop())
    vision_mgr = _install_vision(asyncio.get_running_loop())

    # ★ 서버 켜질 때 백그라운드 작업 2개를 계속 돌림:
    #   consumer = 방송 큐 소비(connection_manager) / watchdog = 3초 무소식 로봇 OFFLINE 처리.
    #   lifespan은 "서버 수명"을 관리 — yield 위=시작, 아래(finally)=종료 정리.
    consumer = asyncio.create_task(manager.consumer_loop(), name="broadcast-consumer")
    watchdog = asyncio.create_task(_heartbeat_watchdog(), name="heartbeat-watchdog")
    tasks = [consumer, watchdog]

    # 데모 시뮬레이터 — 실장비 없이 로봇을 실제로 구동한다(AMR_DEMO_SIM=1).
    if settings.demo_sim:
        from app.demo_sim import demo_sim_loop

        tasks.append(asyncio.create_task(demo_sim_loop(), name="demo-sim"))
        log.info("데모 시뮬레이터 활성화 (AMR_DEMO_SIM=1)")

    log.info("REST %d개 라우터 · WS /ws/monitor 준비 완료", len(ALL_ROUTERS))
    log.info("=" * 70)

    try:
        yield
    finally:
        if vision_mgr is not None:
            vision_mgr.stop()
        if bridge_mgr is not None:
            bridge_mgr.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
