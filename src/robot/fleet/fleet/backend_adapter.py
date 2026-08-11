#!/usr/bin/env python3
"""Backend(§10 통합 command) ↔ Fleet(의미별 토픽) 변환 노드.

백엔드 robot_bridge_node 는 로봇으로 `String /backend/amr_1/command`,
`/backend/amr_2/command` 를 발행한다(patrol_interfaces 규약). payload 는
§10 봉투 JSON: ``{command_id, command_type, payload, issued_at}``.

Fleet(fleet_node)은 이와 다른 "의미별 토픽"을 구독한다:
  * ``/backend/map_points``        {"zones":[{zone_id, robot, points:[{x,y,yaw,point_type}]}]}
  * ``/backend/dock``              {"robots":[ns, ...]}
  * ``/backend/emergency_stop_all``{"stop": bool}

이 노드는 양측을 붙이는 **얇은 통역기**다. 백엔드/Fleet 코드는 건드리지 않는다.
논리 로봇 ns 매핑: amr_1 → robot3, amr_2 → robot8 (물리 TurtleBot4 네임스페이스).

Phase 2 (하행) 범위: START_PATROL / DOCK / ESTOP / RESET.
  · START_PATROL: 로봇당 노드를 저장했다가(백엔드가 로봇별로 따로 보냄) 모아서
    map_points 를 한 번에 발행한다(짧은 debounce 로 두 로봇 명령을 합친다).
  · DOCK: 해당 로봇만 도킹 복귀(/backend/dock {robots:[ns]}).
  · ESTOP: reason 으로 구분한다 — "STOP_AND_DOCK"(개별 정지·복귀)이면 뒤따르는
    DOCK 이 처리하므로 무시하고, 그 외(전체 긴급정지)면 emergency_stop_all{stop:true}.
    (Fleet 긴급정지는 전역이라 개별 estop 을 전역으로 올리면 다른 로봇까지 멈춘다 —
     reason 구분으로 "개별 정지·복귀 시 다른 로봇은 계속 순찰" 요구를 지킨다.)
  · RESET(재개/긴급정지 해제): emergency_stop_all{stop:false}.
PAUSE/RESUME/CANCEL/GOTO 등은 Phase 4 에서 매핑한다(지금은 로그만 남긴다).

Phase 3 (상행) 범위: 로봇 텔레메트리를 백엔드가 구독하는 §10 amr_N 네임스페이스로 재발행.
백엔드는 topic_prefix_map(AMR-01→/amr_1)대로 ``/amr_1/amcl_pose``·``/amr_1/robot_state``·
``/amr_1/battery_state`` 를 구독하는데 로봇은 ``/robot3/*``·``/control/robot3_State`` 로 낸다.
  · pose:    /robotN/amcl_pose (PoseWithCovarianceStamped) → /amr_N/amcl_pose (그대로) — 웹 원 채색·좌표
  · state:   /control/robotN_State (String JSON {robot,status,detail}) → /amr_N/robot_state
             (String "STATE:msg"). status 를 RobotState enum 으로 매핑한다
             (CONTROL_STATE_TO_ROBOT_STATE) - Fleet 대문자 상태는 그대로 통과,
             Control 소문자 세부상태만 enum 으로 올려 프론트가 한글 라벨·허용 명령을
             제대로 붙이게 한다. 콜론 뒤엔 매핑 전 원본 status 를 실어 디버깅에 남긴다.
  · battery: /robotN/battery_state (BatteryState) → /amr_N/battery_state (그대로)
"""

import json
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy, qos_profile_sensor_data)
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

# 백엔드 명령 토픽의 논리 ns(amr_N) → 로봇 물리 ns(robotN)
AMR_TO_ROBOT = {'amr_1': 'robot3', 'amr_2': 'robot8'}
ROBOT_TO_AMR = {v: k for k, v in AMR_TO_ROBOT.items()}
# Fleet map_points 의 zone_id 는 임시 포인트 라벨 접두사로만 쓰인다(그래프 종속 아님).
# 로봇별로 유일하기만 하면 되므로 고정 매핑을 준다.
ROBOT_TO_ZONE = {'robot3': 'zoneAB', 'robot8': 'zoneCD'}

