"""Align the robot-mounted camera toward a gate."""

import json
import math
import time

import rclpy
from nav2_simple_commander.robot_navigator import TaskResult
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import String

ALIGNMENT_TOLERANCE_RAD = 0.05
# nav2 spin()은 내부에서 Duration(sec=time_allowance)를 만드는데 sec 필드가
# int만 허용한다(float면 AssertionError로 죽음) - 반드시 정수로 둔다.
SPIN_TIME_ALLOWANCE_SEC = 10
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
        self._current_x = None
        self._current_y = None
        self._pose_sub = self.navigator.create_subscription(
            PoseWithCovarianceStamped, 'amcl_pose', self._on_pose, 10)
        # TODO: gate 정렬 완료 후 정지 시간을 운영 정책에 맞게 조정한다.
        self.pause_after_alignment_sec = 2.0

    def _on_pose(self, msg):
        position = msg.pose.pose.position
        self._current_x = position.x
        self._current_y = position.y
        orientation = msg.pose.pose.orientation
        sin_yaw = 2.0 * (
            orientation.w * orientation.z + orientation.x * orientation.y)
        cos_yaw = 1.0 - 2.0 * (
            orientation.y * orientation.y + orientation.z * orientation.z)
        self._current_yaw = math.atan2(sin_yaw, cos_yaw)

    def get_target_direction(self, waypoint):
        # 차단기를 바라볼 각도(라디안)를 도착 시점에 계산한다. fleet이 실어준
        # gate_look_at([x, y], 차단기 대상 좌표)이 있으면, nav 도착 오차를
        # 감수하고 굳힌 각도 대신 지금 amcl_pose(실제 위치)에서 그 대상까지의
        # 방향을 그 자리에서 다시 계산한다 - 목표에서 조금 벗어나 도착해도
        # 카메라가 정확히 차단기를 겨냥하게 하기 위함이다.
        look_at = waypoint.get('gate_look_at')
        if look_at is not None and self._current_x is not None:
            lx, ly = look_at
            return math.atan2(ly - self._current_y, lx - self._current_x)
        # 하위호환: look_at이 없으면 예전처럼 웨이포인트가 준 각도를 쓴다.
        return waypoint.get('gate_yaw', waypoint.get('yaw'))

    def align_camera_to_gate(self, waypoint):
        target_direction = self.get_target_direction(waypoint)
        if target_direction is None or self._current_yaw is None:
            self.navigator.info(
                f'[{self.namespace}] gate alignment failed: '
                'target direction or AMCL pose unavailable')
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
                    rclpy.spin_once(self.navigator, timeout_sec=0.05)
                self.navigator.info(
                    f'[{self.namespace}] gate alignment spin timed out')
                return False
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
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
