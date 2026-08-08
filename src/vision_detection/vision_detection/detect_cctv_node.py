#!/usr/bin/env python3

from __future__ import annotations

import glob
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cv2
import rclpy
from ament_index_python.packages import get_package_share_directory
from patrol_interfaces.msg import CamState
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool
from ultralytics import YOLO


@dataclass(frozen=True)
class DetectionBox:
    """UI 프레임에 다시 그릴 최신 YOLO 박스 한 개."""

    x1: int
    y1: int
    x2: int
    y2: int
    class_name: str
    confidence: float


@dataclass
class CameraContext:
    """카메라 한 대의 최신 프레임, 추론 결과 및 ROS 상태."""

    camera_id: str
    camera_number: int
    camera_device: Union[int, str]
    capture: cv2.VideoCapture
    image_publisher: Any
    last_status: Dict[str, bool]
    detection_windows: Dict[str, "deque[bool]"]
    frame_lock: threading.Lock = field(default_factory=threading.Lock)
    result_lock: threading.Lock = field(default_factory=threading.Lock)
    latest_frame: Optional[Any] = None
    latest_capture_ns: int = 0
    frame_sequence: int = 0
    last_inferred_sequence: int = -1
    last_published_sequence: int = -1
    latest_detections: List[DetectionBox] = field(default_factory=list)
    capture_thread: Optional[threading.Thread] = None
    capture_count: int = 0
    last_warning_time: float = 0.0


