# ★WebSocket + 방송 큐(핵심)

"""WebSocket 연결 관리 + 토픽 구독 필터 (B-01~B-07).

구조는 체크리스트가 지정한 auto-dump-bot 패턴을 그대로 잇는다::

    ROS2 Thread(Producer) → asyncio.Queue → Consumer Loop → broadcast() → WS(Front)

큐를 2개로 나눈 이유
--------------------
위치 텔레메트리(5Hz × N대)는 넘치면 버려도 되지만, 이벤트/진압 메시지는
절대 버리면 안 된다. 하나의 큐를 쓰면 위치 데이터가 큐를 채웠을 때 화재
이벤트가 드롭될 수 있다. 그래서 `telemetry_queue`(드롭 허용)와
`event_queue`(드롭 금지, 가득 차면 블로킹)를 분리했다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from app.config import settings
from app.enums import TYPE_TO_TOPIC, WsMessageType, WsTopic
from app.logging_config import get_logger

log = get_logger("ws")

#: 구독과 무관하게 모든 클라이언트가 받아야 하는 타입
ALWAYS_DELIVER = {
    WsMessageType.SNAPSHOT.value,
    WsMessageType.PONG.value,
    WsMessageType.SUBSCRIBED.value,
    WsMessageType.SYSTEM_ALERT.value,
}

#: 드롭이 허용되는 타입 — 다음 프레임이 곧 오므로 최신값만 있으면 된다
DROPPABLE = {WsMessageType.ROBOT_STATUS.value, WsMessageType.DETECTION.value}


def envelope(type_: str, payload: dict[str, Any] | list[Any] | None = None) -> dict[str, Any]:
    """서버 → 클라이언트 공통 봉투 (명세서 §9-3)."""
    return {
        "type": type_,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "payload": payload if payload is not None else {},
    }


# [공부 메모] ★ 백엔드→화면 "방송국". WebSocket 붙은 화면들한테 소식을 뿌림.
#   이것도 프로듀서-컨슈머임: publish()로 큐에 넣기만 하고, consumer_loop()가 꺼내 방송.
#   ★리뷰 포인트: 큐를 왜 2개로 나눴나?
#     - telemetry_queue: 위치 같은 고빈도. 넘치면 오래된 거 버려도 됨(곧 새 게 옴).
#     - event_queue: 화재/이벤트. 절대 안 버림(하나라도 놓치면 큰일).
#     하나로 합치면 위치 데이터가 큐 채웠을 때 화재 이벤트가 밀려 드롭될 수 있음 → 분리.
class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()
        # 위치·탐지 등 고빈도 (넘치면 오래된 것 드롭)
        self.telemetry_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=settings.broadcast_queue_size)
        # 이벤트·진압 등 유실 금지
        self.event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=settings.event_queue_size)
        #: 로봇별 최신 상태 캐시 (B-03) — 신규 접속 SNAPSHOT 을 DB 없이 즉시 채운다
        self.latest_status: dict[str, dict] = {}

    # ── 연결 수명주기 ─────────────────────────────────────────────────────
    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            # 기본 구독 = 전체. 클라이언트가 subscribe 를 보내면 좁혀진다.
            self._clients[websocket] = {t.value for t in WsTopic}
        log.info("WS 접속 — 현재 %d개 연결", len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)
        log.info("WS 해제 — 현재 %d개 연결", len(self._clients))

    async def subscribe(self, websocket: WebSocket, topics: list[str]) -> list[str]:
        """구독 토픽 지정 (B-04). 알 수 없는 토픽은 조용히 버리고 유효한 것만 남긴다."""
        valid = {t for t in topics if t in {x.value for x in WsTopic}}
        async with self._lock:
            self._clients[websocket] = valid
        log.info("WS 구독 변경 → %s", sorted(valid) or "(없음)")
        return sorted(valid)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ── 송신 ──────────────────────────────────────────────────────────────
    async def send_to(self, websocket: WebSocket, message: dict) -> None:
        if websocket.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await websocket.send_text(json.dumps(message, ensure_ascii=False, default=str))
        except Exception as exc:  # 끊긴 소켓 — 정리만 하고 넘어간다
            log.debug("WS 송신 실패, 연결 정리: %s", exc)
            await self.disconnect(websocket)

    async def broadcast(self, message: dict) -> int:
        """구독한 클라이언트에게만 전달 (B-73). 전달된 클라이언트 수를 돌려준다."""
        type_ = message.get("type", "")
        required_topic = TYPE_TO_TOPIC.get(type_)

        async with self._lock:
            targets = [
                ws
                for ws, topics in self._clients.items()
                if type_ in ALWAYS_DELIVER or required_topic is None or required_topic in topics
            ]

        for ws in targets:
            await self.send_to(ws, message)
        return len(targets)

    # ── 큐 적재 (ROS 스레드 등 프로듀서가 호출) ───────────────────────────
    def publish(self, type_: str, payload: dict | list | None = None) -> None:
        """동기 컨텍스트(ROS 콜백 스레드)에서 안전하게 큐에 넣는다.

        큐가 가득 찬 경우:
        * 드롭 허용 타입 → 가장 오래된 것을 버리고 최신을 넣는다 (B-06).
        * 유실 금지 타입 → 버리지 않고 WARN 만 남긴다. 소비 루프가 곧 비운다.
        """
        message = envelope(type_, payload)
        queue = self.telemetry_queue if type_ in DROPPABLE else self.event_queue
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            if type_ in DROPPABLE:
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                    log.warning("텔레메트리 큐 포화 — 가장 오래된 프레임 1건 드롭 (type=%s)", type_)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass
            else:
                log.error("이벤트 큐 포화 — %s 적재 실패. 소비 루프 지연 의심", type_)

    if True:  # 가독성용 구분

        async def publish_async(self, type_: str, payload: dict | list | None = None) -> None:
            """async 컨텍스트(라우터)에서는 큐를 거치지 않고 바로 브로드캐스트한다."""
            await self.broadcast(envelope(type_, payload))

    # ── 소비 루프 (B-02) ──────────────────────────────────────────────────
    # ★ 컨슈머(요리사). main.py lifespan에서 백그라운드 태스크로 계속 돎.
    #   event_queue(화재)를 항상 먼저 다 비우고, 그 다음 telemetry(위치) 처리 = 우선순위.
    #   둘 다 비면 20ms 잠깐 쉼(폴링). broadcast는 구독한 화면한테만 보냄.
    async def consumer_loop(self) -> None:
        """두 큐를 소비해 브로드캐스트한다. 이벤트 큐를 항상 먼저 비운다."""
        log.info("브로드캐스트 소비 루프 시작")
        try:
            while True:
                drained = False
                while not self.event_queue.empty():
                    await self.broadcast(self.event_queue.get_nowait())
                    drained = True
                if not self.telemetry_queue.empty():
                    await self.broadcast(self.telemetry_queue.get_nowait())
                    drained = True
                if not drained:
                    await asyncio.sleep(0.02)  # 50Hz 폴링 — 5Hz 텔레메트리에 충분
        except asyncio.CancelledError:  # pragma: no cover - 종료 경로
            log.info("브로드캐스트 소비 루프 종료")
            raise


manager = ConnectionManager()
