import time

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor

from std_srvs.srv import SetBool
from patrol_interfaces.srv import CheckGate


class DetectMainNode(Node):
    """전체 detection 흐름을 관리하는 중앙 노드.

    외부(UI)로부터 CheckGate 서비스 요청을 받으면,
    ambient_node를 쓰로틀 -> station_node에게 차단기 검사 요청 -> ambient_node 복구
    순서로 다른 노드들을 제어하고 최종 결과를 응답/발행한다.
    """

    def __init__(self):
        super().__init__('detect_main_node')

        self.declare_parameter('service_call_timeout_sec', 5.0)
        self.service_call_timeout_sec = self.get_parameter('service_call_timeout_sec').value

        # 서비스 콜백 안에서 다른 서비스를 동기적으로 호출해야 하므로
        # 콜백들이 서로 다른 스레드에서 동시에 실행될 수 있게 ReentrantCallbackGroup + MultiThreadedExecutor 사용
        callback_group = ReentrantCallbackGroup()

        # 외부(UI)에서 호출하는 진입점 서비스
        self.check_gate_srv = self.create_service(
            CheckGate, '/backend/check_gate', self._check_gate_callback,
            callback_group=callback_group)
        # station_node의 차단기 검사 서비스 클라이언트
        self.inspect_gate_client = self.create_client(
            CheckGate, '/detection/inspect_gate', callback_group=callback_group)
        # ambient_node의 캠 구독 처리 속도(쓰로틀) 조절 서비스 클라이언트
        self.set_throttle_client = self.create_client(
            SetBool, '/detection/set_throttle', callback_group=callback_group)

        self.get_logger().info('detect_main_node ready')

    def _call_blocking(self, client, request):
        """비동기 서비스 호출을 동기처럼 기다렸다가 결과를 반환.

        MultiThreadedExecutor에서 다른 스레드가 응답 콜백을 처리해주기 때문에
        여기서는 future.done()이 될 때까지 짧게 sleep하며 폴링만 하면 데드락 없이 동작한다.
        """
        if not client.wait_for_service(timeout_sec=self.service_call_timeout_sec):
            return None
        future = client.call_async(request)
        deadline = time.monotonic() + self.service_call_timeout_sec
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        return future.result() if future.done() else None

    def _check_gate_callback(self, request, response):
        # detect_ambient_node cam 이미지 sub 속도 조절(하락)
        self._call_blocking(self.set_throttle_client, SetBool.Request(data=True))

        # detect_station_node amr cam 이미지 추론 진행 (활성화/추론/비활성화가 이 서비스 콜백 내부에서 전부 처리됨)
        # station이 어느 카메라를 볼지 알아야 하므로 UI 요청 필드를 그대로 전달
        inspect_request = CheckGate.Request()
        inspect_request.robot_id = request.robot_id
        inspect_request.gate_id = request.gate_id
        inspect_request.gate_state = request.gate_state
        inspect_result = self._call_blocking(self.inspect_gate_client, inspect_request)

        # detect_ambient_node cam 이미지 sub 속도 조절(원상 복구)
        self._call_blocking(self.set_throttle_client, SetBool.Request(data=False))

        if inspect_result is None:
            self.get_logger().error('detect_station_node did not respond in time')
            response.gate_state_equal = False
            response.error_state = 3  # 로봇 cam 연결 안 됨(station 응답 없음도 같은 코드로 취급)
            return response

        response.gate_state_equal = inspect_result.gate_state_equal
        response.error_state = inspect_result.error_state
        return response


def main(args=None):
    rclpy.init(args=args)
    node = DetectMainNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
