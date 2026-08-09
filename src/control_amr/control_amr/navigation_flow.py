"""Navigation and collision-related scaffolding for the AMR Control Node."""

import json
import math
import time

import rclpy
from nav2_simple_commander.robot_navigator import TaskResult
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

# 가장 가까운 장애물이 이 거리(m)보다 가까우면 충돌 위험으로 본다.
# TODO: 실제 로봇/공간 기준으로 튜닝 필요.
COLLISION_RISK_DISTANCE_M = 0.05

# 코스트맵 클리어 후 재시도를 이 횟수까지만 허용하고, 넘으면 request_route_update로 넘긴다.
# TODO: 이상적인 정책이 정해지면 값을 조정한다.
MAX_RECOVERY_ATTEMPTS = 2


class NavigationFlowSupport:
    """Hooks for navigation result checks, collision checks, and recovery."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self.latest_scan_ranges = None
        self._recovery_attempts = 0

        # TurtleBot4Navigator가 namespace로 생성돼있어서 상대 토픽 이름을 쓰면
        # 자동으로 /<namespace>/scan 으로 붙는다 (다른 곳의 String 구독들과 동일 패턴).
        self.navigator.create_subscription(LaserScan, 'scan', self._on_scan, 10)

        # ------------------------------------------------------------
        # Fleet Node/UI에 충돌 위험/복구 상태를 알리는 topic들.
        # NOTE: 아래 topic 이름은 전부 "임의로 지정한 이름"이다. mission_flow.py의
        # patrol_status 패턴과 이름 규칙만 맞췄고, Fleet Node를 담당하는 팀원과
        # 이름/메시지 형식을 반드시 맞춰야 한다.
        # ------------------------------------------------------------
        self.collision_risk_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/collision_risk', 10)
        self.recovery_status_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/navigation_recovery', 10)

    def _on_scan(self, msg):
        self.latest_scan_ranges = msg.ranges

    def check_collision_risk(self):
        if not self.latest_scan_ranges:
            return False
        valid_ranges = [r for r in self.latest_scan_ranges if r > 0.0 and math.isfinite(r)]
        if not valid_ranges:
            return False
        return min(valid_ranges) < COLLISION_RISK_DISTANCE_M

    def handle_collision_risk(self):
        closest = min(self.latest_scan_ranges) if self.latest_scan_ranges else None
        self.navigator.info(
            f'[{self.namespace}] collision risk detected (closest={closest})')
        msg = String()
        msg.data = json.dumps({'robot': self.namespace, 'closest_distance': closest})
        self.collision_risk_pub.publish(msg)

    def get_navigation_result(self):
        result = self.navigator.getResult()
        if result == TaskResult.SUCCEEDED:
            return 'succeeded'
        if result == TaskResult.CANCELED:
            return 'canceled'
        if result == TaskResult.FAILED:
            return 'failed'
        return 'unknown'

    def handle_navigation_result(self, result):
        if result == 'succeeded':
            self._recovery_attempts = 0
            return True
        if result == 'canceled':
            self.navigator.info(f'[{self.namespace}] navigation canceled')
            return False
        self.navigator.info(f'[{self.namespace}] navigation failed (result={result})')
        return False

    def wait_until_pose_reached(self):
        # time.sleep() 대신 spin_once를 반복 호출해서, 대기 중에도 이 navigator에
        # 걸려있는 다른 구독(mission/anomaly/occupancy grant 등) 콜백이 계속 처리되게 한다.
        while not self.navigator.isTaskComplete():
            if self.check_collision_risk():
                self.handle_collision_risk()
                self.navigator.cancelTask()
                while not self.navigator.isTaskComplete():
                    rclpy.spin_once(self.navigator, timeout_sec=0.1)
                return False
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        return self.get_navigation_result() == 'succeeded'

    def recover_from_navigation_failure(self, reason):
        self.navigator.info(f'[{self.namespace}] navigation recovery requested: {reason}')
        self._recovery_attempts += 1

        if self._recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            self.navigator.info(f'[{self.namespace}] recovery attempts exhausted')
            self._publish_recovery_status(reason, exhausted=True)
            self._recovery_attempts = 0
            return False

        self._publish_recovery_status(reason, exhausted=False)
        self.navigator.clearAllCostmaps()
        started_at = time.monotonic()
        while time.monotonic() - started_at < 1.0:
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        return True

    def _publish_recovery_status(self, reason, exhausted):
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace,
            'reason': reason,
            'attempt': self._recovery_attempts,
            'exhausted': exhausted,
        })
        self.recovery_status_pub.publish(msg)
