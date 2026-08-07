import os
from collections import deque

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

from vision_detection.yolo_utils import YoloDetector
from patrol_interfaces.msg import CamState

# AMR 주변에서 감지되면 "이상 상황"으로 취급할 클래스 이름들
DEFAULT_ANOMALY_CLASSES = ['fire', 'smoke', 'coolant']
# YOLO 모델
DEFAULT_MODEL_PATH = os.path.join(get_package_share_directory('vision_detection'), 'models', 'ambient_best.pt')
# 신뢰도 값
CONF_THRESHOLD = 0.5

# CamState.msg의 state 값 (detect_cctv_node와 동일한 규칙)
STATE_BY_CLASS = {'fire': 0, 'smoke': 1, 'coolant': 2}
# CamState.msg의 camera_id 값 (0/1은 cctv1/cctv2가 사용, AMR 캠은 2번부터)
CAMERA_ID_BY_ROBOT = {'robot3': 2, 'robot8': 3}
# 감지 해제 판단용 윈도우 크기. 감지(켜짐)는 1프레임만 봐도 즉시 반응하지만,
# 해제(꺼짐)는 최근 이 프레임 수 중 과반이 미검출이어야 확정한다 (detect_cctv_node와 동일한 방식).
EVENT_WINDOW_SIZE = 3


class DetectAmbientNode(Node):
    """AMR 주변 이상 상황(화재/연기/냉각수 누출 등)을 상시 감지하는 노드.

    AMR 캠 이미지를 항상 구독하며 YOLO로 추론하고, 결과 이미지와 이상상황 여부를 발행한다.
    detect_station_node가 차단기 검사를 하는 동안에는 set_throttle 서비스로
    프레임 처리 비율을 낮춰 리소스를 station_node 쪽에 양보한다.
    """

    def __init__(self):
        super().__init__('detect_ambient_node')

        self.declare_parameter('model_path', DEFAULT_MODEL_PATH)
        self.declare_parameter('amr_cam_topics', ['/robot3/oakd/rgb/preview/image_raw',
                                                    '/robot8/oakd/rgb/preview/image_raw'])
        self.declare_parameter('anomaly_classes', DEFAULT_ANOMALY_CLASSES)
        #평상시 1장마다 1번씩 처리, 쓰로틀 시 10장마다 1번씩 처리
        self.declare_parameter('normal_process_every_n', 1)
        self.declare_parameter('throttled_process_every_n', 10)

        model_path = self.get_parameter('model_path').value
        conf_threshold = CONF_THRESHOLD
        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.anomaly_classes = set(self.get_parameter('anomaly_classes').value)
        self.normal_process_every_n = self.get_parameter('normal_process_every_n').value
        self.throttled_process_every_n = self.get_parameter('throttled_process_every_n').value

        self.detector = YoloDetector(model_path, conf_threshold)

        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        # detect_station_node가 차단기 검사 중일 때는 구독을 끊는 대신
        # 프레임을 N장마다 1번만 처리하도록 낮춰서(쓰로틀) 리소스를 아낀다.
        self.process_every_n = self.normal_process_every_n
        self._frame_counters = {}

        # CamState 재발행을 막기 위해 클래스별로 "지금 이상상황이 진행 중인가"를 기억해둔다.
        # 켜짐은 즉시 반영하고, 꺼짐은 최근 프레임 윈도우의 과반 판정으로만 반영한다.
        self._active_classes = {}
        self._recent_detections = {}
        self._camera_id_by_topic = {}

        self._subs = []
        self._image_pubs = {}
        self._anomaly_pubs = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_id_from_topic(topic)
            self._frame_counters[topic] = 0
            self._active_classes[topic] = set()
            self._recent_detections[topic] = {
                class_name: deque(maxlen=EVENT_WINDOW_SIZE) for class_name in self.anomaly_classes
            }
            self._camera_id_by_topic[topic] = CAMERA_ID_BY_ROBOT.get(robot_id)
            self._image_pubs[topic] = self.create_publisher(
                Image, f'/detection/{robot_id}_cam/detection_image', image_qos)
            self._anomaly_pubs[topic] = self.create_publisher(
                Bool, f'/detection/{robot_id}_cam/anomaly_detected', 10)
            self._subs.append(self.create_subscription(
                Image, topic, self._make_image_callback(topic), image_qos))

        # detect_cctv_node와 동일한 이상상황 이벤트 토픽 (fire/smoke/coolant 새로 인식 시 발행)
        self.cam_state_pub = self.create_publisher(CamState, '/detection/cam_state', 10)

        # detect_main_node가 차단기 검사 전/후에 호출해 쓰로틀을 켜고 끄는 서비스
        self.set_throttle_srv = self.create_service(
            SetBool, '/detection/set_throttle', self._set_throttle_callback)

        self.get_logger().info('detect_ambient_node ready')

    @staticmethod
    def _robot_id_from_topic(topic):
        # e.g. '/robot3/oakd/rgb/preview/image_raw' -> 'robot3'
        return topic.strip('/').split('/')[0]

    def _set_throttle_callback(self, request, response):
        if request.data:
            self.process_every_n = self.throttled_process_every_n
            response.message = 'ambient throttled down'
        else:
            self.process_every_n = self.normal_process_every_n
            response.message = 'ambient throttle restored'
        response.success = True
        return response

    def _make_image_callback(self, topic):
        def callback(msg):
            # process_every_n 프레임마다 한 번만 실제 추론을 수행 (쓰로틀 적용 시 N이 커짐)
            self._frame_counters[topic] += 1
            if self._frame_counters[topic] % self.process_every_n != 0:
                return

            annotated_image, detections = self.detector.infer_from_msg(msg)
            detected_classes = {d['class_name'] for d in detections if d['class_name'] in self.anomaly_classes}
            self._anomaly_pubs[topic].publish(Bool(data=bool(detected_classes)))

            # 이상상황이 감지되는 동안에는 매 프레임 이미지를 계속 보내고, 감지가 끝나면(빈 집합) 멈춘다
            if detected_classes:
                self._image_pubs[topic].publish(self.detector.to_image_msg(annotated_image))

            # CamState: 감지(켜짐)는 1프레임만 봐도 즉시 발행하되, 이미 진행 중인 상황은 재발행하지
            # 않는다. 진행 중 여부(꺼짐 판정)는 최근 프레임 윈도우의 과반으로만 해제해서,
            # 프레임 간 미세한 검출 흔들림(flicker) 때문에 같은 상황이 반복 발행되는 것을 막는다.
            camera_id = self._camera_id_by_topic[topic]
            majority = EVENT_WINDOW_SIZE // 2 + 1
            active_classes = self._active_classes[topic]
            for class_name in self.anomaly_classes:
                detected = class_name in detected_classes
                window = self._recent_detections[topic][class_name]
                window.append(detected)

                if detected and class_name not in active_classes:
                    active_classes.add(class_name)
                    self.cam_state_pub.publish(CamState(camera_id=camera_id, state=STATE_BY_CLASS[class_name]))
                elif (
                    class_name in active_classes
                    and len(window) == window.maxlen
                    and sum(window) < majority
                ):
                    # 윈도우가 다 찼을 때만 해제 판정 (덜 찬 상태에서 미스 1번으로 바로 꺼짐 처리되는 것 방지)
                    active_classes.discard(class_name)

        return callback


def main(args=None):
    rclpy.init(args=args)
    node = DetectAmbientNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
