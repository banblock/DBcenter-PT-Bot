import threading
import time

import rclpy
from cv_bridge import CvBridge
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from patrol_interfaces.srv import CheckGate

from vision_detection.gate_color_detector import GateColorDetector

class DetectStationNode(Node):
    """차단기(gate) 상태를 검사하는 노드.

    평소에는 AMR 캠을 구독하지 않고 대기하다가, main_node가 inspect_gate
    서비스를 호출한 순간에만 구독을 열어 프레임을 여러 장 모으고
    (Hough Circle + HSV 색상판별로) 판정한 뒤 과반수 투표로 최종 상태를 정하고 구독을 닫는다.
    프레임 1장만으로 판정하면 모션 블러/조명 반사 같은 순간적인 오검출에 취약해서
    안전 판단(차단기 상태)에는 여러 장을 모아 다수결로 확정한다.
    """

    def __init__(self):
        """파라미터 로드, GateColorDetector 생성, robot_id→토픽 매핑, 서비스/토픽 등록."""
        super().__init__('detect_station_node')

        self.task_started = False

        self.declare_parameter('amr_cam_topics', ['/robot3/oakd/rgb/preview/image_raw',
                                                    '/robot8/oakd/rgb/preview/image_raw'])
        self.declare_parameter('fresh_frame_timeout_sec', 2.0)
        self.declare_parameter('sample_frame_count', 3)
        self.declare_parameter('hough_dp', 1.2)
        self.declare_parameter('hough_min_dist', 50.0)
        self.declare_parameter('hough_param1', 100.0)
        self.declare_parameter('hough_param2', 30.0)
        self.declare_parameter('hough_min_radius', 10)
        self.declare_parameter('hough_max_radius', 100)
        # 빨간색은 OpenCV HSV에서 hue 0/180 양쪽에 걸쳐 있어서 "닫힘" 판정엔 범위가 2개 필요
        self.declare_parameter('closed_hsv_lower1', [0, 70, 50])
        self.declare_parameter('closed_hsv_upper1', [10, 255, 255])
        self.declare_parameter('closed_hsv_lower2', [170, 70, 50])
        self.declare_parameter('closed_hsv_upper2', [180, 255, 255])
        self.declare_parameter('open_hsv_lower', [40, 70, 50])
        self.declare_parameter('open_hsv_upper', [80, 255, 255])
        self.declare_parameter('min_color_ratio', 0.3)

        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.fresh_frame_timeout_sec = self.get_parameter('fresh_frame_timeout_sec').value
        self.sample_frame_count = self.get_parameter('sample_frame_count').value

        hough_params = {
            'dp': self.get_parameter('hough_dp').value,
            'min_dist': self.get_parameter('hough_min_dist').value,
            'param1': self.get_parameter('hough_param1').value,
            'param2': self.get_parameter('hough_param2').value,
            'min_radius': self.get_parameter('hough_min_radius').value,
            'max_radius': self.get_parameter('hough_max_radius').value,
        }
        closed_hsv_ranges = [
            (self.get_parameter('closed_hsv_lower1').value, self.get_parameter('closed_hsv_upper1').value),
            (self.get_parameter('closed_hsv_lower2').value, self.get_parameter('closed_hsv_upper2').value),
        ]
        open_hsv_ranges = [
            (self.get_parameter('open_hsv_lower').value, self.get_parameter('open_hsv_upper').value),
        ]
        self.detector = GateColorDetector(
            hough_params, closed_hsv_ranges, open_hsv_ranges,
            min_color_ratio=self.get_parameter('min_color_ratio').value)

        self.bridge = CvBridge()

        self._image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        # inspect_gate 처리 중 도착한 최신 프레임들을 토픽별로 캐싱
        self._latest_frames = {}
        self._frame_lock = threading.Lock()
        # 구독 시작 후 새 프레임이 한 장이라도 도착했는지 알리는 이벤트
        self._new_frame_event = threading.Event()
        # 구독을 상시 유지하지 않고 inspect_gate 호출마다 열고 닫는다.
        # -> 평소엔 detect_ambient_node만 AMR 캠을 소비하게 해서 이중 디코딩/트래픽을 없앤다.
        self._subs = {}

        # 요청의 robot_id(정수)로 어떤 카메라 토픽을 볼지 찾기 위한 매핑
        self._topic_by_robot_id = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_num_from_topic(topic)
            self._topic_by_robot_id[robot_id] = topic

        # 서비스 콜백 안에서 새 프레임을 기다리는 동안에도 구독 콜백이 동시에
        # 실행돼야 하므로 ReentrantCallbackGroup + MultiThreadedExecutor 사용
        self.inspect_gate_srv = self.create_service(
            CheckGate, '/detection/inspect_gate', self._inspect_gate_callback,
            callback_group=ReentrantCallbackGroup())

        # detect_cctv_node와 동일하게 /ui/start(True=시작/False=정지)로 검사 요청 수락 여부를 제어
        self.start_subscription = self.create_subscription(
            Bool, '/ui/start', self._start_callback, 1)

    def _start_callback(self, message: Bool) -> None:
        """/ui/start 수신 시 task_started를 갱신(True=검사 요청 수락, False=거부)."""
        if message.data == self.task_started:
            return
        self.task_started = message.data

    @staticmethod
    def _robot_num_from_topic(topic):
        """토픽 문자열에서 로봇 번호(정수)를 뽑는다. e.g. '/robot3/oakd/rgb/preview/image_raw' -> 3"""
        robot_str = topic.strip('/').split('/')[0]
        return int(''.join(filter(str.isdigit, robot_str)))

    def _make_image_callback(self, topic):
        """토픽별 이미지 콜백 클로저를 생성: 검사 중에만 최신 프레임을 캐싱하고 새 프레임 도착을 알린다."""
        def callback(msg):
            with self._frame_lock:
                self._latest_frames[topic] = msg
            self._new_frame_event.set()

        return callback

    def _activate_cam_sub(self, topic):
        """검사 시작: 요청받은 로봇의 카메라 하나만 구독을 연다."""
        self._latest_frames = {}
        self._new_frame_event.clear()
        self._subs[topic] = self.create_subscription(
            Image, topic, self._make_image_callback(topic), self._image_qos)

    def _deactivate_cam_sub(self, topic):
        """검사 종료: 열었던 구독을 정리한다."""
        sub = self._subs.pop(topic, None)
        if sub is not None:
            self.destroy_subscription(sub)

    def _collect_frames(self, topic):
        """fresh_frame_timeout_sec 예산 안에서 서로 다른 프레임을 최대 sample_frame_count장 모은다."""
        frames = []
        deadline = time.monotonic() + self.fresh_frame_timeout_sec
        for _ in range(self.sample_frame_count):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._new_frame_event.clear()
            if not self._new_frame_event.wait(timeout=remaining):
                break
            with self._frame_lock:
                msg = self._latest_frames.get(topic)
            if msg is not None:
                frames.append(msg)
        return frames

    def _inspect_gate_callback(self, request, response):
        """main_node의 검사 요청 진입점: 캠 구독을 열고 프레임을 모아 색상 판정 후 구독을 닫고 응답."""
        if not self.task_started:
            response.gate_state_equal = False
            response.error_state = 3  # 로봇 cam 연결 안 됨(감지 미시작도 같은 코드로 취급)
            return response

        topic = self._topic_by_robot_id.get(request.robot_id)
        if topic is None:
            response.gate_state_equal = False
            response.error_state = 3  # 로봇 cam 연결 안 됨
            return response

        self._activate_cam_sub(topic)
        try:
            frames = self._collect_frames(topic)
            if not frames:
                response.gate_state_equal = False
                response.error_state = 3  # 로봇 cam 연결 안 됨
                return response

            # 프레임 1장만으로 판정하면 모션 블러/조명 반사에 취약하므로 여러 장을 모아 과반수로 확정한다.
            results = []
            for msg in frames:
                cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
                _, gate_closed = self.detector.detect(cv_image, draw=False)
                if gate_closed is not None:
                    results.append(gate_closed)

            majority = len(frames) // 2 + 1
            if len(results) < majority:
                response.gate_state_equal = False
                response.error_state = 2  # 차단기 못 찾음
                return response

            gate_closed = sum(results) >= (len(results) // 2 + 1)

            # DB 기준 상태(request.gate_state)와 카메라로 본 실제 상태가 같은지 비교
            response.gate_state_equal = (gate_closed == request.gate_state)
            response.error_state = 0 if response.gate_state_equal else 1  # 0:정상, 1:상태 불일치
            return response
        finally:
            self._deactivate_cam_sub(topic)


def main(args=None):
    """노드 진입점. 서비스 콜백 안에서 구독 콜백을 동시에 처리해야 하므로 MultiThreadedExecutor 사용."""
    rclpy.init(args=args)
    node = DetectStationNode()
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
