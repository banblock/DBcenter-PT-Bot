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

        # 이상신호 재개(작업복귀) - 비전 노드가 자동으로 판정하는 게
        # 아니라, 로봇이 이상 위치에 도착해 카메라로 상황을 계속 비추는
        # 동안 HMI에서 그 영상을 보고 운영자가 "재개"/"도킹" 둘 중
        # 하나를 최종 결정한다(사용자 확인). "도킹" 결정은 이미 있는
        # /fleet/<ns>/dock(dock_pending)을 그대로 재사용하고(운영자가
        # 아무 때나 누르는 도킹 복귀와 완전히 같은 경로), "재개" 결정만
        # 이 신규 신호가 필요하다.
        self.anomaly_resume_pending = False
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/anomaly_resume',
            self._on_anomaly_resume, 10)

        # 긴급정지 - {"stop": true/false}. 정지 시 즉시 cancelTask()하고,
        # 해제되기 전까지는 새 이동 명령을 아예 내보내지 않는다 (자세한
        # 설계 배경은 docs/control_emergency_dock_integration.md 참고).
        self.emergency_stopped = False
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/emergency_stop',
            self._on_emergency_stop, 10)

        # 도킹 복귀 - 통로 그래프로 라우팅된 웨이포인트 리스트
        # (zone_router.route_to_point(), /fleet/<ns>/mission과 같은 모양).
        # 그 경로를 점유 프로토콜을 지키며 따라간 뒤 실제 Dock 액션을
        # 호출한다. 도킹 후에는 self.docked를 세워서 재순찰 신호(아직
        # Fleet에 없음)가 오기 전까지 새 미션을 무시한다.
        self.dock_pending = False
        self.dock_route = None
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

    def _on_anomaly_resume(self, msg):
        # 페이로드 검증이 딱히 필요 없다 - Fleet이 이미 이 로봇 전용
        # 토픽(/fleet/<ns>/anomaly_resume)으로 걸러서 보내주므로, 여기
        # 도착한 것 자체가 "이 로봇 재개하라"는 신호다.
        self.anomaly_resume_pending = True

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
        # Fleet이 이제 좌표 하나가 아니라 통로 그래프로 라우팅한
        # 웨이포인트 리스트를 보낸다(zone_router.route_to_point() 참고,
        # /fleet/<ns>/mission과 같은 모양) - _validate_mission()으로 그
        # 검증 로직을 그대로 재사용한다.
        try:
            route = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self._report_failure('invalid_dock_json', {'error': str(exc)})
            return
        if not self.mission_flow.validate_mission(route, publish_rejection=False):
            self._report_failure('invalid_dock_route', {'route': route})
            return
        self.dock_route = route
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
        """이상신호 대응 - CCTV/AMR 감지 위치로 이동한다(자체 감지면
        그 로봇의 현재 위치가 곧 이상 위치라 사실상 제자리 정지).
        비전 노드가 자동으로 판정하지 않는다 - 도착하면 그 자리에서
        카메라로 상황을 계속 비추며 운영자 판단을 기다린다. HMI가 그
        영상을 보고 "재개"/"도킹" 둘 중 하나를 최종 결정한다(사용자
        확인 결과 - 자동 판정이 아니라 사람이 최종 결정권을 가지는 게
        맞는 설계).

        두 결정 다 이미 있거나 새로 만든 신호를 그대로 쓴다:
        - 재개: 신규 /fleet/<ns>/anomaly_resume(self.anomaly_resume_pending)
        - 도킹: 기존 /fleet/<ns>/dock(self.dock_pending) - 운영자가 아무
          때나 누르는 도킹 복귀와 완전히 같은 경로라 이상신호 전용
          신호를 따로 안 만들었다. 이 함수가 직접 _handle_dock()을
          불러서 그래프 라우팅/점유 보호까지 그대로 이어받는다.

        반환값은 _handle_navigation_interrupt()의 다른 분기들과 같은
        컨벤션: True면 원래 웨이포인트로 재시도, list면(도킹 성공)
        미션을 그걸로 교체."""
        loc = self.anomaly_location
        self.anomaly_pending = False
        self._publish_state('anomaly_moving', loc)
        self.navigator.info(
            f'[{self.namespace}] heading to anomaly at '
            f'({loc["x"]}, {loc["y"]})')
        pose = self.navigator.getPoseStamped(
            [float(loc['x']), float(loc['y'])], float(loc['yaw']))
        self.navigator.startToPose(pose)
        if not self._wait_for_anomaly_arrival(pose):
            self._report_failure('anomaly_arrival_failed', loc)
            self._publish_anomaly_done(loc, confirmed=None, failed=True)
            return True

        self._publish_state('anomaly_waiting', loc)
        self.navigator.info(
            f'[{self.namespace}] holding position, camera on anomaly - '
            'waiting for operator decision (resume/dock)...')
        self.anomaly_resume_pending = False
        while not self.anomaly_resume_pending and not self.dock_pending:
            if self.emergency_stopped:
                self._handle_emergency_stop()
                continue
            rclpy.spin_once(self.navigator, timeout_sec=0.5)

        if self.dock_pending:
            self.navigator.info(
                f'[{self.namespace}] operator decided: dock return')
            self._publish_anomaly_done(loc, confirmed=True, failed=False)
            return self._handle_dock()

        self.anomaly_resume_pending = False
        self.navigator.info(
            f'[{self.namespace}] operator decided: resume patrol')
        self._publish_anomaly_done(loc, confirmed=False, failed=False)
        self._publish_state('patrol_resuming')
        return True

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
        """도킹 복귀 - Fleet이 통로 그래프로 라우팅해 보낸 웨이포인트
        리스트(self.dock_route, zone_router.route_to_point() 참고)를
        순찰 미션과 같은 점유 프로토콜(크로싱 요청/해제)로 따라간 뒤,
        마지막 지점에서 실제 Dock 액션을 호출한다. 예전엔 좌표 하나로
        Nav2에 직행해서 도킹 이동이 공유 통로를 가로질러도 occupancy
        중재를 전혀 안 받았는데(0번 순찰 지점과 같은 부류의 문제,
        하드웨어 테스트에서 지적됨), 이제는 각 홉마다 크로싱을 확보하고
        지나간 뒤 반납한다.

        성공하면 빈 리스트를 돌려줘서 호출부가 현재 미션을 종료
        처리하게 한다 (route_replaced 경로 재사용 - mission_aborted로
        실패 취급하지 않으면서 원래 웨이포인트로는 돌아가지 않음). 도킹
        이후 재순찰을 언제/어떻게 트리거할지는 아직 Fleet 쪽에 신호가
        없어서 미구현 - self.docked가 True인 동안 _on_mission()이 새
        미션을 무시하므로 명시적 재개 신호가 오기 전까지는 도킹 상태
        그대로 대기한다."""
        route = self.dock_route
        self.dock_pending = False
        final = route[-1]
        self._publish_state('dock_moving', final)
        self.navigator.info(
            f'[{self.namespace}] heading to dock station via {len(route)} '
            f'waypoint(s), final ({final["x"]}, {final["y"]})...')

        for idx, wp in enumerate(route):
            point_id = wp.get('point_id')
            if point_id and self.granted_point != point_id:
                if not self._request_crossing(point_id):
                    self._report_failure('dock_crossing_timeout', wp)
                    return True
            pose = self.navigator.getPoseStamped([wp['x'], wp['y']], wp['yaw'])
            while not self._move_to(pose):
                interrupt_result = self._handle_navigation_interrupt(wp)
                if isinstance(interrupt_result, list) or not interrupt_result:
                    # 경로 재계산(list)이나 복구 불가능한 실패 - 순찰
                    # 미션과 달리 도킹 이동은 그대로 이어받지 않고 실패
                    # 처리한다(anomaly 이동도 지금 이 정도 수준의 재시도만
                    # 함 - 재요청은 Fleet이 다시 dock 명령을 보내면 됨).
                    if self.granted_point is not None:
                        self._release_crossing(self.granted_point)
                    self._report_failure('dock_arrival_failed', wp)
                    return True
            # 다음 웨이포인트도 같은 point_id면 여기서 놓지 않고 그대로
            # 들고 간다. 마지막 지점이면(그 지점 자체가 공유 자원 위일
            # 수 있으니) 아예 여기서 안 놓고 실제 도킹(navigator.dock())
            # 까지 끝난 뒤에야 놓는다. 순찰 미션의 동일한 문제(알려진
            # 이슈 #4)와 같은 이유.
            is_last = idx + 1 >= len(route)
            if point_id and not is_last and point_id != route[idx + 1].get('point_id'):
                self._release_crossing(point_id)

        self._publish_state('docking', final)
        self.navigator.info(f'[{self.namespace}] docking...')
        self.navigator.dock()
        if self.granted_point is not None:
            self._release_crossing(self.granted_point)
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

    def _wait_for_anomaly_arrival(self, pose):
        return self.navigation_flow.wait_until_pose_reached(
            lambda: self.emergency_stopped, pose)

    def _handle_navigation_interrupt(self, waypoint):
        if self.navigation_interrupt_reason == 'emergency_stop':
            return self._handle_emergency_stop()
        if self.navigation_interrupt_reason == 'dock':
            return self._handle_dock()
        if self.navigation_interrupt_reason == 'anomaly':
            return self._handle_anomaly()
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
            self.mission_flow.wait_until_next_patrol(
                should_interrupt=lambda: (
                    self.dock_pending or self.anomaly_pending))
            # 도킹/이상신호 둘 다 이 10분 대기 도중에도 즉시 반응해야
            # 하는 인터럽트다(알려진 이슈 #3) - _handle_dock()/
            # _handle_anomaly() 모두 self.dock_route/self.anomaly_location
            # 만 보고 동작하므로 웨이포인트 컨텍스트 없이 여기서 바로
            # 불러도 된다.
            if self.dock_pending or self.anomaly_pending:
                if self.dock_pending:
                    self._handle_dock()
                else:
                    self._handle_anomaly()
                # 인터럽트 처리가 끝나기 전(도킹이면 self.docked가 아직
                # False, 이상신호는 원래부터 아무 가드도 없음)에도 Fleet은
                # 같은 순찰 미션을 계속 재발행하고 _on_mission()이 그걸
                # 그대로 받아버려서, self.mission이 옛 순찰 미션으로 몰래
                # 다시 채워질 수 있다. 그 상태로 그냥 _wait_for_mission()을
                # 부르면 self.mission이 이미 non-None이라 대기를 건너뛰고
                # (도킹이면 undock도 없이) 바로 그 미션을 재개해버린다
                # (하드웨어 테스트 중 실제로 관찰됨). 명시적으로 비워서
                # 항상 새 미션만 받아들이게 한다.
                self.mission = None
                self._wait_for_mission()
                continue
            self.mission_flow.request_next_mission()
            self._wait_for_mission()

    def _run_current_mission(self):

        waypoints = list(self.mission)
        i = 0
        while i < len(waypoints):
            wp = waypoints[i]
            point_id = wp.get('point_id')
            # 이미 같은 point_id를 쥐고 있으면(직전 웨이포인트에서 이어짐)
            # 다시 요청하지 않는다 - 아래에서 release를 미루는 것과
            # 짝을 이루는 부분.
            if point_id and self.granted_point != point_id:
                if not self._request_crossing(point_id):
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
                    if self.granted_point is not None:
                        self._release_crossing(self.granted_point)
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

            # 다음 웨이포인트도 같은 point_id를 쓰면 여기서 놓지 않고
            # 그대로 들고 간다 - 순찰 지점이 하필 공유 통로/교차로 위에
            # 있어서 로봇이 그 자리에 멈춰 서는 경우, 여기서 놓았다가
            # 다음 홉에서 다시 요청하는 그 짧은 틈에 다른 로봇이 grant를
            # 가로챌 수 있다(하드웨어 테스트 중 실제로 관찰된 문제 -
            # 알려진 이슈 #4와 같은 부류).
            next_point_id = (
                waypoints[i + 1].get('point_id') if i + 1 < len(waypoints)
                else None)
            if point_id and point_id != next_point_id:
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
            # abort 원인과 무관하게, 지금 쥐고 있는 크로싱이 있으면 반드시
            # 놓는다 - 안 그러면 Fleet 쪽에 이 로봇이 영구 홀더로 남아
            # 다른 로봇을 영영 막는다.
            if self.granted_point is not None:
                self._release_crossing(self.granted_point)
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
