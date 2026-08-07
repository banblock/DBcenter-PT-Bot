import os

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

from vision_detection.yolo_utils import YoloDetector

# AMR 주변에서 감지되면 "이상 상황"으로 취급할 클래스 이름들
DEFAULT_ANOMALY_CLASSES = ['fire', 'smoke', 'coolant']
# YOLO 모델
DEFAULT_MODEL_PATH = os.path.join(get_package_share_directory('vision_detection'), 'models', 'best.pt')
# 신뢰도 값
CONF_THRESHOLD = 0.5


class DetectAmbientNode(Node):
    """AMR 주변 이상 상황(화재/연기/냉각수 누출 등)을 상시 감지하는 노드.

    AMR 캠 이미지를 항상 구독하며 YOLO로 추론하고, 결과 이미지와 이상상황 여부를 발행한다.
    detect_station_node가 차단기 검사를 하는 동안에는 set_throttle 서비스로
    프레임 처리 비율을 낮춰 리소스를 station_node 쪽에 양보한다.
    """

    def __init__(self):
        super().__init__('detect_ambient_node')

        self.declare_parameter('model_path', DEFAULT_MODEL_PATH)
        self.declare_parameter('amr_cam_topics', ['/robot3/okad/preview/image_raw',
                                                    '/robot8/okad/preview/image_raw'])
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

        self._subs = []
        self._image_pubs = {}
        self._anomaly_pubs = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_id_from_topic(topic)
            self._frame_counters[topic] = 0
            self._image_pubs[topic] = self.create_publisher(
                Image, f'/detection/{robot_id}_cam/detection_image', image_qos)
            self._anomaly_pubs[topic] = self.create_publisher(
                Bool, f'/detection/{robot_id}_cam/anomaly_detected', 10)
            self._subs.append(self.create_subscription(
                Image, topic, self._make_image_callback(topic), image_qos))

        # detect_main_node가 차단기 검사 전/후에 호출해 쓰로틀을 켜고 끄는 서비스
        self.set_throttle_srv = self.create_service(
            SetBool, '/detection/set_throttle', self._set_throttle_callback)

        self.get_logger().info('detect_ambient_node ready')

    @staticmethod
    def _robot_id_from_topic(topic):
        # e.g. '/robot3/okad/preview/image_raw' -> 'robot3'
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
            self._image_pubs[topic].publish(self.detector.to_image_msg(annotated_image))

            anomaly = any(d['class_name'] in self.anomaly_classes for d in detections)
            self._anomaly_pubs[topic].publish(Bool(data=anomaly))

        return callback


def main(args=None):
    rclpy.init(args=args)
    node = DetectAmbientNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
