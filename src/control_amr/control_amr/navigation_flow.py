"""Navigation, collision detection, and recovery support."""

import json
import math
import time

import rclpy
from nav2_simple_commander.robot_navigator import TaskResult
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

# 가장 가까운 장애물이 이 거리(m)보다 가까우면 충돌 위험으로 본다.
# 0.30m였을 때 도킹 스테이션에서 나오자마자(도크/벽이 가까운 상태) 바로
# 걸려버렸고, Fleet 쪽에 route_update_request 응답이 아직 없어서(별도
# 이슈) 한 번 걸리면 10초 뒤 mission_aborted로 끝나버림 - 그래서 우선
# 최대한 낮춰둔 값. 실제 LiDAR range_min보다 낮으면(_on_scan이
# msg.range_min으로 이미 걸러서) 사실상 트리거 안 되니 이보다 더 낮춰도
# 의미 없음. 이 체크는 어디까지나 보조 안전장치이고, 1차 장애물 회피는
# Nav2 costmap/컨트롤러가 담당한다.
# TODO: 실제 로봇/공간 기준으로 재튜닝 필요.
COLLISION_RISK_DISTANCE_M = 0.05
FRONT_SCAN_HALF_ANGLE_RAD = math.radians(35.0)

# 코스트맵 클리어 후 재시도를 이 횟수까지만 허용하고, 넘으면 request_route_update로 넘긴다.
# TODO: 이상적인 정책이 정해지면 값을 조정한다.
MAX_RECOVERY_ATTEMPTS = 2


class NavigationFlowSupport:
    """Hooks for navigation result checks, collision checks, and recovery."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self._closest_front_obstacle = None
        self._recovery_attempts = 0

        # TurtleBot4Navigator가 namespace로 생성돼있어서 상대 토픽 이름을 쓰면
        # 자동으로 /<namespace>/scan 으로 붙는다 (다른 곳의 String 구독들과 동일 패턴).
        self.navigator.create_subscription(
            LaserScan, 'scan', self._on_scan, 10)

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
        front_ranges = []
        for index, distance in enumerate(msg.ranges):
            angle = msg.angle_min + index * msg.angle_increment
            angle = math.atan2(math.sin(angle), math.cos(angle))
            if (abs(angle) <= FRONT_SCAN_HALF_ANGLE_RAD and
                    math.isfinite(distance) and
                    msg.range_min <= distance <= msg.range_max):
                front_ranges.append(distance)
        self._closest_front_obstacle = (
            min(front_ranges) if front_ranges else None)

    def check_collision_risk(self):
        return (self._closest_front_obstacle is not None and
                self._closest_front_obstacle < COLLISION_RISK_DISTANCE_M)

    def handle_collision_risk(self):
        closest = self._closest_front_obstacle
        self.navigator.info(
            f'[{self.namespace}] collision risk detected (closest={closest})')
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace, 'closest_distance': closest})
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
        self.navigator.info(
            f'[{self.namespace}] navigation failed (result={result})')
        return False

    def recover_from_navigation_failure(self, reason):
        self.navigator.info(
            f'[{self.namespace}] navigation recovery requested: {reason}')
        self._recovery_attempts += 1

        if self._recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            self.navigator.info(
                f'[{self.namespace}] recovery attempts exhausted')
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
