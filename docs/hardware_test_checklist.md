# 하드웨어 테스트 체크리스트 (Fleet ↔ Control 통합)

`fleet-control` 브랜치, 커밋 `3161ad0` + 상태 토픽 수정(이 문서 작성 시점 기준) 이후
기준. 실제 로봇(`robot3`, `robot8`)으로 처음 실행해보는 테스트라 순서대로,
안전도가 낮은 것부터 진행하는 걸 권장합니다.

## 사전 준비

### 통신(DDS) 사전 점검 — 노드 켜기 전에 먼저

지난 테스트(2026-08-09)에서 긴급정지 발행이 fleet_node에 배달되지 않는 문제가
있었음: `emergency_stop.sh`의 `ros2 topic pub --once`는 매칭 구독자를 찾아
발행까지 했는데(매칭이 없으면 `Waiting for at least 1 matching
subscription(s)...`에서 멈춰 있어야 함), fleet_node에는 수신 로그가 전혀 안
찍혔고, 같은 시점에 `ros2 topic info /backend/emergency_stop_all -v`는 토픽이
존재하지 않는다고 나옴. fleet_node는 이 토픽을 받으면 어떤 경우에도 로그를
남기게 돼 있으므로(`emergency stop-all triggered` 또는 `no registered robot`)
**로그가 없다 = 코드가 아니라 전송 계층에서 유실된 것.**

`ros2 topic pub`은 자기가 직접 DDS 디스커버리를 하고, `ros2 topic info/list`는
백그라운드 **ros2 데몬**의 캐시를 보므로 둘이 다르게 보이면 데몬/환경 문제다.
테스트에 쓰는 **모든 터미널**(fleet_node 터미널, 스크립트 터미널, 각 로봇
노트북)에서 순서대로:

```bash
# 1) 터미널 간 환경 일치 확인 - 셋 다 모든 터미널에서 같아야 함
echo "DOMAIN=$ROS_DOMAIN_ID LOCALHOST=$ROS_LOCALHOST_ONLY RMW=$RMW_IMPLEMENTATION"

# 2) 데몬 캐시 초기화 (stale 데몬이 엉터리 topic list를 보여주는 것 방지)
ros2 daemon stop && ros2 daemon start

# 3) 서로 보이는지 확인 - 스크립트 머신에서 fleet_node가 보여야 함
ros2 node list
```

- [ ] 환경 변수 3종이 모든 터미널에서 동일한지 확인 (`.bashrc`에 로봇용
      도메인 설정이 있는 머신에서 새 터미널을 열면 조용히 어긋나기 쉬움)
- [ ] 데몬 재시작 후 `ros2 node list`에 상대 머신 노드가 보이는지 확인
- [ ] 그래도 안 보이면 네트워크 계층 순서대로:
  - `sudo ufw status` — 방화벽이 켜져 있으면 디스커버리(멀티캐스트)만 통과하고
    유저 데이터(유니캐스트 UDP)가 막히는 조합이 가능함. 테스트 중엔 끄는 게 확실
  - 유선+무선 인터페이스가 같이 켜져 있으면 DDS가 상대가 못 닿는 인터페이스
    IP를 광고할 수 있음 — 테스트 네트워크가 아닌 쪽은 내릴 것
  - 한쪽에서 `ros2 multicast receive`, 다른 쪽에서 `ros2 multicast send`로
    멀티캐스트 통과 확인
- [ ] 발행이 실제로 배달됐는지는 **fleet_node 로그로 판정**할 것 —
      `ros2 topic pub`의 "publishing #1" 출력은 디스커버리 매칭까지만 증명하고
      데이터 배달은 증명하지 못함. 매칭된 구독자가 어딘가 열려 있던
      `topic echo` 창일 수도 있으니, 의심되면
      `ros2 topic info <topic> -v`로 구독 노드 이름까지 확인

- [ ] `fleet` 패키지, `control_amr` 패키지 최신 상태로 colcon build
- [ ] `fleet_node` 1개 실행 (도메인 어디서든 무관) — 실행하면 바로 순찰이
      시작되지 않고 터미널에 "스페이스바를 누르면 순찰을 시작합니다..."가
      뜬 채로 대기함. `robot3`/`robot8`을 원하는 시작 위치에 정렬해두고
      준비되면 그 터미널에서 **스페이스바**를 눌러야 `DEFAULT_ZONES` 미션이
      실제로 발행됨 (`fleet_node`를 실행하는 터미널이 tty가 아니면 이 대기를
      건너뛰고 바로 시작함)
- [ ] `robot3`, `robot8` 각각 TurtleBot4 bringup + Nav2/AMCL + `control_node` 실행
- [ ] `/backend/map_points`로 구역 데이터 발행해서 미션이 실제로 흐르는지 확인
      (안 하면 `DEFAULT_ZONES`로 시작하므로 생략 가능 — 이 경우도 스페이스바
      대기와 무관하게 즉시 적용됨, Backend 데이터가 항상 우선)
