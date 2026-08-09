"""Mission-level scaffolding for the AMR Control Node."""

import json
import time

import rclpy
from std_msgs.msg import String

# mission(=waypoint 목록)의 각 waypoint가 반드시 가지고 있어야 하는 필드.
# control_node.py의 run()에서 wp['x'], wp['y'], wp['yaw']를 .get() 없이
# 바로 꺼내 쓰기 때문에, 여기서 미리 없으면 걸러줘야 control_node.py가
# KeyError로 죽는 걸 막을 수 있다.
REQUIRED_WAYPOINT_FIELDS = ('x', 'y', 'yaw')

# 경로 갱신 요청 후 Fleet Node 응답을 기다리는 최대 시간.
# TODO: 운영 정책이 정해지면 값을 조정한다.
ROUTE_UPDATE_TIMEOUT_SEC = 10.0


class MissionFlowSupport:
    """Hooks for mission validation, route updates, and completion handling."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        # TODO: 1시간 주기 순찰 정책이 확정되면 parameter로 받을 수 있게 한다.
        self.patrol_interval_sec = 60 * 60

        # ------------------------------------------------------------
        # Fleet Node와 주고받을 topic들.
        # NOTE: 아래 topic 이름은 전부 "임시로 지은 이름"이다. Fleet Node를
        # 담당하는 팀원과 이름/메시지 형식을 맞춘 뒤 반드시 수정해야 한다.
        # (control_node.py에 이미 있는 '/fleet/occupancy_request' 같은
        # 기존 topic들의 네이밍 패턴을 그대로 따라서 지었다.)
        # ------------------------------------------------------------

        # 미션 검증에 실패했을 때 이유를 알리는 topic.
        self.mission_reject_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/mission_reject', 10)

        # 경로 갱신 요청(request) / 응답(response) topic 쌍.
        # control_node.py의 occupancy 요청-허가 패턴과 동일하게, 응답이
        # 올 때까지 요청을 주기적으로 재전송하며 기다리는 방식으로 만들었다.
        self.route_update_request_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/route_update_request', 10)
        self._route_update_response = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/route_update_response',
            self._on_route_update_response, 10)

        # 미션 완료를 알리는 topic.
        self.mission_complete_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/mission_complete', 10)

        # 다음 순찰 미션을 요청하는 topic. 실제 새 미션 "내용"은 이 topic의
        # 응답이 아니라, control_node.py가 이미 구독하고 있는
        # '/fleet/{namespace}/mission' topic으로 다시 내려온다고 가정한다.
        # 그래서 이 요청에 대한 별도의 response topic은 만들지 않았다.
        self.next_mission_request_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/request_next_mission', 10)

        # 순찰 cycle 시작/종료 상태를 Fleet Node/UI에 알리는 topic.
        self.patrol_status_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/patrol_status', 10)

        # 순찰 cycle을 이 로봇 스스로 추적하기 위한 내부 상태.
        # (Fleet Node가 아니라 로봇 쪽에서 "다음 순찰까지 얼마나 남았는지"를
        # 계산하는 데 쓴다. 아직 control_node.py에서 이 cycle 로직 자체를
        # 반복 호출하는 부분은 없어서, 지금은 이 클래스 안에서만 일관되게
        # 동작하도록 만들어뒀다 - 나중에 control_node.py 쪽에 순찰 반복
        # loop이 추가되면 그대로 연결해서 쓸 수 있다.)
        self._patrol_cycle_id = 0
        self._last_patrol_finished_at = None

    # ------------------------------------------------------------------
    # 미션 검증
    # ------------------------------------------------------------------
    def validate_mission(self, mission):
        """mission은 waypoint(dict) 목록이어야 한다.
        각 waypoint는 x, y, yaw(숫자)를 반드시 가져야 하고,
        point_id(문자열)와 has_gate(불리언)는 있으면 타입만 검사한다."""
        if not isinstance(mission, list) or len(mission) == 0:
            self._reject_mission('mission_must_be_nonempty_list')
            return False

        for index, waypoint in enumerate(mission):
            if not isinstance(waypoint, dict):
                self._reject_mission('waypoint_must_be_object', {'index': index})
                return False

            for field in REQUIRED_WAYPOINT_FIELDS:
                value = waypoint.get(field)
                is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
                if field not in waypoint or not is_number:
                    self._reject_mission(
                        'waypoint_missing_or_invalid_field',
                        {'index': index, 'field': field})
                    return False

            point_id = waypoint.get('point_id')
            if point_id is not None and not isinstance(point_id, str):
                self._reject_mission('point_id_must_be_string', {'index': index})
                return False

            if 'has_gate' in waypoint and not isinstance(waypoint['has_gate'], bool):
                self._reject_mission('has_gate_must_be_bool', {'index': index})
                return False

        return True

    def _reject_mission(self, reason, detail=None):
        self.navigator.info(f'[{self.namespace}] mission rejected: {reason} ({detail})')
        msg = String()
        msg.data = json.dumps({'robot': self.namespace, 'reason': reason, 'detail': detail})
        self.mission_reject_pub.publish(msg)

    # ------------------------------------------------------------------
    # 경로 갱신
    # ------------------------------------------------------------------
    def _on_route_update_response(self, msg):
        try:
            self._route_update_response = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.navigator.info(f'[{self.namespace}] invalid route_update_response: {exc}')

    def request_route_update(self, reason, current_waypoint=None):
        """Fleet Node에 새 경로를 요청하고 응답이 올 때까지 기다린다.
        control_node.py의 _request_crossing()과 같은 패턴: 응답이 올 때까지
        0.5초 간격으로 요청을 재전송하면서 spin_once로 대기한다."""
        self._route_update_response = None
        request = String()
        request.data = json.dumps({
            'robot': self.namespace,
            'reason': reason,
            'current_waypoint': current_waypoint,
        })
        self.navigator.info(f'[{self.namespace}] requesting route update: {reason}')

        started_at = time.monotonic()
        last_publish = 0.0
        while self._route_update_response is None:
            now = time.monotonic()
            if now - started_at >= ROUTE_UPDATE_TIMEOUT_SEC:
                self.navigator.info(f'[{self.namespace}] route update request timed out')
                return None
            if now - last_publish >= 0.5:
                self.route_update_request_pub.publish(request)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

        # TODO: control_node.py가 이 반환값(새 waypoint 목록)을 실제
        # self.mission에 반영하도록 연결해야 한다. control_node.py는 지금
        # 이 반환값을 "None이 아니면 처리된 것"으로만 취급하고 있어서, 새
        # 경로 내용을 mission에 반영하는 부분은 아직 없다 (다른 팀원 파일이라
        # 이번 작업 범위에서는 다루지 않았다).
        return self._route_update_response.get('waypoints')

    # ------------------------------------------------------------------
    # 미션 완료
    # ------------------------------------------------------------------
    def handle_mission_complete(self):
        self.navigator.info(f'[{self.namespace}] mission complete')
        msg = String()
        msg.data = json.dumps({'robot': self.namespace})
        self.mission_complete_pub.publish(msg)
        self.mark_patrol_cycle_finished()

    def choose_post_mission_action(self):
        # 배터리 상태를 보고 충전(charge) 복귀를 결정하는 부분은
        # state_flow.py(다른 팀원 파일)의 책임 영역이라 여기서는 다루지 않는다.
        # 이 메서드는 "순찰 주기상 다음 미션을 바로 요청할지, 대기할지"만 결정한다.
        if self.should_start_next_patrol():
            self.request_next_patrol_mission()
            return 'request_next_mission'
        return 'standby'

    # ------------------------------------------------------------------
    # 1시간 주기 순찰(patrol cycle) 관리
    # ------------------------------------------------------------------
    def get_patrol_interval_sec(self):
        # TODO: ROS parameter 또는 Fleet Node 설정값으로 순찰 주기를 덮어쓸 수 있게 한다.
        return self.patrol_interval_sec

    def wait_until_next_patrol_cycle(self):
        """다음 순찰 시작 시점까지 대기한다. 아직 한 번도 순찰을 마친 적이
        없으면(초기 상태) 바로 시작해도 되므로 대기하지 않는다."""
        if self._last_patrol_finished_at is None:
            return True

        interval = self.get_patrol_interval_sec()
        remaining = interval - (time.monotonic() - self._last_patrol_finished_at)
        while remaining > 0:
            # time.sleep() 대신 spin_once를 반복 호출해서, 대기 중에도
            # anomaly 같은 다른 콜백이 계속 처리되도록 한다.
            rclpy.spin_once(self.navigator, timeout_sec=min(1.0, remaining))
            remaining = interval - (time.monotonic() - self._last_patrol_finished_at)
        return True

    def should_start_next_patrol(self):
        if self._last_patrol_finished_at is None:
            return True
        elapsed = time.monotonic() - self._last_patrol_finished_at
        return elapsed >= self.get_patrol_interval_sec()

    def request_next_patrol_mission(self):
        self.navigator.info(f'[{self.namespace}] requesting next patrol mission')
        msg = String()
        msg.data = json.dumps({'robot': self.namespace, 'reason': 'patrol_interval_elapsed'})
        self.next_mission_request_pub.publish(msg)
        # 실제 새 미션 내용은 이 요청의 응답이 아니라
        # '/fleet/{namespace}/mission' topic으로 내려온다 (위 설명 참고).
        return None

    def mark_patrol_cycle_started(self):
        self._patrol_cycle_id += 1
        self.navigator.info(f'[{self.namespace}] patrol cycle {self._patrol_cycle_id} started')
        self._publish_patrol_status('started')

    def mark_patrol_cycle_finished(self):
        self._last_patrol_finished_at = time.monotonic()
        next_in_sec = self.get_patrol_interval_sec()
        self.navigator.info(
            f'[{self.namespace}] patrol cycle {self._patrol_cycle_id} finished, '
            f'next patrol in {next_in_sec} sec')
        self._publish_patrol_status('finished', {'next_patrol_in_sec': next_in_sec})

    def _publish_patrol_status(self, status, detail=None):
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace,
            'cycle_id': self._patrol_cycle_id,
            'status': status,
            'detail': detail,
        })
        self.patrol_status_pub.publish(msg)
