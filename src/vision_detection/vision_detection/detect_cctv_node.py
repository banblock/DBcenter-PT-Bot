from __future__ import annotations

import glob
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from patrol_interfaces.msg import CamState
from rclpy.node import Node
from rclpy.qos import (
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool
from ultralytics import YOLO

@dataclass
class CameraContext:
    """카메라 한 대에 속한 캡처 및 ROS 퍼블리셔 상태."""

    camera_id: str
    camera_number: int
    camera_device: Union[int, str]
    capture: cv2.VideoCapture
    image_publisher: Any
    last_status: Dict[str, bool]
    detection_windows: Dict[str, "deque[bool]"]
    frame_lock: threading.Lock = field(default_factory=threading.Lock)
    latest_frame: Optional[Any] = None
    frame_sequence: int = 0
    last_inferred_sequence: int = -1
    capture_thread: Optional[threading.Thread] = None

class DetectCctvNode(Node):
    """한 ROS 2 노드에서 웹캠 여러 대의 YOLO 이상 감지를 수행한다.
    카메라마다 전용 스레드가 계속 프레임을 읽어 최신 프레임 1장만 유지하고,
    타이머가 그 프레임들을 모아 한 번에 배치로 추론한다(카메라 대수만큼 모델을
    따로 부르는 것보다 GPU 효율이 좋음). 결과 이미지는 JPEG로 압축해서 발행하고
    (네트워크 대역폭 절감), 판정은 detect_ambient_node와 동일하게 최근 프레임
    윈도우의 과반수로 확정한다(순간적인 오검출/흔들림 방지).
    """

    STATUS_STATES = {"fire": 0, "smoke": 1, "coolant": 2,}
    # 최근 N프레임 중 과반 이상 감지되면 확정 (켜짐/꺼짐 모두 동일 기준)
    DETECTION_WINDOW_SIZE = 3

    def __init__(self) -> None:
        """파라미터 로드, 모델 로드+워밍업, 카메라 오픈, 캡처 스레드 시작, 타이머 등록까지 한 번에 수행."""
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
        self.declare_parameter("jpeg_quality", 80)
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
        self.jpeg_quality = int(self.get_parameter("jpeg_quality").value)
        self.device = str(self.get_parameter("device").value)
        self.image_width = int(self.get_parameter("image_width").value)
        self.image_height = int(self.get_parameter("image_height").value)
        self.camera_fps = float(self.get_parameter("camera_fps").value)
        self.inference_size = int(self.get_parameter("inference_size").value)

        self._validate_camera_parameters(camera_devices, camera_ids)

        # 두 카메라가 동일한 학습 모델을 공유하여 메모리 사용량을 줄인다.
        self.model = YOLO(self.model_path)
        self._warmup_model(len(camera_ids))
        self.cameras: List[CameraContext] = []
        self.task_started = False
        self.shutdown_event = threading.Event()
        self._cameras_released = False

        self.start_subscription = self.create_subscription(
            Bool, "/ui/start", self._start_callback, 1,)

        self.status_publisher = self.create_publisher(
            CamState, "/detection/cam_state", 10,)

        self._image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        try:
            for camera_number, (camera_device, camera_id) in enumerate(
                zip(camera_devices, camera_ids), start=0
            ):
                camera = self._create_camera_context(
                    camera_device, camera_id, camera_number
                )
                self.cameras.append(camera)
            self._start_capture_threads()
        except Exception:
            self._release_cameras()
            raise

        # 처리 주기를 camera_fps에 맞춰서 카메라가 새 프레임을 주는 만큼 매번 추론한다.
        timer_period = 1.0 / self.camera_fps
        self.timer = self.create_timer(timer_period, self.process_frames)

    @staticmethod
    def _validate_camera_parameters(
        camera_devices: List[str], camera_ids: List[str]
    ) -> None:
        """camera_devices/camera_ids 파라미터 조합이 유효한지(개수 일치, 중복/빈값 없음) 검사."""
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
        """상대경로면 패키지 share 디렉토리 기준으로, 절대경로면 그대로 실제 모델 파일 위치를 반환."""
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

    def _warmup_model(self, batch_size: int) -> None:
        """실제 카메라 프레임이 들어오기 전에 더미 이미지로 한 번 추론해서 CUDA
        초기화(커널 컴파일, cuDNN 알고리즘 탐색, 가중치 GPU 전송 등) 비용을 노드
        시작 시점(어차피 /ui/start 대기 중이라 실사용에 영향 없는 구간)으로 옮겨둔다 -
        안 그러면 노드 시작 직후 들어오는 첫 프레임의 추론이 몇 초씩 늦어질 수 있다.
        """
        dummy_frame = np.zeros((self.image_height, self.image_width, 3), dtype=np.uint8)
        predict_kwargs = {
            "source": [dummy_frame] * max(batch_size, 1),
            "conf": self.confidence,
            "imgsz": self.inference_size,
            "augment": True,
            "verbose": False,
        }
        if self.device:
            predict_kwargs["device"] = self.device

        self.model.predict(**predict_kwargs)

    def _create_camera_context(self, camera_device: str, camera_id: str, camera_number: int,) -> CameraContext:
        """카메라 1대를 열고 전용 퍼블리셔/상태를 묶은 CameraContext를 만든다."""
        capture = self._open_camera(camera_device, camera_id)
        image_publisher = self.create_publisher(
            CompressedImage,
            f"/detection/{camera_id}/detection_image",
            self._image_qos,
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
        """클래스별로 최근 프레임 감지 여부를 담을 빈 debounce 윈도우를 생성."""
        return {
            event_name: deque(maxlen=self.DETECTION_WINDOW_SIZE)
            for event_name in self.STATUS_STATES
        }

    def _default_status(self) -> Dict[str, bool]:
        """모든 클래스가 미검출(False)인 초기 상태 딕셔너리를 생성."""
        return dict.fromkeys(self.STATUS_STATES, False)

    def _publish_initial_status(self, camera: CameraContext) -> None:
        """구독자가 시작 시 정상 상태(False)를 받을 수 있게 발행한다."""
        camera.last_status = self._default_status()
        camera.detection_windows = self._new_detection_windows()
        for event_name in self.STATUS_STATES:
            self._publish_status_message(camera, event_name)

    def _start_callback(self, message: Bool) -> None:
        """/ui/start 수신 시 task_started를 갱신하고, 상태가 바뀌면 모든 카메라의 초기 상태를 재발행."""
        if message.data == self.task_started:
            return

        self.task_started = message.data

        for camera in self.cameras:
            self._publish_initial_status(camera)

    @staticmethod
    def _convert_camera_device(value: Union[int, str]) -> Union[int, str]:
        """"0"처럼 숫자로만 된 문자열은 정수 인덱스로, 그 외(/dev/videoN 등)는 문자열 그대로 사용."""
        if isinstance(value, int):
            return value

        text = str(value).strip()
        if text.isdigit():
            return int(text)
        return text

    def _open_camera(
        self, camera_device: Union[int, str], camera_id: str
    ) -> cv2.VideoCapture:
        """cv2.VideoCapture를 열고 해상도/FPS/버퍼 크기 등 캡처 옵션을 설정."""
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

        return capture

    def _start_capture_threads(self) -> None:
        """카메라마다 전용 스레드를 시작해 드라이버 버퍼를 계속 비운다.
        타이머 콜백에서 직접 capture.read()를 하면 그 순간 드라이버 버퍼에 남아있던
        오래된 프레임을 읽을 수 있다(특히 추론이 잠깐 밀리면 버퍼가 쌓임). 이상감지
        용도라 오래된 프레임으로 판단이 늦어지면 안 되므로, 별도 스레드가 계속
        최신 프레임 1장만 유지하도록 덮어쓴다.
        """
        for camera in self.cameras:
            camera.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(camera,),
                name=f"{camera.camera_id}_latest_frame_capture",
                daemon=True,
            )
            camera.capture_thread.start()

    def _capture_loop(self, camera: CameraContext) -> None:
        """캡처 스레드 본체: 종료 신호 전까지 계속 프레임을 읽어 latest_frame만 덮어쓴다."""
        while not self.shutdown_event.is_set():
            success, frame = camera.capture.read()

            if not success or frame is None:
                continue

            with camera.frame_lock:
                camera.latest_frame = frame
                camera.frame_sequence += 1

    def _snapshot_latest_frame(self, camera: CameraContext) -> Optional[tuple[Any, int]]:
        """다른 스레드가 교체해도 안전하도록 최신 프레임 복사본을 반환한다."""
        with camera.frame_lock:
            if camera.latest_frame is None:
                return None
            return camera.latest_frame.copy(), camera.frame_sequence

    def process_frames(self) -> None:
        """타이머 콜백: 카메라별 최신 프레임을 모아 배치 추론하고 상태/이미지를 발행."""
        if not self.task_started:
            return

        cameras_with_frames: List[CameraContext] = []
        frames: List[Any] = []
        frame_sequences: List[int] = []

        for camera in self.cameras:
            snapshot = self._snapshot_latest_frame(camera)
            if snapshot is None:
                continue
            frame, sequence = snapshot
            # 지난 추론 이후 새 프레임이 안 들어왔으면 같은 화면을 다시 추론하지 않는다
            # (타이머 주기가 실제 카메라 프레임레이트를 순간적으로 앞지를 때 GPU 낭비 방지).
            if sequence == camera.last_inferred_sequence:
                continue
            cameras_with_frames.append(camera)
            frames.append(frame)
            frame_sequences.append(sequence)

        if not frames:
            return

        try:
            predict_kwargs = {
                "source": frames,
                "conf": self.confidence,
                "imgsz": self.inference_size,
                # TTA 효과는 모델 아키텍처마다 달라서(오히려 나빠지는 경우도 있음)
                # 모델을 바꾸면 재검증 필요.
                "augment": True,
                "verbose": False,
            }
            if self.device:
                predict_kwargs["device"] = self.device

            results = self.model.predict(**predict_kwargs)
            if len(results) != len(cameras_with_frames):
                raise RuntimeError(
                    "YOLO 결과 수가 입력한 카메라 프레임 수와 다릅니다."
                )

            for camera, result, sequence in zip(cameras_with_frames, results, frame_sequences):
                detected_status = self._extract_detected_status(result)
                current_boxes = self._extract_boxes(result)
                stable_status = self._apply_debounce(camera, detected_status)
                self._publish_status(camera, stable_status, current_boxes)
                self._publish_image(camera, result.plot())
                camera.last_inferred_sequence = sequence
        except Exception as exc:
            self.get_logger().error(f"YOLO 배치 처리 중 오류: {exc}")

    def _extract_detected_status(self, result: Any) -> Dict[str, bool]:
        """YOLO 결과 1장에서 클래스별 검출 여부(True/False)만 뽑는다."""
        detected_status = self._default_status()

        if result.boxes is not None:
            for class_index in result.boxes.cls.tolist():
                class_name = self._class_name_from_index(
                    int(class_index), result.names
                )
                if class_name in detected_status:
                    detected_status[class_name] = True

        return detected_status

    def _extract_boxes(self, result: Any) -> Dict[str, tuple]:
        """클래스별 대표 bounding box(신뢰도 최고 1개)를 뽑는다.

        반환: {class_name: (x1, y1, x2, y2, conf)} - 이미지 픽셀 좌표.
        호모그래피 입력용으로 CamState에 실어 보낸다(백엔드가 픽셀→맵 변환).
        """
        boxes: Dict[str, tuple] = {}
        if result.boxes is None:
            return boxes

        xyxy = result.boxes.xyxy.tolist()
        confs = result.boxes.conf.tolist()
        classes = result.boxes.cls.tolist()
        for (x1, y1, x2, y2), conf, class_index in zip(xyxy, confs, classes):
            class_name = self._class_name_from_index(int(class_index), result.names)
            if class_name not in self.STATUS_STATES:
                continue
            existing = boxes.get(class_name)
            if existing is None or conf > existing[4]:
                boxes[class_name] = (float(x1), float(y1), float(x2), float(y2), float(conf))
        return boxes

    @staticmethod
    def _class_name_from_index(
        class_index: int, names: Union[Dict[int, str], list]
    ) -> str:
        """YOLO 결과의 클래스 인덱스를 클래스 이름 문자열로 변환(names가 dict/list 둘 다 가능)."""
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

    def _publish_image(self, camera: CameraContext, frame: Any) -> None:
        """UI 프레임을 JPEG로 압축해서 발행한다(raw BGR8 대비 DDS 전송량을 크게 줄임 -
        CCTV 2대를 네트워크로 계속 스트리밍하는 배포 환경에서 대역폭이 실제로 문제됨)."""
        success, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if not success:
            return

        message = CompressedImage()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = camera.camera_id
        message.format = "jpeg"
        message.data = encoded.tobytes()
        camera.image_publisher.publish(message)

    def _publish_status(
        self,
        camera: CameraContext,
        detected_status: Dict[str, bool],
        current_boxes: Dict[str, tuple],
    ) -> None:
        """이전 상태와 비교해 바뀐 클래스만 CamState로 발행."""
        if detected_status == camera.last_status:
            return

        for event_name, detected in detected_status.items():
            if detected == camera.last_status[event_name]:
                continue

            # 감지(켜짐)일 때만 bbox를 실어 보낸다. 해제(꺼짐)는 box=None -> 무효 bbox(-1).
            box = current_boxes.get(event_name) if detected else None
            self._publish_status_message(camera, event_name, box)

        camera.last_status = detected_status.copy()

    def _publish_status_message(
        self, camera: CameraContext, event_name: str, box: Optional[tuple] = None
    ) -> None:
        """클래스 하나에 대한 CamState 메시지 1건을 구성해서 발행."""
        message = CamState()
        message.camera_id = camera.camera_number
        message.state = self.STATUS_STATES[event_name]
        if box is not None:
            x1, y1, x2, y2, conf = box
            message.bbox_x1 = float(x1)
            message.bbox_y1 = float(y1)
            message.bbox_x2 = float(x2)
            message.bbox_y2 = float(y2)
            message.confidence = float(conf)
        else:
            # 해제/박스 없음 -> 무효 bbox(-1) + confidence 0. 백엔드가 좌표를 안 채운다.
            message.bbox_x1 = -1.0
            message.bbox_y1 = -1.0
            message.bbox_x2 = -1.0
            message.bbox_y2 = -1.0
            message.confidence = 0.0
        self.status_publisher.publish(message)

    def _release_cameras(self) -> None:
        """캡처 스레드를 정지시키고 모든 카메라 장치를 해제(중복 호출 방지)."""
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
        """노드 종료 시 카메라 자원부터 정리한 뒤 부모 클래스의 종료 처리를 수행."""
        self._release_cameras()
        return super().destroy_node()

def main(args: Optional[list] = None) -> None:
    rclpy.init(args=args)
    node: Optional[DetectCctvNode] = None

    try:
        node = DetectCctvNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().fatal(str(exc))
        else:
            print(f"detect_cctv_node 시작 실패: {exc}")
        raise
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__":
    main()