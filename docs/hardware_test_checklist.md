# 하드웨어 테스트 체크리스트 (Fleet ↔ Control 통합)

`fleet-control` 브랜치, 커밋 `3161ad0` + 상태 토픽 수정(이 문서 작성 시점 기준) 이후
기준. 실제 로봇(`robot3`, `robot8`)으로 처음 실행해보는 테스트라 순서대로,
안전도가 낮은 것부터 진행하는 걸 권장합니다.

## 사전 준비

- [ ] `fleet` 패키지, `control_amr` 패키지 최신 상태로 colcon build
- [ ] `fleet_node` 1개 실행 (도메인 어디서든 무관)
- [ ] `robot3`, `robot8` 각각 TurtleBot4 bringup + Nav2/AMCL + `control_node` 실행
- [ ] `/backend/map_points`로 구역 데이터 발행해서 미션이 실제로 흐르는지 확인
      (안 하면 `DEFAULT_ZONES`로 시작하므로 생략 가능)
- [ ] `ros2 topic list`로 아래 토픽이 다 떠 있는지 확인
  - `/fleet/robot3/mission`, `/fleet/robot8/mission`
  - `/fleet/robot3/emergency_stop`, `/fleet/robot8/emergency_stop`
  - `/fleet/robot3/dock`, `/fleet/robot8/dock`
  - `/fleet/robot3/anomaly`, `/fleet/robot8/anomaly`
  - `/fleet/anomaly_trigger`, `/fleet/anomaly_done`
  - `/backend/emergency_stop_all`, `/backend/dock`, `/backend/map_points`
  - `/control/robot3_State`, `/control/robot8_State` ← **이번에 고친 부분, 0번에서 확인**
  - `/robot3/amcl_pose`, `/robot8/amcl_pose`

## 시나리오 순서

### 0. 상태 토픽 배선 확인 (신규 수정, 가장 먼저)

이전에는 `control_node`가 `/fleet/<ns>/state`라는 아무도 구독하지 않는 토픽에
상태를 발행하고 있어서, Fleet UI에는 Control의 세부 상태가 전혀 반영되지
않는 버그가 있었습니다. `state_flow.py`를 `/control/<ns>_State`로 발행하도록
고쳤고(Fleet의 `robot_status.py`와 같은 토픽 공유), 페이로드 키도 `'state'` →
`'status'`로 맞췄습니다. **이 변경 자체가 실행 검증이 안 된 상태이므로 가장
먼저 확인해주세요.**

```
ros2 topic echo /control/robot3_State
```

- [ ] 로봇이 미션을 받아 움직이는 동안, Fleet이 보내는 대략상태
      (`PATROLLING` 등)뿐 아니라 Control이 보내는 세부상태
      (`waiting_mission`, `gate_checking`, `crossing_wait` 등)도 **같은
      토픽**에서 번갈아 보이는지 확인
- [ ] 페이로드에 `"status"` 키로 오는지 확인 (예전엔 `"state"`였음 — UI나
      Backend가 이 키를 구독해서 파싱하는 쪽이 있다면 이 변경을 미리
      알려줄 것)

### 1. 긴급정지 전체 (`stop=true`)

```
ros2 topic pub --once /backend/emergency_stop_all std_msgs/msg/String "{data: '{\"stop\": true}'}"
```

- [ ] 순찰 중이던 로봇이 **즉시** 정지하는지 (콜백에서 바로 `cancelTask()`
      호출하도록 구현돼 있어 폴링 주기를 기다리지 않아야 함)
- [ ] `/control/<ns>_State`에 `emergency_stopped` 상태가 찍히는지
- [ ] Fleet 쪽 상태도 `EMERGENCY_STOP`으로 바뀌는지

### 2. 긴급정지 해제 (`stop=false`)

```
ros2 topic pub --once /backend/emergency_stop_all std_msgs/msg/String "{data: '{\"stop\": false}'}"
```

