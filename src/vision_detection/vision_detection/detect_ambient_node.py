import os
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Bool
from std_srvs.srv import SetBool
from ultralytics import YOLO

from patrol_interfaces.msg import CamState

# AMR 주변에서 감지되면 "이상 상황"으로 취급할 클래스 이름들
DEFAULT_ANOMALY_CLASSES = ['fire', 'smoke', 'coolant']
# 신뢰도 값
CONF_THRESHOLD = 0.25

# 3모델 WBF(Weighted Boxes Fusion) 앙상블 구성. 서로 다른 학습 버전(v2/v3) +
# 아키텍처(yolov8n/yolo26n/yolo11n)를 섞어야 앙상블 다양성이 생긴다는 걸 실측으로
# 확인했음(CCTV에서는 같은 버전 데이터로 학습한 n급 3개를 섞었더니 상관성이 너무
# 높아서 오히려 단일 모델보다 나빴는데, AMR은 버전을 섞으니 F1이 단일 최고 모델
# 대비 크게 개선됨: 0.907 -> 0.956, 오탐 6개->0개).
# AMR 카메라(oakd rgb preview)는 실측상 ~10Hz라 3모델 순차 추론(로봇 2대분 ~60ms)도
# 프레임 주기(100ms) 안에 충분히 들어와서 실시간성 문제 없음(CCTV처럼 30fps를
# 맞춰야 하는 상황이 아님).
ENSEMBLE_MODEL_FILES = ['ambient_yolov8n_v2.pt', 'ambient_yolo26n_v2.pt', 'ambient_yolo11n_v3.pt']
ENSEMBLE_MODEL_TTA = [True, False, True]  # yolo26 계열은 augment=True 미지원(자동 revert)
WBF_MERGE_IOU = 0.5

# CamState.msg의 state 값 (detect_cctv_node와 동일한 규칙)
STATE_BY_CLASS = {'fire': 0, 'smoke': 1, 'coolant': 2}
# CamState.msg의 camera_id 값 (0/1은 cctv1/cctv2가 사용, AMR 캠은 2번부터)
CAMERA_ID_BY_ROBOT = {'robot3': 2, 'robot8': 3}
# 감지 해제 판단용 윈도우 크기. 감지(켜짐)는 1프레임만 봐도 즉시 반응하지만,
# 해제(꺼짐)는 최근 이 프레임 수 중 과반이 미검출이어야 확정한다 (detect_cctv_node와 동일한 방식).
EVENT_WINDOW_SIZE = 3

CLASS_COLORS = {'fire': (0, 0, 255), 'smoke': (0, 255, 255), 'coolant': (255, 128, 0)}


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def weighted_boxes_fusion(boxes_list, scores_list, labels_list, iou_thr=0.5):
    """모델별 예측을 confidence 가중 평균으로 병합(외부 ensemble-boxes 라이브러리 없이
    직접 구현 - 검증 환경에서 numba/coverage 패키지 충돌로 그 라이브러리를 못 씀).
    여러 모델이 동의한 박스일수록 합쳐진 confidence가 높아지는 게 단순 NMS와 다른 점."""
    all_boxes, all_scores, all_labels, all_model_idx = [], [], [], []
    for m_idx, (boxes, scores, labels) in enumerate(zip(boxes_list, scores_list, labels_list)):
        for b, s, l in zip(boxes, scores, labels):
            all_boxes.append(b)
            all_scores.append(s)
            all_labels.append(l)
            all_model_idx.append(m_idx)

    if not all_boxes:
        return np.zeros((0, 4)), np.zeros(0), np.zeros(0)

    all_boxes = np.array(all_boxes)
    all_scores = np.array(all_scores)
    all_labels = np.array(all_labels)
    n_models = len(boxes_list)

    order = np.argsort(-all_scores)
    used = np.zeros(len(order), dtype=bool)
    fused_boxes, fused_scores, fused_labels = [], [], []

    for idx in order:
        if used[idx]:
            continue
        cluster = [idx]
        used[idx] = True
        for jdx in order:
            if used[jdx] or all_labels[jdx] != all_labels[idx]:
                continue
            if _iou(all_boxes[idx], all_boxes[jdx]) >= iou_thr:
                cluster.append(jdx)
                used[jdx] = True
        c_boxes = all_boxes[cluster]
        c_scores = all_scores[cluster]
        w = c_scores / c_scores.sum()
        fused_box = (c_boxes * w[:, None]).sum(axis=0)
        # 합의한 모델 수 비례로 confidence 보정(여러 모델이 동의할수록 신뢰도 상승)
        avg_score = c_scores.mean() * (len({all_model_idx[c] for c in cluster}) / n_models)
        fused_boxes.append(fused_box)
        fused_scores.append(avg_score)
        fused_labels.append(all_labels[idx])

    return np.array(fused_boxes), np.array(fused_scores), np.array(fused_labels)


