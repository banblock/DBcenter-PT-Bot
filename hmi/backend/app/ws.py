"""WebSocket 엔드포인트 — /ws/monitor (API 명세서 §9).

접속 직후 SNAPSHOT 을 1회 보낸다. 프론트는 이 한 프레임만으로 화면 전체를 그릴 수
있어야 한다 (F-03). 그래야 새로고침·재연결 뒤에 REST 를 여러 번 때리지 않는다.

클라이언트 → 서버는 3종만 받는다: subscribe / snapshot / ping.
정의되지 않은 action 은 조용히 무시한다 — 알 수 없는 입력에 연결을 끊으면
프론트 버전이 하나만 달라도 관제 화면이 죽는다.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import crud
from app.connection_manager import envelope, manager
from app.database import SessionLocal
from app.enums import WsMessageType
from app.logging_config import get_logger

router = APIRouter()
log = get_logger("ws")


def build_snapshot() -> dict:
    """SNAPSHOT payload (B-05). 화면 초기 렌더에 필요한 최소 집합."""
    db = SessionLocal()
    try:
        robots = [crud.robots.to_dict(r) for r in crud.robots.list_all(db)]
        missions = [
            crud.robots.mission_to_dict(db, m)
            for m in crud.robots.list_missions(db, limit=50)
        ]
        # 열린 이벤트를 우선 보여준다. 종결된 건 이력 화면에서 페이지네이션으로.
        events_rows, _ = crud.events.query(db, page=1, size=50)
        events = [crud.events.to_dict(e) for e in events_rows]
        zones = [
            {
                "zone_id": z.zone_id,
                "name": z.name,
                "polygon": z.polygon_json,
                "risk_base": z.risk_base,
                "camera_ids": z.camera_ids,
            }
            for z in crud.patrol.list_zones(db)
        ]
        nodes = [
            {
                "node_id": n.node_id,
                "name": n.name,
                "zone_id": n.zone_id,
                "x": n.x,
                "y": n.y,
                "theta": n.theta,
                "is_blindspot": n.is_blindspot,
                "priority_score": n.priority_score,
            }
            for n in crud.patrol.list_nodes(db)
        ]
        active_map = crud.maps.get_active(db)
        return {
            "robots": robots,
            "missions": missions,
            "events": events,
            "zones": zones,
            "nodes": nodes,
            "map": (
                {
                    "map_id": active_map.map_id,
                    "name": active_map.name,
                    "image_url": active_map.image_path,
                    "resolution": active_map.resolution,
                    "origin": [active_map.origin_x, active_map.origin_y, active_map.origin_theta],
                    "width": active_map.width,
                    "height": active_map.height,
                }
                if active_map
                else None
            ),
            "suppression_mode": crud.system.get_suppression_mode(db),
        }
    finally:
        db.close()


@router.websocket("/ws/monitor")
async def monitor(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await manager.send_to(websocket, envelope(WsMessageType.SNAPSHOT.value, build_snapshot()))

        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("WS 파싱 실패 — 무시: %s", raw[:120])
                continue

            action = message.get("action")
            if action == "subscribe":
                topics = await manager.subscribe(websocket, message.get("topics") or [])
                await manager.send_to(
                    websocket, envelope(WsMessageType.SUBSCRIBED.value, {"topics": topics})
                )
            elif action == "snapshot":
                await manager.send_to(
                    websocket, envelope(WsMessageType.SNAPSHOT.value, build_snapshot())
                )
            elif action == "ping":
                await manager.send_to(websocket, envelope(WsMessageType.PONG.value, {}))
            else:
                log.debug("알 수 없는 WS action 무시: %r", action)

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - 한 클라이언트 오류가 서버를 흔들면 안 된다
        log.exception("WS 처리 중 예외 — 연결을 정리합니다")
    finally:
        await manager.disconnect(websocket)
