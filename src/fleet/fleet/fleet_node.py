#!/usr/bin/env python3
"""Fleet Node - 시스템 설계도의 AMR FLEET NODE 쪽을 구현한 테스트 노드.

설계도와 매칭되는 책임:
- Backend가 준 구역/지점 데이터로 각 로봇의 순찰 경로를 만드는데, 한
  번의 긴 Nav2 목표가 아니라 고정 통로 그래프(route_graph.py /
  zone_router.py) 위에서 홉 단위로 만든다 - 맵이 좁고 TurtleBot4
  localization이 긴 직선 이동에서 잘 버티지 못하기 때문
  (구역이 겹치는가? -> 각 구역의 순찰 루트 생성)
- 어느 웨이포인트에 차단기 점검이 필요한지(has_gate), 어느 웨이포인트가
  공유 교차 지점인지(point_id) 태깅한다 - 여기 통로는 전부 외길이라,
  두 로봇 이상의 경로가 같은 통로를 쓰면 그게 곧 교차 지점이다
  (교차 가능 지점 계산 및 저장 -> 각 순찰 포인트에 차단기가 있는가?)
- 교차 지점 점유를 중재해서 두 로봇이 동시에 같은 지점을 점유하지
  못하게 한다 (교차지점인가? -> 점유돼있는가?)
- 로봇 상태가 바뀌면 UI용 상태 토픽에 퍼블리시한다 (robot_status.py) -
  다만 Fleet이 실제로 아는 EMERGENCY_STOP/IDLE/PATROLLING/DISPATCHING
  4개 상태 한정이고, 나머지 UI팀 robot_state 어휘(INSPECTING/CHARGING/
  DOCKING 등)는 그 정보를 실제로 가진 Control Node 쪽 몫이다
  (로봇 상태 변화 감지? -> 로봇 상태 퍼블리시)
- 긴급정지 요청을 받으면(비상정지 수신) 등록된 로봇 전체에 정지 신호를
  전파하고, 해제 요청(전체 재개)이 오면 긴급정지 중이던 로봇만 골라
  해제 신호를 내려준다 - 실제로 Nav2를 멈추거나 재개하는 처리는 Control
  Node 쪽 몫이고(control_node.py의 이상신호 인터럽트 처리와 동일한
  패턴), Fleet은 "정지하라"/"해제됐다"는 신호만 로봇별로 내려준다
  (비상정지 수신 -> 순찰 진행중인가?)
- 도킹 복귀 요청을 받으면(명령 분기 -> 도킹 복귀) 대상 로봇에게 고정된
  도킹 스테이션 대기 지점(DOCK_STATIONS)을 내려준다 - 그 지점까지
  이동해서 실제 irobot_create_msgs/action/Dock을 호출하는 건 Control
  Node 몫이다
- 이상신호를 받으면(이상신호 감지 비전노드 -> 임무 수행 로봇 선택)
  HMI가 보낸 페이로드 모양으로 AMR 자체 감지({"robot"}만 있음, 그
  로봇을 제자리에 세움)와 CCTV 감지({"x","y"}만 있음, 가장 가까운
  로봇을 급파)를 구분해서 처리한다 (anomaly_control.py) - 긴급정지
  중인 로봇은 두 경로 모두에서 제외한다

ROS 2 도메인 어디서든 한 번만 실행하면 된다 (특정 로봇과 같은 위치에
있을 필요 없음). 각 로봇의 Control Node와는 정식 .srv/.msg 타입이
아니라 그냥 std_msgs/String + JSON으로 통신하므로, colcon 빌드 없이도
바로 시험해볼 수 있다.
"""

import functools
import json
import os
import sys
import termios
import threading
import tty

import rclpy
from rclpy.node import Node
from rclpy.qos import (QoSProfile, QoSDurabilityPolicy,
                        QoSReliabilityPolicy, QoSHistoryPolicy)
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import String

from fleet import anomaly_control
from fleet import dock_control
from fleet import robot_selector
from fleet import robot_status
from fleet import zone_router
from fleet.route_graph import RouteGraph
from fleet.default_zones import DEFAULT_ZONES

MISSION_QOS = QoSProfile(
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    reliability=QoSReliabilityPolicy.RELIABLE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)

