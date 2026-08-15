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
        # Fleet의 robot_status.py가 /control/<ns>_State에 EMERGENCY_STOP/
        # DISPATCHING/PATROLLING/IDLE 4개 상태를 퍼블리시하고, Control은 같은
        # 토픽에 나머지 세부 상태(도킹, 이상신호 대응 등)를 이어서 퍼블리시하는
        # 설계다(fleet_node.py의 _check_robot_status() 주석 참고). 예전엔
        # 여기서 /fleet/<ns>/state라는 별도 토픽에 쐈는데, 구독자가 없어서
        # Fleet UI에 Control 쪽 세부 상태가 전혀 반영되지 않는 문제가 있었다.
        self.state_pub = navigator.create_publisher(
            String, f'/control/{namespace}_State', 10)
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
        # Fleet 쪽 페이로드({'robot':..., 'status':...})와 키를 맞추기 위해
        # 'state'가 아니라 'status'로 보낸다 - 같은 토픽을 두 노드가 나눠
        # 쓰므로 구독자가 하나의 스키마로 파싱할 수 있어야 한다.
        return self._publish_json(self.state_pub, {
            'robot': self.namespace,
            'status': state,
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
