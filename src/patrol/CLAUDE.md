# patrol 패키지 — AI 인수인계서

DBcenter-PT-Bot의 AMR Control Node(순찰 담당 영역) 구현 상황 정리. 이 문서는
다음에 이 코드를 이어받는 AI(또는 사람)가 처음부터 대화를 복기하지 않고도
설계 의도와 현재 상태를 파악하게 하는 것이 목적.

## 이 노드가 하는 일 (목표)

Fleet Node로부터 순찰 좌표 리스트를 받아, Nav2와 연동해 AMR을 좌표 순서대로
이동시키는 ROS2(ament_python, Humble) 노드. 리스트를 다 돌면 도킹 스테이션에
복귀해 대기하다 다시 순찰을 나가고, 비전(CCTV)이 이상을 감지해 Fleet 경유로
중단 신호가 오면 진행 중이던 지점을 기억해뒀다가 나중에 그 자리부터 재개한다.

## 파일 구조와 책임 분리 (중요한 설계 원칙)

이 구조는 사용자가 명시적으로 요구한 원칙에 따라 나뉘어 있다:
**"진짜 외부 시스템(Fleet, Nav2)과의 통신"과 "patrol 자신의 내부 동작"을
분리한다.** Dock/Undock 액션과 대기 Timer는 ROS 메시지가 오간다는 점에서
기술적으로는 통신이지만, 로봇이 "자기 자신을 정지/재개시키는" 내부 동작으로
간주해 patrol_node.py가 아니라 내부 로직 쪽에 둔다.

```
patrol/
├── patrol/
│   ├── patrol_node.py   # ROS Node. Fleet/Nav2 통신 + 배선(wiring)만.
│   ├── run_patrol.py    # PatrolController. 순찰 시퀀싱 순수 로직 (rclpy 비의존).
│   └── stop_patrol.py   # StopController. Dock/Undock/Timer 실제 ROS 자원 관리.
├── setup.py / package.xml / setup.cfg / resource/  # ament_python 표준 스캐폴딩
```

### patrol_node.py — ROS Node, 통신 계층
- `robot_id`(기본 1), `standby_duration_sec`(기본 **10.0초, 테스트용** — 실제
  1시간 운용 시 3600.0으로 바꿔야 함) 파라미터 선언.
- `StopController`를 먼저 만들고 `PatrolController`를 만들어 서로 콜백으로
  엮는다(순서 중요 — 아래 "두 컨트롤러 배선" 참고).
- Fleet 통신 진입점 3개 (지금은 실제 구독/서비스/액션이 없어 **직접 호출하는
  형태의 스텁**, 테스트 스크립트에서 이 메서드들을 직접 호출해서 검증함):
  - `receive_patrol_route(patrol_points, point_type)` → `controller.receive_patrol_route(...)`
  - `receive_abort_signal()` → `controller.handle_abort_signal()` 실행 후 `_publish_patrol_stopped()`(Fleet 발행, 지금은 로그만)
  - `receive_resume_signal()` → `controller.resume_from_saved_point()`
- `_send_navigate_goal(point)` — Nav2 NavigateToPose 연동 예정 자리. **지금은
  로그만 찍는 스텁**이고, 실제로 로봇이 움직이지 않는다.
- `destroy_node()`에서 `stop_controller.shutdown()`으로 타이머 정리.

### run_patrol.py — PatrolController, 순찰 시퀀싱
rclpy에 의존하지 않는 순수 파이썬 클래스. `navigate_fn`, `enter_standby_fn`,
`logger`를 생성자 주입으로 받아 실제 통신은 호출만 하고 로직만 담당 —
그래서 `PatrolController(navigate_fn=print, enter_standby_fn=print, logger=...)`
처럼 ROS 없이도 단위 테스트 가능.

상태:
- `patrol_points`: Fleet가 준 전체 마스터 리스트 (재순찰 시 재사용, 안 바뀜)
- `save_way_point`: 아직 방문 안 한 지점 큐 (앞에서부터 `pop(0)`)
- `_current_point`: 방금 `navigate_fn`으로 보냈지만 Nav2 도착 확인 전인 지점.
  중단 신호가 오면 도착 여부를 모르므로 재시도 대상으로 남겨두기 위해 추적함.
- `is_patrolling`: 전체 순찰 상태 플래그

핵심 메서드:
- `receive_patrol_route(points, point_type)` — 리스트 세팅 후 `_advance_patrol()`
- `_advance_patrol()` — **핵심 분기**: `save_way_point`에서 다음 지점을
  pop → 있으면 `navigate_fn(point)` 호출, 없으면 `is_patrolling=False` +
  `enter_standby_fn()` 호출 (도킹 스테이션 복귀 트리거)
