"""
Run the AMR Control Node for one namespaced robot.

Each instance runs on that robot's laptop and receives a namespace as a CLI
argument:

    python3 control_node.py robot3
    python3 control_node.py robot8

It waits for its mission (waypoint list with gate/crossing tags) from the
Fleet Node, then walks the waypoints one at a time:
  - if a waypoint is a shared crossing point, requests occupancy from the
    Fleet Node and waits for a grant before moving (교차지점인가? ->
    점유돼있는가?)
  - navigates to the waypoint
  - if the waypoint is tagged has_gate, requests a vision-based gate check
    (해당 위치 차단기 유무 확인 -> 차단기 점검)
  - releases the crossing point once past it
"""

import json
import math
import sys
import time

import rclpy
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
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
        self.gate_alignment_flow = GateAlignmentFlowSupport(
            namespace, self.navigator)
        self.navigation_interrupt_reason = None
        self.crossing_request_timeout_sec = 15.0
        self.mission_aborted = False

        self.mission = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/mission',
            self._on_mission, MISSION_QOS)

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

        # 긴급정지 - {"stop": true/false}. 정지 시 즉시 cancelTask()하고,
        # 해제되기 전까지는 새 이동 명령을 아예 내보내지 않는다 (자세한
        # 설계 배경은 docs/control_emergency_dock_integration.md 참고).
        self.emergency_stopped = False
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/emergency_stop',
            self._on_emergency_stop, 10)

        # 도킹 복귀 - {"x","y","yaw"}. 지정된 도킹 스테이션 대기 지점까지
        # 이동한 뒤 실제 Dock 액션을 호출한다. 도킹 후에는 self.docked를
        # 세워서 재순찰 신호(아직 Fleet에 없음)가 오기 전까지 새 미션을
        # 무시한다.
        self.dock_pending = False
        self.dock_location = None
        self.docked = False
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/dock', self._on_dock, 10)

    def _on_mission(self, msg):
        if self.docked:
            # Fleet의 _publish_missions()가 1초마다 같은 미션을 계속
            # 재발행하는데, 도킹 중에 이걸 그대로 받아버리면 재순찰
            # 신호 없이도 바로 다시 움직이게 된다 - 도킹 중엔 무시한다.
            return
        try:
            mission = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self._report_failure('invalid_mission_json', {'error': str(exc)})
            return
        if self._validate_mission(mission):
            self.mission = mission

    def _on_anomaly(self, msg):
        try:
            location = json.loads(msg.data)
            if not self._is_valid_pose(location):
                raise ValueError('x, y, yaw must be finite numbers')
        except (json.JSONDecodeError, ValueError) as exc:
            self._report_failure('invalid_anomaly_json', {'error': str(exc)})
            return
        self.anomaly_location = location
        self.anomaly_pending = True

    @staticmethod
    def _is_valid_pose(value):
        if not isinstance(value, dict):
            return False
        if any(isinstance(value.get(field), bool)
               for field in ('x', 'y', 'yaw')):
            return False
        try:
            coordinates = [float(value[field]) for field in ('x', 'y', 'yaw')]
        except (KeyError, TypeError, ValueError):
            return False
        return all(math.isfinite(coordinate) for coordinate in coordinates)

    def _on_emergency_stop(self, msg):
        try:
            payload = json.loads(msg.data) if msg.data else {}
            stop = payload.get('stop', True)
            if not isinstance(stop, bool):
                raise ValueError('stop must be a bool')
        except (json.JSONDecodeError, ValueError) as exc:
            self._report_failure(
                'invalid_emergency_stop_json', {'error': str(exc)})
            return
        self.emergency_stopped = stop
        if stop:
            self.navigator.info(
                f'[{self.namespace}] EMERGENCY STOP received')
            # 여기서 직접 cancelTask()를 부르지 않는다 - 이 콜백 자체가
            # nav2_simple_commander의 isTaskComplete()(즉 _move_to()의
            # while 루프) 안에서 spin_until_future_complete()가 콜백을
            # 처리하는 도중에 실행되는 경우가 있는데, cancelTask()도
            # 내부에서 spin_until_future_complete()를 쓰고 이 함수는
            # get_global_executor()로 프로세스 전역 SingleThreadedExecutor
            # 하나를 공유한다. 즉 콜백 안에서 cancelTask()를 부르면 "이미
            # 스핀 중인 전역 executor를 재진입해서 다시 스핀"하게 되는데
            # SingleThreadedExecutor는 이런 재진입을 지원하지 않아 그대로
            # 멈춰버린다(하드웨어 테스트 중 실제로 관찰됨 - 이동 중이던
            # 로봇이 긴급정지를 받자마자 "Canceling current task." 이후
            # 완전히 응답 없음).
            #
            # 대신 emergency_stopped 플래그만 세운다 - _move_to()의 while
            # 루프가 매 반복(약 0.1초 간격)마다 이 플래그를 확인해서,
            # 콜백 스택 밖의 안전한 위치에서 _cancel_navigation_task()를
            # 부른다. "즉시 정지"라는 의도는 이 정도 지연으로 충분히
            # 유지되고, 그 사이 로봇이 이동 중이 아니었다면(예: 크로싱
            # grant를 기다리던 중) 애초에 취소할 작업도 없다.
        else:
            self.navigator.info(
                f'[{self.namespace}] emergency stop released')

    def _on_dock(self, msg):
        try:
            location = json.loads(msg.data)
            if not self._is_valid_pose(location):
                raise ValueError('x, y, yaw must be finite numbers')
        except (json.JSONDecodeError, ValueError) as exc:
            self._report_failure('invalid_dock_json', {'error': str(exc)})
            return
        self.dock_location = location
        self.dock_pending = True

    def _on_grant(self, msg):
        try:
            grant = json.loads(msg.data)
            robot = grant['robot']
            granted = grant['granted']
            point = grant['point']
            if not isinstance(robot, str) or not isinstance(granted, bool):
                raise ValueError('robot/granted types are invalid')
            if not isinstance(point, str):
                raise ValueError('point must be a string')
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            self._report_failure(
                'invalid_occupancy_grant', {'error': str(exc)})
            return
        if robot == self.namespace and granted:
            self.granted_point = point

    def _wait_for_mission(self):
        self._publish_state('waiting_mission')
        self.navigator.info(
            f'[{self.namespace}] waiting for mission from Fleet Node...')
        while self.mission is None:
            rclpy.spin_once(self.navigator, timeout_sec=0.5)

    def _request_crossing(self, point_id):
        # spin_once(timeout_sec=X) returns as soon as ANY callback fires, not
        # after X seconds - with other subscriptions active it returns almost
        # immediately, turning this into a publish busy-loop. Rate-limit the
        # publish explicitly instead of relying on spin_once for pacing.
        self._publish_state('crossing_wait', {'point_id': point_id})
        self.navigator.info(
            f'[{self.namespace}] requesting crossing point {point_id}...')
        req = String()
        req.data = json.dumps({'robot': self.namespace, 'point': point_id})
        last_publish = 0.0
        started_at = time.monotonic()
        while self.granted_point != point_id:
            now = time.monotonic()
            if (self.crossing_request_timeout_sec is not None and
                    now - started_at >= self.crossing_request_timeout_sec):
                self._report_failure(
                    'crossing_grant_timeout', {'point_id': point_id})
                return False
            if now - last_publish >= 0.5:
                self.request_pub.publish(req)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        self.navigator.info(
            f'[{self.namespace}] granted crossing point {point_id}')
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
        self.navigator.info(
            f'[{self.namespace}] gate present - running gate check...')
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
        """
        Navigate to a pose while allowing anomaly and collision interrupts.

        Return whether the goal was reached successfully.
        """
        self.navigation_interrupt_reason = None
        if self.emergency_stopped:
            # 이미 정지 상태라면 goToPose() 자체를 내보내지 않는다 -
            # 정지 중에 새 이동 명령이 나가는 순간이 없어야 한다.
            self.navigation_interrupt_reason = 'emergency_stop'
            return False
        self.navigator.goToPose(pose)
        while not self.navigator.isTaskComplete():
            if self.emergency_stopped:
                self.navigation_interrupt_reason = 'emergency_stop'
                self._cancel_navigation_task()
                return False
            if self._check_collision_risk():
                self.navigation_interrupt_reason = 'collision_risk'
                self._cancel_navigation_task()
                self._handle_collision_risk()
                return False
            if self.dock_pending:
                self.navigation_interrupt_reason = 'dock'
                self.navigator.info(
                    f'[{self.namespace}] dock command received - '
                    'interrupting patrol')
                self._cancel_navigation_task()
                return False
            if self.anomaly_pending:
                self.navigation_interrupt_reason = 'anomaly'
                self.navigator.info(
                    f'[{self.namespace}] anomaly signal received - '
                    'interrupting patrol')
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
            f'[{self.namespace}] heading to anomaly at '
            f'({loc["x"]}, {loc["y"]})')
        pose = self.navigator.getPoseStamped(
            [float(loc['x']), float(loc['y'])], float(loc['yaw']))
        self.navigator.startToPose(pose)
        # Stub for the real anomaly inspection (camera/vision node).
        if not self._wait_for_anomaly_arrival():
            self._report_failure('anomaly_arrival_failed', loc)
            self._publish_anomaly_done(loc, confirmed=None, failed=True)
            return
        self._publish_state('anomaly_checking', loc)
        self.navigator.info(f'[{self.namespace}] checking anomaly...')
        inspection = self.inspection_flow.inspect_anomaly(loc)
        if inspection['confirmed'] is None:
            self._report_failure('anomaly_check_failed', loc)
            self._publish_anomaly_done(loc, confirmed=None, failed=True)
            return
        self.navigator.info(
            f'[{self.namespace}] anomaly check done, resuming patrol')
        self._publish_anomaly_done(
            loc, confirmed=inspection['confirmed'], failed=False)
        self._publish_state('patrol_resuming')

    def _publish_anomaly_done(self, loc, confirmed, failed):
        # 도착/점검 실패로 여기까지 왔더라도 반드시 발행해야 한다 - 안 그러면
        # 이 로봇이 Fleet의 _anomaly_busy에서 영영 안 빠져서 이후 이상신호
        # 급파 후보에서도 영구 제외된다 (known issue 1번).
        done = String()
        done.data = json.dumps({
            'robot': self.namespace,
            'confirmed': confirmed,
            'location': loc,
            'failed': failed,
        })
        self.anomaly_done_pub.publish(done)

    def _handle_emergency_stop(self):
        """긴급정지 - 해제될 때까지 제자리에서 대기한 뒤, 해제되면
        중단됐던 웨이포인트로 이동을 재시도한다 (True를 반환하면 호출부가
        같은 pose로 _move_to()를 다시 시도함)."""
        self._publish_state('emergency_stopped')
        self.navigator.info(
            f'[{self.namespace}] holding position until emergency stop '
            'is released...')
        while self.emergency_stopped:
            rclpy.spin_once(self.navigator, timeout_sec=0.5)
        self.navigator.info(
            f'[{self.namespace}] emergency stop released, resuming patrol')
        self._publish_state('patrol_resuming')
        return True

    def _handle_dock(self):
        """도킹 복귀 - 지정된 도킹 스테이션 대기 지점까지 이동한 뒤 실제
        Dock 액션을 호출한다. 성공하면 빈 리스트를 돌려줘서 호출부가
        현재 미션을 종료 처리하게 한다 (route_replaced 경로 재사용 -
        mission_aborted로 실패 취급하지 않으면서 원래 웨이포인트로는
        돌아가지 않음). 도킹 이후 재순찰을 언제/어떻게 트리거할지는
        아직 Fleet 쪽에 신호가 없어서 미구현 - self.docked가 True인 동안
        _on_mission()이 새 미션을 무시하므로 명시적 재개 신호가 오기
        전까지는 도킹 상태 그대로 대기한다."""
        loc = self.dock_location
        self.dock_pending = False
        self._publish_state('dock_moving', loc)
        self.navigator.info(
            f'[{self.namespace}] heading to dock station at '
            f'({loc["x"]}, {loc["y"]})')
        pose = self.navigator.getPoseStamped(
            [float(loc['x']), float(loc['y'])], float(loc['yaw']))
        self.navigator.startToPose(pose)
        if not self.navigation_flow.wait_until_pose_reached():
            self._report_failure('dock_arrival_failed', loc)
            return True

        self._publish_state('docking', loc)
        self.navigator.info(f'[{self.namespace}] docking...')
        self.navigator.dock()
        self.docked = True
        self._publish_state('docked')
        self.navigator.info(
            f'[{self.namespace}] docked - mission ending, waiting for an '
            'explicit resume signal (not implemented on Fleet side yet)')
        return []

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

    def _handle_navigation_interrupt(self, waypoint):
        if self.navigation_interrupt_reason == 'emergency_stop':
            return self._handle_emergency_stop()
        if self.navigation_interrupt_reason == 'dock':
            return self._handle_dock()
        if self.navigation_interrupt_reason == 'anomaly':
            self._handle_anomaly()
            return True
        if self.navigation_interrupt_reason == 'collision_risk':
            return self._request_route_update('collision_risk', waypoint)
        recovered = self.navigation_flow.recover_from_navigation_failure(
            'navigation_failed')
        if recovered:
            return True
        return self._request_route_update('navigation_failed', waypoint)

    def _report_waypoint_reached(self, waypoint_index, waypoint):
        return self.state_flow.report_waypoint_reached(
            waypoint_index, waypoint)

    def _report_failure(self, reason, detail=None):
        return self.state_flow.report_failure(reason, detail)

    def _handle_mission_complete(self):
        self.mission_flow.handle_mission_complete()

    def run(self):
        self._wait_for_mission()
        self.navigator.waitUntilNav2Active()

        if self.navigator.getDockedStatus():
            self.navigator.info(f'[{self.namespace}] docked, undocking...')
            self.navigator.undock()

        while rclpy.ok():
            self.mission_aborted = False
            mission_completed = self._run_current_mission()
            self.mission = None
            if not mission_completed:
                # abort는 이 로봇 1대만의 문제로 프로세스를 죽일 이유가 아니다
                # (known issue 2번) - 다음 미션을 기다리는 상태로 돌아간다.
                # Fleet이 같은 미션을 주기 재발행 중이라 대개 곧바로 재시도된다.
                self._wait_for_mission()
                continue
            self._publish_state('patrol_waiting', {
                'delay_sec': self.mission_flow.get_next_patrol_delay_sec()})
            self.mission_flow.wait_until_next_patrol()
            self.mission_flow.request_next_mission()
            self._wait_for_mission()

    def _run_current_mission(self):

        waypoints = list(self.mission)
        i = 0
        while i < len(waypoints):
            wp = waypoints[i]
            point_id = wp.get('point_id')
            if point_id and not self._request_crossing(point_id):
                self.mission_aborted = True
                break

            self._publish_state(
                'moving', {'waypoint_index': i, 'waypoint': wp})
            self.navigator.info(
                f'[{self.namespace}] moving to waypoint '
                f'{i + 1}/{len(waypoints)}')
            pose = self.navigator.getPoseStamped([wp['x'], wp['y']], wp['yaw'])
            route_replaced = False
            while not self._move_to(pose):
                interrupt_result = self._handle_navigation_interrupt(wp)
                if isinstance(interrupt_result, list):
                    if point_id and self.granted_point == point_id:
                        self._release_crossing(point_id)
                    waypoints = interrupt_result
                    self.mission = waypoints
                    i = 0
                    route_replaced = True
                    break
                if not interrupt_result:
                    self._report_failure('navigation_interrupt_unresolved', wp)
                    self.mission_aborted = True
                    break
            if route_replaced:
                continue
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
            i += 1

        if self.mission_aborted:
            self._report_failure('mission_aborted')
            return False
        self._handle_mission_complete()
        return True


def main():
    if len(sys.argv) != 2:
        print(
            'Usage: python3 control_node.py <namespace> '
            '(e.g. robot3 or robot8)')
        sys.exit(1)
    namespace = sys.argv[1]

    rclpy.init()
    node = None
    try:
        node = ControlNode(namespace)
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.navigator.destroy_node()
        # Ctrl+C(SIGINT)를 받으면 rclpy.init()이 걸어둔 기본 핸들러가 이미
        # context를 shutdown 해버리는 경우가 있어서, 그 뒤에 또
        # shutdown()을 부르면 "rcl_shutdown already called" 에러로 죽는다
        # - rclpy.ok()로 아직 살아있을 때만 호출한다.
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
