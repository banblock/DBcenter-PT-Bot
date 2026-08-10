# Fleet ↔ Control 통합 코드 리뷰 — 알려진 문제 11가지

`fleet-control` 브랜치(커밋 `5d7c8c2` 기준) 코드 리뷰에서 찾은 것. 하드웨어
테스트 전에 통신 배선/알고리즘 관점으로 `src/fleet/`와
`src/control_amr/` 전체를 다시 읽으면서 정리했다. 심각도는 "지금 구성
(비전 노드 없음, DEFAULT_ZONES에 gate 없음)에서 실제로 터질 확률"
기준으로 나눴다.

---

## 심각 — 하드웨어 테스트에서 바로 터질 수 있음

### 1. ~~이상신호 실패 시 `anomaly_done`이 영영 안 나가서 로봇이 Fleet에서 영구 busy가 됨~~ — **해결됨**

`_handle_anomaly()`의 도착 실패/점검 실패 경로 모두에서 `_publish_anomaly_done()`
(신규 helper, `confirmed: null` + `failed: true`)을 호출하도록 수정. Fleet의
`_on_anomaly_done()`은 `robot` 필드만 보고 `_anomaly_busy`에서 제거하므로 그대로
호환됨.

<details><summary>원본 문제 설명</summary>

**대상**: `src/control_amr/control_amr/control_node.py:303` (`_handle_anomaly`),
`src/control_amr/control_amr/inspection_flow.py:84` (`inspect_anomaly`)

`_handle_anomaly()`는 도착 실패(`anomaly_arrival_failed`)나 점검 실패
(`anomaly_check_failed`)면 `anomaly_done`을 발행하지 않고 그냥 `return`한다.
그런데 지금 저장소에는 비전 노드가 없어서 `inspect_anomaly()`는
`/fleet/<ns>/anomaly_check_response`를 15초 기다리다 **반드시 타임아웃**된다.

**실패 시나리오**: 이상신호를 한 번이라도 보내면 그 로봇은 Fleet의
`_anomaly_busy`(`fleet_node.py:127`)에서 영영 안 빠지고, `robot_status.py`가
계속 `DISPATCHING`으로 판정하며, 이후 CCTV 감지 급파 후보에서도 영구
제외된다(`anomaly_control.eligible_candidates`). `anomaly_done.sh`를 수동으로
쏘면 풀리긴 하지만, 실제 운영에서는 아무도 안 쏴줄 신호다.

**제안**: 실패 경로에서도 `anomaly_done`을(`confirmed: null` 또는 별도
`failed: true` 필드로) 발행하도록 고칠 것.

</details>

### 2. ~~미션이 한 번이라도 실패(abort)하면 `control_node` 프로세스가 그대로 종료됨~~ — **해결됨**

`run()`의 메인 루프에서 `_run_current_mission()`이 `False`(abort)를 반환해도
더 이상 `return`하지 않고, `self.mission = None` 후 `_wait_for_mission()`으로
돌아가 다음 미션을 기다리도록 수정. Fleet이 같은 미션을 1초 간격으로 계속
재발행하므로(`fleet_node.py`의 `_publish_missions()` 타이머) 대개 곧바로
재시도된다.

<details><summary>원본 문제 설명</summary>

**대상**: `src/control_amr/control_amr/control_node.py:437-448` (`run`)

`run()`은 `_run_current_mission()`이 `False`를 반환하면(`mission_aborted`)
바로 `return` — 즉 미션 abort 한 번에 노드 프로세스 자체가 죽는다. abort로
이어지는 경로가 여러 개 있고 전부 지금 구성에서 현실적으로 발생 가능하다:

- **교차 지점 grant 15초 타임아웃**
  (`control_node.py:67`, `crossing_request_timeout_sec`): 상대 로봇이
  gate 정렬(스핀 최대 10초) + 정지(2초) + gate 점검(최대 10초) + sleep(2초)를
  하는 동안 통로를 붙잡고 있으면 15초를 쉽게 넘긴다. `_request_crossing()`
  루프는 `emergency_stopped`를 안 보고 타이머를 계속 돌리므로, 긴급정지-전체
  중에 grant 대기 중이던 로봇도 15초 뒤 그대로 abort된다.
- **충돌위험 1회 감지** → `collision_risk` 인터럽트 →
  `_request_route_update()`(`mission_flow.py:95`) → Fleet이
  `/fleet/<ns>/route_update_request`를 구독하지 않으므로(문제 6 참고) 10초
  타임아웃 → abort → 프로세스 종료. `COLLISION_RISK_DISTANCE_M`을 0.05m로
  낮춰 회피 중이지만(커밋 `9cf1ab7`), 한 번이라도 걸리면 즉시 죽는다.
