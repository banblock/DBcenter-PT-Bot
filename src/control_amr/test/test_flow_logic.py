"""Unit tests for flow logic that does not require a running ROS graph."""

import json

from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from control_amr.mission_flow import MissionFlowSupport
from control_amr.state_flow import StateFlowSupport


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class FakeNavigator:
    def __init__(self):
        self.publishers = []
        self.subscriptions = []
        self.logs = []

    def create_publisher(self, _msg_type, _topic, _qos):
        publisher = FakePublisher()
        self.publishers.append(publisher)
        return publisher

    def create_subscription(self, _msg_type, topic, callback, _qos):
        self.subscriptions.append((topic, callback))
        return object()

    def info(self, message):
        self.logs.append(message)


def test_mission_validation_rejects_non_finite_coordinates():
    flow = MissionFlowSupport('robot3', FakeNavigator())
    assert flow.validate_mission([{'x': 0.0, 'y': 1.0, 'yaw': 0.0}])
    assert not flow.validate_mission(
        [{'x': float('nan'), 'y': 1.0, 'yaw': 0.0}])


def test_route_response_accepts_only_valid_waypoints():
    flow = MissionFlowSupport('robot3', FakeNavigator())
    invalid = String()
    invalid.data = json.dumps({'waypoints': 'not-a-list'})
    flow._on_route_update_response(invalid)
    assert flow._route_update_response is None

    valid = String()
    valid.data = json.dumps({
        'waypoints': [{'x': 0.0, 'y': 1.0, 'yaw': 0.0}]})
    flow._on_route_update_response(valid)
    assert flow._route_update_response == [
        {'x': 0.0, 'y': 1.0, 'yaw': 0.0}]


def test_next_mission_request_contains_robot_and_reason():
    navigator = FakeNavigator()
    flow = MissionFlowSupport('robot3', navigator)
    flow.request_next_mission()
    payload = json.loads(flow.next_mission_request_pub.messages[-1].data)
    assert payload == {
        'robot': 'robot3',
        'reason': 'patrol_delay_elapsed',
    }


def test_battery_messages_are_republished_without_control_decisions():
    flow = StateFlowSupport('robot3', FakeNavigator())
    battery = BatteryState()
    battery.percentage = 0.5
    battery.voltage = 14.5
    battery.current = float('nan')
    flow._on_battery(battery)
    payload = json.loads(flow.battery_pub.messages[-1].data)
    assert payload['robot'] == 'robot3'
    assert payload['percentage'] == 0.5
    assert payload['voltage'] == 14.5
    assert payload['current'] is None