class DetectAmbientNode(Node):
    """AMR 주변 이상 상황(화재/연기/냉각수 누출 등)을 상시 감지하는 노드.

    AMR 캠 이미지를 항상 구독하며 서로 다른 버전/아키텍처로 학습된 YOLO 3개를
    WBF(Weighted Boxes Fusion)로 앙상블해 추론하고, 결과 이미지와 이상상황 여부를
    발행한다. detect_station_node가 차단기 검사를 하는 동안에는 set_throttle
    서비스로 프레임 처리 비율을 낮춰 리소스를 station_node 쪽에 양보한다.
    """

    def __init__(self):
        super().__init__('detect_ambient_node')

        self.declare_parameter('model_paths', ENSEMBLE_MODEL_FILES)
        self.declare_parameter('model_tta', ENSEMBLE_MODEL_TTA)
        self.declare_parameter('amr_cam_topics', ['/robot3/oakd/rgb/preview/image_raw',
                                                    '/robot8/oakd/rgb/preview/image_raw'])
        self.declare_parameter('anomaly_classes', DEFAULT_ANOMALY_CLASSES)
        #평상시 1장마다 1번씩 처리, 쓰로틀 시 10장마다 1번씩 처리
        self.declare_parameter('normal_process_every_n', 1)
        self.declare_parameter('throttled_process_every_n', 10)
        self.declare_parameter('jpeg_quality', 80)

        model_paths = list(self.get_parameter('model_paths').value)
        model_tta = list(self.get_parameter('model_tta').value)
        if len(model_paths) != len(model_tta):
            raise ValueError('model_paths와 model_tta의 항목 수가 같아야 합니다.')

        self.conf_threshold = CONF_THRESHOLD
        self.amr_cam_topics = self.get_parameter('amr_cam_topics').value
        self.anomaly_classes = set(self.get_parameter('anomaly_classes').value)
        self.normal_process_every_n = self.get_parameter('normal_process_every_n').value
        self.throttled_process_every_n = self.get_parameter('throttled_process_every_n').value
        self.jpeg_quality = int(self.get_parameter('jpeg_quality').value)

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
                CompressedImage, f'/detection/{robot_id}_cam/detection_image', image_qos)
            self._anomaly_pubs[topic] = self.create_publisher(
                Bool, f'/detection/{robot_id}_cam/anomaly_detected', 10)
            self._subs.append(self.create_subscription(
                Image, topic, self._make_image_callback(topic), image_qos))

        # detect_cctv_node와 동일한 이상상황 이벤트 토픽 (fire/smoke/coolant 새로 인식 시 발행)
        self.cam_state_pub = self.create_publisher(CamState, '/detection/cam_state', 10)

        # detect_main_node가 차단기 검사 전/후에 호출해 쓰로틀을 켜고 끄는 서비스
        self.set_throttle_srv = self.create_service(
            SetBool, '/detection/set_throttle', self._set_throttle_callback)

        self.get_logger().info(f'detect_ambient_node ready (ensemble={len(self.models)}개 모델)')

    @staticmethod
    def _resolve_model_path(configured_path: str) -> str:
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
        """더미 이미지로 앙상블 모델 전부를 한 번씩 미리 추론해서 CUDA 초기화 비용
        (첫 실제 추론이 ~1.3초까지 걸리는 원인, detect_cctv_node에서 실측 확인됨)을
        노드 시작 시점으로 옮긴다."""
        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        started = time.perf_counter()
        for model, use_tta in zip(self.models, self.model_tta):
            model.predict(dummy, conf=self.conf_threshold, augment=use_tta, verbose=False)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.get_logger().info(f'모델 워밍업 완료 ({elapsed_ms:.0f}ms, {len(self.models)}개)')

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
            boxes_list, scores_list, labels_list, iou_thr=WBF_MERGE_IOU)
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

        annotated_image = self._draw_detections(cv_image, detections)
        return annotated_image, detections

    @staticmethod
    def _draw_detections(frame, detections):
        annotated = frame.copy()
        height, width = annotated.shape[:2]
        for det in detections:
            x1, y1, x2, y2 = det['xyxy']
            x1 = max(0, min(int(x1), width - 1))
            y1 = max(0, min(int(y1), height - 1))
            x2 = max(0, min(int(x2), width - 1))
            y2 = max(0, min(int(y2), height - 1))
            color = CLASS_COLORS.get(det['class_name'], (0, 255, 0))
            label = f"{det['class_name']} {det['confidence']:.2f}"
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(annotated, label, (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return annotated

    def _to_compressed_image_msg(self, cv_image, frame_id=''):
        """JPEG로 압축해서 발행 - raw Image 대비 대역폭을 30~50배 줄인다.

        AMR은 WiFi로 붙어있고 nav2/lidar 등 로봇 제어 트래픽과 대역폭을 같이 쓰는데,
        하필 실제 이상상황이 감지되는 동안에만 매 프레임 이미지를 계속 보내므로
        raw로 두면 정작 중요한 순간에 대역폭을 잡아먹는다.
        """
        ok, encoded = cv2.imencode('.jpg', cv_image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise RuntimeError('JPEG 압축 실패')
        msg = CompressedImage()
        msg.header.frame_id = frame_id
        msg.format = 'jpeg'
        msg.data = encoded.tobytes()
        return msg

    def _make_image_callback(self, topic):
        def callback(msg):
            # process_every_n 프레임마다 한 번만 실제 추론을 수행 (쓰로틀 적용 시 N이 커짐)
            self._frame_counters[topic] += 1
            if self._frame_counters[topic] % self.process_every_n != 0:
                return

            annotated_image, detections = self._infer_from_msg(msg)
            detected_classes = {d['class_name'] for d in detections if d['class_name'] in self.anomaly_classes}
            self._anomaly_pubs[topic].publish(Bool(data=bool(detected_classes)))

            # 이상상황이 감지되는 동안에는 매 프레임 이미지를 계속 보내고, 감지가 끝나면(빈 집합) 멈춘다
            if detected_classes:
                self._image_pubs[topic].publish(self._to_compressed_image_msg(annotated_image))

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
