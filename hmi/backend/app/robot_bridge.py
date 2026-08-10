# ★로봇단(ROS2) 연동(핵심)

# ════════════════════════════════════════════════════════════════
# [공부 메모] ★★ 백엔드에서 제일 중요한 파일. 리뷰 하이라이트 ★★
#
# 두 덩어리로 나뉨(리뷰 때 이 구분을 먼저 설명):
#   1) RobotBridge  = §10 "규칙" 자체. 명령 봉투 만들기 / 세션 / ACK 처리.
#                     순수 파이썬이라 ROS 없이도 pytest로 검증됨.
#   2) Ros2Bridge   = 그 둘레의 진짜 ROS2 배선 + "비동기 큐 프로듀서-컨슈머".
#
# ★ 제일 헷갈렸고 제일 중요한 부분 (Ros2Bridge):
#   ROS 콜백은 '다른 스레드'(rclpy executor)에서 옴. 근데 DB/WebSocket은 asyncio
#   '이벤트 루프' 스레드에서 해야 됨. 두 스레드는 세계가 다름.
#   → 콜백에서 바로 처리하면 (a) 로봇 메시지 받는 게 밀리고 (b) asyncio 객체를
#     남의 스레드에서 만져서 꼬임.
#   → 그래서: 콜백(프로듀서)은 값만 뽑아 loop.call_soon_threadsafe로 큐에 "넣기만".
#     컨슈머 코루틴(루프)이 하나씩 꺼내 처리. = 패스트푸드(주문받기 vs 요리) 분리.
#   상행은 큐로 받고, 하행(명령 발행)은 Publisher.publish 직접(원래 스레드 안전). 비대칭!
# ════════════════════════════════════════════════════════════════

