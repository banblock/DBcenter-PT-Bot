"""AMR 주변 이상상황(화재/연기/냉각수 누출)을 상시 감지하는 노드.

AMR 캠 이미지를 항상 구독하며 서로 다른 아키텍처의 YOLO 3개를 WBF(Weighted
Boxes Fusion)로 앙상블 추론해 CamState를 발행한다.
"""

from collections import deque
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from ultralytics import YOLO
from cv_bridge import CvBridge

from ament_index_python.packages import get_package_share_directory

from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Bool
from std_srvs.srv import SetBool
from patrol_interfaces.msg import CamState

from vision_detection.node_utils import (
    CAMERA_ID_BY_NAME,
    CLASS_COLORS,
    STATE_BY_CLASS,
    declare_parameters_from_yaml,
    draw_detections,
    weighted_boxes_fusion,
)

# 진입(켜짐)은 최근 HIT_WINDOW프레임 중 HIT_REQUIRED장 이상 검출되면 확정한다(연속일
# 필요는 없음 - 중간에 한두 번 놓쳐도 누적이 안 사라짐). 연속 요구 방식보다 실제
# 탐지까지 걸리는 시간이 짧고 확실하다. 대신 산발적인(비연속) 노이즈 방어력은 연속
# 방식보다 약하지만, 확인된 노이즈 패턴이 1프레임짜리 순간 플래시뿐이라 이 방식으로도
# 충분히 걸러진다.
HIT_WINDOW = 5
HIT_REQUIRED = 3

# 해제(꺼짐)는 연속 이 프레임 수만큼 미검출이어야 확정한다. 너무 길면(=꺼짐 판정이
# 너무 느리면) AMR이 다른 지점으로 이동한 뒤에도 active 상태가 오래 남아서, 그 사이
# 실제로 다른 위치에서 발생한 새 이상상황을 "이미 진행 중"으로 착각해 재발행을
# 놓칠 위험이 커진다.
OFF_MISS_THRESHOLD = 5

