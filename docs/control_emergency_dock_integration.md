# Control Node에 긴급정지 · 도킹 복귀 처리 추가

**대상 파일**: `src/control_amr/control_amr/control_node.py`
**작업자**: Claude (Fleet Node 쪽 작업자) — `fleet-control` 통합 브랜치에서 Lee 팀의
`control_node.py`에 직접 손을 댔습니다. 리뷰 부탁드립니다.
**배경**: `fleet` 브랜치가 `/fleet/<ns>/emergency_stop`, `/fleet/<ns>/dock` 두 토픽을
발행하도록 구현했는데(긴급정지 해제·도킹 복귀), `lee` 브랜치의 `control_node.py`에는
이 두 토픽을 구독하는 코드가 전혀 없어서 통합 테스트가 불가능한 상태였습니다. 이
문서는 그 갭을 메우기 위해 `control_node.py`에 어떤 코드를 추가/수정했는지 정리한
것입니다.

---

## 1. 무엇을 추가했나

### 1-1. 긴급정지 (`/fleet/<ns>/emergency_stop` 구독)

- `__init__`에 `self.emergency_stopped = False` 상태와 구독 추가
- `_on_emergency_stop(msg)` 신규: `{"stop": bool}` 파싱, `self.emergency_stopped`
  갱신. `stop=true`면 **콜백 안에서 바로** `cancelTask()` 호출 (폴링 주기까지 기다리지
  않고 즉시 정지 명령이 나가야 해서)
- `_move_to()`에 `emergency_stopped` 체크 2곳 추가:
  - `goToPose()` 호출 **전**: 이미 정지 상태면 이동 명령 자체를 내보내지 않음
  - 폴링 루프 안: 이동 중 정지 신호가 오면 취소하고 인터럽트로 처리 (기존
    collision_risk/anomaly 체크와 같은 자리, 우선순위상 **가장 먼저** 체크)
- `_handle_emergency_stop()` 신규: `emergency_stopped`가 `False`로 돌아올 때까지
  제자리에서 `spin_once()`로 대기하다가, 해제되면 중단됐던 웨이포인트로 이동을
  재시도 (`True` 반환 → `_run_current_mission()`의 `while not self._move_to(pose):`
  루프가 같은 pose로 재시도)
- `_handle_navigation_interrupt()`에 `'emergency_stop'` 분기 추가 (다른 분기들보다
  먼저 체크)

### 1-2. 도킹 복귀 (`/fleet/<ns>/dock` 구독)

- `__init__`에 `self.dock_pending`, `self.dock_location`, `self.docked` 상태와 구독
  추가
- `_on_dock(msg)` 신규: `{"x","y","yaw"}` 파싱 (기존 `_is_valid_pose()` 검증 로직
  그대로 재사용), `dock_pending`/`dock_location` 세팅
- `_move_to()` 폴링 루프에 `dock_pending` 체크 추가 (collision_risk 다음, anomaly
  보다 먼저 - 도킹은 운영자의 명시적 명령이라 자율 급파인 anomaly보다 우선)
- `_handle_dock()` 신규:
  1. `dock_location`으로 `startToPose()` + `navigation_flow.wait_until_pose_reached()`
     로 도착 대기 (이상신호 처리와 동일한 패턴 재사용)
  2. 도착 실패 시 실패 보고 후 원래 웨이포인트로 복귀 시도 (`True` 반환, 이상신호
     실패 처리와 동일한 방식)
  3. 도착 성공 시 `self.navigator.dock()` 호출 (실제 `irobot_create_msgs/action/Dock`
     액션)
  4. `self.docked = True` 세팅, `[]`(빈 리스트) 반환
- `_handle_navigation_interrupt()`에 `'dock'` 분기 추가
- `_on_mission()`에 가드 추가: **`self.docked`가 `True`면 새 미션 수신을 무시**
  (이유는 아래 3번 참고)

---

## 2. `_move_to()` 인터럽트 우선순위

기존에는 `collision_risk` → `anomaly` 순서였습니다. 이번에 추가하면서 다음 순서로
정렬했습니다:

```
emergency_stop > collision_risk > dock > anomaly
```

- `emergency_stop`을 최우선으로 둔 이유: Fleet 쪽 `robot_status.py`의 상태 판정
  우선순위(EMERGENCY_STOP > DISPATCHING > PATROLLING > IDLE)와 맞추기 위함입니다.
  Fleet이 이미 "긴급정지가 다른 모든 활동보다 우선"이라는 전제로 설계돼 있어서,
  Control 쪽도 같은 전제를 따르는 게 일관적이라고 판단했습니다.
- `dock`을 `anomaly`보다 위에 둔 이유: 도킹 복귀는 운영자가 명시적으로 누른
  override 명령이고, anomaly는 자율 판단(비전 감지) 급파라서 사람이 개입한 쪽이
  우선이어야 한다고 판단했습니다.