"""ROS2 인터페이스 (API 명세서 §10 — ``robot_bridge.py``).

명세서 §10 이 규정한 로봇↔백엔드 토픽 계약을 **그대로** 구현한다. 이 파일은
`app/bridge.py` 의 `Bridge` 프로토콜(`publish_command` / `connected`)을 만족하므로
`set_bridge()` 로 NullBridge 를 대체해 끼울 수 있고, 라우터 코드는 한 줄도 바뀌지 않는다.

명세 대응 (§10)
--------------
* §10-1 구독 (로봇 → 백엔드): ``/{robot_id}/robot_state`` 등 7개 토픽
* §10-2 발행 (백엔드 → 로봇): ``/backend/{robot_id}/command`` (std_msgs/String, JSON 직렬화)
  (백엔드가 발행하므로 /backend prefix — patrol_interfaces 규약)
* §10-3 명령 ACK: ``/{robot_id}/command_ack`` → ``{command_id, accepted, reason, eta_sec}`` (로봇 발행, 백엔드 구독)

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

from app.config import settings
from app.crud import ids
from app.logging_config import get_logger

log = get_logger("bridge")

# ── §10 토픽 이름 (네임스페이스 = ROS-safe robot ns) ────────────────────────
# [통신 규칙] 백엔드가 "발행(publish)"하는 토픽은 이름 앞에 /backend 를 붙인다.
#   (patrol_interfaces 규약 — 보내는 쪽 prefix. 백엔드가 로봇/비전으로 보내는 것 = /backend)
#   → COMMAND_TOPIC 은 백엔드→로봇 발행이라 /backend 를 붙인다.
#   ↔ ACK_TOPIC(command_ack) 및 아래 INBOUND_TOPICS 는 "로봇이 발행 → 백엔드가 구독"하는
#     것이라 백엔드가 보내는 게 아니다 → /backend 를 붙이지 않는다(로봇 네임스페이스 그대로).
#
# [네임스페이스] 논리 robot_id(예: AMR-01)에는 ROS2 토픽에 못 쓰는 하이픈이 있다. 그래서
#   토픽을 만들 때는 robot_id 를 그대로 쓰지 않고 settings.topic_prefix_map 으로 매핑한
#   ROS-safe 네임스페이스(예: /amr_1)를 쓴다. DB/WS/프론트는 여전히 AMR-01 을 쓴다.
#   실 로봇·fake_robot_node·비전 노드도 반드시 같은 /amr_1·/amr_2 네임스페이스로 발행/구독해야 한다.


def robot_ns(robot_id: str) -> str:
    """논리 robot_id → ROS-safe 네임스페이스(선행 슬래시 포함). 예: AMR-01 → /amr_1.

    topic_prefix_map 에 없으면 언더스코어 id(amr_1 등)처럼 이미 ROS-safe 인 경우이므로
    ``/{robot_id}`` 를 그대로 쓴다.
    """
    ns = settings.topic_prefix_map.get(robot_id) or f"/{robot_id}"
    return ns if ns.startswith("/") else f"/{ns}"


def command_topic(robot_id: str) -> str:
    """§10-2 백엔드→로봇 명령 토픽. 예: AMR-01 → /backend/amr_1/command."""
    return f"/backend{robot_ns(robot_id)}/command"


# 아래 `{robot_id}` 자리표시는 문서용이다. 실제 토픽은 robot_ns()/command_topic() 이 만든다.
COMMAND_TOPIC = "/backend/{robot_ns}/command"  # 백엔드 → 로봇 (발행)
ACK_TOPIC = "/{robot_ns}/command_ack"  # 로봇 → 백엔드 (구독)

#: §10-1 구독 토픽 → 타입. 문서화용(실제 구독 생성은 robot_ns() 로 네임스페이스를 만든다).
INBOUND_TOPICS: dict[str, str] = {
    "/{robot_ns}/robot_state": "std_msgs/String",
    "/{robot_ns}/amcl_pose": "geometry_msgs/PoseWithCovarianceStamped",
    "/{robot_ns}/battery_state": "sensor_msgs/BatteryState",
    "/{robot_ns}/detection": "std_msgs/String",
    "/{robot_ns}/aruco_correction": "std_msgs/String",
    "/{robot_ns}/safety_event": "std_msgs/String",
    "/{robot_ns}/checkpoint": "std_msgs/String",
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
        """§10-2 — ``/backend/{robot_id}/command`` 로 명령 envelope 를 발행한다.

        반환은 NullBridge 와 동일한 ``{command_id, accepted, dispatched}`` 모양이라
        기존 라우터가 그대로 쓴다. ``dispatched`` 는 실제로 전송에 성공했는지다
        (세션이 끊겨 있으면 False — 화면이 "정말 전달됐는지"를 구분할 수 있어야 한다).
        """
        # ← 하행(명령)의 시작. 명세 §10-2 표에 없는 타입이면 바로 거부(오타/규격이탈 차단).
        #   아래서 envelope {command_id, command_type, payload, issued_at} 만들어 토픽에 발행.
        #   inflight에 등록해두고 ACK 오면 지움(안 오면 재접속 때 다시 쏨).
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
        topic = command_topic(robot_id)
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


def _yaw_from_quaternion(z: float, w: float) -> float:
    """평면 주행이라 z·w 만으로 yaw 를 복원한다 (roll·pitch≈0 가정)."""
    import math

    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


# ══════════════════════════════════════════════════════════════════════════
# 실기 연동 커넥터 (rclpy) — 프로듀서-컨슈머 (auto-dump-bot 구조 계승)
# ══════════════════════════════════════════════════════════════════════════
#
# 스레드 경계 (auto-dump-bot code/backend/robot_bridge.py 와 동일한 규약)
# ------------------------------------------------------------------------
#   상행 (로봇 → 백엔드):  ROS spin 스레드가 콜백을 받는다. 콜백은 asyncio/DB/WS 를
#     **절대 직접 만지지 않는다.** ROS 메시지에서 원시 값만 뽑아
#     ``loop.call_soon_threadsafe(_enqueue, item)`` 로 이벤트 루프에 넘긴다(프로듀서).
#     이벤트 루프의 컨슈머 코루틴이 큐를 비우며 ``bridge.on_*`` (파싱·DB·브로드캐스트)를
#     실행한다(컨슈머). 고빈도 텔레메트리로 큐가 차면 가장 오래된 것을 버린다.
#   하행 (백엔드 → 로봇):  라우터가 ``publish_command`` → ``node.publish`` →
#     ``Publisher.publish`` 를 직접 호출한다. rmw 가 스레드 안전을 보장하므로
#     call_soon_threadsafe 로 감쌀 필요가 없다(상·하행 비대칭은 의도된 설계).
INBOUND_QUEUE_SIZE = 2000


class Ros2Bridge:
    """rclpy 노드 + 상행 프로듀서-컨슈머를 묶은 실기 브리지 관리자.

    ``self.bridge`` 가 §10 로직(`RobotBridge`)이고, 이 클래스는 그 둘레의
    스레드/큐/수명주기만 담당한다. `set_bridge(mgr.bridge)` 로 라우터에 꽂는다.
    """

    def __init__(self, robot_ids: list[str], sink: RobotBridgeSink, loop: Any) -> None:  # pragma: no cover
        import asyncio
        import threading

        import rclpy
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from rclpy.node import Node
        from sensor_msgs.msg import BatteryState
        from std_msgs.msg import String

        self._loop = loop
        self._inbound: asyncio.Queue = asyncio.Queue(maxsize=INBOUND_QUEUE_SIZE)
        self._stop = threading.Event()

        bridge_ref: dict[str, RobotBridge] = {}

        class _Node(Node):
            def __init__(self, produce) -> None:
                super().__init__("robot_bridge")
                self._produce = produce
                self._cmd_pubs = {
                    rid: self.create_publisher(String, command_topic(rid), 10)
                    for rid in robot_ids
                }
                for rid in robot_ids:
                    ns = robot_ns(rid)
                    self.create_subscription(String, f"{ns}/command_ack",
                                             lambda m, r=rid: produce("on_command_ack", r, m.data), 10)
                    self.create_subscription(String, f"{ns}/robot_state",
                                             lambda m, r=rid: produce("on_robot_state", r, m.data), 10)
                    self.create_subscription(PoseWithCovarianceStamped, f"{ns}/amcl_pose",
                                             lambda m, r=rid: self._pose(r, m), 10)
                    self.create_subscription(BatteryState, f"{ns}/battery_state",
                                             lambda m, r=rid: produce("on_battery_state", r, m.percentage), 10)
                    self.create_subscription(String, f"{ns}/detection",
                                             lambda m, r=rid: produce("on_detection", r, m.data), 10)
                    self.create_subscription(String, f"{ns}/aruco_correction",
                                             lambda m, r=rid: produce("on_aruco_correction", r, m.data), 10)
                    self.create_subscription(String, f"{ns}/safety_event",
                                             lambda m, r=rid: produce("on_safety_event", r, m.data), 10)
                    self.create_subscription(String, f"{ns}/checkpoint",
                                             lambda m, r=rid: produce("on_checkpoint", r, m.data), 10)

            def _pose(self, rid, msg) -> None:
                # 쿼터니언→yaw 변환만 ROS 스레드에서(값만 뽑기). 처리는 컨슈머가 한다.
                p = msg.pose.pose
                self._produce("on_amcl_pose", rid,
                              (p.position.x, p.position.y,
                               _yaw_from_quaternion(p.orientation.z, p.orientation.w)))

            def publish(self, topic: str, payload: str) -> None:
                for rid, pub in self._cmd_pubs.items():
                    if topic == command_topic(rid):
                        pub.publish(String(data=payload))
                        return

        if not rclpy.ok():
            rclpy.init()
        self._node = _Node(self._produce)
        self.bridge = RobotBridge(
            robot_ids, publisher=self._node.publish, sink=sink,
            transport_connected=lambda: rclpy.ok(),
        )
        bridge_ref["b"] = self.bridge

        # 컨슈머 코루틴(이벤트 루프) + spin 스레드(ROS) 기동
        self._consumer_task = loop.create_task(self._consume())
        self._spin_thread = threading.Thread(target=self._spin, name="rclpy-spin", daemon=True)
        self._spin_thread.start()
        log.info("[robot_bridge] ROS2 브리지 기동 — 발행 %d개, 구독 8토픽/로봇, 상행 큐 컨슈머 1", len(robot_ids))

    # ── 프로듀서 (ROS 스레드) ────────────────────────────────────────────
    # ★★ 여기가 스레드 경계의 핵심. ROS 콜백(다른 스레드) → 이벤트 루프로 넘기는 다리.
    #    call_soon_threadsafe = "남의 스레드에서 이벤트 루프한테 안전하게 일 시키기"의
    #    표준 방법. 이거 없이 asyncio.Queue를 딴 스레드에서 만지면 꼬임. (제일 강조할 줄)
    def _produce(self, method: str, robot_id: str, arg: Any) -> None:  # pragma: no cover
        """ROS 콜백에서 호출. 이벤트 루프에 안전하게 넘기기만 한다(처리 안 함)."""
        self._loop.call_soon_threadsafe(self._enqueue, (method, robot_id, arg))

    def _enqueue(self, item: tuple) -> None:  # pragma: no cover
        """이벤트 루프에서 실행됨. 큐가 차면 가장 오래된 프레임을 버린다(고빈도 대비)."""
        import asyncio

        try:
            self._inbound.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._inbound.get_nowait()
                self._inbound.put_nowait(item)
                log.warning("[robot_bridge] 상행 큐 포화 — 오래된 프레임 1건 드롭")
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # ── 컨슈머 (이벤트 루프) ─────────────────────────────────────────────
    # ★ "요리사". 큐에서 하나씩 꺼내(await get) 실제 처리(bridge.on_* → DB저장 + 방송).
    #   여긴 이벤트 루프 위라서 asyncio/DB 만져도 안전. 프로듀서와 여기가 짝.
    async def _consume(self) -> None:  # pragma: no cover
        """큐를 비우며 §10 처리(bridge.on_*)를 이벤트 루프에서 수행한다."""
        import asyncio

        while not self._stop.is_set():
            try:
                method, robot_id, arg = await self._inbound.get()
            except asyncio.CancelledError:
                break
            try:
                fn = getattr(self.bridge, method)
                if method == "on_amcl_pose":
                    x, y, theta = arg
                    fn(robot_id, x=x, y=y, theta=theta)
                else:
                    fn(robot_id, arg)
            except Exception:  # noqa: BLE001 - 한 프레임 실패가 컨슈머를 멈추면 안 된다
                log.exception("[robot_bridge] 상행 처리 실패 (%s %s)", method, robot_id)

    def _spin(self) -> None:  # pragma: no cover
        import rclpy
        from rclpy.executors import SingleThreadedExecutor

        # 전용 executor 로 스핀한다(전역 executor 공유 시 vision_bridge 스핀 스레드와
        # "generator already executing" 충돌). 노드마다 executor 를 분리한다.
        executor = SingleThreadedExecutor()
        executor.add_node(self._node)
        try:
            while rclpy.ok() and not self._stop.is_set():
                executor.spin_once(timeout_sec=0.5)
        except Exception:  # noqa: BLE001
            log.exception("[robot_bridge] rclpy spin 종료")
        finally:
            executor.remove_node(self._node)

    def stop(self) -> None:  # pragma: no cover
        """lifespan 종료 시 정리."""
        import rclpy

        self._stop.set()
        self._consumer_task.cancel()
        self._spin_thread.join(timeout=2.0)
        try:
            self._node.destroy_node()
        except Exception:  # noqa: BLE001
            pass
        if rclpy.ok():
            rclpy.shutdown()
        log.info("[robot_bridge] ROS2 브리지 종료")


def build_ros2_bridge(robot_ids: list[str], sink: RobotBridgeSink, loop: Any) -> "Ros2Bridge":
    """ROS2 실기 브리지(프로듀서-컨슈머)를 만든다. `loop` = FastAPI 이벤트 루프.

    rclpy 가 없으면(개발/CI) 명확한 오류를 던진다 → AMR_BRIDGE_BACKEND=loopback 을 쓸 것.
    """
    try:  # pragma: no cover - rclpy 없는 환경(pytest venv)에서는 여기서 끝
        import rclpy  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ROS2(rclpy) 를 임포트할 수 없습니다. `source /opt/ros/humble/setup.bash` 후 "
            "실행하거나, 개발/CI 에서는 AMR_BRIDGE_BACKEND=loopback 을 쓰십시오."
        ) from exc
    return Ros2Bridge(robot_ids, sink, loop)  # pragma: no cover
