#!/usr/bin/env python3

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import cv2
import rclpy
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from detect_cctv_interfaces.msg import CctvStatus
from rclpy.node import Node
from rclpy.qos import (
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import Image
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
    detection_streaks: Dict[str, int]
    missed_streaks: Dict[str, int]
    last_warning_time: float = 0.0


class DetectCctvNode(Node):
    """한 ROS 2 노드에서 웹캠 여러 대의 YOLO 이상 감지를 수행한다."""

    STATUS_STATES = {
        "fire": 0,
        "smoke": 1,
        "coolant": 2,
    }
    DETECTION_CONFIRM_FRAMES = 3
    RELEASE_CONFIRM_FRAMES = 10

    def __init__(self) -> None:
        super().__init__("detect_cctv_node")

        # 문자열 배열로 선언하면 카메라 인덱스("0")와 /dev 경로를 모두 사용할 수 있다.
        self.declare_parameter("camera_devices", ["0", "2"])
        self.declare_parameter("camera_ids", ["cctv1", "cctv2"])
        self.declare_parameter("model_path", "models/best.pt")
        self.declare_parameter("confidence", 0.5)
        self.declare_parameter("publish_hz", 10.0)
        self.declare_parameter("device", "")
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        self.declare_parameter("camera_fps", 15.0)

        camera_devices = [
            str(value) for value in self.get_parameter("camera_devices").value
        ]
        camera_ids = list(self.get_parameter("camera_ids").value)
        configured_model_path = str(self.get_parameter("model_path").value)
        self.model_path = self._resolve_model_path(configured_model_path)
        self.confidence = float(self.get_parameter("confidence").value)
        self.publish_hz = float(self.get_parameter("publish_hz").value)
        self.device = str(self.get_parameter("device").value)
        self.image_width = int(self.get_parameter("image_width").value)
        self.image_height = int(self.get_parameter("image_height").value)
        self.camera_fps = float(self.get_parameter("camera_fps").value)

        self._validate_camera_parameters(camera_devices, camera_ids)

        self.bridge = CvBridge()
        # 두 카메라가 동일한 학습 모델을 공유하여 메모리 사용량을 줄인다.
        self.model = YOLO(self.model_path)
        self.cameras: List[CameraContext] = []
        self.task_started = False

        self.start_subscription = self.create_subscription(
            Bool,
            "/ui/start",
            self._start_callback,
            1,
        )

        self.status_publisher = self.create_publisher(
            CctvStatus,
            "/detection/status",
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
        except Exception:
            self._release_cameras()
            raise

        timer_period = 1.0 / self.publish_hz
        self.timer = self.create_timer(timer_period, self.process_frames)

        camera_summary = ", ".join(
            f"{camera.camera_id}={camera.camera_device}"
            for camera in self.cameras
        )
        self.get_logger().info(
            f"다중 CCTV 준비 완료 | cameras=[{camera_summary}] | "
            f"model={self.model_path} | conf={self.confidence:.2f} | "
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

    @staticmethod
    def _resolve_model_path(configured_path: str) -> str:
        model_path = Path(configured_path).expanduser()

        if model_path.is_absolute():
            resolved_path = model_path
        else:
            resolved_path = (
                Path(get_package_share_directory("detect_cctv")) / model_path
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
            Image,
            f"/detection/{camera_id}/detection_image",
            image_qos,
        )
        return CameraContext(
            camera_id=camera_id,
            camera_number=camera_number,
            camera_device=self._convert_camera_device(camera_device),
            capture=capture,
            image_publisher=image_publisher,
            last_status={
                "fire": False,
                "smoke": False,
                "coolant": False,
            },
            detection_streaks={event_name: 0 for event_name in self.STATUS_STATES},
            missed_streaks={event_name: 0 for event_name in self.STATUS_STATES},
        )

    def _publish_initial_status(self, camera: CameraContext) -> None:
        """구독자가 시작 시 정상 상태(False)를 받을 수 있게 발행한다."""
        camera.last_status = {
            "fire": False,
            "smoke": False,
            "coolant": False,
        }
        camera.detection_streaks = {
            event_name: 0 for event_name in self.STATUS_STATES
        }
        camera.missed_streaks = {
            event_name: 0 for event_name in self.STATUS_STATES
        }
        for event_name in self.STATUS_STATES:
            self._publish_status_message(camera, event_name, False)

    def _start_callback(self, message: Bool) -> None:
        if message.data == self.task_started:
            return

        self.task_started = message.data

        if self.task_started:
            for camera in self.cameras:
                self._publish_initial_status(camera)
            self.get_logger().info(
                "/ui/start=True 수신: CCTV 탐지 및 토픽 발행을 시작합니다."
            )
            return

        for camera in self.cameras:
            self._publish_initial_status(camera)
        self.get_logger().info(
            "/ui/start=False 수신: CCTV 탐지 및 토픽 발행을 중지합니다."
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

    def process_frames(self) -> None:
        if not self.task_started:
            return

        for camera in self.cameras:
            self._process_frame(camera)

    def _process_frame(self, camera: CameraContext) -> None:
        success, frame = camera.capture.read()

        if not success or frame is None:
            now = time.monotonic()
            if now - camera.last_warning_time >= 5.0:
                self.get_logger().warning(
                    f"{camera.camera_id}: 카메라 프레임을 읽지 못했습니다."
                )
                camera.last_warning_time = now
            return

        try:
            predict_kwargs = {
                "source": frame,
                "conf": self.confidence,
                "verbose": False,
            }
            if self.device:
                predict_kwargs["device"] = self.device

            result = self.model.predict(**predict_kwargs)[0]
            detected_status = {
                "fire": False,
                "smoke": False,
                "coolant": False,
            }

            if result.boxes is not None:
                for class_index in result.boxes.cls.tolist():
                    class_name = self._class_name_from_index(
                        int(class_index), result.names
                    )
                    if class_name in detected_status:
                        detected_status[class_name] = True

            detected_status = self._apply_debounce(camera, detected_status)
            self._publish_image(camera, result.plot())
            self._publish_status(camera, detected_status)
        except Exception as exc:
            self.get_logger().error(
                f"{camera.camera_id}: YOLO 처리 중 오류: {exc}"
            )

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
        stable_status = camera.last_status.copy()

        for event_name, detected in detected_status.items():
            if detected:
                camera.detection_streaks[event_name] = min(
                    camera.detection_streaks[event_name] + 1,
                    self.DETECTION_CONFIRM_FRAMES,
                )
                camera.missed_streaks[event_name] = 0
                if (
                    camera.detection_streaks[event_name]
                    >= self.DETECTION_CONFIRM_FRAMES
                ):
                    stable_status[event_name] = True
                continue

            camera.detection_streaks[event_name] = 0
            camera.missed_streaks[event_name] = min(
                camera.missed_streaks[event_name] + 1,
                self.RELEASE_CONFIRM_FRAMES,
            )
            if camera.missed_streaks[event_name] >= self.RELEASE_CONFIRM_FRAMES:
                stable_status[event_name] = False

        return stable_status

    def _publish_image(self, camera: CameraContext, frame: Any) -> None:
        message = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = camera.camera_id
        camera.image_publisher.publish(message)

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

            self._publish_status_message(camera, event_name, detected)

        camera.last_status = detected_status.copy()

    def _publish_status_message(
        self, camera: CameraContext, event_name: str, detected: bool
    ) -> None:
        message = CctvStatus()
        message.camera_id = camera.camera_number
        message.state = self.STATUS_STATES[event_name]
        message.detected = detected
        self.status_publisher.publish(message)

    def _release_cameras(self) -> None:
        for camera in self.cameras:
            camera.capture.release()

    def destroy_node(self) -> bool:
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
