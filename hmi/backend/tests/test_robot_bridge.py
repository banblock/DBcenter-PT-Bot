"""robot_bridge (§10) 계약 검증.

ROS2/DB 없이 순수 로직만 본다: 발행 envelope(§10-2)·ACK(§10-3)·구독 파싱(§10-1)·
다중 AMR 세션·재접속 복구. 발행은 기록용 퍼블리셔로, 구독 부수효과는 기록용 sink 로 잡는다.
"""

from __future__ import annotations

import json

import pytest

from app.robot_bridge import COMMAND_TOPIC, RobotBridge


class RecordingPublisher:
    """발행된 (topic, envelope) 를 순서대로 모은다."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    def __call__(self, topic: str, payload: str) -> None:
        self.frames.append((topic, json.loads(payload)))

    def for_robot(self, robot_id: str) -> list[dict]:
        want = COMMAND_TOPIC.format(robot_id=robot_id)
        return [env for topic, env in self.frames if topic == want]


class RecordingSink:
    def __init__(self) -> None:
        self.status: list[tuple[str, dict]] = []
        self.detections: list[tuple[str, dict]] = []
        self.pose_corrections: list[tuple[str, dict]] = []
        self.safety: list[tuple[str, str, str]] = []
        self.checkpoints: list[tuple[str, str]] = []
        self.acks: list[tuple[str, dict]] = []

    def robot_status(self, robot_id, **fields):
        self.status.append((robot_id, fields))

    def pose_corrected(self, robot_id, payload):
        self.pose_corrections.append((robot_id, payload))

    def detection(self, robot_id, payload):
        self.detections.append((robot_id, payload))

    def safety_event(self, robot_id, code, detail):
        self.safety.append((robot_id, code, detail))

    def checkpoint(self, robot_id, raw):
        self.checkpoints.append((robot_id, raw))

    def command_ack(self, robot_id, payload):
        self.acks.append((robot_id, payload))


@pytest.fixture
def pub() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def bridge(pub: RecordingPublisher, sink: RecordingSink) -> RobotBridge:
    return RobotBridge(["amr_1", "amr_2"], publisher=pub, sink=sink)


# ══════════════════════════════════════════════════════════════════════════
# §10-2 발행 envelope
# ══════════════════════════════════════════════════════════════════════════
def test_command_envelope_matches_spec(bridge: RobotBridge, pub: RecordingPublisher):
    """§10-2 — /{robot_id}/command 로 {command_id, command_type, payload, issued_at} 발행."""
    result = bridge.publish_command("amr_1", "GOTO", {"waypoints": [{"x": 3.2, "y": -1.5, "theta": 1.57}], "event_id": "EV-1"})

    assert result["accepted"] is True
    frames = pub.for_robot("amr_1")
    assert len(frames) == 1
    env = frames[0]
    assert set(env) == {"command_id", "command_type", "payload", "issued_at"}
    assert env["command_type"] == "GOTO"
    assert env["command_id"] == result["command_id"]
    assert env["payload"]["event_id"] == "EV-1"
    assert env["issued_at"]  # ISO8601 문자열


def test_unknown_command_type_rejected(bridge: RobotBridge):
    """§10-2 표에 없는 command_type 은 거부 — 오타·규격 이탈 차단."""
    with pytest.raises(ValueError):
        bridge.publish_command("amr_1", "TELEPORT", {})


def test_estop_command_dispatches(bridge: RobotBridge, pub: RecordingPublisher):
    bridge.publish_command("amr_1", "ESTOP", {"reason": "OPERATOR"})
    assert pub.for_robot("amr_1")[0]["command_type"] == "ESTOP"


def test_anomaly_hold_command_accepted(bridge: RobotBridge, pub: RecordingPublisher):
    """자체 감지 제자리 정지 — ANOMALY_HOLD 가 §10-2 화이트리스트를 통과해 발행돼야 한다."""
    result = bridge.publish_command("amr_1", "ANOMALY_HOLD", {"event_id": "EV-9"})
    assert result["accepted"] is True  # 화이트리스트 미등록이면 ValueError 로 거부됐을 것
    assert "ANOMALY_HOLD" in [env["command_type"] for _topic, env in pub.frames]


def test_anomaly_resume_command_accepted(bridge: RobotBridge, pub: RecordingPublisher):
    """이상 대응 작업 복귀 — ANOMALY_RESUME 가 §10-2 화이트리스트를 통과해 발행돼야 한다."""
    result = bridge.publish_command("amr_1", "ANOMALY_RESUME", {})
    assert result["accepted"] is True
    assert "ANOMALY_RESUME" in [env["command_type"] for _topic, env in pub.frames]


# ══════════════════════════════════════════════════════════════════════════
# 단일 AMR 목표 하달 · 도착 확인 (§10-2 → §10-3)
# ══════════════════════════════════════════════════════════════════════════
def test_single_amr_goal_dispatch_and_arrival(bridge: RobotBridge, pub: RecordingPublisher, sink: RecordingSink):
    result = bridge.publish_command("amr_1", "GOTO", {"waypoints": [{"x": 2.0, "y": 1.0, "theta": 0.0}], "event_id": None})
    cmd_id = result["command_id"]

    # 발행 직후엔 ACK 전이라 inflight 에 남아 있다.
    session = bridge._session("amr_1")  # noqa: SLF001 - 테스트 확인용
    assert cmd_id in session.inflight
    assert session.inflight[cmd_id].accepted is None

    # §10-3 — 로봇이 도착 확인(accepted=true, eta_sec) 을 보낸다.
    ack = bridge.on_command_ack("amr_1", json.dumps({"command_id": cmd_id, "accepted": True, "reason": None, "eta_sec": 24}))

    assert ack["eta_sec"] == 24
    assert cmd_id not in session.inflight, "확정된 명령은 inflight 에서 빠져야 한다"
    assert sink.acks == [("amr_1", ack)]


def test_ack_for_unknown_command_is_ignored(bridge: RobotBridge):
    assert bridge.on_command_ack("amr_1", json.dumps({"command_id": "CMD-없음", "accepted": True})) is not None
    # 파싱 실패는 None
    assert bridge.on_command_ack("amr_1", "이건 JSON 이 아니다") is None


# ══════════════════════════════════════════════════════════════════════════
# 다중 AMR 동시 명령 처리
# ══════════════════════════════════════════════════════════════════════════
def test_multi_amr_concurrent_commands_are_isolated(bridge: RobotBridge, pub: RecordingPublisher):
    r1 = bridge.publish_command("amr_1", "START_PATROL", {"mission_id": "MSN-1", "nodes": [], "loop": True})
    r2 = bridge.publish_command("amr_2", "DOCK", {"dock_id": "DOCK-2"})

    f1, f2 = pub.for_robot("amr_1"), pub.for_robot("amr_2")
    assert len(f1) == 1 and len(f2) == 1
    assert f1[0]["command_type"] == "START_PATROL"
    assert f2[0]["command_type"] == "DOCK"
    # 세션이 서로 섞이지 않는다.
    assert r1["command_id"] in bridge._session("amr_1").inflight  # noqa: SLF001
    assert r2["command_id"] in bridge._session("amr_2").inflight  # noqa: SLF001
    assert r1["command_id"] not in bridge._session("amr_2").inflight  # noqa: SLF001


# ══════════════════════════════════════════════════════════════════════════
# 통신 끊김 후 재접속 복구
# ══════════════════════════════════════════════════════════════════════════
def test_disconnect_preserves_inflight_and_reconnect_resends(bridge: RobotBridge, pub: RecordingPublisher):
    result = bridge.publish_command("amr_1", "GOTO", {"waypoints": [{"x": 1.0, "y": 1.0, "theta": 0.0}], "event_id": None})
    cmd_id = result["command_id"]
    assert len(pub.for_robot("amr_1")) == 1

    # 끊김 — ACK 못 받은 명령은 버리지 않고 보존한다.
    bridge.on_disconnect("amr_1")
    assert bridge.is_online("amr_1") is False
    assert cmd_id in bridge._session("amr_1").inflight  # noqa: SLF001

    # 재접속 — 미확정 명령을 다시 쏜다.
    resent = bridge.on_reconnect("amr_1")
    assert resent == [cmd_id]
    assert bridge.is_online("amr_1") is True
    assert len(pub.for_robot("amr_1")) == 2, "재접속 시 명령이 재전송돼야 한다"


def test_acked_command_not_resent_after_reconnect(bridge: RobotBridge, pub: RecordingPublisher):
    result = bridge.publish_command("amr_1", "GOTO", {"waypoints": [], "event_id": None})
    bridge.on_command_ack("amr_1", json.dumps({"command_id": result["command_id"], "accepted": True, "eta_sec": 5}))
    bridge.on_disconnect("amr_1")
    resent = bridge.on_reconnect("amr_1")
    assert resent == [], "이미 확정된 명령은 재전송 대상이 아니다"
    assert len(pub.for_robot("amr_1")) == 1


# ══════════════════════════════════════════════════════════════════════════
# §10-1 구독 파싱
# ══════════════════════════════════════════════════════════════════════════
def test_robot_state_string_rule(bridge: RobotBridge, sink: RecordingSink):
    """"STATE:msg" 규칙 — 콜론 앞 상태, 뒤 메시지."""
    bridge.on_robot_state("amr_1", "PATROLLING:노드 N-004 이동 중")
    robot_id, fields = sink.status[-1]
    assert robot_id == "amr_1"
    assert fields["state"] == "PATROLLING"
    assert fields["state_msg"] == "노드 N-004 이동 중"
    assert bridge.is_online("amr_1") is True  # 구독 프레임 = 살아 있음


def test_battery_percentage_normalized(bridge: RobotBridge, sink: RecordingSink):
    bridge.on_battery_state("amr_1", 0.76)  # BatteryState.percentage 0~1
    assert sink.status[-1][1]["battery"] == 76


def test_detection_and_aruco_json(bridge: RobotBridge, sink: RecordingSink):
    bridge.on_detection("amr_1", json.dumps({"type": "FIRE", "conf": 0.87, "bbox": [1, 2, 3, 4]}))
    bridge.on_aruco_correction("amr_1", json.dumps({"marker_id": 7, "error_m": 0.08}))
    assert sink.detections[-1][1]["type"] == "FIRE"
    assert sink.pose_corrections[-1][1]["marker_id"] == 7


def test_safety_event_code_rule(bridge: RobotBridge, sink: RecordingSink):
    bridge.on_safety_event("amr_1", "ERR_NAV:경로 계획 실패")
    assert sink.safety[-1] == ("amr_1", "ERR_NAV", "경로 계획 실패")


def test_malformed_detection_is_ignored(bridge: RobotBridge, sink: RecordingSink):
    bridge.on_detection("amr_1", "{깨진 JSON")
    assert sink.detections == []