# /fleet/anomaly_trigger 메시지에 좌표가 안 실려 왔을 때 쓰는 기본값 -
# 실제 비전 노드가 감지한 좌표 대신 쓰는 자리표시자.
DEFAULT_ANOMALY = {'x': -2.33, 'y': 0.0313, 'yaw': 0.0}



def _default_graph_path():
    """colcon 빌드로 설치된 share 경로를 우선 쓰고, 실패하면(빌드 전
    상태) 이 파일 옆의 소스 경로로 대체한다 - 이 패키지의 다른 부분과
    마찬가지로 `python3 fleet_node.py`를 빌드 없이 바로 실행해도
    동작하게 하기 위함."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return os.path.join(get_package_share_directory('fleet'), 'config', 'route_graph.yaml')
    except Exception:
        return os.path.join(os.path.dirname(__file__), '..', 'config', 'route_graph.yaml')


class FleetNode(Node):
    def __init__(self):
        super().__init__('fleet_node')
        self._lock = threading.Lock()
        self._occupied = {}  # point_id -> 현재 그 지점을 점유 중인 로봇 네임스페이스

        self.graph = RouteGraph.from_yaml(_default_graph_path())

        # 로봇별 퍼블리셔는 미리 만들어두지 않고, 실제로 그 로봇의
        # 미션이 생기는 시점에 _ensure_robot_pubs()에서 지연 생성한다
        # (구역 구성이 바뀌면 로봇 목록도 바뀔 수 있어서 고정 딕셔너리로
        # 못 박아둘 수 없음).
        self._mission_pubs = {}
        self._anomaly_pubs = {}
        self._emergency_pubs = {}
        self._dock_pubs = {}
        # 이상신호 로봇 선정용 로봇별 최신 위치. amcl_pose 구독도
        # _ensure_robot_pubs()에서 로봇이 처음 등장할 때 지연 생성한다.
        self._pose_subs = {}
        self._robot_pose = {}  # ns -> (x, y)
        # 로봇 상태 퍼블리시 (robot_status.py). _status_pubs도 다른
        # 로봇별 퍼블리셔와 마찬가지로 _ensure_robot_pubs()에서 지연
        # 생성하고, _robot_status는 detect_changes()가 in-place로
        # 갱신하는 "마지막으로 퍼블리시한 상태" 기록이다.
        self._status_pubs = {}
        self._robot_status = {}
        self.grant_pub = self.create_publisher(String, '/fleet/occupancy_grant', 10)
        self.create_subscription(String, '/fleet/occupancy_request', self._on_request, 10)
        self.create_subscription(String, '/fleet/occupancy_release', self._on_release, 10)

        # 이상신호 감지 비전노드가 아직 Fleet에 직접 연결되지 않아서,
        # HMI(Backend)가 감지 결과를 대신 중계해준다 - 페이로드 모양으로
        # 두 경로를 구분한다: {"robot":"robot3"}만 있으면 AMR 자체 감지,
        # {"x":-0.3,"y":2}만 있으면 CCTV 감지 (자세한 판정은
        # _on_anomaly_trigger()/anomaly_control.py 참고). 같은 JSON을
        # 손으로 퍼블리시해도 그대로 감지 상황을 흉내낼 수 있다.
        self._anomaly_busy = set()  # 현재 이상 상황 대응 중이라 순찰을 이탈한 로봇들
        self.create_subscription(
            String, '/fleet/anomaly_trigger', self._on_anomaly_trigger, 10)
        self.create_subscription(
            String, '/fleet/anomaly_done', self._on_anomaly_done, 10)

        # 긴급정지 - {"stop": true}면 등록된 로봇 전체를 정지시키고,
        # {"stop": false}면 그중 긴급정지 중이던 로봇만 골라 해제한다.
        # "/backend/emergency_stop_all" 토픽명과 {"stop": bool} 스키마는
        # 설계도 원본에 이 부분 ROS 브릿지 라벨이 아직 없어서 기존
        # /backend/system_start(bool) 패턴을 따라 임시로 정한 것 - Backend
        # 팀과 확정 필요 (HANDOFF.md 참고). 같은 스키마를 로봇별
        # /fleet/<ns>/emergency_stop에도 그대로 실어보내므로, Control
        # Node는 stop 필드만 보고 정지/재개를 구분하면 된다 (아직 Control
        # 쪽에 이 토픽 구독이 없음 - lee 브랜치 기준, 통합 시 추가 필요).
        self._emergency_stopped = set()  # 긴급정지된 로봇들
        self.create_subscription(
            String, '/backend/emergency_stop_all', self._on_emergency_stop_all, 10)

        # 도킹 복귀 - "AMR 순찰 정지 및 도킹". {"robots": [...]}로 특정
        # 로봇만 지정할 수 있고, robots 필드가 없거나 비어 있으면 등록된
        # 로봇 전체가 대상이다. Fleet은 DOCK_STATIONS의 고정 좌표를 그대로
        # 내려줄 뿐, 그 지점까지 이동해서 실제 Dock 액션을 호출하는 건
        # Control Node 몫이다.
        self.create_subscription(String, '/backend/dock', self._on_dock_return, 10)

        # 설계도의 실제 Backend 입력 (구역, 지점좌표 -> /backend/map_points),
        # {"zones": [{zone_id, robot, points: [{x,y,yaw,point_type}, ...]}]}
        # 형태. corners(구역 모서리)는 여기서 안 쓴다 - 통로 그래프는
        # 고정된 공용 인프라라서 구역마다 다시 계산할 대상이 아니다.
        self.create_subscription(String, '/backend/map_points', self._on_map_points, 10)

        self.missions = {}
        # DEFAULT_ZONES는 바로 적용하지 않고 스페이스바를 눌러야 적용된다 -
        # 하드웨어 테스트에서 로봇을 원하는 위치에 정렬해두고 원하는
        # 시점에 순찰을 시작하기 위함 (_wait_for_start_key 참고). 실제
        # Backend가 붙어서 /backend/map_points가 오면 이 대기와 무관하게
        # _on_map_points()가 즉시 적용한다 - Backend 데이터가 우선이다.
        self._start_event = threading.Event()
        self._started = False
        threading.Thread(target=self._wait_for_start_key, daemon=True).start()
        self.create_timer(0.2, self._check_start_signal)

        # TRANSIENT_LOCAL QoS에 더해서, 이 Fleet Node보다 늦게 뜬
        # Control Node도 자기 미션을 받을 수 있도록 주기적으로
        # 재발행한다.
        self.create_timer(1.0, self._publish_missions)
        # 설계도 '로봇 상태 변화 감지?' - 1Hz로 폴링해서 바뀐 로봇만
        # 퍼블리시한다 (self-loop 구조를 폴링 타이머로 구현).
        self.create_timer(1.0, self._check_robot_status)

    def _wait_for_start_key(self):
        """별도 스레드에서 stdin을 raw 모드로 읽어 스페이스바 입력을
        기다린다. rclpy Node 상태(퍼블리셔 생성 등)는 이 스레드에서 직접
        건드리지 않고 _start_event만 세팅한다 - 실제 _apply_zones() 호출은
        _check_start_signal()이 타이머 콜백(스핀 스레드)에서 처리해서
        Node API를 스핀 스레드 하나에서만 쓰도록 한다.

        stdin이 tty가 아니면(launch로 백그라운드 실행 등) 대기 없이 바로
        시작한다. 대기 중 Ctrl+C로 종료하면 터미널이 raw 모드로 남을 수
        있으니, 그런 경우 `stty sane`으로 복구하면 된다."""
        if not sys.stdin.isatty():
            self.get_logger().warn(
                'stdin이 터미널이 아니라 스페이스바 대기를 건너뛰고 바로 시작합니다.')
            self._start_event.set()
            return

        self.get_logger().info('스페이스바를 누르면 순찰을 시작합니다...')
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch == ' ':
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        self._start_event.set()

    def _check_start_signal(self):
        if self._started or not self._start_event.is_set():
            return
        self._started = True
        self.get_logger().info('순찰 시작 - DEFAULT_ZONES 적용')
        self._apply_zones(DEFAULT_ZONES)

    def _ensure_robot_pubs(self, ns):
        if ns not in self._mission_pubs:
            self._mission_pubs[ns] = self.create_publisher(
                String, f'/fleet/{ns}/mission', MISSION_QOS)
        if ns not in self._anomaly_pubs:
            self._anomaly_pubs[ns] = self.create_publisher(String, f'/fleet/{ns}/anomaly', 10)
        if ns not in self._emergency_pubs:
            self._emergency_pubs[ns] = self.create_publisher(
                String, f'/fleet/{ns}/emergency_stop', 10)
        if ns not in self._dock_pubs:
            self._dock_pubs[ns] = self.create_publisher(String, f'/fleet/{ns}/dock', 10)
        if ns not in self._status_pubs:
            # UI팀이 준 robot_state.msg 인터페이스의 토픽 이름
            # (/control/robot3_State, /control/robot8_state)이 로봇마다
            # 대소문자가 다르게 적혀 있었다(스크린샷 원본 그대로) - 여기서는
            # 그 오탈자를 따라가지 않고 `_State`로 통일한다.
            self._status_pubs[ns] = self.create_publisher(String, f'/control/{ns}_State', 10)
        if ns not in self._pose_subs:
            # 설계도의 `localization amcl -> 임무 수행 로봇 선택` 입력.
            # Nav2/AMCL이 각 로봇 네임스페이스 밑에 표준으로 퍼블리시하는
            # 토픽이라 별도 인터페이스 정의 없이 그대로 구독한다.
            self._pose_subs[ns] = self.create_subscription(
                PoseWithCovarianceStamped, f'/{ns}/amcl_pose',
                functools.partial(self._on_robot_pose, ns), 10)

    def _on_robot_pose(self, ns, msg):
        p = msg.pose.pose.position
        self._robot_pose[ns] = (p.x, p.y)

    def _apply_zones(self, zones):
        """zone_router로 구역/지점 데이터를 실제 로봇별 미션으로 라우팅하고,
        어떤 교차 지점이 발견됐는지 로그로 남긴 뒤 필요한 퍼블리셔를
        준비한다."""
        missions, crossing_log = zone_router.build_missions(self.graph, zones)
        for eid, robots, point_id in crossing_log:
            self.get_logger().info(
                f'crossing point {point_id}: aisle {eid} shared by {robots}')
        self.missions = missions
        for ns in missions:
            self._ensure_robot_pubs(ns)

    def _on_map_points(self, msg):
        try:
            payload = json.loads(msg.data)
            zones = payload['zones']
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.get_logger().warn(f'bad /backend/map_points payload, ignoring: {exc}')
            return
        # 실제 Backend 데이터는 스페이스바 대기와 무관하게 즉시 적용/발행한다
        # - 그리고 _started를 세워서, 이후에 스페이스바가 눌려도
        # _check_start_signal()이 DEFAULT_ZONES로 이 데이터를 덮어쓰지
        # 않게 한다.
        self._started = True
        self._apply_zones(zones)
        self._publish_missions()
        self.get_logger().info(
            f"zones updated from /backend/map_points: {[z['zone_id'] for z in zones]}")

    def _publish_missions(self):
        for ns, waypoints in self.missions.items():
            msg = String()
            msg.data = json.dumps(waypoints)
            self._mission_pubs[ns].publish(msg)

    def _check_robot_status(self):
        """설계도 '로봇 상태 변화 감지? -> 로봇 상태 퍼블리시' - robot_status.py
        참고. Fleet이 아는 EMERGENCY_STOP/IDLE/PATROLLING/DISPATCHING 4개
        상태 한정이고, INSPECTING/REPORTING/RESUMING 같은 이상신호 세부
        상태나 CHARGING/DOCKING/ERROR 등은 Control Node가 같은
        /control/<ns>_State 토픽에 이어서 퍼블리시할 몫이라 여기서는
        다루지 않는다."""
        changes = robot_status.detect_changes(
            self._status_pubs.keys(), self.missions, self._anomaly_busy,
            self._emergency_stopped, self._robot_status)
        for ns, status in changes:
            self.get_logger().info(f'robot status changed: {ns} -> {status}')
            msg = String()
            msg.data = json.dumps({'robot': ns, 'status': status})
            self._status_pubs[ns].publish(msg)

    def _on_request(self, msg):
        req = json.loads(msg.data)
        robot, point = req['robot'], req['point']
        with self._lock:
            holder = self._occupied.get(point)
            if holder is None or holder == robot:
                self._occupied[point] = robot
                granted = True
            else:
                granted = False
        status = 'granted' if granted else f'denied (held by {holder})'
        self.get_logger().info(f'occupancy request: {robot} wants {point} -> {status}')
        self._send_grant(robot, point, granted)

    def _send_grant(self, robot, point, granted):
        msg = String()
        msg.data = json.dumps({'robot': robot, 'point': point, 'granted': granted})
        self.grant_pub.publish(msg)

    def _on_anomaly_trigger(self, msg):
        """이상신호는 HMI가 보내는 페이로드 모양으로 두 경로가 갈린다
        (anomaly_control.py 참고):
        - {"robot": ns} 만 있으면 AMR 자체 감지 - 그 로봇 자신의 카메라가
          감지한 것이므로 좌표 없이 로봇 id만 온다. 그 로봇의 최근 위치를
          그대로 이상 위치로 써서 제자리에 세운다.
        - {"x", "y"} 만 있으면 CCTV 감지 - 좌표만 오므로, 순찰 중이고
          이상신호 대응 중도 긴급정지 중도 아닌 로봇 중 통로 그래프
          최단경로 기준으로 가장 가까운 로봇을 골라 급파한다
          (robot_selector.py, amcl_pose 기반).
        두 경로 모두 최종 로봇이 긴급정지 중이면 무시한다 - 이미 멈춰서
        대기 중인 로봇에 새 이동 명령을 얹으면 안 된다(도킹 복귀와 달리
        운영자의 명시적 override가 아니라 자동 판단이라 안전하게 제외)."""
        try:
            payload = json.loads(msg.data) if msg.data else {}
        except json.JSONDecodeError:
            payload = {}

        robot = payload.get('robot')
        if robot is not None:
            loc = anomaly_control.resolve_self_location(
                robot, self._robot_pose, DEFAULT_ANOMALY)
        else:
            loc = {
                'x': payload.get('x', DEFAULT_ANOMALY['x']),
                'y': payload.get('y', DEFAULT_ANOMALY['y']),
                'yaw': payload.get('yaw', DEFAULT_ANOMALY['yaw']),
            }
            candidates = anomaly_control.eligible_candidates(
                self.missions, self._anomaly_busy, self._emergency_stopped)
            if not candidates:
                self.get_logger().warn('anomaly trigger: no eligible robot available, ignoring')
                return
            robot = robot_selector.select_nearest_robot(
                self.graph.copy(), loc, self._robot_pose, candidates)

        if robot not in self._anomaly_pubs:
            self.get_logger().warn(f'anomaly trigger: unknown robot {robot!r}, ignoring')
            return
        if robot in self._emergency_stopped:
            self.get_logger().warn(f'anomaly trigger: {robot} is emergency-stopped, ignoring')
            return
        if robot in self._anomaly_busy:
            self.get_logger().info(
                f'anomaly trigger: {robot} already handling an anomaly, ignoring')
            return

        self._anomaly_busy.add(robot)
        self.get_logger().info(f'anomaly at {loc} -> dispatching {robot}')
        out = String()
        out.data = json.dumps(loc)
        self._anomaly_pubs[robot].publish(out)

    def _on_anomaly_done(self, msg):
        payload = json.loads(msg.data)
        robot = payload.get('robot')
        self._anomaly_busy.discard(robot)
        self.get_logger().info(f'{robot} finished handling anomaly, ready for new triggers')

    def _on_emergency_stop_all(self, msg):
        """설계도 '비상정지 수신'(정지) / '전채 재개'(해제) - stop 필드로
        분기한다. 실제 Nav2 cancelTask()/재개 호출과 도킹/일시정지 세부
        처리는 Control Node 몫이라 Fleet은 신호만 내려주고 끝 -
        _check_robot_status()가 다음 폴링 tick에 emergency_stopped 변화를
        보고 EMERGENCY_STOP <-> DISPATCHING/PATROLLING/IDLE을 퍼블리시한다
        (compute_status()의 우선순위 규칙이 그대로 처리하므로, 해제 후
        어떤 상태로 돌아갈지는 여기서 따로 계산하지 않는다)."""
        try:
            payload = json.loads(msg.data) if msg.data else {}
        except json.JSONDecodeError:
            payload = {}

        if payload.get('stop', True):
            self._stop_all_robots()
        else:
            self._release_all_robots()

    def _stop_all_robots(self):
        robots = list(self._status_pubs.keys())
        if not robots:
            self.get_logger().warn('emergency_stop_all: no registered robot, ignoring')
            return

        self.get_logger().warn(f'emergency stop-all triggered -> {robots}')
        out = String()
        out.data = json.dumps({'stop': True})
        for ns in robots:
            self._emergency_stopped.add(ns)
            self._emergency_pubs[ns].publish(out)

    def _release_all_robots(self):
        # 긴급정지 중이 아니었던 로봇에는 해제 신호를 보낼 이유가 없으니
        # _emergency_stopped에 실제로 들어있는 로봇만 대상으로 한다.
        robots = list(self._emergency_stopped)
        if not robots:
            self.get_logger().warn(
                'emergency_stop_all: no robot is emergency-stopped, ignoring release')
            return

        self.get_logger().warn(f'emergency stop released -> {robots}')
        self._release_emergency(robots)

    def _release_emergency(self, robots):
        """_emergency_stopped에서 robots를 빼고 /fleet/<ns>/emergency_stop에
        해제 신호를 내려보낸다 - _release_all_robots()(전체 재개)와
        _on_dock_return()(긴급정지 중인 로봇에 도킹 복귀, 암묵적 해제) 둘 다
        같은 해제 절차를 타야 하므로 공통 함수로 뺐다."""
        out = String()
        out.data = json.dumps({'stop': False})
        for ns in robots:
            self._emergency_stopped.discard(ns)
            self._emergency_pubs[ns].publish(out)

    def _on_dock_return(self, msg):
        """설계도 '명령 분기 -(도킹 복귀)-> AMR 순찰 정지 및 도킹'. 대상
        로봇 판정은 dock_control.resolve_dock_targets()(순수 로직, ROS
        의존 없이 test_dock_control.py로 검증됨)에 맡기고, Fleet은 그
        결과대로 로봇별 도킹 스테이션 좌표를 퍼블리시하기만 한다.

        도킹 복귀는 운영자가 명시적으로 내리는 복귀 명령이라 긴급정지
        중인 로봇도 대상에서 빼지 않는다 - 대신 그런 로봇은 도킹 복귀와
        함께 긴급정지도 암묵적으로 해제한다(_release_emergency()) -
        그렇지 않으면 Control이 "정지하라"는 신호만 받은 채로 dock 명령을
        받는 모순이 생긴다.

        payload: {"robots": ["robot3", ...]} - robots가 없거나 비어 있으면
        등록된 로봇 전체가 대상이다 (긴급정지-전체와 동일 패턴).
        """
        try:
            payload = json.loads(msg.data) if msg.data else {}
        except json.JSONDecodeError:
            payload = {}

        dispatched, unknown, released_from_emergency, skipped_no_station = (
            dock_control.resolve_dock_targets(
                payload.get('robots'), self._status_pubs.keys(), self._emergency_stopped))

        if unknown:
            self.get_logger().warn(f'dock: unregistered robot(s) {unknown}, ignoring those')
        for ns in skipped_no_station:
            self.get_logger().warn(f'dock: no dock station configured for {ns}, skipping')

        if not dispatched:
            self.get_logger().warn('dock: no target robot, ignoring')
            return

        if released_from_emergency:
            self.get_logger().warn(
                f'dock: implicitly releasing emergency stop for {released_from_emergency}')
            self._release_emergency(released_from_emergency)

        for ns in dispatched:
            out = String()
            out.data = json.dumps(dock_control.DOCK_STATIONS[ns])
            self._dock_pubs[ns].publish(out)
        self.get_logger().warn(f'dock return triggered -> {dispatched}')

    def _on_release(self, msg):
        rel = json.loads(msg.data)
        robot, point = rel['robot'], rel['point']
        with self._lock:
            if self._occupied.get(point) == robot:
                del self._occupied[point]
                self.get_logger().info(f'{robot} released {point}')


def main():
    rclpy.init()
    node = FleetNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    # Ctrl+C(SIGINT)를 받으면 rclpy.init()이 걸어둔 기본 핸들러가 이미
    # context를 shutdown 해버리는 경우가 있어서, 그 뒤에 또 shutdown()을
    # 부르면 "rcl_shutdown already called" 에러로 죽는다 - rclpy.ok()로
    # 아직 살아있을 때만 호출한다.
    if rclpy.ok():
        rclpy.shutdown()


if __name__ == '__main__':
    main()
