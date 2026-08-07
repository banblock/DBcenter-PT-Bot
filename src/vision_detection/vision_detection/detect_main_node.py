import time

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

from vision_detection_interfaces.srv import CheckGate, InspectGate


class DetectMainNode(Node):
    def __init__(self):
        super().__init__('detect_main_node')

        self.declare_parameter('service_call_timeout_sec', 5.0)
        self.service_call_timeout_sec = self.get_parameter('service_call_timeout_sec').value

        callback_group = ReentrantCallbackGroup()

        self.check_result_pub = self.create_publisher(Bool, '/detection/check_result', 10)

        self.inspect_gate_client = self.create_client(
            InspectGate, '/detect_station_node/inspect_gate', callback_group=callback_group)
        self.set_throttle_client = self.create_client(
            SetBool, '/detect_ambient_node/set_throttle', callback_group=callback_group)

        self.check_gate_srv = self.create_service(
            CheckGate, '~/check_gate', self._check_gate_callback,
            callback_group=callback_group)

        self.get_logger().info('detect_main_node ready')

    def _call_blocking(self, client, request):
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

        # detect_station_node amr cam 이미지 추론 진행
        inspect_result = self._call_blocking(self.inspect_gate_client, InspectGate.Request())

        # detect_ambient_node cam 이미지 sub 속도 조절(원상 복구)
        self._call_blocking(self.set_throttle_client, SetBool.Request(data=False))

        if inspect_result is None:
            response.success = False
            response.gate_closed = False
            response.message = 'detect_station_node did not respond in time'
            return response

        response.success = inspect_result.success
        response.gate_closed = inspect_result.gate_closed
        response.message = inspect_result.message

        self.check_result_pub.publish(Bool(data=response.gate_closed))
        return response


def main(args=None):
    rclpy.init(args=args)
    node = DetectMainNode()
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
