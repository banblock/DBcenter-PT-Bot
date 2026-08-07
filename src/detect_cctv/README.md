# detect_cctv

웹캠 2대를 CCTV 1, CCTV 2로 사용하여 화재, 연기, 냉각수 누수를 탐지하는 ROS 2 Python 패키지입니다.

하나의 `detect_cctv_node.py`와 하나의 ROS 2 노드가 웹캠 2대를 함께 관리합니다.

노드는 `/ui/start` (`std_msgs/msg/Bool`)을 구독하며, `True`를 받은 뒤부터
카메라 추론과 이미지·이상 상태 토픽 발행을 시작합니다. `False`를 받으면
처리를 중지하고 이상 상태를 `False`로 초기화합니다.

## 발행 토픽

### CCTV 1

- `/detection/cctv1/detection_image` (`sensor_msgs/msg/Image`)

### CCTV 2

- `/detection/cctv2/detection_image` (`sensor_msgs/msg/Image`)

### 통합 이상 상태

- `/detection/status` (`detect_cctv_interfaces/msg/CctvStatus`)

```text
int32 camera_id  # 0: CCTV1, 1: CCTV2
int32 state      # 0: fire, 1: smoke, 2: coolant
bool detected    # true: 감지 시작, false: 감지 해제
```

상태가 바뀐 이상 종류만 발행하며, CCTV1과 CCTV2의 모든 상태는
`/detection/status` 토픽 하나로 전달합니다.

탐지 영상은 UI(main)에서 구독하고, 이상 상태 토픽은 `detect_main_node`에서 구독하도록 구성합니다.

## 준비

YOLO 모델 파일을 `models/best.pt`로 준비합니다. 노드는 설치된 패키지의
`share/detect_cctv/models/best.pt` 경로를 자동으로 찾습니다.

모델 클래스 이름은 기본적으로 다음과 같다고 가정합니다.

- `fire`
- `smoke`
- `coolant`

학습 모델의 클래스 이름은 정확히 `fire`, `smoke`, `coolant`이어야 합니다.

Ultralytics를 설치합니다.

```bash
pip install ultralytics
```

웹캠 장치를 확인합니다.

```bash
v4l2-ctl --list-devices
```

웹캠 하나가 `/dev/video0`, `/dev/video1`처럼 장치를 두 개 생성할 수 있으므로 두 번째 웹캠 영상 장치가 `/dev/video2`일 수 있습니다.

## 빌드

패키지를 ROS 2 워크스페이스의 `src` 폴더에 복사합니다.

```bash
cd ~/ros2_ws
colcon build --packages-up-to detect_cctv --symlink-install
source install/setup.bash
```

## 웹캠 2대 동시 실행

```bash
ros2 run detect_cctv detect_cctv_node
```

인자 없이 실행하면 `detect_cctv_node.py`에 선언된 기본 설정을 사용합니다.

다른 터미널에서 시작 신호를 발행합니다.

```bash
ros2 topic pub --once \
  /ui/start std_msgs/msg/Bool "{data: true}"
```

중지 신호는 다음과 같습니다.

```bash
ros2 topic pub --once \
  /ui/start std_msgs/msg/Bool "{data: false}"
```

## 토픽 확인

```bash
ros2 topic list | grep cctv
```

```bash
ros2 topic echo /detection/status
```

영상은 다음 명령으로 확인할 수 있습니다.

```bash
ros2 run rqt_image_view rqt_image_view
```

## 장치 번호 변경

`detect_cctv_node.py`에서 다음 기본값을 실제 장치 번호에 맞게 수정합니다.

```python
self.declare_parameter("camera_devices", ["0", "2"])
self.declare_parameter("camera_ids", ["cctv1", "cctv2"])
```