- [ ] `ros2 topic list`로 아래 토픽이 다 떠 있는지 확인
  - `/fleet/robot3/mission`, `/fleet/robot8/mission`
  - `/fleet/robot3/emergency_stop`, `/fleet/robot8/emergency_stop`
  - `/fleet/robot3/dock`, `/fleet/robot8/dock`
  - `/fleet/robot3/anomaly`, `/fleet/robot8/anomaly`
  - `/fleet/robot3/anomaly_resume`, `/fleet/robot8/anomaly_resume`
  - `/fleet/anomaly_trigger`, `/fleet/anomaly_done`
  - `/backend/emergency_stop_all`, `/backend/dock`, `/backend/map_points`,
    `/backend/anomaly_resume`
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

⚠️ 비전 노드 자동 판정 방식에서 **운영자 결정 방식으로 설계가 바뀌었습니다**
(사용자 확인 결과) — 로봇이 이상 위치에 도착하면 자동으로 판정하지 않고
그 자리에서 카메라로 상황을 계속 비추며 무기한 대기합니다. 운영자가
HMI에서 그 영상을 보고 "재개"(`/backend/anomaly_resume`) 또는
"도킹"(`/backend/dock`, 기존 도킹 복귀와 동일 경로) 중 하나를 보내야
움직입니다.

```
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String "{data: '{\"robot\": \"robot3\"}'}"
```

- [ ] 로봇이 제자리에 멈추고, 자기 현재 위치를 이상 위치로 쓰는지
- [ ] `anomaly_moving` → `anomaly_waiting` 상태 순서로 찍히는지
- [ ] `anomaly_waiting` 상태에서 계속 대기하는지(예전처럼 15초 뒤
      자동으로 순찰 복귀하면 안 됨)

재개 결정:
```
ros2 topic pub --once /backend/anomaly_resume std_msgs/msg/String "{data: '{\"robot\": \"robot3\"}'}"
```
- [ ] `patrol_resuming` 상태가 찍히고 순찰로 정상 복귀하는지

도킹 결정 (재개 대신 이걸 보내는 경우):
```
ros2 topic pub --once /backend/dock std_msgs/msg/String "{data: '{\"robots\": [\"robot3\"]}'}"
```
- [ ] `anomaly_waiting` 대기 중에도 도킹 복귀가 바로 반응하는지 (시나리오
      3의 도킹 경로를 그대로 이어받음 - 그래프 라우팅 + occupancy 보호)

### 5. 이상신호 — CCTV 감지

CCTV 좌표는 랙 안쪽 등 로봇이 못 가는 지점일 수 있어서, 로봇은 그
좌표로 직행하지 않고 **통로 그래프 위 가장 가까운 지점까지만** 이동한
뒤 카메라(정면)를 원래 좌표 쪽으로 돌린다(`RouteGraph.nearest_point()`/
`anomaly_control.snap_cctv_location()`). 통로 그래프 밖 좌표로 테스트해야
의미 있다 - 예: 아래 명령은 랙 안쪽으로 가정한 좌표.

```
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String "{data: '{\"x\": -2.7, \"y\": 1.5}'}"
```

- [ ] 순찰 중 + 이상신호 대응 중 아님 + 긴급정지 중 아님인 로봇 중
      **가장 가까운 로봇**이 급파되는지 (로봇 2대 다 순찰 중일 때 테스트해야
      의미 있음)
- [ ] 로봇이 좌표(-2.7, 1.5)로 직행하지 않고, 통로 그래프 경로(홉별
      `crossing_wait`/`crossing_granted` 로그)를 따라 이동하는지
- [ ] 도착 지점이 통로 그래프 위(예: V_BC 통로 근처)이고, 로봇이 그
      지점에서 원래 좌표(-2.7, 1.5) 방향을 보고 서는지(yaw 확인)
- [ ] 도착 후 시나리오 4와 동일하게 `anomaly_waiting`에서 무기한
      대기하는지, `anomaly_resume`/`dock`으로 정상 종료되는지
- [ ] 대응 중인 로봇에게 새 이상신호 트리거를 또 보내면 무시되는지
      (`_anomaly_busy`에 남아있는 동안은 급파 후보에서 제외 - `anomaly_waiting`
      대기 중에도 아직 `anomaly_done`을 안 보냈으니 계속 제외 상태여야 함)

### 6. 인터럽트 우선순위 충돌 테스트

`_move_to()` 우선순위: `emergency_stop > collision_risk > dock > anomaly`

- [ ] 이상신호 급파 **이동 중**에 긴급정지를 걸면 즉시 멈추고, 해제하면
      같은 목표로 이동을 재개하는지
- [ ] 이상신호 도착 후 **`anomaly_waiting` 대기 중**에 긴급정지를 걸어도
      반응하는지(멈췄다가 해제되면 대기 계속) — 이동 중과는 다른
      코드 경로라 별도로 확인 필요
- [ ] `anomaly_waiting` 대기 중에 `dock`을 보내면 재개 대신 도킹으로
      전환되는지 (`anomaly_resume` 대신 `dock`을 보내는 경우)
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