- **collision_risk를 emergency_stop보다 아래에 둔 건 재검토가 필요할 수 있습니다.**
  물리적 충돌 위험이 더 급한 것 아니냐는 반론이 있을 수 있어서, 이 순서가 팀
  판단과 다르면 조정해주세요.

---

## 3. 왜 `_on_mission()`에 도킹 가드를 추가했나

Fleet의 `fleet_node.py`는 `_publish_missions()`를 **1초마다** 실행해서, 등록된 모든
로봇에게 자기 미션을 계속 재발행합니다(Control이 늦게 뜨는 경우를 대비한 설계).
문제는 이게 로봇의 현재 상태(도킹 중인지 아닌지)와 무관하게 무조건 나간다는
겁니다.

만약 `_on_mission()`이 이걸 그대로 받아버리면: 로봇이 도킹을 마치자마자(늦어도
1초 이내) 같은 미션이 다시 도착 → `self.mission`이 다시 채워짐 → 이후 미션 루프가
재순찰을 시작 → **재순찰 신호도 없이, 언도킹 호출도 없이 그냥 다시 움직이려고
시도**하는 버그가 생깁니다.

그래서 `self.docked`가 `True`인 동안은 `_on_mission()`이 새 미션을 조용히
무시하도록 가드를 걸었습니다. `_handle_dock()`이 성공하면 빈 리스트를 반환해서
현재 미션이 정상 종료 처리되고(`mission_aborted`가 아니라 `handle_mission_complete()`
경로), `run()`의 바깥 루프가 `wait_until_next_patrol()`(10분 타이머) →
`request_next_mission()` → `_wait_for_mission()` 순서로 흘러가는데, 이 전체 구간
동안 `_on_mission()`이 계속 무시하기 때문에 결과적으로 **로봇은 도킹된 채로
무한정 대기**하게 됩니다.

---

## 4. 알려진 미구현 / 확인이 필요한 부분

- **도킹 이후 재순찰(언도킹) 트리거가 없습니다.** Fleet 쪽에 "이 로봇 재순찰 시작해"
  같은 신호가 아직 없어서(원래 설계도의 "1시간 자동 순찰 재개"/"복귀명령 인식"이
  Fleet에 미구현), Control이 받을 신호 자체가 없습니다. 지금은 도킹하면 그
  세션에서는 계속 도킹 상태로 남습니다 - 다음 작업으로 Fleet 쪽에 로봇별
  재순찰(언도킹) 신호를 추가해야 합니다.
- **`_handle_dock()`이 컴파일/문법 검증만 됐고, 실제 `TurtleBot4Navigator`로
  실행해보진 못했습니다.** 이 환경에 ROS 2 Humble 자체는 설치돼 있지만
  (`rclpy`/`std_msgs`/`turtlebot4_navigation` 전부 import는 됨), 실제 로봇이나
  시뮬레이터가 없어서 `navigator.dock()` 호출이 실제로 어떻게 동작하는지는 확인 못
  했습니다. 하드웨어/시뮬레이터에서 검증 부탁드립니다.
- **`ControlNode` 자체는 자동 테스트가 없습니다** (기존에도 없었음).
  `test_flow_logic.py`가 `FakeNavigator`로 `MissionFlowSupport`/`StateFlowSupport`
  같은 순수 로직 클래스는 테스트하지만, `ControlNode.__init__`이
  `TurtleBot4Navigator`를 직접 생성해서(주입식이 아님) 같은 방식으로 테스트하기
  어렵습니다. 기존 컨벤션을 그대로 따랐고, 별도로 리팩터링하진 않았습니다.
- **(발견한 별개 이슈, 이번에 안 건드림)** `state_flow.py`가 로봇 상태를
  `/fleet/<ns>/state`에 발행하는데, Fleet의 `robot_status.py`는
  `/control/<ns>_State`를 UI용으로 발행합니다. 두 토픽이 이름이 달라서 서로
  구독하는 관계가 아닌 것 같은데, 원래 의도된 설계인지 확인이 필요해 보입니다.

---

## 5. 검증 상태

- `python3 -m py_compile control_node.py` 통과
- 기존 `test_flow_logic.py` 4개 테스트 전부 통과 (제가 건드리지 않은
  `mission_flow.py`/`state_flow.py`라 당연하지만, 회귀 확인 차원에서 재실행함)
- 신규 로직(`_handle_emergency_stop`, `_handle_dock`, `_on_emergency_stop`,
  `_on_dock`)은 **코드 리딩/수동 트레이스로만 검증**했고 실제 실행 검증은 못 했음

## 6. 관련 Fleet 쪽 코드

- `src/fleet/fleet/fleet_node.py` - `/fleet/<ns>/emergency_stop`,
  `/fleet/<ns>/dock` 발행부
- `src/fleet/fleet/dock_control.py` - 도킹 대상 판정 (긴급정지 중인 로봇도 대상에
  포함시키고 암묵 해제하는 정책)
- `docs/fleet_node_pipeline.drawio` - Fleet 쪽 파이프라인 다이어그램 (섹션 ⑨, ⑩)
