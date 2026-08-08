"""Control Node - test implementation of the AMR Control NODE side of the
system chart's control architecture. Run one instance per robot; each
instance is meant to run on that robot's own laptop (namespace passed as
a CLI arg so it's a standalone process, not tied to any other robot):

    python3 control_node.py robot3
    python3 control_node.py robot8

It waits for its mission (waypoint list with gate/crossing tags) from the
Fleet Node, then walks the waypoints one at a time:
  - if a waypoint is a shared crossing point, requests occupancy from the
    Fleet Node and waits for a grant before moving (교차지점인가? ->
    점유돼있는가?)
  - navigates to the waypoint
  - if the waypoint is tagged has_gate, performs a (stubbed) gate check
    (해당 위치 차단기 유무 확인 -> 차단기 점검)
  - releases the crossing point once past it
"""

import json
import sys
import time

import rclpy
from rclpy.qos import (QoSProfile, QoSDurabilityPolicy,
                        QoSReliabilityPolicy, QoSHistoryPolicy)
from std_msgs.msg import String
from turtlebot4_navigation.turtlebot4_navigator import TurtleBot4Navigator

try:
    from control_amr.gate_alignment_flow import GateAlignmentFlowSupport
    from control_amr.inspection_flow import InspectionFlowSupport
    from control_amr.mission_flow import MissionFlowSupport
    from control_amr.navigation_flow import NavigationFlowSupport
    from control_amr.state_flow import StateFlowSupport
except ModuleNotFoundError:
    from gate_alignment_flow import GateAlignmentFlowSupport
    from inspection_flow import InspectionFlowSupport
    from mission_flow import MissionFlowSupport
    from navigation_flow import NavigationFlowSupport
    from state_flow import StateFlowSupport

MISSION_QOS = QoSProfile(
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    reliability=QoSReliabilityPolicy.RELIABLE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)