class DetectCctvNode(Node):
    """최신 프레임 캡처, YOLO 추론, UI 발행을 분리한 CCTV 노드."""

    STATUS_STATES = {
        "fire": 0,
        "smoke": 1,
        "coolant": 2,
    }
    # 최근 N프레임 중 과반 이상 감지되면 확정 (켜짐/꺼짐 모두 동일 기준)
    DETECTION_WINDOW_SIZE = 3

    def __init__(self) -> None:
        super().__init__("detect_cctv_node")

        # 문자열 배열로 선언하면 카메라 인덱스("0")와 /dev 경로를 모두 사용할 수 있다.
        # 비워두면(기본값) 연결된 웹캠을 자동 탐지한다.
        self.declare_parameter("camera_devices", [])
        self.declare_parameter("camera_ids", ["cctv1", "cctv2"])
        # 노트북/PC 내장 카메라가 보통 낮은 인덱스를 차지하므로, 자동 탐지 시 이보다
        # 낮은 인덱스의 /dev/videoN은 후보에서 제외한다.
        self.declare_parameter("min_camera_index", 2)
        self.declare_parameter("model_path", "models/cctv_best.pt")
        self.declare_parameter("confidence", 0.5)
        self.declare_parameter("inference_hz", 15.0)
        # 기존 publish_hz 이름을 유지하며 UI 압축 영상 발행 목표 주기로 사용한다.
        self.declare_parameter("publish_hz", 30.0)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("stats_interval_sec", 5.0)
        self.declare_parameter("device", "")
        self.declare_parameter("image_width", 1280)
        self.declare_parameter("image_height", 960)
        self.declare_parameter("camera_fps", 30.0)
        self.declare_parameter("inference_size", 640)

        camera_ids = list(self.get_parameter("camera_ids").value)
        self.min_camera_index = int(self.get_parameter("min_camera_index").value)

        configured_devices = [
            str(value) for value in self.get_parameter("camera_devices").value
        ]
        camera_devices = configured_devices or self._discover_camera_devices(len(camera_ids))

        configured_model_path = str(self.get_parameter("model_path").value)
        self.model_path = self._resolve_model_path(configured_model_path)
        self.confidence = float(self.get_parameter("confidence").value)
        self.inference_hz = float(self.get_parameter("inference_hz").value)
        self.publish_hz = float(self.get_parameter("publish_hz").value)
        self.jpeg_quality = int(self.get_parameter("jpeg_quality").value)
        self.stats_interval_sec = float(
            self.get_parameter("stats_interval_sec").value
        )
        self.device = str(self.get_parameter("device").value)
        self.image_width = int(self.get_parameter("image_width").value)
        self.image_height = int(self.get_parameter("image_height").value)
        self.camera_fps = float(self.get_parameter("camera_fps").value)
        self.inference_size = int(self.get_parameter("inference_size").value)

        self._validate_camera_parameters(camera_devices, camera_ids)
        if self.inference_hz <= 0.0 or self.publish_hz <= 0.0:
            raise ValueError("inference_hz와 publish_hz는 0보다 커야 합니다.")
        if self.stats_interval_sec <= 0.0:
            raise ValueError("stats_interval_sec는 0보다 커야 합니다.")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality는 1부터 100 사이여야 합니다.")

        # 두 카메라가 동일한 학습 모델을 공유하여 메모리 사용량을 줄인다.
        self.model = YOLO(self.model_path)
        self.cameras: List[CameraContext] = []
        self.task_started = False
        self.shutdown_event = threading.Event()
        self._cameras_released = False

        # 추론이 진행 중이어도 UI 타이머가 다른 실행 스레드에서 동작하도록 그룹을 분리한다.
        self.start_callback_group = MutuallyExclusiveCallbackGroup()
        self.inference_callback_group = MutuallyExclusiveCallbackGroup()
        self.ui_callback_group = MutuallyExclusiveCallbackGroup()
        self.stats_callback_group = MutuallyExclusiveCallbackGroup()

        self.inference_batch_count = 0
        self.inference_total_ms = 0.0
        self.ui_publish_counts: Dict[str, int] = {}
        self.compressed_byte_counts: Dict[str, int] = {}
        self.last_stats_time = time.monotonic()
        self.last_inference_batch_count = 0
        self.last_inference_total_ms = 0.0
        self.last_capture_counts: Dict[str, int] = {}
        self.last_ui_publish_counts: Dict[str, int] = {}
        self.last_compressed_byte_counts: Dict[str, int] = {}

        self.start_subscription = self.create_subscription(
            Bool,
            "/ui/start",
            self._start_callback,
            1,
            callback_group=self.start_callback_group,
        )

        self.status_publisher = self.create_publisher(
            CamState,
            "/detection/cam_state",
            10,
        )

        try:
            for camera_number, (camera_device, camera_id) in enumerate(
                zip(camera_devices, camera_ids), start=0
            ):
                camera = self._create_camera_context(
                    camera_device, camera_id, camera_number
                )
                self.cameras.append(camera)
                self.ui_publish_counts[camera_id] = 0
                self.compressed_byte_counts[camera_id] = 0
                self.last_capture_counts[camera_id] = 0
                self.last_ui_publish_counts[camera_id] = 0
                self.last_compressed_byte_counts[camera_id] = 0
            self._start_capture_threads()
        except Exception:
            self._release_cameras()
            raise

        self.inference_timer = self.create_timer(
            1.0 / self.inference_hz,
            self.process_inference,
            callback_group=self.inference_callback_group,
        )
        self.ui_timer = self.create_timer(
            1.0 / self.publish_hz,
            self.publish_ui_frames,
            callback_group=self.ui_callback_group,
        )
        self.stats_timer = self.create_timer(
            self.stats_interval_sec,
            self.report_performance,
            callback_group=self.stats_callback_group,
        )

        camera_summary = ", ".join(
            f"{camera.camera_id}={camera.camera_device}"
            for camera in self.cameras
        )
        self.get_logger().info(
            f"다중 CCTV 준비 완료 | cameras=[{camera_summary}] | "
            f"model={self.model_path} | conf={self.confidence:.2f} | "
            f"inference={self.inference_hz:.1f}Hz | "
            f"ui={self.publish_hz:.1f}Hz | "
            f"jpeg_quality={self.jpeg_quality} | "
            "/ui/start 대기 중"
        )

    @staticmethod
    def _validate_camera_parameters(
        camera_devices: List[str], camera_ids: List[str]
    ) -> None:
        if not camera_devices:
            raise ValueError("camera_devices에는 하나 이상의 장치가 필요합니다.")
        if len(camera_devices) != len(camera_ids):
            raise ValueError(
                "camera_devices와 camera_ids의 항목 수가 같아야 합니다."
            )
        if any(not camera_id for camera_id in camera_ids):
            raise ValueError("camera_ids에는 빈 값을 사용할 수 없습니다.")
        if len(set(camera_ids)) != len(camera_ids):
            raise ValueError("camera_ids는 서로 달라야 합니다.")

    def _discover_camera_devices(self, needed_count: int) -> List[str]:
        """/dev/video*를 뒤져서 실제로 프레임을 읽을 수 있는 장치를 needed_count개 찾는다.

        내장 카메라는 보통 낮은 인덱스를 차지하므로 min_camera_index 미만은 후보에서 제외한다.
        """
        candidates = sorted(
            glob.glob("/dev/video*"),
            key=lambda path: int(re.sub(r"\D", "", path) or -1),
        )
        candidates = [
            path for path in candidates
            if int(re.sub(r"\D", "", path) or -1) >= self.min_camera_index
        ]

        discovered: List[str] = []
        for path in candidates:
            if self._camera_can_capture(path):
                discovered.append(path)
            if len(discovered) >= needed_count:
                break

        if len(discovered) < needed_count:
            raise RuntimeError(
                f"연결된 웹캠을 {needed_count}대 찾지 못했습니다 "
                f"(발견: {discovered}, 탐지 후보: {candidates}). "
                "camera_devices 파라미터로 직접 지정하거나 min_camera_index를 조정하세요."
            )

        self.get_logger().info(f"웹캠 자동 탐지: {discovered}")
        return discovered

    @staticmethod
    def _camera_can_capture(device_path: str) -> bool:
        """실제로 프레임을 읽을 수 있는 캡처 노드인지 확인한다.

        웹캠 하나가 캡처용/메타데이터용 두 /dev/video 노드를 함께 만드는 경우가 많아서,
        단순히 열리는지만으로는 실제 캡처 가능한 노드를 구분할 수 없다.
        """
        capture = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            return False
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        success, frame = capture.read()
        capture.release()
        return success and frame is not None

    @staticmethod
    def _resolve_model_path(configured_path: str) -> str:
        model_path = Path(configured_path).expanduser()

        if model_path.is_absolute():
            resolved_path = model_path
        else:
            resolved_path = (
                Path(get_package_share_directory("vision_detection")) / model_path
            )

        if not resolved_path.is_file():
            raise FileNotFoundError(
                f"YOLO 모델 파일을 찾을 수 없습니다: {resolved_path}"
            )

        return str(resolved_path)

    def _create_camera_context(
        self,
        camera_device: str,
        camera_id: str,
        camera_number: int,
    ) -> CameraContext:
        capture = self._open_camera(camera_device, camera_id)
        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        image_publisher = self.create_publisher(
            CompressedImage,
            f"/detection/{camera_id}/detection_image",
            image_qos,
        )
        return CameraContext(
            camera_id=camera_id,
            camera_number=camera_number,
            camera_device=self._convert_camera_device(camera_device),
            capture=capture,
            image_publisher=image_publisher,
            last_status=self._default_status(),
            detection_windows=self._new_detection_windows(),
        )

    def _new_detection_windows(self) -> Dict[str, "deque[bool]"]:
        return {
            event_name: deque(maxlen=self.DETECTION_WINDOW_SIZE)
            for event_name in self.STATUS_STATES
        }

    def _default_status(self) -> Dict[str, bool]:
        return dict.fromkeys(self.STATUS_STATES, False)

    def _publish_initial_status(self, camera: CameraContext) -> None:
        """구독자가 시작 시 정상 상태(False)를 받을 수 있게 발행한다."""
        with camera.result_lock:
            camera.last_status = self._default_status()
            camera.detection_windows = self._new_detection_windows()
            camera.latest_detections = []
        for event_name in self.STATUS_STATES:
            self._publish_status_message(camera, event_name)

    def _start_callback(self, message: Bool) -> None:
        if message.data == self.task_started:
            return

        # 상태 초기화 중 추론/UI 콜백이 중간 상태를 사용하지 않도록 잠시 정지한다.
        self.task_started = False
        for camera in self.cameras:
            self._publish_initial_status(camera)
        self.task_started = message.data

        action = "시작" if self.task_started else "중지"
        self.get_logger().info(
            f"/ui/start={self.task_started} 수신: CCTV 탐지 및 토픽 발행을 {action}합니다."
        )

    @staticmethod
    def _convert_camera_device(value: Union[int, str]) -> Union[int, str]:
        if isinstance(value, int):
            return value

        text = str(value).strip()
        if text.isdigit():
            return int(text)
        return text

    def _open_camera(
        self, camera_device: Union[int, str], camera_id: str
    ) -> cv2.VideoCapture:
        device = self._convert_camera_device(camera_device)
        capture = cv2.VideoCapture(device, cv2.CAP_V4L2)

        capture.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG"),
        )
        if self.image_width > 0:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.image_width)
        if self.image_height > 0:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.image_height)
        capture.set(cv2.CAP_PROP_FPS, self.camera_fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                f"{camera_id}: 카메라를 열 수 없습니다: {camera_device}. "
            )

        actual_fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        actual_format = "".join(
            chr((actual_fourcc >> (8 * index)) & 0xFF) for index in range(4)
        )
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        self.get_logger().info(
            f"{camera_id}: 카메라 설정 | device={device} | "
            f"format={actual_format} | size={actual_width}x{actual_height} | "
            f"fps={actual_fps:.1f}"
        )
        return capture

    def _start_capture_threads(self) -> None:
        """카메라마다 전용 스레드를 시작해 드라이버 버퍼를 계속 비운다."""
        for camera in self.cameras:
            camera.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(camera,),
                name=f"{camera.camera_id}_latest_frame_capture",
                daemon=True,
            )
            camera.capture_thread.start()

    def _capture_loop(self, camera: CameraContext) -> None:
        """프레임을 계속 읽고 이전 값 위에 최신 프레임만 덮어쓴다."""
        while not self.shutdown_event.is_set():
            success, frame = camera.capture.read()

            if not success or frame is None:
                now = time.monotonic()
                if now - camera.last_warning_time >= 5.0:
                    self.get_logger().warning(
                        f"{camera.camera_id}: 카메라 프레임을 읽지 못했습니다."
                    )
                    camera.last_warning_time = now
                continue

            captured_ns = time.monotonic_ns()
            with camera.frame_lock:
                # 큐에 추가하지 않고 최신 한 장만 교체하므로 과거 프레임이 누적되지 않는다.
                camera.latest_frame = frame
                camera.latest_capture_ns = captured_ns
                camera.frame_sequence += 1
                camera.capture_count += 1

    def _snapshot_latest_frame(
        self, camera: CameraContext
    ) -> Optional[tuple[Any, int, int]]:
        """다른 스레드가 교체해도 안전하도록 최신 프레임 복사본을 반환한다."""
        with camera.frame_lock:
            if camera.latest_frame is None:
                return None
            return (
                camera.latest_frame.copy(),
                camera.frame_sequence,
                camera.latest_capture_ns,
            )

    def process_inference(self) -> None:
        """최신 카메라 프레임 묶음만 YOLO로 배치 추론한다."""
        if not self.task_started:
            return

        cameras_with_frames: List[CameraContext] = []
        frames: List[Any] = []
        frame_sequences: List[int] = []

        any_new_frame = False
        for camera in self.cameras:
            snapshot = self._snapshot_latest_frame(camera)
            if snapshot is None:
                continue
            frame, sequence, _ = snapshot
            if sequence != camera.last_inferred_sequence:
                any_new_frame = True
            cameras_with_frames.append(camera)
            frames.append(frame)
            frame_sequences.append(sequence)

        # 카메라가 새 프레임을 만들지 않았다면 같은 화면을 다시 추론하지 않는다.
        if not frames or not any_new_frame:
            return

        try:
            predict_kwargs = {
                "source": frames,
                "conf": self.confidence,
                "imgsz": self.inference_size,
                "verbose": False,
            }
            if self.device:
                predict_kwargs["device"] = self.device

            inference_started = time.perf_counter()
            results = self.model.predict(**predict_kwargs)
            inference_ms = (time.perf_counter() - inference_started) * 1000.0
            if len(results) != len(cameras_with_frames):
                raise RuntimeError(
                    "YOLO 결과 수가 입력한 카메라 프레임 수와 다릅니다."
                )

            for camera, result, sequence in zip(
                cameras_with_frames, results, frame_sequences
            ):
                detected_status = self._extract_detected_status(result)
                detections = self._extract_detection_boxes(result)
                with camera.result_lock:
                    stable_status = self._apply_debounce(camera, detected_status)
                    self._publish_status(camera, stable_status)
                    camera.latest_detections = detections
                    camera.last_inferred_sequence = sequence

            self.inference_batch_count += 1
            self.inference_total_ms += inference_ms
        except Exception as exc:
            self.get_logger().error(f"YOLO 배치 처리 중 오류: {exc}")

    def publish_ui_frames(self) -> None:
        """YOLO 완료를 기다리지 않고 최신 프레임에 최근 박스를 그려 발행한다."""
        if not self.task_started:
            return

        for camera in self.cameras:
            snapshot = self._snapshot_latest_frame(camera)
            if snapshot is None:
                continue
            frame, sequence, _ = snapshot

            # 새 카메라 프레임이 없으면 동일 영상을 중복 발행하지 않는다.
            if sequence == camera.last_published_sequence:
                continue

            with camera.result_lock:
                detections = list(camera.latest_detections)

            annotated_frame = self._draw_detections(frame, detections)
            if self._publish_image(camera, annotated_frame):
                camera.last_published_sequence = sequence
                self.ui_publish_counts[camera.camera_id] += 1

    def _extract_detection_boxes(self, result: Any) -> List[DetectionBox]:
        """Ultralytics 결과를 UI 스레드에서 안전하게 쓸 일반 Python 값으로 복사한다."""
        detections: List[DetectionBox] = []
        if result.boxes is None:
            return detections

        coordinates = result.boxes.xyxy.tolist()
        confidences = result.boxes.conf.tolist()
        class_indices = result.boxes.cls.tolist()
        for xyxy, confidence, class_index in zip(
            coordinates, confidences, class_indices
        ):
            detections.append(
                DetectionBox(
                    x1=int(xyxy[0]),
                    y1=int(xyxy[1]),
                    x2=int(xyxy[2]),
                    y2=int(xyxy[3]),
                    class_name=self._class_name_from_index(
                        int(class_index), result.names
                    ),
                    confidence=float(confidence),
                )
            )
        return detections

    @staticmethod
    def _draw_detections(
        frame: Any, detections: List[DetectionBox]
    ) -> Any:
        """최신 원본 프레임 위에 가장 최근 추론 박스를 그린다."""
        colors = {
            "fire": (0, 0, 255),
            "smoke": (0, 255, 255),
            "coolant": (255, 128, 0),
        }
        height, width = frame.shape[:2]

        for detection in detections:
            x1 = max(0, min(detection.x1, width - 1))
            y1 = max(0, min(detection.y1, height - 1))
            x2 = max(0, min(detection.x2, width - 1))
            y2 = max(0, min(detection.y2, height - 1))
            color = colors.get(detection.class_name, (0, 255, 0))
            label = f"{detection.class_name} {detection.confidence:.2f}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label_y = max(20, y1 - 8)
            cv2.putText(
                frame,
                label,
                (x1, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )

        return frame

    def report_performance(self) -> None:
        """실제 캡처·추론·UI 발행 속도와 최신 프레임 나이를 출력한다."""
        now = time.monotonic()
        elapsed = now - self.last_stats_time
        if elapsed <= 0.0:
            return

        inference_delta = (
            self.inference_batch_count - self.last_inference_batch_count
        )
        inference_hz = inference_delta / elapsed

        capture_parts = []
        ui_parts = []
        bandwidth_parts = []
        age_parts = []
        now_ns = time.monotonic_ns()
        for camera in self.cameras:
            with camera.frame_lock:
                capture_count = camera.capture_count
                latest_capture_ns = camera.latest_capture_ns

            capture_delta = capture_count - self.last_capture_counts[camera.camera_id]
            ui_count = self.ui_publish_counts[camera.camera_id]
            ui_delta = ui_count - self.last_ui_publish_counts[camera.camera_id]
            compressed_bytes = self.compressed_byte_counts[camera.camera_id]
            compressed_delta = (
                compressed_bytes
                - self.last_compressed_byte_counts[camera.camera_id]
            )
            age_ms = (
                (now_ns - latest_capture_ns) / 1_000_000.0
                if latest_capture_ns > 0
                else float("nan")
            )

            capture_parts.append(f"{camera.camera_id}={capture_delta / elapsed:.1f}")
            ui_parts.append(f"{camera.camera_id}={ui_delta / elapsed:.1f}")
            bandwidth_mbps = compressed_delta * 8.0 / elapsed / 1_000_000.0
            bandwidth_parts.append(
                f"{camera.camera_id}={bandwidth_mbps:.1f}Mbps"
            )
            age_parts.append(f"{camera.camera_id}={age_ms:.1f}ms")
            self.last_capture_counts[camera.camera_id] = capture_count
            self.last_ui_publish_counts[camera.camera_id] = ui_count
            self.last_compressed_byte_counts[camera.camera_id] = compressed_bytes

        inference_time_delta = (
            self.inference_total_ms - self.last_inference_total_ms
        )
        average_inference_ms = (
            inference_time_delta / inference_delta
            if inference_delta > 0
            else 0.0
        )
        self.get_logger().info(
            "실시간 성능 | "
            f"capture_hz=[{', '.join(capture_parts)}] | "
            f"inference_hz={inference_hz:.1f} | "
            f"inference_avg={average_inference_ms:.1f}ms | "
            f"ui_hz=[{', '.join(ui_parts)}] | "
            f"stream=[{', '.join(bandwidth_parts)}] | "
            f"latest_age=[{', '.join(age_parts)}]"
        )

        self.last_stats_time = now
        self.last_inference_batch_count = self.inference_batch_count
        self.last_inference_total_ms = self.inference_total_ms

    def _extract_detected_status(self, result: Any) -> Dict[str, bool]:
        detected_status = self._default_status()

        if result.boxes is not None:
            for class_index in result.boxes.cls.tolist():
                class_name = self._class_name_from_index(
                    int(class_index), result.names
                )
                if class_name in detected_status:
                    detected_status[class_name] = True

        return detected_status

    @staticmethod
    def _class_name_from_index(
        class_index: int, names: Union[Dict[int, str], list]
    ) -> str:
        if isinstance(names, dict):
            return str(names.get(class_index, class_index))
        return str(names[class_index])

    def _apply_debounce(
        self, camera: CameraContext, detected_status: Dict[str, bool]
    ) -> Dict[str, bool]:
        """최근 DETECTION_WINDOW_SIZE프레임 중 과반 이상 감지되면 확정 상태로 반영."""
        stable_status = camera.last_status.copy()
        majority = self.DETECTION_WINDOW_SIZE // 2 + 1

        for event_name, detected in detected_status.items():
            window = camera.detection_windows[event_name]
            window.append(detected)
            stable_status[event_name] = sum(window) >= majority

        return stable_status

    def _publish_image(self, camera: CameraContext, frame: Any) -> bool:
        """UI 프레임을 JPEG로 압축하여 DDS 전송량을 줄인다."""
        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not success:
            self.get_logger().warning(
                f"{camera.camera_id}: UI 프레임 JPEG 압축에 실패했습니다."
            )
            return False

        message = CompressedImage()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = camera.camera_id
        message.format = "jpeg"
        message.data = encoded.tobytes()
        camera.image_publisher.publish(message)
        self.compressed_byte_counts[camera.camera_id] += len(message.data)
        return True

    def _publish_status(
        self, camera: CameraContext, detected_status: Dict[str, bool]
    ) -> None:
        if detected_status == camera.last_status:
            return

        for event_name, detected in detected_status.items():
            if detected == camera.last_status[event_name]:
                continue

            state_text = "감지" if detected else "해제"
            self.get_logger().info(
                f"{camera.camera_id}: {event_name} {state_text}"
            )

            self._publish_status_message(camera, event_name)

        camera.last_status = detected_status.copy()

    def _publish_status_message(self, camera: CameraContext, event_name: str) -> None:
        message = CamState()
        message.camera_id = camera.camera_number
        message.state = self.STATUS_STATES[event_name]
        self.status_publisher.publish(message)

    def _release_cameras(self) -> None:
        if self._cameras_released:
            return
        self._cameras_released = True
        self.shutdown_event.set()

        # 정상적인 read() 반환을 먼저 기다린 뒤 장치를 해제한다.
        for camera in self.cameras:
            if camera.capture_thread is not None:
                camera.capture_thread.join(timeout=0.5)

        for camera in self.cameras:
            camera.capture.release()

        # release()로 read()가 풀린 스레드의 종료를 한 번 더 기다린다.
        for camera in self.cameras:
            if camera.capture_thread is not None and camera.capture_thread.is_alive():
                camera.capture_thread.join(timeout=0.5)

    def destroy_node(self) -> bool:
        self._release_cameras()
        return super().destroy_node()


def main(args: Optional[list] = None) -> None:
    rclpy.init(args=args)
    node: Optional[DetectCctvNode] = None
    executor: Optional[MultiThreadedExecutor] = None

    try:
        node = DetectCctvNode()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().fatal(str(exc))
        else:
            print(f"detect_cctv_node 시작 실패: {exc}")
        raise
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