class DetectAmbientNode(Node):
    """AMR 주변 이상 상황(화재/연기/냉각수 누출 등)을 상시 감지하는 노드.

    AMR 캠 이미지를 항상 구독하며 서로 다른 버전/아키텍처로 학습된 YOLO 3개를
    WBF(Weighted Boxes Fusion)로 앙상블해 추론하고, 결과 이미지와 이상상황 여부를
    발행한다. detect_station_node가 차단기 검사를 하는 동안에는 set_throttle
    서비스로 프레임 처리 비율을 낮춰 리소스를 station_node 쪽에 양보한다.
    """

    def __init__(self):
        """파라미터 로드, 모델 3개 로드+워밍업, 카메라별 구독/퍼블리셔, 서비스/토픽 등록."""
        super().__init__('detect_ambient_node')

        self.task_started = False

        declare_parameters_from_yaml(self, 'detect_ambient_node')

        model_paths = self.get_parameter('model_paths').value
        model_tta = self.get_parameter('model_tta').value
        if len(model_paths) != len(model_tta):
            raise ValueError('model_paths와 model_tta의 항목 수가 같아야 합니다.')

        self.conf_threshold = self.get_parameter('conf_threshold').value
        self.wbf_merge_iou = self.get_parameter('wbf_merge_iou').value
        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.anomaly_classes = set(self.get_parameter('anomaly_classes').value)
        self.normal_process_every_n = self.get_parameter('normal_process_every_n').value
        self.throttled_process_every_n = self.get_parameter('throttled_process_every_n').value
        self.jpeg_quality = self.get_parameter('jpeg_quality').value

        self.bridge = CvBridge()
        self.models = [YOLO(self._resolve_model_path(p)) for p in model_paths]
        self.model_tta = model_tta
        self._warmup_models()

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
        # 진입은 최근 HIT_WINDOW프레임 중 HIT_REQUIRED장 이상 검출되면, 해제는 연속
        # 미검출 카운트가 OFF_MISS_THRESHOLD에 닿아야 반영한다.
        self._active_classes = {}
        self._hit_windows = {}
        self._miss_counts = {}
        self._camera_id_by_topic = {}

        self._subs = []
        self._image_pubs = {}
        for topic in self.amr_cam_topics:
            robot_id = self._robot_id_from_topic(topic)
            self._frame_counters[topic] = 0
            self._active_classes[topic] = set()
            self._hit_windows[topic] = {
                class_name: deque(maxlen=HIT_WINDOW) for class_name in self.anomaly_classes
            }
            self._miss_counts[topic] = {class_name: 0 for class_name in self.anomaly_classes}
            self._camera_id_by_topic[topic] = CAMERA_ID_BY_NAME.get(robot_id)
            self._image_pubs[topic] = self.create_publisher(
                CompressedImage, f'/detection/{robot_id}_cam/detection_image', image_qos)
            self._subs.append(self.create_subscription(
                Image, topic, self._make_image_callback(topic), image_qos))

        # 이상상황 이벤트 토픽 (fire/smoke/coolant 새로 인식 시 발행)
        self.cam_state_pub = self.create_publisher(CamState, '/detection/cam_state', 10)

        # detect_main_node가 차단기 검사 전/후에 호출해 쓰로틀을 켜고 끄는 서비스
        self.set_throttle_srv = self.create_service(
            SetBool, '/detection/set_throttle', self._set_throttle_callback)

        # detect_cctv_node와 동일하게 /ui/start(True=시작/False=정지)로 감지 on/off
        self.start_subscription = self.create_subscription(
            Bool, '/ui/start', self._start_callback, 1)

    def _start_callback(self, message: Bool) -> None:
        """/ui/start 수신 시 task_started를 갱신(True=이미지 콜백 처리 시작, False=중지)."""
        if message.data == self.task_started:
            return
        self.task_started = message.data

    @staticmethod
    def _resolve_model_path(configured_path: str) -> str:
        """상대경로면 패키지 share/models 디렉토리 기준으로, 절대경로면 그대로 실제 모델 파일 위치를 반환."""
        model_path = Path(configured_path).expanduser()
        if model_path.is_absolute():
            resolved_path = model_path
        else:
            resolved_path = Path(get_package_share_directory('vision_detection')) / 'models' / model_path.name \
                if '/' not in configured_path else Path(get_package_share_directory('vision_detection')) / model_path
        if not resolved_path.is_file():
            raise FileNotFoundError(f'YOLO 모델 파일을 찾을 수 없습니다: {resolved_path}')
        return str(resolved_path)

    def _warmup_models(self, imgsz=640):
        """더미 이미지로 앙상블 모델 전부를 한 번씩 미리 추론해서 CUDA 초기화 비용을
        노드 시작 시점으로 옮긴다 - 안 그러면 첫 실제 추론이 몇 초씩 늦어질 수 있다."""
        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        for model, use_tta in zip(self.models, self.model_tta):
            model.predict(dummy, conf=self.conf_threshold, augment=use_tta, verbose=False)


    @staticmethod
    def _robot_id_from_topic(topic):
        """토픽 문자열에서 로봇 id를 뽑는다. e.g. '/robot3/oakd/rgb/preview/image_raw' -> 'robot3'"""
        return topic.strip('/').split('/')[0]

    def _set_throttle_callback(self, request, response):
        """main_node가 station 검사 전/후에 호출: 프레임 처리 주기를 낮췄다/복구한다."""
        if request.data:
            self.process_every_n = self.throttled_process_every_n
            response.message = 'ambient throttled down'
        else:
            self.process_every_n = self.normal_process_every_n
            response.message = 'ambient throttle restored'
        response.success = True
        return response

    def _infer_from_msg(self, image_msg):
        """sensor_msgs/Image를 받아 앙상블 3모델로 추론 후 WBF로 병합하고
        (박스가 그려진 이미지, 탐지 결과 목록)을 반환한다.

        detections는 {class_name, confidence, xyxy} 딕셔너리의 리스트.
        """
        cv_image = self.bridge.imgmsg_to_cv2(image_msg, desired_encoding='bgr8')
        h, w = cv_image.shape[:2]

        boxes_list, scores_list, labels_list = [], [], []
        names = None
        for model, use_tta in zip(self.models, self.model_tta):
            result = model.predict(cv_image, conf=self.conf_threshold, augment=use_tta, verbose=False)[0]
            names = result.names
            xyxy = result.boxes.xyxy.cpu().numpy()
            boxes_list.append((xyxy / np.array([w, h, w, h])).tolist())
            scores_list.append(result.boxes.conf.cpu().numpy().tolist())
            labels_list.append(result.boxes.cls.cpu().numpy().astype(int).tolist())

        fused_boxes, fused_scores, fused_labels = weighted_boxes_fusion(
            boxes_list, scores_list, labels_list, iou_thr=self.wbf_merge_iou)
        keep = fused_scores >= self.conf_threshold
        fused_boxes = (fused_boxes[keep] * np.array([w, h, w, h])) if len(fused_boxes) else fused_boxes
        fused_scores = fused_scores[keep]
        fused_labels = fused_labels[keep].astype(int)

        detections = []
        for box, score, cls_id in zip(fused_boxes, fused_scores, fused_labels):
            detections.append({
                'class_name': names[int(cls_id)],
                'confidence': float(score),
                'xyxy': [float(v) for v in box],
            })

        # 화면에도 추적 대상(anomaly_classes)만 표시한다 - 제외한 smoke 등은 박스도 안 그린다.
        drawable = [d for d in detections if d['class_name'] in self.anomaly_classes]
        annotated_image = draw_detections(cv_image, drawable)
        return annotated_image, detections

    def _to_compressed_image_msg(self, cv_image, frame_id=''):
        """JPEG로 압축해서 발행 - raw Image 대비 대역폭을 30~50배 줄인다."""
        ok, encoded = cv2.imencode('.jpg', cv_image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise RuntimeError('JPEG 압축 실패')
        msg = CompressedImage()
        msg.header.frame_id = frame_id
        msg.format = 'jpeg'
        msg.data = encoded.tobytes()
        return msg

    def _make_image_callback(self, topic):
        """토픽별 이미지 콜백 클로저를 생성: 추론 → 이상상황 이미지 발행 → CamState 진입/해제 판단."""
        def callback(msg):
            if not self.task_started:
                return

            # process_every_n 프레임마다 한 번만 실제 추론을 수행 (쓰로틀 적용 시 N이 커짐)
            self._frame_counters[topic] += 1
            if self._frame_counters[topic] % self.process_every_n != 0:
                return

            annotated_image, detections = self._infer_from_msg(msg)
            detected_classes = {d['class_name'] for d in detections if d['class_name'] in self.anomaly_classes}

            # 이상상황이 감지되는 동안에는 매 프레임 이미지를 계속 보내고, 감지가 끝나면(빈 집합) 멈춘다
            if detected_classes:
                self._image_pubs[topic].publish(self._to_compressed_image_msg(annotated_image))

            # CamState: 진입은 최근 HIT_WINDOW프레임 중 HIT_REQUIRED장 이상 검출되면,
            # 해제는 연속 OFF_MISS_THRESHOLD프레임 미검출이어야 확정한다. 둘 다 모델이
            # 가끔 놓치거나(recall<100%) 순간 노이즈로 헛 잡는 것만으로 같은 상황이
            # 반복 발행되는 걸 막기 위함.
            camera_id = self._camera_id_by_topic[topic]
            active_classes = self._active_classes[topic]
            hit_windows = self._hit_windows[topic]
            miss_counts = self._miss_counts[topic]
            for class_name in self.anomaly_classes:
                detected = class_name in detected_classes

                if class_name in active_classes:
                    if detected:
                        miss_counts[class_name] = 0
                    else:
                        miss_counts[class_name] += 1
                        if miss_counts[class_name] >= OFF_MISS_THRESHOLD:
                            active_classes.discard(class_name)
                            miss_counts[class_name] = 0
                            hit_windows[class_name].clear()
                else:
                    window = hit_windows[class_name]
                    window.append(detected)
                    if sum(window) >= HIT_REQUIRED:
                        window.clear()
                        miss_counts[class_name] = 0
                        active_classes.add(class_name)
                        # AMR 캠은 CCTV처럼 고정 설치가 아니라 호모그래피(픽셀->맵) 캘리브레이션이
                        # 없어서 bbox를 실어 보내도 백엔드가 맵 좌표로 못 바꾼다. 그래서 항상
                        # 무효 bbox(-1)+confidence 0으로 보낸다.
                        self.cam_state_pub.publish(CamState(
                            camera_id=camera_id, state=STATE_BY_CLASS[class_name],
                            bbox_x1=-1.0, bbox_y1=-1.0, bbox_x2=-1.0, bbox_y2=-1.0, confidence=0.0))

        return callback

def main(args=None):
    rclpy.init(args=args)
    node = DetectAmbientNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()