"""ROS2 인터페이스 (API 명세서 §10 — ``robot_bridge.py``).

명세서 §10 이 규정한 로봇↔백엔드 토픽 계약을 **그대로** 구현한다. 이 파일은
`app/bridge.py` 의 `Bridge` 프로토콜(`publish_command` / `connected`)을 만족하므로
`set_bridge()` 로 NullBridge 를 대체해 끼울 수 있고, 라우터 코드는 한 줄도 바뀌지 않는다.

명세 대응 (§10)
--------------
* §10-1 구독 (로봇 → 백엔드): ``/{robot_id}/robot_state`` 등 7개 토픽
* §10-2 발행 (백엔드 → 로봇): ``/{robot_id}/command`` (std_msgs/String, JSON 직렬화)
* §10-3 명령 ACK: ``/{robot_id}/command_ack`` → ``{command_id, accepted, reason, eta_sec}``

계층 분리
--------
이 모듈은 **전송(transport)** 과 **부수효과(sink)** 를 주입받는다.
* transport: 실제 ROS2 발행을 담당하는 ``publisher(topic, payload)`` 콜러블.
  ROS2 가 없는 환경(이 저장소)에서는 기록만 하는 콜러블을 넣어 순수 단위 테스트가 된다.
  실장비에서는 `build_ros2_bridge()` 가 rclpy String 퍼블리셔로 채운다.
* sink: 구독으로 들어온 상태를 DB 반영·WS 브로드캐스트로 흘려보내는 어댑터.
  테스트는 기록용 sink 를, 운영은 `BackendSink` 를 넣는다.

이렇게 나눈 덕에 "메시지 규격(§10)" 로직은 ROS2 설치 없이 전부 검증할 수 있고,
실기 연동(PC2 ROS2 노드 ↔ Bridge)은 rclpy 가림막 뒤의 얇은 배선만 남는다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

from app.crud import ids
from app.logging_config import get_logger

log = get_logger("bridge")

# ── §10 토픽 이름 (네임스페이스 = robot_id) ─────────────────────────────────
COMMAND_TOPIC = "/{robot_id}/command"
ACK_TOPIC = "/{robot_id}/command_ack"

#: §10-1 구독 토픽 → 타입. 실제 rclpy 구독 생성과 문서화에 함께 쓴다.
INBOUND_TOPICS: dict[str, str] = {
    "/{robot_id}/robot_state": "std_msgs/String",
    "/{robot_id}/amcl_pose": "geometry_msgs/PoseWithCovarianceStamped",
    "/{robot_id}/battery_state": "sensor_msgs/BatteryState",
    "/{robot_id}/detection": "std_msgs/String",
    "/{robot_id}/aruco_correction": "std_msgs/String",
    "/{robot_id}/safety_event": "std_msgs/String",
    "/{robot_id}/checkpoint": "std_msgs/String",
}

#: §10-2 command_type 고정값. 여기 없는 타입으로 하달하면 거부한다(오타·규격 이탈 차단).
COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "START_PATROL",
        "PAUSE",
        "RESUME",
        "CANCEL",
        "GOTO",
        "INSPECT",
        "EVACUATE",
        "ESTOP",
        "RESET",
        "DOCK",
    }
)

Publisher = Callable[[str, str], None]


def _now_iso() -> str:
    """§10-2 ``issued_at`` — 오프셋 포함 ISO8601 (예: ``2026-08-05T09:45:03+09:00``)."""
    return datetime.now().astimezone().isoformat()


# ══════════════════════════════════════════════════════════════════════════
# Sink — 구독으로 들어온 상태의 종착지 (DB 반영 / WS 브로드캐스트)
# ══════════════════════════════════════════════════════════════════════════
class RobotBridgeSink(Protocol):
    """§10-1 로 들어온 데이터를 백엔드로 흘려보내는 어댑터. 순수 테스트에서는 기록만 한다."""

    def robot_status(self, robot_id: str, **fields: Any) -> None: ...
    def pose_corrected(self, robot_id: str, payload: dict) -> None: ...
    def detection(self, robot_id: str, payload: dict) -> None: ...
    def safety_event(self, robot_id: str, code: str, detail: str) -> None: ...
    def checkpoint(self, robot_id: str, raw: str) -> None: ...
    def command_ack(self, robot_id: str, payload: dict) -> None: ...


class NullSink:
    """아무것도 하지 않는 기본 sink. 발행만 하고 구독은 무시할 때 쓴다."""

    def robot_status(self, robot_id: str, **fields: Any) -> None: ...
    def pose_corrected(self, robot_id: str, payload: dict) -> None: ...
    def detection(self, robot_id: str, payload: dict) -> None: ...
    def safety_event(self, robot_id: str, code: str, detail: str) -> None: ...
    def checkpoint(self, robot_id: str, raw: str) -> None: ...
    def command_ack(self, robot_id: str, payload: dict) -> None: ...


# ══════════════════════════════════════════════════════════════════════════
# 세션 — AMR 1대의 연결/명령 추적
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class _InflightCommand:
    command_id: str
    command_type: str
    payload: dict
    issued_at: str
    accepted: bool | None = None  # ACK 전 None
    eta_sec: float | None = None


@dataclass
class AmrSession:
    """등록된 AMR 한 대. robot_id 네임스페이스로 pub/sub 이 갈린다 (§10)."""

    robot_id: str
    connected: bool = False
    last_seen: datetime | None = None
    last_state: str | None = None
    #: ACK 를 아직 못 받은 하달 명령. 재접속 시 이걸 다시 쏴 복구한다.
    inflight: dict[str, _InflightCommand] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# Bridge 본체
# ══════════════════════════════════════════════════════════════════════════
class RobotBridge:
    """§10 계약을 구현한 다중 AMR 브리지. `Bridge` 프로토콜을 만족한다.

    스레드 안전: ROS2 구독 콜백은 실행기(executor) 스레드에서, 명령 하달은 라우터
    (async 이벤트루프) 스레드에서 온다. 세션 사전 변경은 전부 락으로 감싼다.
    """

    def __init__(
        self,
        robot_ids: list[str],
        publisher: Publisher,
        sink: RobotBridgeSink | None = None,
        *,
        transport_connected: Callable[[], bool] | None = None,
    ) -> None:
        self._pub = publisher
        self._sink: RobotBridgeSink = sink or NullSink()
        self._transport_connected = transport_connected
        self._sessions: dict[str, AmrSession] = {rid: AmrSession(rid) for rid in robot_ids}
        self._lock = threading.RLock()

    # ── Bridge 프로토콜 ──────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        """전송 계층이 살아 있는가. rclpy 트랜스포트가 주입되면 그 판정을 따른다."""
        if self._transport_connected is not None:
            return self._transport_connected()
        with self._lock:
            return any(s.connected for s in self._sessions.values())

    def publish_command(self, robot_id: str, command_type: str, payload: dict) -> dict:
        """§10-2 — ``/{robot_id}/command`` 로 명령 envelope 를 발행한다.

        반환은 NullBridge 와 동일한 ``{command_id, accepted, dispatched}`` 모양이라
        기존 라우터가 그대로 쓴다. ``dispatched`` 는 실제로 전송에 성공했는지다
        (세션이 끊겨 있으면 False — 화면이 "정말 전달됐는지"를 구분할 수 있어야 한다).
        """
        if command_type not in COMMAND_TYPES:
            raise ValueError(f"알 수 없는 command_type: {command_type!r} (§10-2 표 이탈)")

        session = self._session(robot_id)
        cmd = _InflightCommand(
            command_id=ids.next_command_id(),
            command_type=command_type,
            payload=payload,
            issued_at=_now_iso(),
        )
        envelope = {
            "command_id": cmd.command_id,
            "command_type": cmd.command_type,
            "payload": cmd.payload,
            "issued_at": cmd.issued_at,
        }

        dispatched = self._emit(robot_id, envelope)
        with self._lock:
            session.inflight[cmd.command_id] = cmd
        log.info("[robot_bridge] %s ← %s %s", robot_id, command_type, cmd.command_id)
        return {"command_id": cmd.command_id, "accepted": True, "dispatched": dispatched}

    # ── 세션 관리 (등록·연결·재접속) ─────────────────────────────────────
    def sessions(self) -> list[AmrSession]:
        with self._lock:
            return list(self._sessions.values())

    def is_online(self, robot_id: str) -> bool:
        with self._lock:
            s = self._sessions.get(robot_id)
            return bool(s and s.connected)

    def register(self, robot_id: str) -> AmrSession:
        """새 AMR 을 세션 표에 넣고 연결됨으로 표시한다. 이미 있으면 재사용."""
        with self._lock:
            session = self._sessions.setdefault(robot_id, AmrSession(robot_id))
            session.connected = True
            session.last_seen = datetime.now().astimezone()
        return session

    def on_disconnect(self, robot_id: str) -> None:
        """통신 끊김. inflight 명령은 버리지 않고 남겨 재접속 때 다시 쏜다 (§복구 정책)."""
        with self._lock:
            s = self._sessions.get(robot_id)
            if s:
                s.connected = False
        log.warning("[robot_bridge] %s 연결 끊김 — inflight %d건 보존", robot_id, self._inflight_count(robot_id))

    def on_reconnect(self, robot_id: str) -> list[str]:
        """재접속 복구. ACK 못 받은 명령을 다시 발행하고, 재전송한 command_id 목록을 돌려준다."""
        session = self.register(robot_id)
        with self._lock:
            pending = [c for c in session.inflight.values() if c.accepted is None]
        for cmd in pending:
            self._emit(
                robot_id,
                {
                    "command_id": cmd.command_id,
                    "command_type": cmd.command_type,
                    "payload": cmd.payload,
                    "issued_at": cmd.issued_at,
                },
            )
        if pending:
            log.info("[robot_bridge] %s 재접속 — %d건 재전송", robot_id, len(pending))
        return [c.command_id for c in pending]

    # ── §10-1 구독 콜백 (로봇 → 백엔드) ──────────────────────────────────
    def on_robot_state(self, robot_id: str, raw: str) -> None:
        """``"STATE:msg"`` 규칙 — 콜론 앞이 상태값, 뒤가 사람이 읽는 메시지."""
        state, _, msg = raw.partition(":")
        state = state.strip()
        session = self._touch(robot_id)
        session.last_state = state
        self._sink.robot_status(robot_id, state=state, state_msg=msg.strip() or None)

    def on_amcl_pose(self, robot_id: str, x: float, y: float, theta: float) -> None:
        self._touch(robot_id)
        self._sink.robot_status(robot_id, x=x, y=y, theta=theta)

    def on_battery_state(self, robot_id: str, percentage: float) -> None:
        """sensor_msgs/BatteryState.percentage(0~1) → 0~100 정수."""
        self._touch(robot_id)
        pct = round(percentage * 100) if percentage is not None and percentage <= 1.0 else round(percentage or 0)
        self._sink.robot_status(robot_id, battery=pct)

    def on_detection(self, robot_id: str, raw: str) -> None:
        """``{"type":"FIRE","conf":0.87,"bbox":[...]}`` JSON."""
        payload = self._parse_json(robot_id, "detection", raw)
        if payload is not None:
            self._touch(robot_id)
            self._sink.detection(robot_id, payload)

    def on_aruco_correction(self, robot_id: str, raw: str) -> None:
        """``{"marker_id":7,"error_m":0.08,...}`` JSON → POSE_CORRECTED."""
        payload = self._parse_json(robot_id, "aruco_correction", raw)
        if payload is not None:
            self._touch(robot_id)
            self._sink.pose_corrected(robot_id, payload)

    def on_safety_event(self, robot_id: str, raw: str) -> None:
        """``"ERR_NAV:경로 계획 실패"`` — 콜론 앞 코드, 뒤 상세."""
        code, _, detail = raw.partition(":")
        self._touch(robot_id)
        self._sink.safety_event(robot_id, code.strip(), detail.strip())

    def on_checkpoint(self, robot_id: str, raw: str) -> None:
        """복귀점 문자열(예: ``"PATROLLING|N-004|3"``) 원문 전달 — 해석은 sink 소관."""
        self._touch(robot_id)
        self._sink.checkpoint(robot_id, raw)

    # ── §10-3 명령 ACK ───────────────────────────────────────────────────
    def on_command_ack(self, robot_id: str, raw: str) -> dict | None:
        """``{command_id, accepted, reason, eta_sec}`` — inflight 를 확정 처리한다.

        도착 확인(도착 = ACK accepted True)이 오면 해당 명령을 inflight 에서 뺀다.
        거부(accepted False)면 inflight 에서 빼되 sink 가 실패를 알 수 있게 넘긴다.
        """
        payload = self._parse_json(robot_id, "command_ack", raw)
        if payload is None or "command_id" not in payload:
            return None
        cmd_id = payload["command_id"]
        accepted = bool(payload.get("accepted", False))
        with self._lock:
            session = self._sessions.get(robot_id)
            cmd = session.inflight.get(cmd_id) if session else None
            if cmd is not None:
                cmd.accepted = accepted
                cmd.eta_sec = payload.get("eta_sec")
                # 확정된 명령은 inflight 에서 제거 — 재접속 재전송 대상에서 빠진다.
                session.inflight.pop(cmd_id, None)
        self._touch(robot_id)
        self._sink.command_ack(robot_id, payload)
        return payload

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _session(self, robot_id: str) -> AmrSession:
        with self._lock:
            return self._sessions.setdefault(robot_id, AmrSession(robot_id))

    def _touch(self, robot_id: str) -> AmrSession:
        """구독 프레임이 도착했다 = 로봇이 살아 있다. last_seen/connected 갱신."""
        with self._lock:
            session = self._sessions.setdefault(robot_id, AmrSession(robot_id))
            session.connected = True
            session.last_seen = datetime.now().astimezone()
            return session

    def _inflight_count(self, robot_id: str) -> int:
        s = self._sessions.get(robot_id)
        return len(s.inflight) if s else 0

    def _emit(self, robot_id: str, envelope: dict) -> bool:
        topic = COMMAND_TOPIC.format(robot_id=robot_id)
        try:
            self._pub(topic, json.dumps(envelope, ensure_ascii=False, default=str))
            return True
        except Exception as exc:  # noqa: BLE001 - 전송 실패가 라우터를 죽이면 안 된다
            log.error("[robot_bridge] %s 발행 실패: %s", topic, exc)
            return False

    def _parse_json(self, robot_id: str, kind: str, raw: str) -> dict | None:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("[robot_bridge] %s %s JSON 파싱 실패 — 무시", robot_id, kind)
            return None
        return value if isinstance(value, dict) else None


# ══════════════════════════════════════════════════════════════════════════
# 실기 연동 커넥터 (rclpy) — ROS2 가 있을 때만 동작
# ══════════════════════════════════════════════════════════════════════════
def build_ros2_bridge(robot_ids: list[str], sink: RobotBridgeSink) -> RobotBridge:
    """rclpy 로 §10 토픽 pub/sub 을 잇는 실기 브리지를 만든다.

    이 저장소에는 ROS2(rclpy) 가 없으므로 여기서 import 를 시도하고, 없으면 명확한
    오류를 던진다. 실장비(PC2 ROS2 노드)에서 이 함수가 켜지면 `RobotBridge` 본체는
    그대로 두고 퍼블리셔/구독만 실제 토픽으로 채워진다.
    """
    try:  # pragma: no cover - ROS2 미설치 환경에서는 실행되지 않는다
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import String
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ROS2(rclpy) 가 설치되어 있지 않습니다. 실기 연동은 PC2 ROS2 Humble 노드에서 "
            "실행하세요. (개발/CI 에서는 AMR_BRIDGE_BACKEND=loopback 을 쓰십시오.)"
        ) from exc

    # 이 블록은 rclpy 가 있는 환경에서만 의미가 있어 커버리지 대상에서 제외한다.
    class _Ros2Node(Node):  # pragma: no cover
        def __init__(self) -> None:
            super().__init__("robot_bridge")
            self._cmd_pubs = {
                rid: self.create_publisher(String, COMMAND_TOPIC.format(robot_id=rid), 10)
                for rid in robot_ids
            }

        def publish(self, topic: str, payload: str) -> None:
            for rid, pub in self._cmd_pubs.items():
                if topic == COMMAND_TOPIC.format(robot_id=rid):
                    pub.publish(String(data=payload))
                    return

    if not rclpy.ok():  # pragma: no cover
        rclpy.init()
    node = _Ros2Node()  # pragma: no cover
    bridge = RobotBridge(  # pragma: no cover
        robot_ids, publisher=node.publish, sink=sink, transport_connected=lambda: rclpy.ok()
    )
    # 구독(§10-1)·ACK(§10-3) 콜백을 node 에 배선하는 코드가 여기 들어간다.
    # 각 토픽마다 create_subscription(String/PoseWithCovarianceStamped/BatteryState, ...) 로
    # bridge.on_* 를 호출하도록 연결한다. (실기 노드에서 채운다)
    return bridge  # pragma: no cover
