import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

from vision_detection.yolo_utils import YoloDetector

# Class names that count as an "abnormal situation" when detected around the AMR.
DEFAULT_ANOMALY_CLASSES = ['person']


class DetectAmbientNode(Node):
    def __init__(self):
        super().__init__('detect_ambient_node')

        self.declare_parameter('model_path', 'yolov8n.pt')
        self.declare_parameter('conf_threshold', 0.5)
        self.declare_parameter('amr_cam_topics', ['/robot3/okad/preview/image_raw',
                                                    '/robot8/okad/preview/image_raw'])
        self.declare_parameter('anomaly_classes', DEFAULT_ANOMALY_CLASSES)
        self.declare_parameter('normal_process_every_n', 1)
        self.declare_parameter('throttled_process_every_n', 10)

        model_path = self.get_parameter('model_path').value
        conf_threshold = self.get_parameter('conf_threshold').value
        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.anomaly_classes = set(self.get_parameter('anomaly_classes').value)
        self.normal_process_every_n = self.get_parameter('normal_process_every_n').value
        self.throttled_process_every_n = self.get_parameter('throttled_process_every_n').value

        self.detector = YoloDetector(model_path, conf_threshold)

        # detect_station_node takes over the AMR cams while a gate check is in
        # progress, so this node throttles itself down instead of unsubscribing.
        self.process_every_n = self.normal_process_every_n
        self._frame_counters = {}

        self._subs = []
        self._image_pubs = {}
        self._anomaly_pubs = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_id_from_topic(topic)
            self._frame_counters[topic] = 0
            self._image_pubs[topic] = self.create_publisher(
                Image, f'/detection/{robot_id}_cam/detection_image', 10)
            self._anomaly_pubs[topic] = self.create_publisher(
                Bool, f'/detection/{robot_id}_cam/anomaly_detected', 10)
            self._subs.append(self.create_subscription(
                Image, topic, self._make_image_callback(topic), 10))

        self.set_throttle_srv = self.create_service(
            SetBool, '~/set_throttle', self._set_throttle_callback)

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
