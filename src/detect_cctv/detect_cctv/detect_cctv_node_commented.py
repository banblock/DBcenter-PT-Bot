from __future__ import annotations

# 반복되는 카메라 읽기 오류를 5초 간격으로 제한할 때 사용한다.
import time

# 카메라별로 묶여 있는 상태를 명확한 자료구조로 표현한다.
from dataclasses import dataclass

# 모델 경로를 운영체제에 독립적으로 조합한다.
from pathlib import Path

# 함수와 멤버의 자료형을 문서화하기 위한 타입 힌트이다.
from typing import Any, Dict, List, Optional, Union

# 웹캠 열기, 프레임 읽기, MJPG/해상도/FPS 설정을 담당한다.
import cv2

# ROS 2 Python 클라이언트 라이브러리이다.
import rclpy

# 다른 노트북에서도 설치된 detect_cctv 패키지 위치를 자동으로 찾는다.
from ament_index_python.packages import get_package_share_directory

# OpenCV BGR 이미지와 sensor_msgs/Image 사이를 변환한다.
from cv_bridge import CvBridge

# 카메라 번호와 세 이상 상태를 한 메시지로 전달하는 커스텀 타입이다.
from detect_cctv_interfaces.msg import CctvStatus

# 모든 ROS 2 Python 노드의 기반 클래스이다.
from rclpy.node import Node