- `on_navigate_result(success)` — Nav2가 (연동되면) 이 메서드를 호출해줘야
  다음 지점으로 이어짐. **지금은 아무도 호출하지 않음** (Nav2 스텁이라서).
- `resume_from_start()` — StopController가 언도킹 완료 후 호출. `save_way_point`를
  `patrol_points`로 리셋해 **처음부터** 재개.
- `handle_abort_signal()` — 순찰 중일 때만 동작. `_current_point`를
  `save_way_point` 맨 앞에 복원하고 `is_patrolling=False`. **도킹은 트리거하지
  않음** — 리스트 소진에 의한 정상 종료와 별개의 비상 경로.
- `resume_from_saved_point()` — `resume_from_start()`와 달리 리스트를 리셋하지
  않고 **남아있던 `save_way_point` 그대로** 이어감 (중단됐던 자리부터 재개).

### stop_patrol.py — StopController, 도킹/대기/언도킹
`node`(rclpy Node 인스턴스), `robot_id`, `standby_duration_sec`, `on_resume`
콜백, `logger`를 주입받는다. ActionClient/Timer 생성에 Node 핸들이 필요해서
patrol_node.py가 `self`를 넘겨준다.

- 액션 이름: `/robot{robot_id}/dock`, `/robot{robot_id}/undock`
  (irobot_create_msgs, 소문자 — 옆 워크스페이스 `turtlebot4_navigation/
  turtlebot4_navigator.py`의 실제 사용 패턴을 참고해 맞춤. `Dock.Goal()` /
  `Undock.Goal()`은 빈 goal, 필드 없음)
- `enter_standby()` → `_send_dock_goal()` → (성공) `_start_standby_timer()`
  → `self._node.create_timer(standby_duration_sec, ...)`로 대기 →
  (타이머 만료) `_send_undock_goal()` → (성공) `self._on_resume()` 호출
- 도킹/언도킹 액션 서버를 못 찾거나 실패하면 로그만 남기고 **그 상태로 멈춤**
  (재시도/알림 로직 없음 — TODO)

### 두 컨트롤러 배선 (patrol_node.py `__init__`)
`PatrolController`(소진 시 `enter_standby` 호출 필요)와 `StopController`(재개
시 `resume_from_start` 호출 필요)가 서로를 참조해야 해서 순환 의존이 생긴다.
해결: `StopController`를 먼저 만들고, 그 `on_resume`은 아직 없는
`self.controller`를 `lambda: self.controller.resume_from_start()`로 지연
참조 → 이후 `PatrolController` 생성 시 `enter_standby_fn=self.stop_controller.enter_standby`는
이미 존재하는 객체라 바로 바인딩 가능.

## 상태 흐름 3가지 시나리오

1. **정상 순찰 → 소진 → 대기 → 재개(처음부터)**
   `receive_patrol_route` → `_advance_patrol` 반복(Nav2 스텁) → 소진 →
   `enter_standby` → Dock → 타이머(대기) → Undock → `resume_from_start`
2. **이상 신호 → 중단 → 저장**
   `receive_abort_signal` → `handle_abort_signal`(진행 중이던 지점을
   `save_way_point` 맨 앞에 보존, `is_patrolling=False`, 도킹 안 함) →
   `_publish_patrol_stopped`(Fleet 발행, 스텁)
3. **복귀 신호 → 저장 지점부터 재개**
   `receive_resume_signal` → `resume_from_saved_point`(리스트 리셋 안 하고
   남은 `save_way_point`부터 이어감)

## 확정된 설계 결정과 근거

- **Fleet 메시지 스펙**: `patrol_points: geometry_msgs/Point[]` +
  `point_type: string`. 처음엔 `Pose2D[]`로 잘못 가정했다가 사용자가 정정함.
  `point_type`은 리스트 전체에 대해 **단일 문자열**로 가정(사용자가 배열
  여부를 명확히 확정하진 않았고, 스펙 표기 그대로 단일값으로 해석 — 지점별로
  달라야 하면 배열로 바꿔야 함).
- **"순찰 노드를 종료"의 의미**: 프로세스(rclpy 노드) 종료가 아니라 "현재
  진행 중인 순찰 활동의 논리적 중단"으로 해석함. 나중에 복귀 신호로 이어서
  진행한다는 요구사항과, 프로세스를 죽이면 state/구독이 다 날아간다는 점에서
  이 해석이 아니면 앞뒤가 안 맞음. **확인은 안 받았고 합리적 추론으로
  진행한 가정**이니 다르면 정정 필요.