- **gate 지점 점검 타임아웃**: 비전 노드가 없어 `gate_check_response`도
  반드시 10초 타임아웃 → abort → 종료. 지금 `DEFAULT_ZONES`에 gate가 없어서
  잠복 중일 뿐, gate가 있는 구역이 오는 순간 100% 재현된다.

**제안**: abort 시 프로세스 종료 대신 `_wait_for_mission()`으로 돌아가
다음 미션을 기다리는 구조로 바꿀 것. (실제 운영에서 control_node가 로봇
1대당 1개 프로세스인데, 이게 죽으면 그 로봇은 재기동 전까지 완전히
이탈한다.)

</details>

### 3. 도킹/이상신호 인터럽트가 `_move_to()` 폴링 중에만 처리됨 — 이동 중이 아니면 무시됨

**대상**: `src/control_amr/control_amr/control_node.py:261-299` (`_move_to`)

인터럽트 체크(`dock_pending`, `anomaly_pending`, `collision_risk`)가 전부
`_move_to()`의 이동 폴링 루프 안에만 있다. 로봇이 `patrol_waiting`
(순찰 사이 10분 대기, `mission_flow.py:123` `wait_until_next_patrol`),
`waiting_mission`, 교차 grant 대기, gate 점검 중일 때는 이 인터럽트들이
전혀 서비스되지 않는다.

**실패 시나리오**: 운영자가 "도킹 복귀"를 눌렀는데 로봇이 순찰 사이
10분 대기 중이면, 명령이 최대 10분 뒤 다음 미션이 시작돼 첫 웨이포인트로
**움직이기 시작한 뒤에야** 도킹 인터럽트가 걸린다. 긴급정지만 콜백에서
즉시 `cancelTask()`를 호출해서 절반쯤 해결돼 있고(`_on_emergency_stop`,
`control_node.py:148`), dock/anomaly는 이 갭에 그대로 노출돼 있다.

**제안**: `wait_until_next_patrol()`/`_wait_for_mission()`처럼 `spin_once`로
대기하는 모든 자리에서 `dock_pending`/`anomaly_pending`도 같이 체크하도록
통일할 것.

### 4. 같은 통로에 연속 웨이포인트가 생기면 점유를 중간에 놓아버리는 레이스

**대상**: `src/fleet/fleet/zone_router.py` (교차 지점 태깅),
`src/control_amr/control_amr/control_node.py:456-506` (`_run_current_mission`)

`zone_router`는 공유 통로를 지나는 홉마다 같은 `point_id`를 붙이는데,
순찰 지점이 공유 통로 **위에** 찍혀서 `RouteGraph.insert_point()`가 엣지를
쪼개면, 한 통로 안에 같은 `point_id`의 웨이포인트가 연속으로 여러 개
생길 수 있다. `control_node`는 웨이포인트마다 도착 → `_release_crossing()`
(`control_node.py:493`) → 다음 웨이포인트에서 재`_request_crossing()`을
하므로, **로봇이 통로 한가운데 있는 상태에서 점유를 놓는 순간**이 생기고,
그 틈에 상대 로봇이 grant를 받아 같은 외길 통로로 진입할 수 있다 — 점유
중재가 애초에 막으려던 상황 그대로다.

**현재 영향**: 지금 HMI 좌표(V_BC 1홉 교차, 커밋 `5d7c8c2`)에서는 교차가
1홉짜리라 안 터지지만, 구역 데이터가 바뀌어 교차 구간이 여러 홉이 되는
순간 조용히 재현된다.

**제안**: "같은 `point_id`가 다음 웨이포인트에도 이어지면 release를
미루는" 처리를 `_run_current_mission()`에 추가할 것.

---

## 중간 — 배선/단위 불일치 (지금은 잠복, 조건이 바뀌면 발현)

### 5. Gate 정렬의 yaw 단위 불일치 (도 vs 라디안)

**대상**: `src/fleet/fleet/zone_router.py:38` (`_heading_deg`),
`src/control_amr/control_amr/gate_alignment_flow.py:49-63`
(`align_camera_to_gate`)

미션 웨이포인트의 `yaw`는 **도(degree)** 단위다(`zone_router._heading_deg`가
`math.degrees()`로 계산하고, TurtleBot4의 `getPoseStamped()` 컨벤션도 도
단위). 그런데 `align_camera_to_gate()`는 waypoint의 `gate_yaw`/`yaw`를 AMCL
쿼터니언에서 뽑은 **라디안** yaw(`_on_pose`, `gate_alignment_flow.py:36`)와
직접 빼서 `spin()`(라디안 인자)에 넘긴다.

**실패 시나리오**: gate 지점이 생기는 순간 카메라가 목표 각도의 약
57배(도/라디안 비율) 어긋난 방향으로 돌려고 시도한다. 지금
`DEFAULT_ZONES`에 gate가 없어서 잠복 중이다.

