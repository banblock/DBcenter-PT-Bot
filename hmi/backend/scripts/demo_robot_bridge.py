"""로봇단(ROS2) 계약 터미널 데모 — 실장비/rclpy 없이 §10 왕복을 눈으로 확인한다.

코드 리뷰용. 실제 로봇이 없으므로, 이 스크립트가 "백엔드 ↔ 로봇" 구간을 한 프로세스
안에서 재현한다:

  1) 백엔드가 로봇으로 명령을 발행 (§10-2 /{robot_id}/command envelope)  ← 하행
  2) 로봇이 명령을 수락/도착 확인 (§10-3 /{robot_id}/command_ack)          ↑ 상행
  3) 로봇이 상태·위치·배터리·탐지를 올림 (§10-1 구독 토픽)                ↑ 상행

실행:
    cd hmi/backend && ./.venv/bin/python -m scripts.demo_robot_bridge
"""

from __future__ import annotations

import json

from app.robot_bridge import COMMAND_TOPIC, RobotBridge


def line(title: str) -> None:
    print(f"\n{'─' * 72}\n▶ {title}\n{'─' * 72}")


class PrintPublisher:
    """백엔드 → 로봇: /{robot_id}/command 로 나가는 프레임을 그대로 출력."""

    def __call__(self, topic: str, payload: str) -> None:
        print(f"  [백엔드→로봇]  publish {topic}")
        print(f"               {payload}")


class PrintSink:
    """로봇 → 백엔드: 구독으로 들어온 것을 백엔드가 어떻게 받는지 출력."""

    def robot_status(self, robot_id, **f):
        print(f"  [로봇→백엔드]  ROBOT_STATUS  {robot_id}  {f}")

    def pose_corrected(self, robot_id, payload):
        print(f"  [로봇→백엔드]  POSE_CORRECTED {robot_id}  {payload}")

    def detection(self, robot_id, payload):
        print(f"  [로봇→백엔드]  DETECTION     {robot_id}  {payload}")

    def safety_event(self, robot_id, code, detail):
        print(f"  [로봇→백엔드]  SAFETY_EVENT  {robot_id}  {code}: {detail}")

    def checkpoint(self, robot_id, raw):
        print(f"  [로봇→백엔드]  CHECKPOINT    {robot_id}  {raw!r}")

    def command_ack(self, robot_id, payload):
        print(f"  [로봇→백엔드]  COMMAND_ACK   {robot_id}  {payload}")


def main() -> None:
    bridge = RobotBridge(["amr_1", "amr_2"], publisher=PrintPublisher(), sink=PrintSink())

    line("1) 하행 — UI 버튼 → 백엔드 → 로봇으로 GOTO 명령 발행 (§10-2)")
    result = bridge.publish_command(
        "amr_1", "GOTO", {"waypoints": [{"x": 3.2, "y": -1.5, "theta": 1.57}], "event_id": None}
    )
    cmd_id = result["command_id"]
    print(f"  publish_command 반환: {result}")
    print(f"  → command_id={cmd_id} 가 inflight 로 대기(ACK 기다림)")

    line("2) 상행 — 로봇이 명령 수락+도착 확인 응답 (§10-3 command_ack)")
    ack = {"command_id": cmd_id, "accepted": True, "reason": None, "eta_sec": 24}
    bridge.on_command_ack("amr_1", json.dumps(ack))
    print(f"  → inflight 에서 제거됨? {cmd_id not in bridge._session('amr_1').inflight}")  # noqa: SLF001

    line("3) 상행 — 로봇 텔레메트리 업로드 (§10-1 구독 토픽)")
    bridge.on_robot_state("amr_1", "PATROLLING:노드 N-004 이동 중")
    bridge.on_amcl_pose("amr_1", x=3.21, y=-1.48, theta=1.55)
    bridge.on_battery_state("amr_1", 0.76)
    bridge.on_detection("amr_1", json.dumps({"type": "SMOKE", "conf": 0.91, "bbox": [120, 80, 340, 420]}))
    bridge.on_safety_event("amr_1", "ERR_NAV:경로 계획 실패")

    line("4) 다중 AMR — 두 대에 동시 명령, 세션 격리 확인")
    bridge.publish_command("amr_1", "START_PATROL", {"mission_id": "MSN-1", "nodes": [], "loop": True})
    bridge.publish_command("amr_2", "DOCK", {"dock_id": "DOCK-2"})
    for rid in ("amr_1", "amr_2"):
        print(f"  {rid} 온라인={bridge.is_online(rid)} inflight={len(bridge._session(rid).inflight)}건")  # noqa: SLF001

    line("5) 통신 끊김 → 재접속 복구 (미확정 명령 재발행)")
    r = bridge.publish_command("amr_2", "GOTO", {"waypoints": [{"x": 1, "y": 1, "theta": 0}], "event_id": None})
    print(f"  amr_2 에 GOTO {r['command_id']} 발행")
    bridge.on_disconnect("amr_2")
    print(f"  amr_2 끊김 → 온라인={bridge.is_online('amr_2')}, inflight 보존됨")
    resent = bridge.on_reconnect("amr_2")
    print(f"  amr_2 재접속 → 재발행된 command_id: {resent}")

    print(f"\n{'═' * 72}\n✅ §10 로봇단 계약 왕복 데모 완료 (하행 발행 · 상행 ACK/텔레메트리 · 재접속 복구)\n{'═' * 72}")


if __name__ == "__main__":
    main()