- [ ] 정지됐던 지점에서 **같은 웨이포인트**로 이동을 재시도하는지
- [ ] `patrol_resuming` 상태가 찍히는지
- [ ] 정상적으로 순찰이 재개되는지

### 3. 도킹 복귀 — ⚠️ 실제 실행 미검증, 가장 주의 깊게 지켜볼 것

```
ros2 topic pub --once /backend/dock std_msgs/msg/String "{data: '{\"robots\": [\"robot3\"]}'}"
```

`_handle_dock()`이 `navigator.dock()`(`irobot_create_msgs/action/Dock`)을
실제로 호출하는 게 이번이 처음입니다. 컴파일/코드 리딩으로만 검증됐습니다.

- [ ] `DOCK_STATIONS['robot3']` 좌표(`x=-4.3, y=1.8, yaw=0.0` — yaw는 실측
      전 임시값)로 이동하는지
- [ ] 도착 후 실제 도킹 액션이 걸리는지 (IR 비콘 기반 자동 접근이 정상
      동작하는지)
- [ ] 도킹 성공 후 로봇이 **도킹된 채로 계속 대기**하는지 (재순찰 안 하는 게
      의도된 동작 — 버그 아님, "알려진 제약" 참고)
- [ ] (여유 있으면) 도킹 스테이션 좌표를 일부러 못 도달하는 값으로 바꿔서
      실패 시 원래 웨이포인트로 복귀 시도하는지도 확인

### 4. 이상신호 — AMR 자체감지

```
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String "{data: '{\"robot\": \"robot3\"}'}"
```

- [ ] 로봇이 제자리에 멈추고, 자기 현재 위치를 이상 위치로 쓰는지
- [ ] `anomaly_moving` → `anomaly_checking` 상태 순서로 찍히는지

완료 신호:
```
ros2 topic pub --once /fleet/anomaly_done std_msgs/msg/String "{data: '{\"robot\": \"robot3\"}'}"
```
- [ ] 완료 후 순찰로 정상 복귀하는지

### 5. 이상신호 — CCTV 감지

```
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String "{data: '{\"x\": -2.33, \"y\": 0.0313}'}"
```

- [ ] 순찰 중 + 이상신호 대응 중 아님 + 긴급정지 중 아님인 로봇 중
      **가장 가까운 로봇**이 급파되는지 (로봇 2대 다 순찰 중일 때 테스트해야
      의미 있음)

### 6. 인터럽트 우선순위 충돌 테스트

`_move_to()` 우선순위: `emergency_stop > collision_risk > dock > anomaly`

- [ ] 이상신호 급파 이동 중에 긴급정지를 걸면 **즉시** 멈추는지
- [ ] 도킹 이동 중에 이상신호 트리거를 보내면 **무시**되는지 (dock이 anomaly
      보다 우선이어야 함)
- [ ] (팀 논의 필요) `collision_risk`가 `emergency_stop`보다 아래인 게
      맞는지 — 물리적 충돌 위험이 더 급한 것 아니냐는 반론 있음, 문서에도
      명시된 재검토 포인트

## 알려진 제약 (버그 아님 — 동작 확인만 하면 됨)

- 도킹 후 재순찰(언도킹) 트리거가 아직 없음 — 세션 내내 도킹 상태 유지가
  정상 동작
- `state_flow.py`가 발행하는 세부 상태 문자열과 Fleet의 대략 상태 문자열이
  대소문자 컨벤션이 다름(`docking` vs `PATROLLING`) — UI 쪽에서 두 컨벤션을
  다 받는 파싱이 되는지는 별도 확인 필요

## 테스트 중 기록

- 시나리오별 성공/실패, 예상과 다르게 동작한 지점의 타임스탬프와 로그
- 3번(도킹)에서 문제 생기면 `_handle_dock()`(`control_node.py`) 주변부터 확인
