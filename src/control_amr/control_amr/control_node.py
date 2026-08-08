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
        self.mission = json.loads(msg.data)

    def _on_anomaly(self, msg):
        self.anomaly_location = json.loads(msg.data)
        self.anomaly_pending = True

    def _on_grant(self, msg):
        grant = json.loads(msg.data)
        if grant['robot'] == self.namespace and grant['granted']:
            self.granted_point = grant['point']

    def _wait_for_mission(self):
        self.navigator.info(f'[{self.namespace}] waiting for mission from Fleet Node...')
        while self.mission is None:
            rclpy.spin_once(self.navigator, timeout_sec=0.5)

    def _request_crossing(self, point_id):
        # spin_once(timeout_sec=X) returns as soon as ANY callback fires, not
        # after X seconds - with other subscriptions active it returns almost
        # immediately, turning this into a publish busy-loop. Rate-limit the
        # publish explicitly instead of relying on spin_once for pacing.
        self.navigator.info(f'[{self.namespace}] requesting crossing point {point_id}...')
        req = String()
        req.data = json.dumps({'robot': self.namespace, 'point': point_id})
        last_publish = 0.0
        while self.granted_point != point_id:
            now = time.monotonic()
            if now - last_publish >= 0.5:
                self.request_pub.publish(req)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        self.navigator.info(f'[{self.namespace}] granted crossing point {point_id}')

    def _release_crossing(self, point_id):
        msg = String()
        msg.data = json.dumps({'robot': self.namespace, 'point': point_id})
        self.release_pub.publish(msg)
        self.granted_point = None

    def _check_gate(self):
        # Stub for the real gate/breaker inspection (camera/vision node).
        self.navigator.info(f'[{self.namespace}] gate present - running gate check...')
        time.sleep(2.0)
        self.navigator.info(f'[{self.namespace}] gate check done')

    def _move_to(self, pose):
        """Navigate to pose, polling isTaskComplete() so an incoming anomaly
        can interrupt the drive. Returns True if the goal was reached, False
        if it was cancelled because an anomaly came in mid-flight."""
        self.navigator.goToPose(pose)
        while not self.navigator.isTaskComplete():
            if self.anomaly_pending:
                self.navigator.info(
                    f'[{self.namespace}] anomaly signal received - interrupting patrol')
                self.navigator.cancelTask()
                while not self.navigator.isTaskComplete():
                    time.sleep(0.1)
                return False
        return True

    def _handle_anomaly(self):
        loc = self.anomaly_location
        self.anomaly_pending = False
        self.navigator.info(
            f'[{self.namespace}] heading to anomaly at ({loc["x"]}, {loc["y"]})')
        pose = self.navigator.getPoseStamped(
            [float(loc['x']), float(loc['y'])], float(loc['yaw']))
        self.navigator.startToPose(pose)
        # Stub for the real anomaly inspection (camera/vision node).
        self.navigator.info(f'[{self.namespace}] checking anomaly...')
        time.sleep(2.0)
        self.navigator.info(f'[{self.namespace}] anomaly check done, resuming patrol')

        done = String()
        done.data = json.dumps({'robot': self.namespace})
        self.anomaly_done_pub.publish(done)

    def run(self):
        self._wait_for_mission()
        self.navigator.waitUntilNav2Active()

        if self.navigator.getDockedStatus():
            self.navigator.info(f'[{self.namespace}] docked, undocking...')
            self.navigator.undock()

        for i, wp in enumerate(self.mission):
            point_id = wp.get('point_id')
            if point_id:
                self._request_crossing(point_id)

            self.navigator.info(
                f'[{self.namespace}] moving to waypoint {i + 1}/{len(self.mission)}')
            pose = self.navigator.getPoseStamped([wp['x'], wp['y']], wp['yaw'])
            while not self._move_to(pose):
                self._handle_anomaly()

            if point_id:
                self._release_crossing(point_id)

            if wp.get('has_gate'):
                self._check_gate()

        self.navigator.info(f'[{self.namespace}] mission complete')


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