- **Dock/Undock 액션 이름**: `/robot{id}/dock`(소문자). 사용자는 원래
  `/robot{id}/Dock`(대문자)이라 적었으나, 실제 irobot_create_msgs/TurtleBot4
  표준 액션 서버 이름 규칙(소문자)을 따름. 로봇 실기 배포 시 실제 네임스페이스와
  액션명이 이것과 일치하는지 확인 필요.
- **목표 지점 방향(orientation)**: `geometry_msgs/Point`엔 방향 정보가 없는데
  Nav2 `NavigateToPose`는 `PoseStamped`(위치+방향)를 요구함. 로컬라이제이션은
  TurtleBot4의 IMU+라이다 기반 AMCL이 담당(사용자 확인). 하지만 "도착 시 어느
  방향을 볼지" 전략은 **아직 미정** — Nav2 담당 팀원과 논의 필요. 지금은 Nav2
  연동 자체가 스텁이라 이 부분 코드도 아직 없음.

## 미구현 / TODO 목록 (우선순위 대략순)

1. **Nav2 NavigateToPose 실제 연동** (`patrol_node.py::_send_navigate_goal`) —
   ActionClient 생성, goal 전송, 완료 시 `controller.on_navigate_result(success)`
   호출. Dock/Undock의 `send_goal_async → 수락확인 → get_result_async → 결과확인`
   3단 콜백 패턴을 그대로 재사용하면 됨 (`stop_patrol.py` 참고).
2. **목표 방향(orientation) 결정 전략** — 위 참고. identity 고정 / 이동방향
   자동정렬 / 기타 중 미정.
3. **이상 신호 시 진행 중인 Nav2 goal 취소** — `handle_abort_signal`은 지금
   `save_way_point`만 갱신하고, 실제 Nav2 goal을 cancel하지 않는다(Nav2가
   없어서). Nav2 연동 시 `cancel_goal_async` 호출 추가 필요
   (`run_patrol.py::on_navigate_result` 독스트링에 TODO 남겨둠).
4. **Fleet 실제 인터페이스 확정** — 토픽/서비스/액션 타입과 이름이 아직 없음.
   확정되면 `patrol_node.py`에 구독/서버를 만들고 콜백에서
   `receive_patrol_route`/`receive_abort_signal`/`receive_resume_signal`을
   그대로 호출하면 됨 (진입점은 이미 준비돼 있음).
5. **Fleet로 종료 신호 발행하는 실제 퍼블리셔/서비스/액션** —
   `_publish_patrol_stopped`가 로그만 찍는 스텁.
6. **point_type 활용처** — 지금은 저장만 하고 아무 데도 안 씀. 원래 설계
   노트에 있던 `arm_move`(지점별 로봇팔 동작?)와 연관될 수 있는데 스펙 미확정.
7. **Nav2 이동 실패 / Dock·Undock 실패 시 복구 전략** — 지금은 전부 로그만
   남기고 그대로 멈춤. 재시도, 알림, 다른 상태로의 전이 등 없음.

## 파라미터

| 이름 | 기본값 | 비고 |
|---|---|---|
| `robot_id` | `1` | 액션 이름(`/robot{id}/dock` 등)에 사용 |
| `standby_duration_sec` | `10.0` | **테스트용**. 실제 1시간 운용 시 `3600.0`으로 변경 |

## 테스트 방법

- ROS 환경 소싱 필요: `source /opt/ros/humble/setup.bash` (이 workspace는
  아직 colcon build 안 돼 있음, 소스만 존재)
- `run_patrol.py`의 `PatrolController`는 rclpy 없이 콜백에 `print`만 꽂아도
  단위 테스트 가능.
- Dock/Undock까지 포함한 전체 사이클은 `rclpy.action.ActionServer`로 가짜
  Dock/Undock 서버를 띄워 통합 테스트함 (세션 스크래치패드에 스크립트로
  작성해서 검증만 하고 repo에는 커밋 안 함 — 필요하면 비슷하게 재작성).
  검증 완료된 케이스: 정상 소진→도킹→대기(단축)→언도킹→재개(처음부터),
  이상신호 중단(진행중 지점 보존)→복귀(저장지점부터 재개).

## 참고: 사고 이력

작업 시작 시 `patrol_node.py`에 실수로 OpenAI API 키로 보이는 문자열이 코드
대신 들어있던 걸 발견함 (git 미추적 상태). 사용자 확인 받고 덮어씀. 실사용
중인 키였다면 폐기(rotate) 권장 — 확인은 안 했음.
