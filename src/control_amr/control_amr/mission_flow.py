"""Mission validation, route-update, and completion support."""

import json
import math
import time

import rclpy
from std_msgs.msg import String

REQUIRED_WAYPOINT_FIELDS = ('x', 'y', 'yaw')
ROUTE_UPDATE_TIMEOUT_SEC = 10.0
NEXT_PATROL_DELAY_SEC = 10 * 60


class MissionFlowSupport:
    """Validate missions and exchange route updates with the Fleet Node."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self.mission_reject_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/mission_reject', 10)
        self.route_update_request_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/route_update_request', 10)
        self.mission_complete_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/mission_complete', 10)
        self.next_mission_request_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/request_next_mission', 10)
        self._route_update_response = None
        navigator.create_subscription(
            String, f'/fleet/{namespace}/route_update_response',
            self._on_route_update_response, 10)

    def validate_mission(self, mission, publish_rejection=True):
        def reject(reason, detail=None):
            if publish_rejection:
                self._reject_mission(reason, detail)
            return False

        if not isinstance(mission, list) or not mission:
            return reject('mission_must_be_nonempty_list')
        for index, waypoint in enumerate(mission):
            if not isinstance(waypoint, dict):
                return reject('waypoint_must_be_object', {'index': index})
            for field in REQUIRED_WAYPOINT_FIELDS:
                value = waypoint.get(field)
                if (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or
                        not math.isfinite(value)):
                    return reject(
                        'waypoint_missing_or_invalid_field',
                        {'index': index, 'field': field})
            point_id = waypoint.get('point_id')
            if point_id is not None and not isinstance(point_id, str):
                return reject('point_id_must_be_string', {'index': index})
            if ('has_gate' in waypoint and
                    not isinstance(waypoint['has_gate'], bool)):
                return reject('has_gate_must_be_bool', {'index': index})
            if waypoint.get('has_gate') and 'gate_yaw' in waypoint:
                gate_yaw = waypoint['gate_yaw']
                if (isinstance(gate_yaw, bool) or
                        not isinstance(gate_yaw, (int, float)) or
                        not math.isfinite(gate_yaw)):
                    return reject(
                        'gate_yaw_must_be_finite_number', {'index': index})
        return True

    def _reject_mission(self, reason, detail=None):
        self.navigator.info(
            f'[{self.namespace}] mission rejected: {reason} ({detail})')
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace, 'reason': reason, 'detail': detail})
        self.mission_reject_pub.publish(msg)

    def _on_route_update_response(self, msg):
        try:
            response = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.navigator.info(
                f'[{self.namespace}] invalid route_update_response: {exc}')
            return
        if not isinstance(response, dict):
            self.navigator.info(
                f'[{self.namespace}] invalid route_update_response: '
                'expected object')
            return
        waypoints = response.get('waypoints')
        if not self.validate_mission(waypoints, publish_rejection=False):
            self.navigator.info(
                f'[{self.namespace}] invalid route_update_response waypoints')
            return
        self._route_update_response = waypoints

    def request_route_update(self, reason, current_waypoint=None):
        self._route_update_response = None
        request = String()
        request.data = json.dumps({
            'robot': self.namespace,
            'reason': reason,
            'current_waypoint': current_waypoint,
        })
        started_at = time.monotonic()
        last_publish = 0.0
        while self._route_update_response is None:
            now = time.monotonic()
            if now - started_at >= ROUTE_UPDATE_TIMEOUT_SEC:
                self.navigator.info(
                    f'[{self.namespace}] route update request timed out')
                return None
            if now - last_publish >= 0.5:
                self.route_update_request_pub.publish(request)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        return self._route_update_response

    def handle_mission_complete(self):
        self.navigator.info(f'[{self.namespace}] mission complete')
        msg = String()
        msg.data = json.dumps({'robot': self.namespace})
        self.mission_complete_pub.publish(msg)

    def wait_until_next_patrol(self):
        self.navigator.info(
            f'[{self.namespace}] next patrol in '
            f'{NEXT_PATROL_DELAY_SEC} seconds')
        deadline = time.monotonic() + NEXT_PATROL_DELAY_SEC
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            rclpy.spin_once(
                self.navigator, timeout_sec=min(0.5, remaining))

    def get_next_patrol_delay_sec(self):
        return NEXT_PATROL_DELAY_SEC

    def request_next_mission(self):
        self.navigator.info(
            f'[{self.namespace}] requesting next patrol mission')
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace,
            'reason': 'patrol_delay_elapsed',
        })
        self.next_mission_request_pub.publish(msg)