**제안**: `align_camera_to_gate()`에서 `target_direction`을
`math.radians()`로 변환한 뒤 비교/스핀할 것.

### 6. Control이 발행하는 토픽 대부분을 Fleet이 구독하지 않음

**대상**: `src/control_amr/control_amr/mission_flow.py`,
`navigation_flow.py`, `state_flow.py`의 각 `create_publisher` 호출부,
`src/fleet/fleet/fleet_node.py`의 구독 목록

`route_update_request`, `mission_reject`, `mission_complete`,
`request_next_mission`, `waypoint_reached`, `failure`, `collision_risk`,
`navigation_recovery`, `battery_state` — 전부 Fleet에 대응하는 구독이 없다.
대부분은 "나중에 UI/로깅용으로 붙일 것"이라 당장 문제는 아니지만,
`route_update_request`는 문제 2번(충돌위험 → 프로세스 종료)의 직접 원인이고
`gate_check_response`/`anomaly_check_response`(비전 노드 몫)는 문제 1·2번의
원인이라 우선순위가 다르다.

**제안**: 최소한 `route_update_request`에 대한 Fleet 쪽 처리(현재 경로
유지 또는 재라우팅 응답)를 다음 작업으로 잡을 것. 나머지는 타입 인터페이스
전환(HANDOFF.md 남은 작업 5번) 때 같이 정리.

### 7. 긴급정지/이상신호/도킹 토픽이 volatile QoS라 늦게 뜬 Control이 신호를 놓침

**대상**: `src/fleet/fleet/fleet_node.py:214-224` (`_ensure_robot_pubs`)

미션(`/fleet/<ns>/mission`)은 `MISSION_QOS`(TRANSIENT_LOCAL) + 1Hz
재발행으로 "늦게 뜬 Control도 받을 수 있게" 보완돼 있는데,
`/fleet/<ns>/emergency_stop`, `/fleet/<ns>/anomaly`, `/fleet/<ns>/dock`은
기본 QoS(volatile, 1회 발행)다.

**실패 시나리오**: 긴급정지 신호를 보낸 직후 그 로봇의 `control_node`가
재시작되면(크래시 복구, 문제 2번 등), Fleet은 그 로봇을 여전히
`EMERGENCY_STOP`으로 알고 있는데 새로 뜬 Control은 정지 신호를 받은 적이
없어서 그냥 미션(latched라서 받음)을 따라 다시 달리기 시작한다.

**제안**: 최소한 `emergency_stop`만이라도 TRANSIENT_LOCAL로 바꾸거나,
미션처럼 "현재 긴급정지 상태"를 주기 재발행할 것.

### 8. `startToPose()`가 블로킹이라 이상신호/도킹 이동 중 충돌 감시가 무의미해질 가능성

**대상**: `src/control_amr/control_amr/control_node.py:303-333`
(`_handle_anomaly`), `:350-380` (`_handle_dock`),
`src/control_amr/control_amr/navigation_flow.py:100-110`
(`wait_until_pose_reached`)

`_handle_anomaly()`/`_handle_dock()`은 `navigator.startToPose()` 호출 후
`wait_until_pose_reached()`로 충돌위험을 감시하는 구조인데,
`TurtleBot4Navigator.startToPose()`는 통상 내부에서 완료까지 블로킹하는
편의 함수다(예제 코드들도 단독 호출로 씀). 그렇다면 `startToPose()`가
반환된 시점엔 이미 로봇이 도착해 있어서, 뒤이은
`wait_until_pose_reached()`의 충돌 감시가 이동 중이 아니라 **도착 후**에야
실행돼 사실상 의미가 없어진다.

**확인 필요**: 이 프로젝트가 쓰는 TurtleBot4 navigator 버전에서
`startToPose()`가 실제로 블로킹인지 논블로킹인지 하드웨어에서 직접
확인해야 한다. 블로킹이면 순찰 이동(`_move_to`)이 쓰는 `goToPose()`
(명시적으로 논블로킹, `isTaskComplete()`로 폴링)로 바꿔야 이동 중 충돌
감시가 실제로 동작한다. (긴급정지는 콜백에서 직접 `cancelTask()`하므로
이 문제와 무관하게 동작.)

---

## 경미 — 견고성 (당장 흐름을 막진 않지만 예외 상황에서 깨짐)

### 9. ~~Fleet 콜백 3개가 JSON 파싱 예외를 안 잡음~~ — **해결됨**

`_on_request`/`_on_anomaly_done`/`_on_release` 모두 다른 콜백들과 같은
try/except 패턴으로 통일. `_on_request`/`_on_release`는 `robot`/`point`가
필수 필드라 `_on_map_points`처럼 `(json.JSONDecodeError, KeyError,
TypeError)`를 잡아 경고 로그 후 return, `_on_anomaly_done`은
`payload.get('robot')`이라 필드 누락엔 원래도 안전해서 `_on_emergency_stop_all`
처럼 `json.JSONDecodeError`만 잡음.

