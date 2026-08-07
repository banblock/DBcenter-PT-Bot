#!/usr/bin/env python3
"""테스트 로봇 ROS2 노드 — 실장비 대역 (코드 리뷰 데모용).

실제 AMR 없이, 백엔드(robot_bridge, AMR_BRIDGE_BACKEND=ros2)와 진짜 ROS2 토픽으로
주고받는다. 명세서 §10 의 로봇 쪽 절반을 구현한다:

  구독  /{robot_id}/command       (std_msgs/String)  ← 백엔드 명령 수신 → 화면에 출력
  발행  /{robot_id}/command_ack   (std_msgs/String)  → §10-3 ACK 되돌림
  발행  /{robot_id}/robot_state   (std_msgs/String)  → "STATE:msg" 1Hz
  발행  /{robot_id}/amcl_pose     (PoseWithCovarianceStamped)
  발행  /{robot_id}/battery_state (sensor_msgs/BatteryState)

실행 (ROS2 Humble 이 source 된 셸에서):
    source /opt/ros/humble/setup.bash
    python3 hmi/backend/scripts/fake_robot_node.py amr_1 amr_2

확인:
    ros2 topic echo /amr_1/command      # 백엔드가 실제로 발행하는지 원시 메시지로 확인
"""

from __future__ import annotations

import json
import math
import sys

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

# command_type → 로봇이 전이하는 상태(데모용 단순 매핑).
CMD_TO_STATE = {
    "START_PATROL": "PATROLLING",
    "PAUSE": "PATROL_PAUSED",
    "RESUME": "RESUMING",
    "CANCEL": "IDLE",
    "GOTO": "DISPATCHING",
    "INSPECT": "INSPECTING",
    "EVACUATE": "DISPATCHING",
    "ESTOP": "EMERGENCY_STOP",
    "RESET": "IDLE",
    "DOCK": "DOCKING",
}


class FakeRobot:
    """로봇 1대의 상태 + 퍼블리셔 묶음."""

    def __init__(self, node: Node, robot_id: str) -> None:
        self.node = node
        self.rid = robot_id
        self.state = "IDLE"
        self.msg = "대기"
        self.battery = 0.9
        self.x, self.y, self.theta = 0.0, 0.0, 0.0

        ns = f"/{robot_id}"
        self.ack_pub = node.create_publisher(String, f"{ns}/command_ack", 10)
        self.state_pub = node.create_publisher(String, f"{ns}/robot_state", 10)
        self.pose_pub = node.create_publisher(PoseWithCovarianceStamped, f"{ns}/amcl_pose", 10)
        self.batt_pub = node.create_publisher(BatteryState, f"{ns}/battery_state", 10)
        node.create_subscription(String, f"{ns}/command", self.on_command, 10)

    # 백엔드 → 로봇: 명령 수신 (§10-2)
    def on_command(self, msg: String) -> None:
        try:
            env = json.loads(msg.data)
        except json.JSONDecodeError:
            self.node.get_logger().warn(f"[{self.rid}] command 파싱 실패: {msg.data!r}")
            return
        ctype = env.get("command_type", "?")
        cmd_id = env.get("command_id", "?")
        print(f"\n📥 [{self.rid}] 명령 수신: {ctype}  (command_id={cmd_id})")
        print(f"     payload: {env.get('payload')}")

        # 상태 전이 + 사람이 읽는 메시지
        self.state = CMD_TO_STATE.get(ctype, self.state)
        self.msg = f"{ctype} 수행 중"

        # 로봇 → 백엔드: ACK 되돌림 (§10-3)
        ack = {"command_id": cmd_id, "accepted": True, "reason": None, "eta_sec": 24}
        self.ack_pub.publish(String(data=json.dumps(ack, ensure_ascii=False)))
        print(f"📤 [{self.rid}] command_ack 발행: {ack}")

    # 로봇 → 백엔드: 주기 텔레메트리 (§10-1)
    def tick(self) -> None:
        self.state_pub.publish(String(data=f"{self.state}:{self.msg}"))

        pose = PoseWithCovarianceStamped()
        pose.header.frame_id = "map"
        pose.pose.pose.position.x = self.x
        pose.pose.pose.position.y = self.y
        pose.pose.pose.orientation.z = math.sin(self.theta / 2.0)
        pose.pose.pose.orientation.w = math.cos(self.theta / 2.0)
        self.pose_pub.publish(pose)

        batt = BatteryState()
        batt.percentage = self.battery
        self.batt_pub.publish(batt)

        # 조금씩 움직이는 척 (데모 시각효과)
        if self.state in ("PATROLLING", "DISPATCHING", "RESUMING"):
            self.x += 0.1
            self.battery = max(0.0, self.battery - 0.001)


class FakeRobotNode(Node):
    def __init__(self, robot_ids: list[str]) -> None:
        super().__init__("fake_robot")
        self.robots = [FakeRobot(self, rid) for rid in robot_ids]
        self.create_timer(1.0, self._tick_all)  # 1Hz 텔레메트리
        print("=" * 64)
        print(f" 테스트 로봇 노드 기동 — {', '.join(robot_ids)}")
        print(" 백엔드 명령을 기다립니다. (웹에서 버튼을 누르거나 REST 호출)")
        print("=" * 64)

    def _tick_all(self) -> None:
        for robot in self.robots:
            robot.tick()


def main() -> None:
    # 파일/파이프로 리다이렉트해도 로그가 바로 보이게 라인 버퍼링.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    robot_ids = sys.argv[1:] or ["amr_1", "amr_2"]
    rclpy.init()
    node = FakeRobotNode(robot_ids)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