# 로봇측 /control/<ns>_State 의 status 값을 백엔드/프론트의 RobotState enum(15종,
# hmi/backend/app/enums.py · hmi/frontend/src/constants/dashboard.ts)으로 매핑한다.
# Fleet(robot_status.py)은 이미 enum 대문자(PATROLLING/IDLE/DISPATCHING/
# EMERGENCY_STOP)를 그대로 내보내므로 이 표의 키(전부 소문자)와 겹치지
# 않아 아래 .get(status, status) 폴백으로 자동 통과된다. Control(control_node.py/
# state_flow.py)이 내보내는 소문자 세부상태만 여기서 enum 으로 올린다 - 안 그러면
# 프론트가 STATE_META 에 없는 원문("moving" 등)을 idle 톤 원문 라벨로 밋밋하게
# 표시한다(크래시는 안 나지만 한글 라벨·허용 명령이 안 붙는다).
# 표에 없는 값은 원문 그대로 통과시킨다(프론트가 폴백으로 견딤).
CONTROL_STATE_TO_ROBOT_STATE = {
    'waiting_mission': 'IDLE',        # 미션 대기 = 대기
    'moving': 'PATROLLING',           # 순찰 웨이포인트 이동
    'crossing_wait': 'PATROLLING',    # 교차점 점유 대기(순찰 진행 중의 짧은 대기)
    'crossing_granted': 'PATROLLING',
    'crossing_released': 'PATROLLING',
    'gate_aligning': 'INSPECTING',    # 차단기 정렬/점검
    'gate_alignment_done': 'INSPECTING',
    'gate_checking': 'INSPECTING',
    'anomaly_moving': 'DISPATCHING',  # 이상지점 이동 중
    'anomaly_waiting': 'INSPECTING',  # 현장 도착·상황 확인(운영자 결정 대기)
    'patrol_paused': 'PATROL_PAUSED',  # 운영자 개별 일시정지
    'patrol_resuming': 'RESUMING',    # 순찰 복귀 중
    'patrol_waiting': 'PATROLLING',   # 순찰 루프 간 대기(순찰 임무 유지)
    'emergency_stopped': 'EMERGENCY_STOP',
    'undocking': 'UNDOCKING',         # 출발 준비(도크 이탈 중)
    'undocked': 'PATROLLING',         # 도크 이탈 완료 → 곧 순찰 이동
    'undock_failed': 'ERROR',         # 재시도 끝에 언도킹 실패 - 운영자에게 노출
    'dock_moving': 'DOCKING',         # 도킹 스테이션으로 이동
    'docking': 'DOCKING',
    'docked': 'CHARGING',             # 도킹 완료·대기(충전)
}

MAP_POINTS_DEBOUNCE_SEC = 0.3  # 로봇별 START_PATROL 을 하나의 map_points 로 합치는 창

# 로봇(TB4/Nav2)의 amcl_pose·battery_state 는 RELIABLE/TRANSIENT_LOCAL 로 나온다 →
# 구독 QoS 를 맞춰야 조용히 유실되지 않는다.
_SRC_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL,
                      history=HistoryPolicy.KEEP_LAST, depth=10)
# 백엔드 §10 구독은 기본 QoS(RELIABLE/VOLATILE/depth10) → 상행 재발행을 여기에 맞춘다.
_UP_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.VOLATILE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)
# 배터리(Create 3 /battery_state)는 amcl_pose 와 달리 latch 되지 않는 스트리밍
# 센서 토픽이라 TRANSIENT_LOCAL 이 아니다. _SRC_QOS(RELIABLE/TRANSIENT_LOCAL)로
# 구독하면 durability 불일치로 메시지가 한 건도 안 들어온다(웹 배터리가 안 뜸).
# 구독을 BEST_EFFORT/VOLATILE(sensor data)로 두면 발행자가 RELIABLE 이든
# BEST_EFFORT 든, VOLATILE 이든 TRANSIENT_LOCAL 이든 모두 호환된다(가장 관대한 쪽).
_BATT_QOS = qos_profile_sensor_data


