"""여러 노드가 공유하는 유틸.

yaml 기반 파라미터 선언 헬퍼(declare_parameters_from_yaml), CamState.msg 규격에
고정된 프로토콜 상수(STATE_BY_CLASS, CAMERA_ID_BY_NAME), detect_cctv_node/
detect_ambient_node가 공통으로 쓰는 WBF 앙상블 유틸(weighted_boxes_fusion,
draw_detections)을 담는다.
"""

from pathlib import Path

import cv2
import numpy as np
import yaml
from ament_index_python.packages import get_package_share_directory
from rcl_interfaces.msg import ParameterDescriptor


def declare_parameters_from_yaml(node, section_name):
    """config/params.yaml의 section_name(ros__parameters) 값들을 기본값으로 declare_parameter를 반복 호출.

    launch나 --ros-args --params-file로 넘어온 오버라이드는 그대로 우선 적용된다 -
    이 함수는 그게 없을 때(예: ros2 run 단독 실행) 쓰일 기본값을 yaml에서 가져올 뿐이다.
    """
    params_path = Path(get_package_share_directory('vision_detection')) / 'config' / 'params.yaml'
    all_params = yaml.safe_load(params_path.read_text())
    defaults = all_params.get(section_name, {}).get('ros__parameters', {})
    for name, value in defaults.items():
        if isinstance(value, list) and not value:
            # 빈 리스트는 원소가 없어 타입 추론이 불가 → NOT_SET으로 선언되어 get 시
            # ParameterUninitializedException이 발생한다. 동적 타이핑을 허용해 회피.
            node.declare_parameter(name, value, ParameterDescriptor(dynamic_typing=True))
        else:
            node.declare_parameter(name, value)


# CamState.msg 프로토콜 상수 - detect_cctv_node/detect_ambient_node가 공유한다.
# CamState.msg 필드 주석(state, camera_id)과 반드시 일치해야 하고 백엔드도 이
# 매핑을 그대로 전제하므로, yaml 파라미터가 아니라 코드 상수로 고정해서 노드별로
# 따로 정의하지 않고 여기서만 관리한다.

# CamState.msg의 state 값
STATE_BY_CLASS = {'fire': 0, 'smoke': 1, 'coolant': 2}

# CamState.msg의 camera_id 값
CAMERA_ID_BY_NAME = {
    'cctv1': 0,
    'cctv2': 1,
    'robot3': 2,
    'robot8': 3,
}

# state 인덱스 -> 클래스 이름 (WBF 결과의 정수 클래스 인덱스를 이름으로 되돌릴 때 사용)
CLASS_NAME_BY_STATE = {value: name for name, value in STATE_BY_CLASS.items()}

CLASS_COLORS = {'fire': (0, 0, 255), 'smoke': (0, 255, 255), 'coolant': (255, 128, 0)}


def _box_iou(a, b):
    """박스 두 개(xyxy) 간 IoU(교집합/합집합 비율)를 계산."""
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
    여러 모델이 동의한 박스일수록 합쳐진 confidence가 높아지는 게 단순 NMS와 다른 점.
    detect_cctv_node/detect_ambient_node가 동일 로직을 공유한다."""
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
            if _box_iou(all_boxes[idx], all_boxes[jdx]) >= iou_thr:
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


def draw_detections(frame, detections):
    """WBF 병합 결과(Result 객체가 아님)를 프레임 위에 클래스별 색상 박스+라벨로 직접 그린다.
    detections: [{class_name, confidence, xyxy}] 리스트."""
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
