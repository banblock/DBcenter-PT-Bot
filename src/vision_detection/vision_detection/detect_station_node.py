import threading

import rclpy
from cv_bridge import CvBridge
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from sensor_msgs.msg import Image

from vision_detection.gate_color_detector import GateColorDetector
from vision_detection_interfaces.srv import InspectGate


class DetectStationNode(Node):
    def __init__(self):
        super().__init__('detect_station_node')

        self.declare_parameter('amr_cam_topics', ['/robot3/okad/preview/image_raw',
                                                    '/robot8/okad/preview/image_raw'])
        self.declare_parameter('fresh_frame_timeout_sec', 2.0)
        self.declare_parameter('hough_dp', 1.2)
        self.declare_parameter('hough_min_dist', 50.0)
        self.declare_parameter('hough_param1', 100.0)
        self.declare_parameter('hough_param2', 30.0)
        self.declare_parameter('hough_min_radius', 10)
        self.declare_parameter('hough_max_radius', 100)
        # red wraps around hue 0/180 in OpenCV HSV, so "closed" needs two ranges
        self.declare_parameter('closed_hsv_lower1', [0, 70, 50])
        self.declare_parameter('closed_hsv_upper1', [10, 255, 255])
        self.declare_parameter('closed_hsv_lower2', [170, 70, 50])
        self.declare_parameter('closed_hsv_upper2', [180, 255, 255])
        self.declare_parameter('open_hsv_lower', [40, 70, 50])
        self.declare_parameter('open_hsv_upper', [80, 255, 255])
        self.declare_parameter('min_color_ratio', 0.3)

        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.fresh_frame_timeout_sec = self.get_parameter('fresh_frame_timeout_sec').value

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

        # Only used while inspect_gate is being served; kept idle otherwise so
        # detect_ambient_node stays the primary consumer of these AMR cams.
        self.active = False
        self._latest_frames = {}
        self._frame_lock = threading.Lock()
        self._new_frame_event = threading.Event()

        self._image_pubs = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_id_from_topic(topic)
            self._image_pubs[topic] = self.create_publisher(
                Image, f'/detection/{robot_id}_cam/detection_image', 10)
            self.create_subscription(
                Image, topic, self._make_image_callback(topic), 10)

        self.inspect_gate_srv = self.create_service(
            InspectGate, '~/inspect_gate', self._inspect_gate_callback,
            callback_group=ReentrantCallbackGroup())

        self.get_logger().info('detect_station_node ready')

    @staticmethod
    def _robot_id_from_topic(topic):
        return topic.strip('/').split('/')[0]

    def _make_image_callback(self, topic):
        def callback(msg):
            with self._frame_lock:
                self._latest_frames[topic] = msg
            if self.active:
                self._new_frame_event.set()

        return callback

    def _inspect_gate_callback(self, request, response):
        self.active = True
        self._new_frame_event.clear()
        try:
            if not self._new_frame_event.wait(timeout=self.fresh_frame_timeout_sec):
                response.success = False
                response.gate_closed = False
                response.message = 'timed out waiting for a fresh AMR camera frame'
                return response

            with self._frame_lock:
                frames = dict(self._latest_frames)

            results = []
            for topic, msg in frames.items():
                cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
                annotated_image, gate_closed = self.detector.detect(cv_image)
                self._image_pubs[topic].publish(self.bridge.cv2_to_imgmsg(annotated_image, encoding='bgr8'))
                if gate_closed is not None:
                    results.append(gate_closed)

            if not results:
                response.success = False
                response.gate_closed = False
                response.message = 'no gate indicator circle detected on any camera'
                return response

            response.success = True
            response.gate_closed = any(results)
            response.message = 'ok'
            return response
        finally:
            self.active = False


def main(args=None):
    rclpy.init(args=args)
    node = DetectStationNode()
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