class BackendAdapter(Node):
    def __init__(self):
        super().__init__('backend_adapter')

        # Fleet 로 나가는 발행자
        self.map_points_pub = self.create_publisher(String, '/backend/map_points', 10)
        self.dock_pub = self.create_publisher(String, '/backend/dock', 10)
        self.estop_pub = self.create_publisher(String, '/backend/emergency_stop_all', 10)
        # 이상감지 출동: 백엔드가 화재 로봇에 GOTO(event.x/y)를 보내면, Fleet 의 좌표 기반
        # 이상 급파(최근접 로봇)로 넘긴다. (이상감지 입력은 비전(CamState→vision_bridge)이 처리)
        self.anomaly_pub = self.create_publisher(String, '/fleet/anomaly_trigger', 10)

        # 개별 일시정지/재개·재순찰은 Fleet 이 다루는 개념이 아니라 Control Node 만의
        # 관심사라, Fleet 을 거치지 않고 로봇별 control 토픽으로 직접 중계한다
        # (anomaly_trigger 처럼 /fleet/* 로 직접 발행하는 기존 패턴과 동일).
        #  · PAUSE/RESUME → /fleet/<robot>/pause {"stop": bool}
        #  · START_PATROL → /fleet/<robot>/repatrol (도킹된 로봇 재순찰용, docked 해제)
        self._pause_pubs = {
            robot: self.create_publisher(String, f'/fleet/{robot}/pause', 10)
            for robot in ROBOT_TO_AMR
        }
        self._repatrol_pubs = {
            robot: self.create_publisher(String, f'/fleet/{robot}/repatrol', 10)
            for robot in ROBOT_TO_AMR
        }

        # 백엔드 명령 구독 (amr_1/amr_2)
        for amr_ns in AMR_TO_ROBOT:
            self.create_subscription(
                String, f'/backend/{amr_ns}/command',
                lambda msg, a=amr_ns: self._on_command(a, msg), 10)

        # START_PATROL 취합용 — 로봇 물리 ns → points 리스트
        self._points_by_robot = {}
        self._flush_deadline = None
        self.create_timer(0.1, self._flush_map_points)

        # ── 상행 (로봇 → 백엔드): robotN 텔레메트리를 §10 amr_N 로 재발행 ──
        self._up_pose_pubs, self._up_state_pubs, self._up_batt_pubs = {}, {}, {}
        self._seen = set()  # (robot, kind) 첫 수신 로그용
        for robot, amr in ROBOT_TO_AMR.items():
            self._up_pose_pubs[robot] = self.create_publisher(
                PoseWithCovarianceStamped, f'/{amr}/amcl_pose', _UP_QOS)
            self._up_state_pubs[robot] = self.create_publisher(
                String, f'/{amr}/robot_state', _UP_QOS)
            self._up_batt_pubs[robot] = self.create_publisher(
                BatteryState, f'/{amr}/battery_state', _UP_QOS)
            self.create_subscription(
                PoseWithCovarianceStamped, f'/{robot}/amcl_pose',
                lambda m, r=robot: self._on_pose(r, m), _SRC_QOS)
            self.create_subscription(
                String, f'/control/{robot}_State',
                lambda m, r=robot: self._on_state(r, m), 10)
            self.create_subscription(
                BatteryState, f'/{robot}/battery_state',
                lambda m, r=robot: self._on_battery(r, m), _BATT_QOS)

        self.get_logger().info(
            'backend_adapter up — subscribing %s, mapping %s'
            % (list(f'/backend/{a}/command' for a in AMR_TO_ROBOT), AMR_TO_ROBOT))

    # ── 백엔드 → Fleet ────────────────────────────────────────────────────
    def _on_command(self, amr_ns, msg):
        try:
            env = json.loads(msg.data)
            ctype = env['command_type']
            payload = env.get('payload') or {}
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.get_logger().warn(f'bad command on /backend/{amr_ns}/command, ignoring: {exc}')
            return

        robot = AMR_TO_ROBOT.get(amr_ns)
        if robot is None:
            self.get_logger().warn(f'unknown amr ns {amr_ns}, ignoring {ctype}')
            return

        if ctype == 'START_PATROL':
            self._handle_start_patrol(robot, payload)
        elif ctype == 'DOCK':
            self._handle_dock(robot)
        elif ctype == 'ESTOP':
            self._handle_estop(robot, payload)
        elif ctype == 'RESET':
            self._handle_reset(robot)
        elif ctype == 'GOTO':
            self._handle_goto(robot, payload)
        elif ctype == 'ANOMALY_HOLD':
            self._handle_anomaly_hold(robot)
        elif ctype == 'CANCEL':
            self._handle_cancel(robot)
        elif ctype in ('PAUSE', 'RESUME'):
            self._handle_pause(robot, ctype == 'PAUSE')
        else:
            # INSPECT/EVACUATE/START_SLAM 등 — 로봇측 대응 토픽 없음
            self.get_logger().info(f'{robot} <- {ctype} (미매핑 · 무시)')

    def _handle_start_patrol(self, robot, payload):
        nodes = payload.get('nodes') or []
        points = [
            {
                'x': float(n['x']),
                'y': float(n['y']),
                'yaw': float(n.get('theta') or 0.0),
                # 백엔드가 차단기 점검 노드에 실어준 point_type('gate')을 그대로
                # 넘긴다 - zone_router 가 이걸 has_gate 로 바꿔, 로봇이 그 웨이포인트
                # 에서 차단기 실측 대조(gate_check_bridge)를 수행한다. 없으면 'normal'.
                'point_type': n.get('point_type') or 'normal',
            }
            for n in nodes
            if 'x' in n and 'y' in n
        ]
        self._points_by_robot[robot] = points
        # 도킹 복귀로 self.docked 가 선 로봇을 다시 순찰시키려면 재순찰 신호가
        # 필요하다 - 안 그러면 Control 이 docked 인 동안 아래 map_points 로 만들어진
        # 미션을 계속 무시한다(도킹 안 한 로봇에는 무해한 no-op). map_points 보다
        # 먼저 보내 docked 를 내려둔다.
        self._repatrol_pubs[robot].publish(String(data=''))
        # 백엔드는 로봇별로 START_PATROL 을 따로 보낸다 → 짧게 모아 한 번에 발행한다.
        self._flush_deadline = time.monotonic() + MAP_POINTS_DEBOUNCE_SEC
        self.get_logger().info(
            f'{robot} <- START_PATROL ({len(points)} points) · repatrol+map_points 예약')

    def _flush_map_points(self):
        if self._flush_deadline is None or time.monotonic() < self._flush_deadline:
            return
        self._flush_deadline = None
        zones = [
            {'zone_id': ROBOT_TO_ZONE.get(robot, f'zone_{robot}'), 'robot': robot, 'points': pts}
            for robot, pts in self._points_by_robot.items()
            if pts
        ]
        if not zones:
            return
        self.map_points_pub.publish(String(data=json.dumps({'zones': zones})))
        self.get_logger().info(
            'published /backend/map_points: %s'
            % {z['robot']: len(z['points']) for z in zones})

    def _handle_pause(self, robot, stop):
        # 개별 순찰 일시정지/재개. Control 이 /fleet/<robot>/pause {"stop":bool}을
        # 구독해 긴급정지와 같은 메커니즘(제자리 대기→같은 웨이포인트 재개)으로
        # 처리하되 상태만 PATROL_PAUSED 로 구분 보고한다.
        self._pause_pubs[robot].publish(String(data=json.dumps({'stop': stop})))
        self.get_logger().info(
            f'{robot} <- {"PAUSE" if stop else "RESUME"} → /fleet/{robot}/pause')

    def _handle_dock(self, robot):
        self.dock_pub.publish(String(data=json.dumps({'robots': [robot]})))
        self.get_logger().info(f'{robot} <- DOCK → /backend/dock')

    def _handle_estop(self, robot, payload):
        reason = str(payload.get('reason') or '')
        if reason == 'STOP_AND_DOCK':
            # 개별 정지·복귀의 일부 — 뒤따르는 DOCK 이 그 로봇만 복귀시킨다.
            # 전역 긴급정지로 올리지 않아야 다른 로봇이 계속 순찰한다.
            self.get_logger().info(f'{robot} <- ESTOP(STOP_AND_DOCK) → DOCK 에 위임(전역 정지 안 함)')
            return
        self.estop_pub.publish(String(data=json.dumps({'stop': True})))
        self.get_logger().info(f'{robot} <- ESTOP(reason={reason}) → emergency_stop_all(stop=true)')

    def _handle_reset(self, robot):
        self.estop_pub.publish(String(data=json.dumps({'stop': False})))
        self.get_logger().info(f'{robot} <- RESET → emergency_stop_all(stop=false)')

    def _handle_goto(self, robot, payload):
        # 이상감지 출동: 백엔드가 선정 로봇에 GOTO(event.x/y)를 보낸다. Fleet 에는 "특정 로봇을
        # 특정 좌표로" 보내는 입력이 없어, 좌표 기반 이상 급파(/fleet/anomaly_trigger {x,y},
        # 최근접 로봇)로 매핑한다. 존이 분리된 데모에선 백엔드 선정 로봇과 최근접이 대개 일치한다.
        wps = payload.get('waypoints') or []
        if not wps:
            self.get_logger().warn(f'{robot} <- GOTO without waypoints · 무시')
            return
        dest = wps[-1]  # 여러 개면 최종 목적지
        try:
            x, y = float(dest['x']), float(dest['y'])
        except (KeyError, TypeError, ValueError):
            self.get_logger().warn(f'{robot} <- GOTO bad waypoint {dest} · 무시')
            return
        self.anomaly_pub.publish(String(data=json.dumps({'x': x, 'y': y})))
        self.get_logger().info(
            f'{robot} <- GOTO({x:.2f},{y:.2f}) → /fleet/anomaly_trigger{{x,y}} (최근접 급파)')

    def _handle_anomaly_hold(self, robot):
        # 자체 감지 제자리 정지: 백엔드가 이상을 자기 카메라로 본 그 로봇을 세우라고 보낸다.
        # 좌표 기반(최근접) 급파와 달리, robot id 를 그대로 실어 자체 감지 경로
        # (/fleet/anomaly_trigger {"robot": ns})로 매핑한다 - fleet_node 가 그 로봇의
        # 현재 위치(amcl_pose)를 이상 위치로 써서 제자리에 정지시킨다.
        self.anomaly_pub.publish(String(data=json.dumps({'robot': robot})))
        self.get_logger().info(
            f'{robot} <- ANOMALY_HOLD → /fleet/anomaly_trigger{{robot:{robot}}} (제자리 정지)')

    def _handle_cancel(self, robot):
        # 순찰 취소: Fleet 에 per-robot 취소 입력이 없다. 재시작 흐름(취소→재시작)에서는
        # 이어지는 START_PATROL 의 map_points 가 미션을 통째로 교체하므로, 여기서는 다음
        # map_points 취합 대상에서만 빼둔다. 진행 중인 로봇을 즉시 세우려면 DOCK 을 쓴다.
        self._points_by_robot.pop(robot, None)
        self.get_logger().info(
            f'{robot} <- CANCEL (map_points 취합에서 제외 · 진행중 정지는 DOCK)')

    # ── 로봇 → 백엔드 (상행) ──────────────────────────────────────────────
    def _on_pose(self, robot, msg):
        # PoseWithCovarianceStamped 그대로 amr_N 네임스페이스로 재발행(형변환 없음).
        self._up_pose_pubs[robot].publish(msg)
        self._first(robot, 'pose', f'/{robot}/amcl_pose → /{ROBOT_TO_AMR[robot]}/amcl_pose')

    def _on_battery(self, robot, msg):
        self._up_batt_pubs[robot].publish(msg)
        self._first(
            robot, 'batt',
            f'/{robot}/battery_state (percentage={msg.percentage}) → '
            f'/{ROBOT_TO_AMR[robot]}/battery_state')

    def _on_state(self, robot, msg):
        # /control/robotN_State (JSON {robot,status,detail}) → 백엔드 "STATE:msg" 규칙.
        try:
            d = json.loads(msg.data)
            status = d.get('status')
        except (json.JSONDecodeError, TypeError):
            status = None
        if not status:
            return
        # status 를 RobotState enum 으로 올린다(표에 없으면 원문 통과 - Fleet 대문자
        # 상태와 미지 상태 모두 그대로). 콜론 뒤 메시지에는 매핑 전 원본 세부상태를
        # 실어, 웹은 enum 라벨을 쓰면서도 토픽을 tail 하면 세밀한 상태를 볼 수 있게 한다.
        mapped = CONTROL_STATE_TO_ROBOT_STATE.get(status, status)
        self._up_state_pubs[robot].publish(String(data=f'{mapped}:{status}'))
        self.get_logger().info(
            f'{robot} state={status} → {mapped} → /{ROBOT_TO_AMR[robot]}/robot_state')

    def _first(self, robot, kind, text):
        """고빈도 텔레메트리(pose/battery)는 첫 수신 때만 로그로 남긴다."""
        key = (robot, kind)
        if key not in self._seen:
            self._seen.add(key)
            self.get_logger().info(f'uplink 시작: {text}')


def main(args=None):
    rclpy.init(args=args)
    node = BackendAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
