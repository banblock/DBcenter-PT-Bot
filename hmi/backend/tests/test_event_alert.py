"""이벤트 알림 서비스 — 다중 클라이언트 동시 구독 & 실시간 푸시 유실 검증.

WS 서버·구독 관리·브로드캐스트·큐 정책은 이미 구현·테스트되어 있으나(test_websocket),
'여러 클라이언트가 동시에 구독한 상태에서의 정확한 전달/격리'와 '연속 이벤트 무유실'을
확인하는 테스트가 없어 이 파일에서 보강한다.

체크리스트 대응:
  · B-04/73 구독 필터·다중 클라이언트 → test_multiple_clients_*
  · B-06/07 이벤트 무유실              → test_event_stream_is_lossless_*

주의: 이 파일은 test_detection_judge/test_errors 뒤, test_smoke 앞에서 돌므로,
그 테스트들이 열어 둔 (type, zone) 및 smoke 의 FIRE·Z01 과 겹치지 않는 조합만 쓴다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.enums import WsMessageType


def _detect(client: TestClient, type_: str, zone_id: str) -> None:
    client.post(
        "/api/events/detect",
        json={"source": "cctv", "camera_id": "CAM-01", "type": type_, "confidence": 0.9, "zone_id": zone_id},
    )


def _drain_until_event(ws, tries: int = 5) -> dict:
    """구독과 무관하게 항상 오는 프레임(SYSTEM_ALERT 등)을 건너뛰고 EVENT 를 집는다."""
    for _ in range(tries):
        msg = ws.receive_json()
        if msg["type"] == WsMessageType.EVENT.value:
            return msg
    raise AssertionError("EVENT 프레임을 받지 못했습니다")


# ══════════════════════════════════════════════════════════════════════════
# 다중 클라이언트 동시 구독 (B-04 · B-73)
# ══════════════════════════════════════════════════════════════════════════
def test_multiple_clients_receive_same_event(client: TestClient):
    """같은 토픽을 구독한 두 클라이언트가 하나의 이벤트를 모두 받는다."""
    with client.websocket_connect("/ws/monitor") as ws_a, client.websocket_connect("/ws/monitor") as ws_b:
        ws_a.receive_json()  # SNAPSHOT
        ws_b.receive_json()
        for ws in (ws_a, ws_b):
            ws.send_json({"action": "subscribe", "topics": ["EVENT"]})
            ws.receive_json()  # ACK

        _detect(client, "BREAKER_ABNORMAL", "Z01")

        for ws in (ws_a, ws_b):
            msg = _drain_until_event(ws)
            assert msg["payload"]["type"] == "BREAKER_ABNORMAL"


def test_concurrent_clients_get_only_their_topics(client: TestClient):
    """동시 접속 상태에서도 구독 필터가 클라이언트별로 독립 적용된다.

    EVENT 구독자는 이벤트를 받고, LOG 만 구독한 클라이언트는 받지 않아야 한다.
    """
    with client.websocket_connect("/ws/monitor") as ws_event, client.websocket_connect("/ws/monitor") as ws_log:
        ws_event.receive_json()
        ws_log.receive_json()
        ws_event.send_json({"action": "subscribe", "topics": ["EVENT"]})
        ws_event.receive_json()
        ws_log.send_json({"action": "subscribe", "topics": ["LOG"]})
        ws_log.receive_json()

        _detect(client, "LOCK_ABNORMAL", "Z02")

        # EVENT 구독자는 받는다.
        assert _drain_until_event(ws_event)["payload"]["type"] == "LOCK_ABNORMAL"
        # LOG 구독자에겐 EVENT 가 가지 않는다 — ping 응답(PONG)이 먼저 와야 정상.
        ws_log.send_json({"action": "ping"})
        assert ws_log.receive_json()["type"] == WsMessageType.PONG.value


# ══════════════════════════════════════════════════════════════════════════
# 실시간 푸시 무유실 (B-06 · B-07)
# ══════════════════════════════════════════════════════════════════════════
def test_event_stream_is_lossless_under_burst(client: TestClient):
    """연속으로 들어온 이벤트가 하나도 누락 없이 구독자에게 전달된다."""
    burst = [("LEAK", "Z01"), ("SMOKE", "Z03"), ("INTRUSION", "Z02")]  # 서로 다른 zone+type (dedup 회피)
    with client.websocket_connect("/ws/monitor") as ws:
        ws.receive_json()  # SNAPSHOT
        ws.send_json({"action": "subscribe", "topics": ["EVENT"]})
        ws.receive_json()  # ACK

        for type_, zone_id in burst:
            _detect(client, type_, zone_id)

        received = []
        for _ in range(len(burst)):
            received.append(_drain_until_event(ws)["payload"]["type"])

        assert received == [t for t, _ in burst], f"순서·건수가 보존돼야 한다: {received}"
