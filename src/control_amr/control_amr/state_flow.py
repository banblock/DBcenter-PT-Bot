"""Robot state and Fleet Node reporting."""

import json
import math

from sensor_msgs.msg import BatteryState
from std_msgs.msg import String


class StateFlowSupport:
    """Publish robot state, waypoint progress, and failures."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self.state_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/state', 10)
        self.waypoint_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/waypoint_reached', 10)
        self.failure_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/failure', 10)
        self.battery_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/battery_state', 10)
        navigator.create_subscription(
            BatteryState, 'battery_state', self._on_battery, 10)

    def _on_battery(self, msg):
        def finite_or_none(value):
            return value if math.isfinite(value) else None

        self._publish_json(self.battery_pub, {
            'robot': self.namespace,
            'percentage': finite_or_none(msg.percentage),
            'voltage': finite_or_none(msg.voltage),
            'current': finite_or_none(msg.current),
            'power_supply_status': msg.power_supply_status,
            'power_supply_health': msg.power_supply_health,
        })

    def _publish_json(self, publisher, payload):
        msg = String()
        msg.data = json.dumps(payload)
        publisher.publish(msg)
        return payload

    def publish_state(self, state, detail=None):
        return self._publish_json(self.state_pub, {
            'robot': self.namespace,
            'state': state,
            'detail': detail,
        })

    def report_waypoint_reached(self, waypoint_index, waypoint):
        return self._publish_json(self.waypoint_pub, {
            'robot': self.namespace,
            'index': waypoint_index,
            'waypoint': waypoint,
        })

    def report_failure(self, reason, detail=None):
        self.navigator.info(f'[{self.namespace}] failure: {reason}')
        return self._publish_json(self.failure_pub, {
            'robot': self.namespace,
            'reason': reason,
            'detail': detail,
        })