<details><summary>원본 문제 설명</summary>

**대상**: `src/fleet/fleet/fleet_node.py:294` (`_on_request`),
`:366` (`_on_anomaly_done`), `:470` (`_on_release`)

세 콜백 모두 `json.loads(msg.data)`를 try/except 없이 바로 호출한다.
다른 콜백들(`_on_map_points`, `_on_anomaly_trigger`,
`_on_emergency_stop_all`, `_on_dock_return`)은 전부 파싱 예외를 잡아서
경고 로그 후 무시하는데 이 세 개만 예외 처리가 빠져 있어 일관성도 어긋난다.

**실패 시나리오**: 잘못된 형식의 메시지 하나가 `/fleet/occupancy_request`
등에 오면 콜백에서 처리되지 않은 예외가 발생해 `fleet_node` 프로세스
전체가 죽는다 — Fleet Node는 로봇 전체를 담당하는 단일 프로세스라 영향
범위가 크다.

**제안**: 세 콜백 모두 다른 콜백들과 같은 `try/except
(json.JSONDecodeError, ...)` 패턴으로 통일할 것.

</details>

### 10. 도킹 후에도 Fleet이 그 로봇을 계속 `PATROLLING`으로 판정함

**대상**: `src/fleet/fleet/fleet_node.py:159` (`self.missions`),
`src/fleet/fleet/robot_status.py:58-76` (`compute_status`)

`control_node`가 도킹하면 `self.docked = True`로 새 미션 수신을 무시할
뿐, Fleet 쪽 `self.missions`에는 그 로봇이 여전히 남아 있다.
`robot_status.compute_status()`는 `ns in missions`면 `PATROLLING`으로
판정하므로, 로봇이 실제로는 도킹된 채 정지해 있는데도 UI에는 계속
순찰 중으로 표시된다.

**영향**: 지금은 도킹 후 재순찰(언도킹) 신호 자체가 미구현이라 이 상태가
오래 지속될 수 있다. HANDOFF.md에 정리된 "재순찰 트리거 추가" 작업을 할
때 같이 정리하면 된다(예: Control이 `docking`/`docked` 상태를
`/control/<ns>_State`로 이미 퍼블리시하고 있으니, 그 상태를 Fleet이
참고하거나 UI가 두 상태를 조합해서 보여주는 방식).

### 11. `/backend/map_points` zone 데이터의 엣지케이스가 처리 안 됨

**대상**: `src/fleet/fleet/fleet_node.py:255-270` (`_on_map_points`),
`src/fleet/fleet/zone_router.py:115-124` (`build_missions`)

- 같은 `robot`에 구역(zone) 두 개가 오면 `per_robot_waypoints[zone['robot']]
  = waypoints`(zone_router.py:123)가 그냥 덮어써서, 먼저 처리된 구역의
  미션이 조용히 사라진다. 경고 로그도 없다.
- `zone['points']`가 빈 리스트로 오면 `_route_zone()`이
  `points[0]`(zone_router.py:71)에서 `IndexError`를 던지고, 이건
  `_on_map_points`의 try/except가 잡는 범위(`JSONDecodeError`,
  `KeyError`, `TypeError`) 밖이라 콜백 예외로 `fleet_node`가 죽는다.

**제안**: `_on_map_points()`에서 zone 파싱 시 robot 중복/points 빈 배열을
사전에 검증하고 경고 로그와 함께 skip하도록 방어 코드 추가.

---

## 요약 — 지금 이대로 하드웨어 테스트 시나리오를 돌리면

- ~~**시나리오 4·5(이상신호)**: 문제 1번 때문에 최소 한 번은 로봇이 영구
  busy로 남을 가능성이 높음 (비전 노드가 없어서 100% 재현).~~ 문제 1번
  해결됨 — 실패해도 `anomaly_done`이 나가서 busy가 안 남는다.
- ~~**시나리오 6(인터럽트 우선순위) 및 교차/충돌/gate가 관여하는 모든 경로**:
  문제 2번 때문에 `control_node` 프로세스 종료로 끝날 수 있음.~~ 문제 2번
  해결됨 — abort돼도 프로세스는 살아서 다음 미션을 기다린다.
- 문제 4·5·8은 지금 구역 데이터(gate 없음, 1홉 교차)에서는 잠복 상태지만
  구역/gate 데이터가 바뀌는 순간 조건 없이 재현되는 유형이라, 그 전에
  고쳐두는 게 안전함.

우선순위 제안: ~~1, 2 → 9~~ (죽는 문제부터, 전부 해결됨) → 3, 4 → 5, 6, 7, 8 → 10, 11.
