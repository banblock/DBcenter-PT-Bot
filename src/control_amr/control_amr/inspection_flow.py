"""Gate and anomaly inspection scaffolding for the AMR Control Node."""

import json
import time

import rclpy
from std_msgs.msg import String

# 차단기 점검 / 이상상황 점검 요청에 대한 응답을 기다리는 최대 시간.
# TODO: 실제 비전 파이프라인 처리 시간에 맞춰 조정한다.
GATE_CHECK_TIMEOUT_SEC = 10.0
ANOMALY_CHECK_TIMEOUT_SEC = 15.0


class InspectionFlowSupport:
    """Hooks for camera/vision based inspection flows."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator

        # ------------------------------------------------------------
        # Fleet Node(비전/CCTV 파이프라인)와 주고받을 topic들.
        # NOTE: 아래 topic 이름은 전부 "임의로 지정한 이름"이다. 다이어그램에는
        # 차단기 확인이 CheckGate.srv라는 서비스로 돼있지만, 이 코드베이스는
        # mission_flow.py의 route_update_request/response와 같은 방식으로
        # String+JSON topic 기반 request/response로 통일했다 - 실제로 서비스를
        # 쓰기로 확정되면 이 부분만 rclpy 서비스 클라이언트로 바꾸면 된다.
        # 비전 노드를 담당하는 팀원과 이름/메시지 형식을 반드시 맞춰야 한다.
        # ------------------------------------------------------------
        self.gate_check_request_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/gate_check_request', 10)
        self._gate_check_response = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/gate_check_response',
            self._on_gate_check_response, 10)

        self.anomaly_check_request_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/anomaly_check_request', 10)
        self._anomaly_check_response = None
        self.navigator.create_subscription(
            String, f'/fleet/{namespace}/anomaly_check_response',
            self._on_anomaly_check_response, 10)

    def _on_gate_check_response(self, msg):
        try:
            self._gate_check_response = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.navigator.info(f'[{self.namespace}] invalid gate_check_response: {exc}')

    def _on_anomaly_check_response(self, msg):
        try:
            self._anomaly_check_response = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.navigator.info(f'[{self.namespace}] invalid anomaly_check_response: {exc}')

    def inspect_gate(self):
        """차단기 상태 확인을 비전 노드에 요청하고 응답이 올 때까지 기다린다.
        mission_flow.request_route_update()와 같은 패턴: 응답이 올 때까지
        0.5초 간격으로 재요청하면서 spin_once로 대기한다 (다른 콜백도 계속 처리됨)."""
        self._gate_check_response = None
        request = String()
        request.data = json.dumps({'robot': self.namespace})
        self.navigator.info(f'[{self.namespace}] requesting gate check')

        started_at = time.monotonic()
        last_publish = 0.0
        while self._gate_check_response is None:
            now = time.monotonic()
            if now - started_at >= GATE_CHECK_TIMEOUT_SEC:
                self.navigator.info(f'[{self.namespace}] gate check request timed out')
                return False
            if now - last_publish >= 0.5:
                self.gate_check_request_pub.publish(request)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

        # TODO: state 값의 실제 종류(열림/닫힘/인식 실패 등)와 그에 따른 처리
        # 정책은 비전 노드 인터페이스가 확정되면 여기서 분기한다. 지금은
        # 'ok'만 통과로 보고 나머지는 전부 실패로 취급한다.
        gate_state = self._gate_check_response.get('state')
        self.navigator.info(f'[{self.namespace}] gate check result: {gate_state}')
        return gate_state == 'ok'

    def wait_for_anomaly_arrival(self):
        # NOTE: control_node.py는 실제로는 navigation_flow.wait_until_pose_reached()를
        # 이동 대기에 쓰고 있어서 이 메서드는 현재 어디서도 호출되지 않는다
        # (control_node.py는 손댈 수 없어서 그대로 훅만 남겨둔다).
        return True

    def inspect_anomaly(self, location):
        """이상지역 도착 후 비전 노드에 실제 이상 여부 확인을 요청한다."""
        self._anomaly_check_response = None
        request = String()
        request.data = json.dumps({'robot': self.namespace, 'location': location})
        self.navigator.info(
            f'[{self.namespace}] requesting anomaly check at '
            f'({location["x"]}, {location["y"]})')

        started_at = time.monotonic()
        last_publish = 0.0
        while self._anomaly_check_response is None:
            now = time.monotonic()
            if now - started_at >= ANOMALY_CHECK_TIMEOUT_SEC:
                self.navigator.info(f'[{self.namespace}] anomaly check request timed out')
                return {'confirmed': None, 'location': location}
            if now - last_publish >= 0.5:
                self.anomaly_check_request_pub.publish(request)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)

        confirmed = self._anomaly_check_response.get('confirmed')
        self.navigator.info(f'[{self.namespace}] anomaly check result: confirmed={confirmed}')
        return {'confirmed': confirmed, 'location': location}