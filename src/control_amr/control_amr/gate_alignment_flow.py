"""Gate camera alignment scaffolding for the AMR Control Node."""

import json
import math
import time

import rclpy
from nav2_simple_commander.robot_navigator import TaskResult
from nav_msgs.msg import Odometry
from std_msgs.msg import String

ALIGNMENT_TOLERANCE_RAD = 0.05
SPIN_TIME_ALLOWANCE_SEC = 10.0
CANCEL_WAIT_SEC = 2.0


def _normalize_angle(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


class GateAlignmentFlowSupport:
    """Hooks for aligning the TurtleBot4 camera at gate waypoints."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        # TODO: Fleet Node/UI와 합의한 topic 이름으로 변경한다.
        self.alignment_done_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/gate_alignment_done', 10)
        self._current_yaw = None
        self._odom_sub = self.navigator.create_subscription(
            Odometry, 'odom', self._on_odom, 10)
        # TODO: gate 정렬 완료 후 정지 시간을 운영 정책에 맞게 조정한다.
        self.pause_after_alignment_sec = 2.0

    def _on_odom(self, msg):
        orientation = msg.pose.pose.orientation
        sin_yaw = 2.0 * (
            orientation.w * orientation.z + orientation.x * orientation.y)
        cos_yaw = 1.0 - 2.0 * (
            orientation.y * orientation.y + orientation.z * orientation.z)
        self._current_yaw = math.atan2(sin_yaw, cos_yaw)

    def get_target_direction(self, waypoint):
        # TODO: waypoint의 gate 방향 필드 이름을 확정한다. 예: gate_yaw, camera_yaw.
        # TODO: 방향 필드가 없을 때 기본값을 waypoint yaw로 할지, 현재 자세로 할지 결정한다.
        return waypoint.get('gate_yaw', waypoint.get('yaw'))

    def align_camera_to_gate(self, waypoint):
        target_direction = self.get_target_direction(waypoint)
        if target_direction is None or self._current_yaw is None:
            self.navigator.info(
                f'[{self.namespace}] gate alignment failed: '
                'target direction or odometry unavailable')
            return False
        spin_dist = _normalize_angle(target_direction - self._current_yaw)
        if abs(spin_dist) < ALIGNMENT_TOLERANCE_RAD:
            return True
        self.navigator.info(
            f'[{self.namespace}] aligning camera to gate direction '
            f'{target_direction:.2f} rad (spin {spin_dist:.2f} rad)')
        self.navigator.spin(
            spin_dist=spin_dist, time_allowance=SPIN_TIME_ALLOWANCE_SEC)
        started_at = time.monotonic()
        while not self.navigator.isTaskComplete():
            if time.monotonic() - started_at >= SPIN_TIME_ALLOWANCE_SEC:
                self.navigator.cancelTask()
                cancel_started_at = time.monotonic()
                while (not self.navigator.isTaskComplete() and
                       time.monotonic() - cancel_started_at < CANCEL_WAIT_SEC):
                    time.sleep(0.05)
                self.navigator.info(
                    f'[{self.namespace}] gate alignment spin timed out')
                return False
            time.sleep(0.1)
        return self.navigator.getResult() == TaskResult.SUCCEEDED

    def publish_alignment_done(self, waypoint, success=True):
        # TODO: Fleet Node/UI와 합의한 메시지 schema로 변경한다.
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace,
            'point_id': waypoint.get('point_id'),
            'success': success,
        })
        self.alignment_done_pub.publish(msg)

    def pause_after_alignment(self):
        # TODO: 정지 중에도 anomaly, emergency stop, cancel 신호를 감시한다.
        started_at = time.monotonic()
        while time.monotonic() - started_at < self.pause_after_alignment_sec:
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

    def prepare_for_gate_check(self, waypoint):
        # TODO: 정렬 실패 시 gate check를 진행할지, mission을 중단할지 정책을 정한다.
        success = self.align_camera_to_gate(waypoint)
        self.publish_alignment_done(waypoint, success)
        if not success:
            return False
        self.pause_after_alignment()
        return True
