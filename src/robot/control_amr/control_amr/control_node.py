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
import sys
import time

import rclpy
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy, qos_profile_sensor_data)
from std_msgs.msg import String
from irobot_create_msgs.action import Undock
from irobot_create_msgs.msg import HazardDetectionVector
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

# ── undock 방어 파라미터 ──────────────────────────────────────────────
# 정상 undock은 실기에서 약 5초(후진 + 180도 회전). 그 2배 남짓을 결과
# 타임아웃으로 잡아 멀쩡한 undock을 중간에 자르지 않게 한다.
UNDOCK_SERVER_WAIT_SEC = 5.0       # 액션 서버 디스커버리 대기
UNDOCK_ACCEPT_TIMEOUT_SEC = 5.0    # goal 수락 응답 대기
UNDOCK_RESULT_TIMEOUT_SEC = 12.0   # goal 수락 후 완료(후진+회전) 대기
UNDOCK_CANCEL_TIMEOUT_SEC = 5.0    # 취소 ack 대기
UNDOCK_STATUS_WAIT_SEC = 3.0       # dock_status 최초 수신 대기
UNDOCK_ATTEMPTS = 3
# 재시도 간격을 길게 두는 이유: 실패의 유력한 원인이 DDS 계층이라
# 곧바로 재발행하면 요청이 미들웨어에 쌓였다가 한꺼번에 로봇으로 몰려가
# Create 3 통신이 끊긴다(실기에서 관측됨).
UNDOCK_BACKOFF_SEC = 30.0


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
        # 순찰/도킹과 같은 모양으로 통로 그래프 라우팅된 웨이포인트
        # 리스트를 받는다(zone_router.route_to_point() 참고).
        self.anomaly_pending = False
        self.anomaly_route = None
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

        # 이상신호 카메라 포착 조기정지 - 목적지(스냅된 지점)로 가는
        # 도중에 로봇 자신의 카메라가 이상 상황을 먼저 포착하면(UI가
        # 알려줌) 끝까지 안 가고 그 자리에서 즉시 멈춘다. 순찰/도킹
        # 이동 중에는 의미가 없어서 _move_to()의 상시 인터럽트 목록에
        # 넣지 않고, _handle_anomaly()가 extra_interrupt로 넘겨줄 때만
        # 감시한다.
        self.anomaly_captured_pending = False
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/anomaly_captured',
            self._on_anomaly_captured, 10)

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

        # undock 실패 원인 구분용 진단. BACKUP_LIMIT(type 0)이 잡히면 통신이
        # 아니라 Create 3 안전장치가 후진을 막고 있는 것이다 - 전원이 켜진
        # 상태에서 로봇을 손으로 옮기면(kidnap) 즉시 활성화된다.
        self._hazard_types = []
        self.navigator.create_subscription(
            HazardDetectionVector, 'hazard_detection',
            self._on_hazard, qos_profile_sensor_data)

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
        # /fleet/<ns>/dock과 동일하게 이제 좌표 하나가 아니라 통로
        # 그래프로 라우팅된 웨이포인트 리스트를 받는다
        # (zone_router.route_to_point() 참고) - _validate_mission()으로
        # 그 검증 로직을 그대로 재사용한다.
        try:
            route = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self._report_failure('invalid_anomaly_json', {'error': str(exc)})
            return
        if not self.mission_flow.validate_mission(route, publish_rejection=False):
            self._report_failure('invalid_anomaly_route', {'route': route})
            return
        self.anomaly_route = route
        self.anomaly_pending = True

    def _on_anomaly_resume(self, msg):
        # 페이로드 검증이 딱히 필요 없다 - Fleet이 이미 이 로봇 전용
        # 토픽(/fleet/<ns>/anomaly_resume)으로 걸러서 보내주므로, 여기
        # 도착한 것 자체가 "이 로봇 재개하라"는 신호다.
        self.anomaly_resume_pending = True

    def _on_anomaly_captured(self, msg):
        # anomaly_resume과 같은 이유로 페이로드 검증이 필요 없다 - 이
        # 로봇 전용 토픽에 도착한 것 자체가 신호다. 이상신호 이동 중이
        # 아닐 때 도착해도(예: 이미 도착해서 대기 중, 또는 순찰 중) 그냥
        # 플래그만 세워두고 무해하게 무시된다 - _handle_anomaly()가
        # 이동을 시작할 때마다 매번 False로 리셋하기 때문이다.
        self.anomaly_captured_pending = True

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

    def _move_to(self, pose, extra_interrupt=None):
        """
        Navigate to a pose while allowing anomaly and collision interrupts.

        `extra_interrupt`: 옵션 `(reason, check)` 튜플 - 순찰/도킹 이동
        중에는 의미가 없고 이상신호 이동 중(카메라 포착 조기정지)에만
        써야 하는 것처럼, 항상 감시하면 안 되는 인터럽트를 위한 것.
        `check()`가 인자 없이 True를 반환하면 그 즉시(진행 중이던 이동
        도중이어도) `navigation_interrupt_reason`을 `reason`으로 세우고
        취소한 뒤 False를 반환한다.

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
            if extra_interrupt is not None and extra_interrupt[1]():
                self.navigation_interrupt_reason = extra_interrupt[0]
                self._cancel_navigation_task()
                return False
        result = self._get_navigation_result()
        return self._handle_navigation_result(result)

    def _traverse_route_with_crossings(
            self, route, crossing_timeout_reason, arrival_failed_reason,
            extra_interrupt=None):
        """route(zone_router.route_to_point()가 만든 웨이포인트 리스트)를
        순찰 미션과 같은 점유 프로토콜로 순서대로 이동한다 - 이미 쥔
        point_id는 재요청하지 않고, 다음 웨이포인트도 같은 point_id면
        도착해도 release를 미룬다(알려진 이슈 #4와 같은 이유). **아직
        더 갈 홉이 남았어도(마지막 웨이포인트든, 조기정지든) 현재 쥐고
        있는 크로싱은 여기서 절대 놓지 않는다** - 호출부가 그 뒤에 할
        일(도킹 액션, 이상신호 대기 등)이 끝날 때까지 계속 쥐고 있어야
        그 지점이 하필 공유 자원 위여도 안전하기 때문이다. 호출부
        책임으로 넘긴다.

        `extra_interrupt`: `_move_to()`에 그대로 전달하는 옵션
        `(reason, check)` - 이상신호 이동 중 카메라 포착 조기정지처럼
        이 호출에서만 감시해야 하는 인터럽트용(도킹은 안 씀). 이게
        발동하면 목적지까지 안 가고 그 자리에서 멈춘 채
        `'stopped_early'`를 반환한다 - 그 순간 쥐고 있던 크로싱도(있다면)
        그대로 유지한다.

        `_handle_dock()`/`_handle_anomaly()` 둘 다 쓰는 공통 로직.
        `'completed'`(끝까지 도착), `'stopped_early'`(extra_interrupt로
        중간에 멈춤), `'failed'`(복구 불가능한 실패, 쥐고 있던 크로싱은
        이미 반납한 뒤) 중 하나를 반환한다."""
        for idx, wp in enumerate(route):
            point_id = wp.get('point_id')
            if point_id and self.granted_point != point_id:
                if not self._request_crossing(point_id):
                    self._report_failure(crossing_timeout_reason, wp)
                    return 'failed'
            pose = self.navigator.getPoseStamped([wp['x'], wp['y']], wp['yaw'])
            while not self._move_to(pose, extra_interrupt=extra_interrupt):
                if (extra_interrupt is not None and
                        self.navigation_interrupt_reason == extra_interrupt[0]):
                    return 'stopped_early'
                interrupt_result = self._handle_navigation_interrupt(wp)
                if isinstance(interrupt_result, list) or not interrupt_result:
                    # 경로 재계산(list)이나 복구 불가능한 실패 - 순찰
                    # 미션과 달리 이 이동은 그대로 이어받지 않고 실패
                    # 처리한다(재요청은 Fleet이 다시 명령을 보내면 됨).
                    if self.granted_point is not None:
                        self._release_crossing(self.granted_point)
                    self._report_failure(arrival_failed_reason, wp)
                    return 'failed'
            is_last = idx + 1 >= len(route)
            if point_id and not is_last and point_id != route[idx + 1].get('point_id'):
                self._release_crossing(point_id)
        return 'completed'

    def _handle_anomaly(self):
        """이상신호 대응 - CCTV/AMR 감지 위치로 통로 그래프 경로를 따라
        이동한다(자체 감지면 그 로봇의 현재 위치가 곧 이상 위치라
        route가 사실상 홉 없는 제자리 정지가 됨, zone_router.route_to_point()
        참고). 비전 노드가 자동으로 판정하지 않는다 - 도착하면 그
        자리에서 카메라로 상황을 계속 비추며 운영자 판단을 기다린다.
        HMI가 그 영상을 보고 "재개"/"도킹" 둘 중 하나를 최종 결정한다
        (사용자 확인 결과 - 자동 판정이 아니라 사람이 최종 결정권을
        가지는 게 맞는 설계).

        목적지로 가는 도중에 로봇 자신의 카메라가 이상 상황을 먼저
        포착하면(UI가 /backend/anomaly_captured로 알려줌) 끝까지 안
        가고 그 자리에서 즉시 멈춘다 - 카메라(로봇 정면) 각도는 멈춘
        순간의 진행 방향 그대로 둔다(사용자 확인 결과, 별도 회전 없음).

        두 결정 다 이미 있거나 새로 만든 신호를 그대로 쓴다:
        - 재개: 신규 /fleet/<ns>/anomaly_resume(self.anomaly_resume_pending)
        - 도킹: 기존 /fleet/<ns>/dock(self.dock_pending) - 운영자가 아무
          때나 누르는 도킹 복귀와 완전히 같은 경로라 이상신호 전용
          신호를 따로 안 만들었다. 이 함수가 직접 _handle_dock()을
          불러서 그래프 라우팅/점유 보호까지 그대로 이어받는다.

        반환값은 _handle_navigation_interrupt()의 다른 분기들과 같은
        컨벤션: True면 원래 웨이포인트로 재시도, list면(도킹 성공)
        미션을 그걸로 교체."""
        route = self.anomaly_route
        self.anomaly_pending = False
        self.anomaly_captured_pending = False
        final = route[-1]
        self._publish_state('anomaly_moving', final)
        self.navigator.info(
            f'[{self.namespace}] heading to anomaly via {len(route)} '
            f'waypoint(s), final ({final["x"]}, {final["y"]})...')
        result = self._traverse_route_with_crossings(
            route, 'anomaly_crossing_timeout', 'anomaly_arrival_failed',
            extra_interrupt=(
                'anomaly_captured', lambda: self.anomaly_captured_pending))
        if result == 'failed':
            self._publish_anomaly_done(final, confirmed=None, failed=True)
            return True
        if result == 'stopped_early':
            self.anomaly_captured_pending = False
            self.navigator.info(
                f'[{self.namespace}] anomaly captured by onboard camera '
                'before reaching target - stopping here')

        self._publish_state('anomaly_waiting', final)
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
            # 이상신호 이동에서 쥐고 있던 크로싱(있다면)을 놓고 도킹으로
            # 넘어간다 - _handle_dock()은 자기 경로의 크로싱만 알지 이걸
            # 모르니, 안 놓으면 두 크로싱을 동시에 쥔 채로 남는다.
            if self.granted_point is not None:
                self._release_crossing(self.granted_point)
            self._publish_anomaly_done(final, confirmed=True, failed=False)
            return self._handle_dock()

        self.anomaly_resume_pending = False
        if self.granted_point is not None:
            self._release_crossing(self.granted_point)
        self.navigator.info(
            f'[{self.namespace}] operator decided: resume patrol')
        self._publish_anomaly_done(final, confirmed=False, failed=False)
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

    def _on_hazard(self, msg):
        self._hazard_types = [d.type for d in msg.detections]

    def _hazard_names(self):
        names = {0: 'BACKUP_LIMIT', 1: 'BUMP', 2: 'CLIFF', 3: 'STALL',
                 4: 'WHEEL_DROP', 5: 'OBJECT_PROXIMITY'}
        return [names.get(t, str(t)) for t in self._hazard_types]

    def _spin_for(self, seconds, should_stop=None):
        """time.sleep과 달리 대기 중에도 콜백을 계속 처리한다.
        should_stop()이 True면 즉시 빠져나온다."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
            if should_stop is not None and should_stop():
                return

    def _wait_while_emergency_stopped(self):
        """긴급정지 중에는 undock(후진)을 절대 시작하지 않는다 - 정지된
        로봇을 움직이면 안 되기 때문. 플래그가 풀릴 때까지 콜백을 돌리며
        대기한다. run()에서만(=콜백 밖에서) 불리므로 재진입 hang(알려진
        이슈 13)이 없고, 해제 신호(_on_emergency_stop)가 이 spin 도중에
        도착해 플래그를 내린다."""
        while self.emergency_stopped and rclpy.ok():
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

    def _dock_status(self, timeout_sec):
        """현재 도킹 여부를 bool로 돌려준다. 한 번도 못 받았으면 None.

        navigator.getDockedStatus()는 dock_status가 한 번도 안 오면
        `while self.is_docked is None`에서 영원히 대기하므로 쓰지 않는다.
        """
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
            if self.navigator.is_docked is not None:
                return bool(self.navigator.is_docked)
        return None

    def _wait_dock_status(self, expected, timeout_sec):
        """dock_status가 expected(bool)가 될 때까지 최대 timeout_sec 대기."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
            if self.navigator.is_docked is not None and \
                    bool(self.navigator.is_docked) == expected:
                return True
        return False

    def _send_undock_goal(self):
        """undock goal을 보내고 (goal_handle, 실패사유)를 돌려준다.

        navigator.undock()/undock_send_goal()을 쓰지 않는 이유:
        wait_for_server()와 goal 수락 대기 양쪽에 타임아웃이 없어서 응답이
        안 오면 그대로 멈춘다(실기에서 "Goal accepted"가 끝내 안 뜨는 경우).

        서버가 디스커버리되지 않았으면 goal을 아예 보내지 않는다 - 보낸
        요청이 미들웨어 큐에 쌓였다가 나중에 한꺼번에 몰려가면 Create 3
        통신이 끊기므로, 쌓을 여지를 안 만드는 게 가장 확실한 방어다.
        """
        client = self.navigator.undock_action_client
        if not client.wait_for_server(timeout_sec=UNDOCK_SERVER_WAIT_SEC):
            return None, 'undock_server_unreachable'

        goal_future = client.send_goal_async(Undock.Goal())
        deadline = time.monotonic() + UNDOCK_ACCEPT_TIMEOUT_SEC
        while not goal_future.done():
            if time.monotonic() >= deadline:
                # 요청이 로봇에 도달했는지 알 수 없는 상태다. 호출부가 긴
                # backoff를 두고 재시도하도록 사유를 구분해서 돌려준다.
                goal_future.cancel()
                return None, 'undock_goal_not_accepted'
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

        handle = goal_future.result()
        if handle is None or not handle.accepted:
            return None, 'undock_goal_rejected'
        return handle, None

    def _cancel_undock_goal(self, handle):
        """진행 중인 undock goal을 취소하고 서버가 받아들였는지 확인한다.
        취소를 확인하지 않고 새 goal을 보내면 Create 3 쪽에 goal이 쌓여
        통신이 끊기므로, ack를 받은 뒤에만 재시도해야 한다."""
        try:
            cancel_future = handle.cancel_goal_async()
        except Exception as exc:
            self.navigator.info(f'[{self.namespace}] undock 취소 요청 실패: {exc}')
            return False
        deadline = time.monotonic() + UNDOCK_CANCEL_TIMEOUT_SEC
        while not cancel_future.done():
            if time.monotonic() >= deadline:
                self.navigator.info(f'[{self.namespace}] undock 취소 응답 없음')
                return False
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        response = cancel_future.result()
        return bool(response is not None and response.goals_canceling)

    def _fail_undock(self, reason, attempts):
        detail = {'attempts': attempts, 'hazards': self._hazard_names()}
        self.navigator.info(
            f'[{self.namespace}] undock 실패 ({reason}) hazards={detail["hazards"]}')
        self._report_failure(reason, detail)
        self._publish_state('undock_failed', detail)

    def _ensure_undocked(self):
        """도킹 상태면 실제로 도크에서 빠져나올 때까지 undock을 시도한다.

        navigator.undock()을 그대로 쓰지 않는 이유:
          - goal 거부/실패를 전부 성공처럼 반환한다(isUndockComplete()가 True).
          - 결과가 안 오면 `while not isUndockComplete()`에서 무한 대기한다.
          - 서버 대기/goal 수락에도 타임아웃이 없다.
          - 반환값이 없어 호출부가 성공 여부를 알 수 없다.

        성공 판정은 우선 액션 결과(후진+180도 회전 완주)로 하고, 결과가
        끝내 안 오면 그때만 dock_status(물리적 이탈)로 폴백한다 - is_docked는
        후진 직후(회전 전)에 이미 False가 될 수 있어서, 그것만으로 성공
        처리하고 goal을 취소하면 회전을 덜 끝낸 자세로 멈춘다.
        """
        # 긴급정지 중이면 undock(후진)을 시작하지 않는다 - 풀릴 때까지 대기.
        self._wait_while_emergency_stopped()

        status = self._dock_status(UNDOCK_STATUS_WAIT_SEC)
        if status is None:
            # dock_status를 한 번도 못 받았다 = 도킹 여부를 모른다. 이 상태로
            # undock을 쏘면 이미 나와 있는 경우 실패하므로 보내지 않는다.
            self._fail_undock('dock_status_unavailable', 0)
            return False
        if status is False:
            return True                       # 이미 도크 밖

        reason = 'undock_failed'
        for attempt in range(1, UNDOCK_ATTEMPTS + 1):
            # 각 시도 직전에도 긴급정지를 다시 확인한다 - backoff 도중
            # 긴급정지가 들어왔을 수 있다. 정지 상태면 풀릴 때까지 대기.
            self._wait_while_emergency_stopped()

            self._publish_state('undocking', {'attempt': attempt})
            self.navigator.info(
                f'[{self.namespace}] undocking... ({attempt}/{UNDOCK_ATTEMPTS})')

            handle, reason = self._send_undock_goal()
            if handle is None:
                self.navigator.info(f'[{self.namespace}] undock goal 실패: {reason}')
                if attempt < UNDOCK_ATTEMPTS:
                    self._spin_for(UNDOCK_BACKOFF_SEC,
                                   should_stop=lambda: self.emergency_stopped)
                continue

            # 성공 판정은 우선 액션 결과로 한다 - is_docked가 후진 직후
            # (180도 회전 전에) 먼저 False로 바뀌더라도 여기서 곧장 성공
            # 처리하지 않는다. 결과가 정상적으로 오면 후진+회전이 다 끝난
            # 것이므로 타임아웃까지 그걸 기다린다.
            result_future = handle.get_result_async()
            deadline = time.monotonic() + UNDOCK_RESULT_TIMEOUT_SEC
            while time.monotonic() < deadline and not result_future.done():
                rclpy.spin_once(self.navigator, timeout_sec=0.1)

            # 결과가 왔든(정상 완주) 안 왔든, 최종 성공 여부는 물리적
            # 이탈(dock_status)로 확인한다 - 결과 status가 실패로 와도 실제로
            # 빠져나왔으면 순찰을 시작할 수 있기 때문.
            if self._wait_dock_status(False, 1.0):
                if not result_future.done():
                    # 결과 타임아웃까지 안 왔는데 물리적으론 빠져나온 상태
                    # (액션이 매달림) - 서버에 goal을 남기지 않도록 취소한다.
                    # 이미 결과 타임아웃까지 기다린 뒤라 회전은 사실상
                    # 끝났거나 애초에 진행이 멈춘 것이므로 여기서 잘라도 된다.
                    self._cancel_undock_goal(handle)
                self.navigator.info(f'[{self.namespace}] undock 확인됨')
                self._publish_state('undocked')
                return True

            reason = 'undock_timeout'
            self.navigator.info(
                f'[{self.namespace}] undock 미완료 - 이전 goal 취소 후 재시도')
            if not self._cancel_undock_goal(handle):
                # 취소가 안 되는데 재발행하면 goal이 쌓여 통신이 끊긴다. 중단.
                self._fail_undock('undock_cancel_failed', attempt)
                return False
            if attempt < UNDOCK_ATTEMPTS:
                self._spin_for(UNDOCK_BACKOFF_SEC,
                               should_stop=lambda: self.emergency_stopped)

        self._fail_undock(reason, UNDOCK_ATTEMPTS)
        return False

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
        if self._traverse_route_with_crossings(
                route, 'dock_crossing_timeout', 'dock_arrival_failed') != 'completed':
            return True

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

        while rclpy.ok():
            # 도킹된 채로 goToPose()를 쏘면 progress_checker 실패 →
            # route_update_request 타임아웃 → abort 루프만 30초 주기로 돈다.
            # 주행 전에 반드시 도크에서 빠져나온다. 시작 시 1회가 아니라 매
            # 미션 앞에서 확인해야 abort 후에도 스스로 복구된다. self.docked는
            # _handle_dock()이 의도적으로 도킹했을 때 세우는 플래그라 그
            # 경우엔 건너뛰고, 이미 도크 밖이면 _ensure_undocked()가
            # dock_status 한 번 읽고 즉시 True를 돌려준다(보통 1초 이내).
            if not self.docked and not self._ensure_undocked():
                self.mission = None
                self._spin_for(UNDOCK_BACKOFF_SEC,
                               should_stop=lambda: self.emergency_stopped)
                self._wait_for_mission()
                continue

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
