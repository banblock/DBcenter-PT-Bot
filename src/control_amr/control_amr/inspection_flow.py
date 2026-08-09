"""Gate and anomaly inspection requests for the AMR Control Node."""

import json
import time

import rclpy
from std_msgs.msg import String

GATE_CHECK_TIMEOUT_SEC = 10.0
ANOMALY_CHECK_TIMEOUT_SEC = 15.0


class InspectionFlowSupport:
    """Exchange inspection requests and results with the vision node."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        self.gate_check_request_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/gate_check_request', 10)
        self.anomaly_check_request_pub = navigator.create_publisher(
            String, f'/fleet/{namespace}/anomaly_check_request', 10)
        self._gate_check_response = None
        self._anomaly_check_response = None
        navigator.create_subscription(
            String, f'/fleet/{namespace}/gate_check_response',
            self._on_gate_check_response, 10)
        navigator.create_subscription(
            String, f'/fleet/{namespace}/anomaly_check_response',
            self._on_anomaly_check_response, 10)

    def _on_gate_check_response(self, msg):
        response = self._decode_object(msg, 'gate_check_response')
        if response is not None and isinstance(response.get('state'), str):
            self._gate_check_response = response

    def _on_anomaly_check_response(self, msg):
        response = self._decode_object(msg, 'anomaly_check_response')
        if (response is not None and
                isinstance(response.get('confirmed'), bool)):
            self._anomaly_check_response = response

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

    def inspect_anomaly(self, location):
        self._anomaly_check_response = None
        request = String()
        request.data = json.dumps({
            'robot': self.namespace, 'location': location})
        self.navigator.info(f'[{self.namespace}] requesting anomaly check')
        response = self._wait_for_response(
            '_anomaly_check_response', self.anomaly_check_request_pub,
            request, ANOMALY_CHECK_TIMEOUT_SEC)
        if response is None:
            self.navigator.info(f'[{self.namespace}] anomaly check timed out')
            return {'confirmed': None, 'location': location}
        return {'confirmed': response['confirmed'], 'location': location}