# 실시간 영상에 적합한 QoS를 적용하기 위한 정책들이다.
from rclpy.qos import (
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

# Bounding Box가 표시된 탐지 영상의 메시지 타입이다.
from sensor_msgs.msg import Image

# UI에서 보내는 시작·중지 신호의 메시지 타입이다.
from std_msgs.msg import Bool

# 학습된 YOLO 가중치를 로드하고 추론을 실행한다.
from ultralytics import YOLO


@dataclass
class CameraContext:
    """카메라 한 대에 속한 실행 상태를 한곳에 모은다.

    노드는 하나지만 각 카메라의 장치, 토픽, 최근 감지 상태와 경고 시간은
    서로 섞이면 안 된다. 이 객체를 카메라마다 하나씩 생성해 독립 관리한다.
    """

    # cctv1, cctv2처럼 토픽과 Image frame_id에 사용할 논리 이름이다.
    camera_id: str

    # 커스텀 메시지의 camera_id에 넣을 0 또는 1 값이다.
    camera_number: int

    # OpenCV가 실제로 여는 정수 인덱스 또는 /dev/video 경로이다.
    camera_device: Union[int, str]

    # 해당 카메라의 OpenCV 캡처 객체이다.
    capture: cv2.VideoCapture

    # /detection/<camera_id>/detection_image Publisher이다.
    image_publisher: Any

    # 상태가 변할 때만 발행하기 위해 직전 값을 보관한다.
    last_status: Dict[str, bool]

    # 읽기 실패 경고가 매 프레임 출력되지 않게 마지막 출력 시각을 저장한다.
    last_warning_time: float = 0.0


class DetectCctvNode(Node):
    """한 ROS 2 노드에서 웹캠 여러 대의 YOLO 이상 감지를 수행한다.

    기존 구조처럼 노드를 카메라 수만큼 실행하지 않는다. 이 객체 하나가
    ``self.cameras``에 여러 CameraContext를 가지고 순서대로 처리한다.
    """

    # CctvStatus.msg에 정한 규칙에 따라 모델 클래스 이름을 상태 번호로 변환한다.
    STATUS_STATES = {
        "fire": 0,
        "smoke": 1,
        "coolant": 2,
    }

    def __init__(self) -> None:
        """파라미터, 모델, 카메라별 Publisher와 Timer를 초기화한다."""

        # ROS graph에는 detect_cctv_node 하나만 등록된다.
        super().__init__("detect_cctv_node")

        # ------------------------------------------------------------------
        # 1. ROS 2 파라미터 선언
        # ------------------------------------------------------------------
        # 문자열 배열로 선언하면 카메라 인덱스("0")와 /dev 경로를 모두 사용할 수 있다.
        # camera_devices와 camera_ids는 같은 위치끼리 한 쌍이다.
        # 예: "0" ↔ "cctv1", "2" ↔ "cctv2"
        self.declare_parameter("camera_devices", ["0", "2"])
        self.declare_parameter("camera_ids", ["cctv1", "cctv2"])

        # models/best.pt는 현재 사용자 홈이 아닌 설치된 패키지 share 기준이다.
        self.declare_parameter("model_path", "models/best.pt")

        # YOLO 최소 신뢰도와 Timer 호출 주기이다.
        self.declare_parameter("confidence", 0.5)
        self.declare_parameter("publish_hz", 10.0)

        # 빈 문자열이면 Ultralytics가 CPU/GPU를 자동 선택한다.
        # "cpu" 또는 NVIDIA GPU 번호 "0"을 지정할 수도 있다.
        self.declare_parameter("device", "")

        # 모든 카메라에 공통으로 요청할 캡처 설정이다.
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        self.declare_parameter("camera_fps", 15.0)

        # ------------------------------------------------------------------
        # 2. 파라미터 읽기 및 자료형 정리
        # ------------------------------------------------------------------
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

        # 카메라 장치와 ID의 개수/고유성이 맞지 않으면 시작 즉시 알려준다.
        self._validate_camera_parameters(camera_devices, camera_ids)

        # ------------------------------------------------------------------
        # 3. 이미지 변환기 및 공유 YOLO 모델 생성
        # ------------------------------------------------------------------
        self.bridge = CvBridge()

        # 두 카메라가 동일한 학습 모델을 공유하여 메모리 사용량을 줄인다.
        self.model = YOLO(self.model_path)

        # 각 항목은 카메라 한 대의 캡처/Publisher/최근 상태를 가진다.
        self.cameras: List[CameraContext] = []

        # UI 시작 명령을 받기 전에는 Timer가 카메라 추론을 수행하지 않는다.
        self.task_started = False

        # 시작 신호는 별도 QoSProfile 없이 기본 QoS의 큐 크기 1을 사용한다.
        self.start_subscription = self.create_subscription(
            Bool,
            "/ui/start",
            self._start_callback,
            1,
        )

        # 상태 Publisher는 별도 QoSProfile 없이 기본 QoS의 큐 크기 10을 사용한다.
        self.status_publisher = self.create_publisher(
            CctvStatus,
            "/detection/status",
            10,
        )

        # ------------------------------------------------------------------
        # 4. camera_devices에 나열된 모든 카메라 열기 및 Publisher 생성
        # ------------------------------------------------------------------
        try:
            for camera_number, (camera_device, camera_id) in enumerate(
                zip(camera_devices, camera_ids), start=0
            ):
                camera = self._create_camera_context(
                    camera_device, camera_id, camera_number
                )
                self.cameras.append(camera)
        except Exception:
            # 중간 카메라에서 실패하면 앞에서 이미 연 장치도 반드시 해제한다.
            self._release_cameras()
            raise

        # 한 번의 Timer callback에서 모든 카메라를 한 차례씩 처리한다.
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
        """카메라 장치 배열과 ID 배열이 안전하게 연결되는지 검사한다.

        두 배열은 zip으로 연결되므로 길이가 다르면 일부 항목이 조용히
        누락될 수 있다. 또한 같은 ID를 두 번 쓰면 토픽 이름이 충돌한다.
        """

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
        """설정된 모델 경로를 실제 파일이 존재하는 절대경로로 바꾼다.

        절대경로는 그대로 검사하고, ``models/best.pt`` 같은 상대경로는
        ``get_package_share_directory``가 찾은 현재 패키지 설치 위치 뒤에
        붙인다. 따라서 사용자명과 워크스페이스 위치가 다른 노트북에서도
        같은 상대경로 설정을 사용할 수 있다.
        """

        model_path = Path(configured_path).expanduser()

        if model_path.is_absolute():
            resolved_path = model_path
        else:
            resolved_path = (
                Path(get_package_share_directory("detect_cctv")) / model_path
            )

        if not resolved_path.is_file():
            # YOLO 내부의 모호한 오류 대신 어느 경로가 잘못됐는지 바로 표시한다.
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
        """카메라 한 대를 열고 그 카메라 전용 Publisher들을 만든다."""

        # 실제 장치를 먼저 열어 잘못된 장치 번호를 시작 단계에서 발견한다.
        capture = self._open_camera(camera_device, camera_id)

        # CCTV1과 CCTV2의 실시간 영상 Publisher에만 적용하는 QoS이다.
        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        # Bounding Box가 그려진 결과 영상 토픽이다.
        image_publisher = self.create_publisher(
            Image,
            f"/detection/{camera_id}/detection_image",
            image_qos,
        )
        # 처음에는 모든 이상 상태가 없는 것으로 기록한다.
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
        )

    def _publish_initial_status(self, camera: CameraContext) -> None:
        """구독자가 시작 시 정상 상태(False)를 받을 수 있게 발행한다."""

        camera.last_status = {
            "fire": False,
            "smoke": False,
            "coolant": False,
        }
        # 각 이상 종류의 정상(False) 상태를 별도 메시지로 알린다.
        for event_name in self.STATUS_STATES:
            self._publish_status_message(camera, event_name, False)

    def _start_callback(self, message: Bool) -> None:
        """UI의 Bool 명령에 따라 전체 CCTV 추론과 발행을 시작/중지한다."""

        # 현재 상태와 같은 명령이 반복되면 불필요한 초기 상태 발행을 막는다.
        if message.data == self.task_started:
            return

        self.task_started = message.data

        if self.task_started:
            # 시작 시 구독 노드가 명확한 정상 상태에서 출발하도록 모든 상태
            # 토픽에 False를 한 번 발행하고 프레임 처리를 허용한다.
            for camera in self.cameras:
                self._publish_initial_status(camera)
            self.get_logger().info(
                "/ui/start=True 수신: CCTV 탐지 및 토픽 발행을 시작합니다."
            )
            return

        # 중지 직전에 감지 상태가 True였다면 수신 측 상태도 정상으로 돌아가도록
        # False로 초기화한다. 이후 이미지는 새로 발행하지 않는다.
        for camera in self.cameras:
            self._publish_initial_status(camera)
        self.get_logger().info(
            "/ui/start=False 수신: CCTV 탐지 및 토픽 발행을 중지합니다."
        )

    @staticmethod
    def _convert_camera_device(value: Union[int, str]) -> Union[int, str]:
        """숫자 문자열은 OpenCV 인덱스로, 경로 문자열은 그대로 반환한다."""

        if isinstance(value, int):
            return value

        text = str(value).strip()
        if text.isdigit():
            return int(text)
        return text

    def _open_camera(
        self, camera_device: Union[int, str], camera_id: str
    ) -> cv2.VideoCapture:
        """V4L2 카메라를 열고 USB 대역폭을 고려한 설정을 요청한다."""

        device = self._convert_camera_device(camera_device)

        # Linux에서 사용할 백엔드를 V4L2로 명시한다.
        capture = cv2.VideoCapture(device, cv2.CAP_V4L2)

        # 비압축 YUYV보다 대역폭이 작은 MJPG를 먼저 요청한다.
        capture.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG"),
        )
        # 카메라가 요청 조합을 지원하지 않으면 드라이버가 다른 값으로
        # 조정할 수 있으므로 아래에서 실제 적용값을 다시 읽어 로그로 남긴다.
        if self.image_width > 0:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.image_width)
        if self.image_height > 0:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.image_height)
        capture.set(cv2.CAP_PROP_FPS, self.camera_fps)
        # YOLO 처리 중 프레임이 누적돼 화면이 늦어지는 현상을 줄인다.
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                f"{camera_id}: 카메라를 열 수 없습니다: {camera_device}. "
            )

        # set() 성공 여부만 믿지 않고 드라이버가 적용한 실제 값을 확인한다.
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
        """Timer가 호출할 때마다 등록된 모든 카메라를 한 번씩 처리한다."""

        # 시작 명령 전 또는 UI가 False로 중지한 동안에는 카메라 읽기,
        # YOLO 추론, 이미지/상태 발행을 모두 건너뛴다.
        if not self.task_started:
            return

        for camera in self.cameras:
            self._process_frame(camera)

    def _process_frame(self, camera: CameraContext) -> None:
        """카메라 한 대의 프레임을 추론하고 영상 및 상태를 발행한다."""

        # read()는 성공 여부와 OpenCV BGR 프레임을 반환한다.
        success, frame = camera.capture.read()

        if not success or frame is None:
            # 시스템 시간이 바뀌어도 항상 증가하는 시계를 경고 간격에 사용한다.
            now = time.monotonic()
            if now - camera.last_warning_time >= 5.0:
                self.get_logger().warning(
                    f"{camera.camera_id}: 카메라 프레임을 읽지 못했습니다."
                )
                camera.last_warning_time = now
            return

        try:
            # verbose=False로 매 프레임 Ultralytics 로그가 쌓이지 않게 한다.
            predict_kwargs = {
                "source": frame,
                "conf": self.confidence,
                "verbose": False,
            }
            if self.device:
                predict_kwargs["device"] = self.device

            # 입력이 한 프레임이므로 결과 목록의 첫 항목만 사용한다.
            result = self.model.predict(**predict_kwargs)[0]

            # 현재 프레임에서 하나라도 검출되면 해당 항목을 True로 바꾼다.
            detected_status = {
                "fire": False,
                "smoke": False,
                "coolant": False,
            }

            if result.boxes is not None:
                # boxes.cls는 Tensor이므로 일반 Python list로 변환해 순회한다.
                for class_index in result.boxes.cls.tolist():
                    class_name = self._class_name_from_index(
                        int(class_index), result.names
                    )
                    # 모델 클래스명이 fire, smoke, coolant 중 하나이면
                    # 같은 이름의 상태를 바로 True로 변경한다.
                    if class_name in detected_status:
                        detected_status[class_name] = True

            # result.plot()은 Bounding Box와 클래스명이 표시된 BGR 이미지를 만든다.
            self._publish_image(camera, result.plot())
            self._publish_status(camera, detected_status)
        except Exception as exc:
            # 한 프레임의 추론 오류로 전체 CCTV 노드를 종료하지 않는다.
            self.get_logger().error(
                f"{camera.camera_id}: YOLO 처리 중 오류: {exc}"
            )

    @staticmethod
    def _class_name_from_index(
        class_index: int, names: Union[Dict[int, str], list]
    ) -> str:
        """모델 버전에 따라 dict 또는 list인 클래스 표를 모두 처리한다."""

        if isinstance(names, dict):
            return str(names.get(class_index, class_index))
        return str(names[class_index])

    def _publish_image(self, camera: CameraContext, frame: Any) -> None:
        """OpenCV BGR 결과를 ROS Image로 변환해 해당 카메라 토픽에 발행한다."""

        message = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")

        # 발행 시각과 카메라 ID를 메시지 헤더에 기록한다.
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = camera.camera_id
        camera.image_publisher.publish(message)

    def _publish_status(
        self, camera: CameraContext, detected_status: Dict[str, bool]
    ) -> None:
        """직전 프레임과 달라진 이상 종류만 CctvStatus로 발행한다."""

        if detected_status == camera.last_status:
            return

        for event_name, detected in detected_status.items():
            if detected == camera.last_status[event_name]:
                continue

            state_text = "감지" if detected else "해제"
            self.get_logger().info(
                f"{camera.camera_id}: {event_name} {state_text}"
            )

            # 감지 시작은 True, 감지 해제는 False로 한 번만 발행한다.
            self._publish_status_message(camera, event_name, detected)

        camera.last_status = detected_status.copy()

    def _publish_status_message(
        self, camera: CameraContext, event_name: str, detected: bool
    ) -> None:
        """카메라·이상 종류·감지 여부를 CctvStatus 하나로 발행한다."""

        message = CctvStatus()
        message.camera_id = camera.camera_number
        message.state = self.STATUS_STATES[event_name]
        message.detected = detected
        self.status_publisher.publish(message)

    def _release_cameras(self) -> None:
        """현재 노드가 점유한 모든 VideoCapture를 해제한다."""

        for camera in self.cameras:
            camera.capture.release()

    def destroy_node(self) -> bool:
        """카메라를 먼저 놓은 뒤 ROS 노드 자원을 정리한다."""

        self._release_cameras()
        return super().destroy_node()


