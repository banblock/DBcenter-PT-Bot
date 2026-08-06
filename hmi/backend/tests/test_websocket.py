"""WebSocket /ws/monitor 계약 검증.

프론트 `WebSocketClient` 가 의존하는 계약만 확인한다.
접속 즉시 SNAPSHOT · subscribe ACK · ping/pong · 모르는 action 무시.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.connection_manager import ConnectionManager, envelope
from app.enums import WsMessageType


def test_snapshot_arrives_first(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        message = ws.receive_json()
        assert message["type"] == WsMessageType.SNAPSHOT.value
        assert "timestamp" in message
        payload = message["payload"]
        # 이 한 프레임만으로 화면을 그릴 수 있어야 한다 (F-03)
        assert {"robots", "missions", "events", "zones", "nodes", "map"} <= set(payload)
        assert len(payload["robots"]) >= 2
        assert payload["zones"], "구역이 비어 있으면 지도를 그릴 수 없다"


def test_subscribe_returns_ack_with_valid_topics(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()  # SNAPSHOT
        ws.send_json({"action": "subscribe", "topics": ["ROBOT_STATUS", "EVENT", "존재하지않음"]})
        ack = ws.receive_json()
        assert ack["type"] == WsMessageType.SUBSCRIBED.value
        assert ack["payload"]["topics"] == ["EVENT", "ROBOT_STATUS"]  # 잘못된 토픽은 버려진다


def test_ping_pong(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == WsMessageType.PONG.value


def test_snapshot_can_be_requested_again(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_json({"action": "snapshot"})
        assert ws.receive_json()["type"] == WsMessageType.SNAPSHOT.value


def test_unknown_action_is_ignored_not_fatal(client: TestClient):
    """모르는 메시지에 연결을 끊으면 프론트 버전이 하나만 달라도 관제가 죽는다."""
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_json({"action": "자폭"})
        ws.send_json({"nonsense": True})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == WsMessageType.PONG.value


def test_malformed_json_is_ignored(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_text("{ 이건 JSON 이 아니다")
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == WsMessageType.PONG.value


def test_event_broadcast_reaches_subscriber(client: TestClient):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_json({"action": "subscribe", "topics": ["EVENT"]})
        ws.receive_json()  # ACK

        client.post(
            "/api/events/detect",
            json={"source": "cctv", "camera_id": "CAM-01", "type": "SMOKE",
                  "confidence": 0.91, "zone_id": "Z02"},
        )
        message = ws.receive_json()
        assert message["type"] == WsMessageType.EVENT.value
        assert message["payload"]["type"] == "SMOKE"


def test_unsubscribed_topic_is_not_delivered(client: TestClient):
    """LOG 만 구독한 클라이언트에게 EVENT 가 가면 안 된다 (B-73)."""
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()
        ws.send_json({"action": "subscribe", "topics": ["LOG"]})
        ws.receive_json()

        client.post(
            "/api/events/detect",
            json={"source": "cctv", "camera_id": "CAM-01", "type": "PERSON",
                  "confidence": 0.6, "zone_id": "Z03"},
        )
        # EVENT 대신 ping 응답이 먼저 와야 정상
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == WsMessageType.PONG.value


# ══════════════════════════════════════════════════════════════════════════
# 큐 정책 (B-06, B-07) — 매니저 단독 검증
# ══════════════════════════════════════════════════════════════════════════
def test_telemetry_queue_drops_oldest_when_full():
    manager = ConnectionManager()
    manager.telemetry_queue._maxsize = 2  # noqa: SLF001 - 테스트용 축소
    for i in range(5):
        manager.publish(WsMessageType.ROBOT_STATUS.value, {"seq": i})
    assert manager.telemetry_queue.qsize() == 2
    remaining = [manager.telemetry_queue.get_nowait()["payload"]["seq"] for _ in range(2)]
    assert remaining == [3, 4], "가장 최신 프레임이 남아야 한다"


def test_event_queue_never_drops():
    """이벤트는 넘쳐도 버리지 않는다 — 화재 신고를 흘리면 안 된다."""
    manager = ConnectionManager()
    manager.event_queue._maxsize = 2  # noqa: SLF001
    for i in range(5):
        manager.publish(WsMessageType.EVENT.value, {"seq": i})
    kept = [manager.event_queue.get_nowait()["payload"]["seq"] for _ in range(2)]
    assert kept == [0, 1], "먼저 들어온 이벤트가 유지되어야 한다"


def test_envelope_shape():
    message = envelope(WsMessageType.LOG.value, {"level": "INFO"})
    assert set(message) == {"type", "timestamp", "payload"}
    assert message["payload"]["level"] == "INFO"
