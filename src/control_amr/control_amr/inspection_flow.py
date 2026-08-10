"""Gate inspection requests for the AMR Control Node.

이상신호(anomaly) 점검은 더 이상 여기 없다 - 원래는 비전 노드가 자동으로
판정하는 걸 기다렸는데(anomaly_check_request/response, 15초 타임아웃),
실제 설계는 자동 판정이 아니라 로봇이 카메라로 상황을 비추는 동안 HMI에서
운영자가 재개/도킹을 직접 결정하는 것이었다(사용자 확인). 그래서 이
왕복은 control_node.py의 _handle_anomaly()가 더는 안 쓴다 - 도킹 판단은
기존 /fleet/<ns>/dock을, 재개 판단은 신규 /fleet/<ns>/anomaly_resume을
직접 구독해서 처리한다."""

import json
import time

import rclpy
from std_msgs.msg import String

GATE_CHECK_TIMEOUT_SEC = 10.0


class InspectionFlowSupport:
    """Exchange gate inspection requests and results with the vision node."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self.gate_check_request_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/gate_check_request', 10)
        self._gate_check_response = None
        navigator.create_subscription(
            String, f'/fleet/{namespace}/gate_check_response',
            self._on_gate_check_response, 10)

    def _on_gate_check_response(self, msg):
        response = self._decode_object(msg, 'gate_check_response')
        if response is not None and isinstance(response.get('state'), str):
            self._gate_check_response = response

    def _decode_object(self, msg, response_name):
        try:
            response = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.navigator.info(
                f'[{self.namespace}] invalid {response_name}: {exc}')
            return None
        if not isinstance(response, dict):
            self.navigator.info(
                f'[{self.namespace}] invalid {response_name}: expected object')
            return None
        return response

    def _wait_for_response(self, attribute, publisher, request, timeout):
        started_at = time.monotonic()
        last_publish = 0.0
        while getattr(self, attribute) is None:
            now = time.monotonic()
            if now - started_at >= timeout:
                return None
            if now - last_publish >= 0.5:
                publisher.publish(request)
                last_publish = now
            rclpy.spin_once(self.navigator, timeout_sec=0.1)
        return getattr(self, attribute)

    def inspect_gate(self):
        self._gate_check_response = None
        request = String()
        request.data = json.dumps({'robot': self.namespace})
        self.navigator.info(f'[{self.namespace}] requesting gate check')
        response = self._wait_for_response(
            '_gate_check_response', self.gate_check_request_pub,
            request, GATE_CHECK_TIMEOUT_SEC)
        if response is None:
            self.navigator.info(f'[{self.namespace}] gate check timed out')
            return False
        state = response['state']
        self.navigator.info(f'[{self.namespace}] gate check result: {state}')
        return state == 'ok'