class ControlNode:
    def __init__(self, namespace):
        self.namespace = namespace
        self.navigator = TurtleBot4Navigator(namespace=namespace)
        # AMCL is assumed to already be localized by hand in RViz beforehand -
        # see multi_robot_nav.py for why this matters.
        self.navigator.initial_pose_received = True
        self.mission_flow = MissionFlowSupport(namespace, self.navigator)
        self.navigation_flow = NavigationFlowSupport(namespace, self.navigator)
        self.inspection_flow = InspectionFlowSupport(namespace, self.navigator)
        self.state_flow = StateFlowSupport(namespace, self.navigator)
        self.gate_alignment_flow = GateAlignmentFlowSupport(namespace, self.navigator)
        self.navigation_interrupt_reason = None
        # TODO: 교차점 점유 허가 대기 timeout 정책이 정해지면 초 단위 값으로 설정한다.
        self.crossing_request_timeout_sec = None
        self.mission_aborted = False

        self.mission = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/mission', self._on_mission, MISSION_QOS)

        self.granted_point = None
        self.navigator.create_subscription(
            String, '/fleet/occupancy_grant', self._on_grant, 10)
        self.request_pub = self.navigator.create_publisher(
            String, '/fleet/occupancy_request', 10)
        self.release_pub = self.navigator.create_publisher(
            String, '/fleet/occupancy_release', 10)

        # Dispatched by Fleet Node when it wants this robot to break off
        # patrol and go check an anomaly (이상신호 감지 -> 임무 수행 로봇 선택).
        self.anomaly_pending = False
        self.anomaly_location = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/anomaly', self._on_anomaly, 10)
        self.anomaly_done_pub = self.navigator.create_publisher(
            String, '/fleet/anomaly_done', 10)

    def _on_mission(self, msg):
        try:
            mission = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self._report_failure('invalid_mission_json', {'error': str(exc)})
            return
        if self._validate_mission(mission):
            self.mission = mission

    def _on_anomaly(self, msg):
        try:
            self.anomaly_location = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self._report_failure('invalid_anomaly_json', {'error': str(exc)})
            return
        self.anomaly_pending = True

    def _on_grant(self, msg):
        try:
            grant = json.loads(msg.data)
            robot = grant['robot']
            granted = grant['granted']
            point = grant['point']
        except (json.JSONDecodeError, KeyError) as exc:
            self._report_failure('invalid_occupancy_grant', {'error': str(exc)})
            return
        if robot == self.namespace and granted:
            self.granted_point = point

    def _wait_for_mission(self):
        self._publish_state('waiting_mission')
        self.navigator.info(f'[{self.namespace}] waiting for mission from Fleet Node...')
        while self.mission is None:
            rclpy.spin_once(self.navigator, timeout_sec=0.5)

    def _request_crossing(self, point_id):
        # spin_once(timeout_sec=X) returns as soon as ANY callback fires, not
        # after X seconds - with other subscriptions active it returns almost
        # immediately, turning this into a publish busy-loop. Rate-limit the
        # publish explicitly instead of relying on spin_once for pacing.
        self._publish_state('crossing_wait', {'point_id': point_id})
        self.navigator.info(f'[{self.namespace}] requesting crossing point {point_id}...')
        req = String()
        req.data = json.dumps({'robot': self.namespace, 'point': point_id})
        last_publish = 0.0
        started_at = time.monotonic()
        while self.granted_point != point_id:
            now = time.monotonic()
            if (self.crossing_request_timeout_sec is not None and
                    now - started_at >= self.crossing_request_timeout_sec):
                self._report_failure('crossing_grant_timeout', {'point_id': point_id})
                return False
            if now - last_publish >= 0.5:
                self.request_pub.publish(req)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        self.navigator.info(f'[{self.namespace}] granted crossing point {point_id}')
        self._publish_state('crossing_granted', {'point_id': point_id})
        return True

    def _release_crossing(self, point_id):
        msg = String()
        msg.data = json.dumps({'robot': self.namespace, 'point': point_id})
        self.release_pub.publish(msg)
        self.granted_point = None
        self._publish_state('crossing_released', {'point_id': point_id})

    def _check_gate(self):
        # Stub for the real gate/breaker inspection (camera/vision node).
        self._publish_state('gate_checking')
        self.navigator.info(f'[{self.namespace}] gate present - running gate check...')
        gate_ok = self.inspection_flow.inspect_gate()
        if not gate_ok:
            self._report_failure('gate_check_failed')
            return False
        time.sleep(2.0)
        self.navigator.info(f'[{self.namespace}] gate check done')
        return True

    def _prepare_gate_alignment(self, waypoint):
        self._publish_state('gate_aligning', waypoint)
        if not self.gate_alignment_flow.prepare_for_gate_check(waypoint):
            self._report_failure('gate_alignment_failed', waypoint)
            return False
        self._publish_state('gate_alignment_done', waypoint)
        return True

    def _move_to(self, pose):
        """Navigate to pose, polling isTaskComplete() so an incoming anomaly
        can interrupt the drive. Returns True if the goal was reached, False
        if it was cancelled because an anomaly came in mid-flight."""
        self.navigation_interrupt_reason = None
        self.navigator.goToPose(pose)
        while not self.navigator.isTaskComplete():
            if self._check_collision_risk():
                self.navigation_interrupt_reason = 'collision_risk'
                self._cancel_navigation_task()
                self._handle_collision_risk()
                return False
            if self.anomaly_pending:
                self.navigation_interrupt_reason = 'anomaly'
                self.navigator.info(
                    f'[{self.namespace}] anomaly signal received - interrupting patrol')
                self.navigator.cancelTask()
                while not self.navigator.isTaskComplete():
                    time.sleep(0.1)
                return False
        result = self._get_navigation_result()
        return self._handle_navigation_result(result)

    def _handle_anomaly(self):
        loc = self.anomaly_location
        self.anomaly_pending = False
        self._publish_state('anomaly_moving', loc)
        self.navigator.info(
            f'[{self.namespace}] heading to anomaly at ({loc["x"]}, {loc["y"]})')
        pose = self.navigator.getPoseStamped(
            [float(loc['x']), float(loc['y'])], float(loc['yaw']))
        self.navigator.startToPose(pose)
        # Stub for the real anomaly inspection (camera/vision node).
        if not self._wait_for_anomaly_arrival():
            self._report_failure('anomaly_arrival_failed', loc)
            return
        self._publish_state('anomaly_checking', loc)
        self.navigator.info(f'[{self.namespace}] checking anomaly...')
        self.inspection_flow.inspect_anomaly(loc)
        time.sleep(2.0)
        self.navigator.info(f'[{self.namespace}] anomaly check done, resuming patrol')

        done = String()
        done.data = json.dumps({'robot': self.namespace})
        self.anomaly_done_pub.publish(done)
        self._publish_state('patrol_resuming')

    def _validate_mission(self, mission):
        return self.mission_flow.validate_mission(mission)

    def _publish_state(self, state, detail=None):
        return self.state_flow.publish_state(state, detail)

    def _check_collision_risk(self):
        return self.navigation_flow.check_collision_risk()

    def _handle_collision_risk(self):
        self.navigation_flow.handle_collision_risk()

    def _cancel_navigation_task(self):
        self.navigator.cancelTask()
        while not self.navigator.isTaskComplete():
            time.sleep(0.1)

    def _request_route_update(self, reason, current_waypoint=None):
        return self.mission_flow.request_route_update(reason, current_waypoint)

    def _get_navigation_result(self):
        return self.navigation_flow.get_navigation_result()

    def _handle_navigation_result(self, result):
        return self.navigation_flow.handle_navigation_result(result)

    def _wait_for_anomaly_arrival(self):
        return self.navigation_flow.wait_until_pose_reached()

    def _check_battery_status(self):
        return self.state_flow.check_battery()

    def _handle_low_battery_if_needed(self):
        battery_state = self._check_battery_status()
        if battery_state in ('low', 'critical'):
            handled = self.state_flow.handle_low_battery()
            if handled:
                self.mission_aborted = True
            return handled
        return False

    def _handle_navigation_interrupt(self, waypoint):
        if self.navigation_interrupt_reason == 'anomaly':
            self._handle_anomaly()
            return True
        if self.navigation_interrupt_reason == 'collision_risk':
            return self._request_route_update('collision_risk', waypoint) is not None
        recovered = self.navigation_flow.recover_from_navigation_failure(
            'navigation_failed')
        if recovered:
            return True
        return self._request_route_update('navigation_failed', waypoint) is not None

    def _report_waypoint_reached(self, waypoint_index, waypoint):
        return self.state_flow.report_waypoint_reached(waypoint_index, waypoint)

    def _report_failure(self, reason, detail=None):
        return self.state_flow.report_failure(reason, detail)

    def _handle_mission_complete(self):
        self.mission_flow.handle_mission_complete()
        next_action = self.mission_flow.choose_post_mission_action()
        if next_action == 'charge':
            self.state_flow.return_to_charger()
        return next_action

    def run(self):
        self._wait_for_mission()
        self.navigator.waitUntilNav2Active()

        if self.navigator.getDockedStatus():
            self.navigator.info(f'[{self.namespace}] docked, undocking...')
            self.navigator.undock()

        for i, wp in enumerate(self.mission):
            point_id = wp.get('point_id')
            if self._handle_low_battery_if_needed():
                break

            if point_id and not self._request_crossing(point_id):
                self.mission_aborted = True
                break

            self._publish_state('moving', {'waypoint_index': i, 'waypoint': wp})
            self.navigator.info(
                f'[{self.namespace}] moving to waypoint {i + 1}/{len(self.mission)}')
            pose = self.navigator.getPoseStamped([wp['x'], wp['y']], wp['yaw'])
            while not self._move_to(pose):
                if not self._handle_navigation_interrupt(wp):
                    self._report_failure('navigation_interrupt_unresolved', wp)
                    self.mission_aborted = True
                    break
            if self.mission_aborted:
                break

            if point_id:
                self._release_crossing(point_id)

            if wp.get('has_gate'):
                if not self._prepare_gate_alignment(wp):
                    self.mission_aborted = True
                    break
                if not self._check_gate():
                    self.mission_aborted = True
                    break

            self._report_waypoint_reached(i, wp)

        if self.mission_aborted:
            self._report_failure('mission_aborted')
        else:
            self._handle_mission_complete()


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 control_node.py <namespace>  (e.g. robot3 or robot8)')
        sys.exit(1)
    namespace = sys.argv[1]

    rclpy.init()
    node = ControlNode(namespace)
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    node.navigator.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
