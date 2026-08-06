# detect_cctv_node 코드 리뷰 발표 가이드

## 1. 노드 목적

웹캠 두 대를 CCTV로 사용하고, YOLO를 통해 화재·연기·냉각수 누수를 감지한다. 탐지 영상은 UI(main)로 발행하고, 이상 상태는 detect_main_node로 발행한다.

## 2. 핵심 설계

하나의 `detect_cctv_node.py`와 하나의 ROS 2 노드가 카메라 컨텍스트 두 개를 관리한다. `camera_devices`와 `camera_ids` 배열을 같은 순서로 연결하여 CCTV별 퍼블리셔와 상태를 분리한다.

YOLO 모델은 한 번만 로드하여 메모리 사용량을 줄이고, 카메라별 캡처·퍼블리셔·최근 감지 상태는 독립적으로 유지한다.

## 3. 처리 흐름

```text
/ui/start=True 수신
→ 초기 이상 상태 False 발행
→ 웹캠 프레임 읽기
→ YOLO 추론
→ 검출 클래스 확인
→ Bounding Box가 표시된 영상 생성
→ /detection/cctv1/detection_image 또는 /detection/cctv2/detection_image 발행
→ 화재·연기·냉각수 누수 상태 비교
→ 상태가 바뀐 경우에만 통합 CctvStatus 토픽 발행
```

`/ui/start=False`를 받으면 프레임 처리와 영상 발행을 중지하고, 남아 있는
이상 상태를 False로 초기화한다.

## 4. 주요 토픽

구독 토픽은 `/ui/start` (`std_msgs/msg/Bool`)이며 UI가 전체 CCTV 작업의
시작과 중지를 제어한다.

| 구분 | 토픽 | 메시지 타입 |
|---|---|---|
| CCTV 1 영상 | `/detection/cctv1/detection_image` | `sensor_msgs/msg/Image` |
| CCTV 2 영상 | `/detection/cctv2/detection_image` | `sensor_msgs/msg/Image` |
| 전체 이상 상태 | `/detection/status` | `detect_cctv_interfaces/msg/CctvStatus` |

이상 상태 메시지는 `camera_id`(0: CCTV1, 1: CCTV2), `state`(0: 화재,
1: 연기, 2: 냉각수 누수), `detected`(감지 시작/해제)로 구성된다. 모든 상태가
`/detection/status` 하나로 전달되므로 구독 노드는 Subscription 하나만 만들면 된다.

## 5. QoS 설명

CCTV1과 CCTV2의 탐지 영상 Publisher에만 공통 `image_qos`를 적용한다.
오래된 영상의 재전송보다 최신 프레임 전달을 우선하도록 `BEST_EFFORT`,
`KEEP_LAST`, `depth=1`로 설정한다. 시작 신호와 이상 상태는 별도 QoSProfile
없이 각각 기본 QoS의 큐 크기 1과 10을 사용한다.

## 6. 중복 발행 방지

`last_status`에 직전 상태를 저장한다. 현재 상태와 직전 상태가 같으면 발행하지 않고, `False → True` 또는 `True → False`로 바뀔 때만 발행한다.

이를 통해 동일한 화재가 계속 감지되는 동안 메인 노드에 같은 경보 명령이 반복 전달되는 것을 줄인다.

## 7. 예외 처리

카메라 프레임을 읽지 못하면 YOLO 추론을 건너뛴다. 로그 도배를 막기 위해 카메라 경고는 5초마다 한 번만 출력한다. 프레임 단위 추론 오류는 로그를 남기고 다음 Timer 주기에 다시 시도한다.

## 8. 발표 시 언급할 개선 가능점

현재 상태 판정은 프레임 한 장을 기준으로 한다. 실제 환경에서는 조명 변화나 순간 오탐으로 상태가 빠르게 바뀔 수 있으므로, 연속 N프레임 이상 검출 시 True로 전환하는 디바운싱 로직을 추가할 수 있다.

추후 `CctvStatus`에 검출 confidence나 위치 정보를 추가하면 기록과 확장이 쉬워진다.

## 9. 기존 패키지에 합칠 때 확인

`detect_cctv_node.py`의 `get_package_share_directory("detect_cctv")`를 실제 기존 패키지 이름으로 변경하고, 코드 기본값인 `camera_devices`와 `camera_ids` 배열 길이를 동일하게 설정해야 한다.