def main(args: Optional[list] = None) -> None:
    """ROS 초기화부터 spin 및 자원 해제까지 생명주기를 관리한다."""

    # 코드에 선언된 기본 파라미터를 사용하여 ROS 2 통신을 초기화한다.
    # args가 전달되면 일반 ROS 명령행 파라미터 재정의는 계속 사용할 수 있다.
    rclpy.init(args=args)

    # 생성자 중간에 모델 또는 카메라 오류가 날 수 있으므로 먼저 None으로 둔다.
    node: Optional[DetectCctvNode] = None

    try:
        # 이 객체 하나가 기본 파라미터에 등록된 카메라 0과 2를 모두 연다.
        node = DetectCctvNode()

        # Timer callback이 반복 실행되도록 ROS 이벤트 루프를 유지한다.
        rclpy.spin(node)
    except KeyboardInterrupt:
        # 사용자의 Ctrl+C는 정상 종료로 처리한다.
        pass
    except Exception as exc:
        # 초기화/실행 실패를 출력하고 다시 발생시켜 프로세스 종료코드를
        # 실패로 남긴다. 다른 도구에서 실패를 정상 종료로 오인하지 않게 한다.
        if node is not None:
            node.get_logger().fatal(str(exc))
        else:
            print(f"detect_cctv_node 시작 실패: {exc}")
        raise
    finally:
        # 어떤 경로로 끝나더라도 카메라와 ROS 자원을 해제한다.
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


# Python 파일을 직접 실행할 때의 진입점이다.
# setup.py의 detect_cctv_node console script도 같은 main()을 사용한다.
if __name__ == "__main__":
    main()
