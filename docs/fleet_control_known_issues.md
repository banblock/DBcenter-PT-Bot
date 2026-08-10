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

### 3. 도킹/이상신호 인터럽트가 `_move_to()` 폴링 중에만 처리됨 — 이동 중이 아니면 무시됨 — **부분 해결됨**

**대상**: `src/control_amr/control_amr/control_node.py` (`_move_to`, `run`),
`src/control_amr/control_amr/mission_flow.py` (`wait_until_next_patrol`)

원본 문제: 인터럽트 체크(`dock_pending`, `anomaly_pending`, `collision_risk`)가
전부 `_move_to()`의 이동 폴링 루프 안에만 있어서, 로봇이 `patrol_waiting`
(순찰 사이 10분 대기), `waiting_mission`, 교차 grant 대기, gate 점검
중일 때는 이 인터럽트들이 전혀 서비스되지 않았다.

**해결된 부분**: `wait_until_next_patrol()`에 `should_interrupt` 콜백을
추가하고 `run()`에서 `dock_pending`/`anomaly_pending` 둘 다 넘겨서, 10분
순찰 대기 중에도 즉시 반응하도록 고쳤다(하드웨어 테스트 중 도킹으로
먼저 재현/수정, 이상신호도 같은 패턴으로 동일 적용). 이 과정에서
`_handle_dock()`/`_handle_anomaly()`가 끝나기 전에 Fleet이 재발행한
옛 순찰 미션이 `self.mission`에 몰래 채워지는 부수 버그도 같이
발견해서 고쳤다(인터럽트 처리 직후 `self.mission = None`으로 명시
클리어) — 안 그러면 도킹 완료 후 undock 없이 바로 옛 미션을 재개해버리는
안전 문제가 있었다(실제 재현됨).

**여전히 남은 부분**: `_request_crossing()`(크로싱 grant 대기, 최대
15초)과 `_wait_for_mission()`은 여전히 `dock_pending`/`anomaly_pending`을
안 본다 — 이 구간에서 인터럽트가 오면 최대 15초까지는 반응이 늦을 수
있다(hang은 아니고 지연 정도). 우선순위가 낮아 보류.

### 4. ~~같은 통로에 연속 웨이포인트가 생기면 점유를 중간에 놓아버리는 레이스~~ — **해결됨**

**대상**: `src/fleet/fleet/zone_router.py` (교차 지점 태깅),
`src/control_amr/control_amr/control_node.py` (`_run_current_mission`,
`_handle_dock`)

`zone_router`는 공유 통로를 지나는 홉마다 같은 `point_id`를 붙이는데,
순찰 지점이 공유 통로 **위에** 찍혀서 `RouteGraph.insert_point()`가 엣지를
쪼개면, 한 통로 안에 같은 `point_id`의 웨이포인트가 연속으로 여러 개
생길 수 있다. `control_node`는 웨이포인트마다 도착 → `_release_crossing()`
→ 다음 웨이포인트에서 재`_request_crossing()`을 하므로, **로봇이 통로
한가운데 있는 상태에서 점유를 놓는 순간**이 생기고, 그 틈에 상대
로봇이 grant를 받아 같은 외길 통로로 진입할 수 있었다 — 점유 중재가
애초에 막으려던 상황 그대로다.

**실제 재현됨**: 이슈 12번(0번 순찰 지점 그래프 라우팅)과 14번(도킹
경로 그래프 라우팅)을 고치면서 순찰/도킹 정지 지점이 공유 통로/교차로
위에 오는 경우가 실제로 생겼고, 하드웨어 테스트에서 바로 재현됐다.

**수정**: `_run_current_mission()`과 `_handle_dock()` 둘 다 (1) 이미
같은 `point_id`를 쥐고 있으면 재요청하지 않고, (2) 다음 웨이포인트도
같은 `point_id`를 쓰면 도착해도 release를 미루도록 변경. `_handle_dock()`은
추가로 마지막 지점의 크로싱을 `navigator.dock()` 실제 도킹 액션이
끝날 때까지도 들고 있는다(도킹 지점 자체가 공유 자원 위일 수 있으므로).
겸사겸사 `_run_current_mission()`의 `mission_aborted` 종료 경로에도
"쥐고 있는 크로싱이 있으면 무조건 놓는다"는 안전장치를 추가해, abort
사유와 무관하게 크로싱이 영구히 안 놓이는 경우를 없앴다.

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

## 2026-08-10 하드웨어 테스트에서 새로 발견된 문제 (robot3/robot8)

### 12. ~~0번 순찰 지점이 그래프 라우팅 없이 직행 이동 — 점유 조정 대상에서 빠짐~~ — **해결됨**

**대상**: `src/fleet/fleet/zone_router.py` (`_route_zone`, `_emit_hop`, `build_missions`),
`src/fleet/fleet/fleet_node.py` (`_apply_zones`, `DEFAULT_ROBOT_START`)

`build_missions()`가 만드는 미션은 순찰 지점 1번부터는 전부 고정 통로
그래프(`route_graph.py`) 위에서 다익스트라로 홉을 쪼개 점유 조정 대상
(`point_id`)을 태깅하는데, **0번(첫) 순찰 지점만은 예외**였다 —
`_route_zone()`이 로봇이 지금 어디서 출발하는지 몰라서 "현재 위치 →
0번 지점" 구간에 대해 경로를 계산할 방법이 없었고, 그냥 0번 지점
좌표를 그대로 웨이포인트로 박아 넣었다(`point_id=None`). Control Node는
이 웨이포인트로 Nav2 자체 플래너(`goToPose`)로 직행하므로, 이 구간은
통로 그래프도 occupancy 프로토콜도 완전히 건너뛴다.

**실패 시나리오 (실제 재현됨)**: robot3의 0번 순찰 지점이 하필 공유
통로(V_BC) 한가운데였다. robot3가 그 지점으로 무보호 직행해 통로에
정차한 사이, robot8이 정상적으로 grant받은 크로싱 구간을 지나가려다
robot3와 물리적으로 계속 부딪혀 이동 실패를 반복했다(`collision_risk`
→ 미구현 `route_update_request` 10초 타임아웃 → abort → 재시도 →
재충돌 반복).

**수정**: `zone_router._route_zone()`이 `start_pos`(로봇 현재 위치)를
받으면 0번 지점도 "시작 위치 → 0번 지점" 홉으로 취급해 `_emit_hop()`으로
그래프 경로를 계산하고, 지나가는 통로/교차로가 공유 자원이면 다른
홉과 동일하게 `point_id`를 태깅하도록 변경. `build_missions()`가
`robot_positions: {robot: (x, y)}`를 받아 각 로봇에 넘겨준다.
`fleet_node.py`는 `self._robot_pose`(amcl_pose 구독분)를 넘기되,
`_apply_zones()`에서 `build_missions()`보다 먼저 `_ensure_robot_pubs()`를
호출해 pose 구독이 최대한 일찍 걸리도록 순서를 바꿨다. 로봇 위치를
아직 모르면(막 켜진 직후 등) 예전처럼 0번 지점 직행 폴백으로 안전하게
떨어진다.

**참고**: 테스트 중 robot8의 AMCL이 lifecycle 서비스 응답 타임아웃으로
Active 전환이 안 돼 `amcl_pose`를 못 받는 문제가 있었다(DDS/시스템 부하
계열로 추정, 코드 문제 아님). 그 상황에 대비해 `fleet_node.py`에
`DEFAULT_ROBOT_START`(robot3: `(-4.6, 1.72)`, robot8: `(-0.15, 0.157)`)를
`self._robot_pose`의 초기값으로 시드해뒀다 — 실제 `amcl_pose`가 들어오면
바로 덮어써지는 자리표시자일 뿐이니, **로봇 시작 위치나 맵이 바뀌면 이
값도 같이 갱신해야 한다.**

### 13. ~~긴급정지 콜백이 `cancelTask()`를 직접 불러서 rclpy 전역 executor를 재진입(reentrant) hang~~ — **해결됨**

**대상**: `src/control_amr/control_amr/control_node.py:148-168` (`_on_emergency_stop`)

처음엔 "유휴 상태에서 이미 끝난 이전 목표를 다시 취소하려다 멈추는"
문제로 보고 `self.navigator.result_future.done()`이 아닐 때만
`cancelTask()`를 부르는 가드를 추가했었는데, 재테스트에서 **진짜로
이동 중이던 로봇(robot3)도 똑같이 멈추는 게 확인돼서** 더 근본적인
원인을 다시 찾았다.

`nav2_simple_commander`의 `isTaskComplete()`와 `cancelTask()`는 둘 다
내부에서 `rclpy.spin_until_future_complete()`/`spin_once()`를 쓰는데,
이 함수들은 `rclpy.get_global_executor()`가 반환하는 **프로세스 전체가
공유하는 단일 `SingleThreadedExecutor` 인스턴스**를 스핀한다. `_move_to()`의
`while not self.navigator.isTaskComplete():` 루프가 이 전역 executor를
스핀하는 도중 콜백(`_on_emergency_stop`)이 실행되는 경우가 있는데, 이
콜백이 곧바로 `cancelTask()`를 부르면 **이미 스핀 중인 같은 executor를
또 스핀**하게 된다 - `SingleThreadedExecutor`는 이런 재진입을 지원하지
않아 그대로 멈춘다. `result_future.done()` 가드는 "유휴 상태 + 재요청"
케이스만 우회했을 뿐, "이동 중 + 콜백에서 직접 취소" 자체가 재진입이라는
근본 원인은 그대로 남아 있었다.

**실패 시나리오 (실제 재현됨, 두 로봇 모두)**:
- robot8: 크로싱 grant를 기다리며 유휴 상태일 때 긴급정지 → 이미 끝난
  이전 목표를 다시 취소하려다 hang (1차 수정으로 해결됨).
- robot3: `goToPose()`로 실제 이동 중일 때 긴급정지 →
  `isTaskComplete()`가 전역 executor를 스핀하는 도중 콜백이 실행되고,
  그 안에서 `cancelTask()`가 같은 executor를 재진입 → hang (2차 수정
  대상, 위 원인 설명 참고).

**수정**: `_on_emergency_stop()`에서 `cancelTask()`를 아예 직접 호출하지
않도록 변경 - `emergency_stopped` 플래그만 세운다. `_move_to()`의 폴링
루프가 매 반복(~0.1초 간격)마다 이 플래그를 콜백 스택 밖의 안전한
위치에서 확인해 `_cancel_navigation_task()`를 부르므로, "즉시 정지"
의도는 그대로 유지되면서 재진입 경로 자체가 사라진다.

**남은 과제 (아직 미수정)**:
- `_request_crossing()`의 대기 루프는 여전히 `self.emergency_stopped`를
  안 본다. hang은 안 나지만, 긴급정지 중에도 크로싱 grant를 계속
  요청하다가 15초 뒤 그냥 abort → 미션 재시도 루프를 탈 수 있다 -
  기능적으로 멈추진 않지만 낭비고 로그도 지저분해짐.
- ~~`_handle_anomaly()`가 쓰는 `navigation_flow.wait_until_pose_reached()`는
  애초에 `emergency_stopped`를 전혀 체크하지 않는다~~ — **해결됨**.
  `wait_until_pose_reached(is_emergency_stopped, pose=None)`로 시그니처를
  바꿔서 긴급정지도 감시하도록 했다: collision_risk와 달리 긴급정지는
  취소 후 그냥 실패 처리하지 않고, 해제될 때까지 제자리에서 기다린
  뒤 같은 `pose`로 이동을 재시도한다(`_move_to()`의 긴급정지 처리와
  같은 패턴). `_handle_anomaly()`가 `_wait_for_anomaly_arrival(pose)`로
  pose를 넘겨준다. (`_handle_dock()`은 이슈 14번에서 `_move_to()`
  기반으로 재작성돼 애초에 이 문제가 없었다.)

### 14. ~~도킹 이동이 통로 그래프/occupancy 없이 좌표 하나로 직행~~ — **해결됨**

**대상**: `src/fleet/fleet/dock_control.py`, `fleet_node.py:_on_dock_return`,
`src/control_amr/control_amr/control_node.py:_on_dock`/`_handle_dock`

`/backend/dock`이 들어오면 Fleet은 `DOCK_STATIONS[ns]` 좌표를 그대로
JSON으로 실어 보냈고, Control Node는 그 좌표 하나로 `startToPose()` 직행
- 이슈 12번(0번 순찰 지점 무보호 직행)과 완전히 같은 부류의 위험을
도킹 이동도 그대로 갖고 있었다: 통로 그래프도 occupancy 중재도 전혀
안 거치므로, 도킹하러 가는 로봇이 다른 로봇이 정상적으로 grant받아
지나가는 통로와 물리적으로 마주칠 수 있었다.

**수정**: `zone_router.py`에 `route_to_point(graph, start_pos, target,
id_prefix)` 신규 - 순찰 미션과 같은 `_emit_hop()` 파이프라인으로
시작 위치→목표 지점 경로를 계산한다. 순찰과 달리 그 순간 다른 로봇과
실제로 겹치는지 미리 알 수 없는 1회성 이동이라, "공유되는 자원만"
거르는 최적화 없이 지나가는 모든 홉에 항상 `point_id`를 태깅한다(안
겹치면 grant가 거의 즉시 나오므로 비용은 미미). `fleet_node.py`가
`self._robot_pose`를 시작 위치로 넘겨 이 함수로 웨이포인트 리스트를
만들고, 좌표 하나 대신 `/fleet/<ns>/mission`과 같은 모양으로
`/fleet/<ns>/dock`에 발행한다. `control_node.py`의 `_on_dock()`은
`mission_flow.validate_mission()`으로 그 리스트를 검증하고,
`_handle_dock()`은 각 홉마다 `_request_crossing()`/`_move_to()`/
`_release_crossing()`으로 순찰과 동일한 점유 프로토콜을 지키며 이동한
뒤 마지막 지점에서 실제 `navigator.dock()`을 호출한다. 로봇 위치를
아직 모르면(amcl_pose 없음) 예전처럼 좌표 하나짜리 무보호 폴백으로
안전하게 떨어진다.

**부수 효과**: 예전엔 `startToPose()` + `wait_until_pose_reached()`를
써서 긴급정지/이상신호 인터럽트를 거의 못 봤는데(이슈 13번 "남은
과제" 참고), 이제 `_move_to()`를 재사용하므로 도킹 이동 중에도
긴급정지/충돌위험 인터럽트가 정상적으로 감지된다.

### 15. 이상신호 대응 설계 변경 — 비전 노드 자동 판정 → 운영자(HMI) 결정

**대상**: `src/control_amr/control_amr/control_node.py` (`_handle_anomaly`),
`src/control_amr/control_amr/inspection_flow.py`,
`src/fleet/fleet/fleet_node.py` (`_on_anomaly_resume`)

기존 구현(이슈 1번 수정 당시 기준)은 로봇이 이상 위치에 도착하면
`inspection_flow.inspect_anomaly()`로 비전 노드 응답을 15초 기다렸다가
(비전 노드가 없어 항상 타임아웃) **자동으로** 원래 순찰 위치로 복귀했다.
사용자 확인 결과 실제 설계는 이게 아니었다: 로봇은 도착하면(자체
감지면 사실상 제자리 정지) 자동 판정 없이 그 자리에서 **카메라로
상황을 계속 비추며 무기한 대기**하고, HMI에서 그 영상을 본 운영자가
"재개" 또는 "도킹" 중 하나를 최종 결정한다.

**변경 내용**:
- `inspection_flow.py`의 `inspect_anomaly()`와 관련 pub/sub
  (`anomaly_check_request`/`_response`)을 제거 - 더 이상 아무도 안 씀
  (gate 점검용 `inspect_gate()`는 그대로 유지).
- `_handle_anomaly()`: 도착 후 `anomaly_waiting` 상태로 전환하고,
  `self.anomaly_resume_pending`(신규) 또는 `self.dock_pending`(기존)이
  될 때까지 대기. 대기 중 긴급정지도 계속 감시(멈췄다가 해제되면 대기
  재개).
  - "재개" 결정 → 신규 `/backend/anomaly_resume` → Fleet이
    `/fleet/<ns>/anomaly_resume`로 중계 → 원래 순찰 웨이포인트로 복귀.
  - "도킹" 결정 → **기존** `/backend/dock`을 그대로 재사용(운영자가
    아무 때나 누르는 도킹 복귀와 완전히 같은 경로) - `_handle_anomaly()`가
    직접 `_handle_dock()`을 호출해서 그래프 라우팅/점유 보호까지
    그대로 이어받는다. 이상신호 전용 도킹 신호는 따로 안 만들었다.
- `_publish_anomaly_done()`의 `confirmed` 필드 의미가 바뀌었다: 이제
  비전 판정 결과가 아니라 운영자 결정을 나타낸다(재개=`False`,
  도킹=`True`, 도착 실패=`None`+`failed=True`). Fleet의 `_on_anomaly_done()`은
  `robot` 필드만 보고 `_anomaly_busy`에서 빼므로 그대로 호환됨.

**하드웨어 테스트 체크리스트도 이 변경에 맞게 갱신함** (시나리오 4 참고) -
예전처럼 `anomaly_done`을 손으로 쏘는 게 아니라 `anomaly_resume`/`dock`을
보내야 한다.

### 16. 이상신호 이동도 좌표 직행 대신 통로 그래프 경로 + 카메라만 회전하도록 변경

**대상**: `src/fleet/fleet/route_graph.py` (`nearest_point`),
`src/fleet/fleet/anomaly_control.py` (`snap_cctv_location`),
`src/fleet/fleet/fleet_node.py` (`_on_anomaly_trigger`),
`src/control_amr/control_amr/control_node.py` (`_traverse_route_with_crossings`,
`_handle_anomaly`, `_handle_dock`)

이슈 15번까지는 도착 후 판정 방식만 고쳤고, 이동 자체는 여전히 이상
좌표로 `startToPose()` 직행이었다 - 이슈 12·14번(0번 순찰 지점/도킹)과
같은 부류의 문제가 그대로 남아있었다: CCTV가 주는 좌표는 카메라가 찍은
실좌표라 랙 안쪽처럼 로봇이 물리적으로 못 가는 지점일 수 있고
(`(-2.7, 1.5)`로 테스트 중 실제로 지적됨 - 가장 가까운 통로 요소에서
0.3~0.5m 떨어짐), 통로 그래프/occupancy도 안 거쳤다.

**수정**: `RouteGraph.nearest_point()`(조회 전용, `insert_point()`와
같은 스냅 규칙이지만 그래프를 변형 안 함) 신규 추가.
`anomaly_control.snap_cctv_location(graph, loc)`이 CCTV 좌표를 통로
그래프 위 가장 가까운 지점으로 스냅하고, 카메라(로봇 정면)가 원래
좌표 쪽을 보도록 yaw를 계산한다 - **CCTV 경로에서만** 쓴다(AMR 자체
감지는 로봇이 이미 서 있는 자리 그대로가 맞으므로 스냅 안 함).
`_on_anomaly_trigger()`가 (급파 로봇 선정까지는 원본 좌표로 거리를
재고) 최종적으로 `zone_router.route_to_point()`로 로봇 현재 위치 →
스냅된 지점까지 그래프 경로를 계산해서 웨이포인트 리스트로 보낸다
(`/fleet/<ns>/dock`과 동일한 모양).

Control Node 쪽은 도킹 이동 루프와 완전히 같은 로직이라
`_traverse_route_with_crossings()`로 공통화해서 `_handle_dock()`/
`_handle_anomaly()` 둘 다 쓰게 했다. `_handle_anomaly()`는 도착 후
`anomaly_waiting`으로 전환하는데, **마지막 웨이포인트의 크로싱을 대기가
끝날 때까지(운영자가 재개/도킹을 결정할 때까지) 계속 쥐고 있는다** -
그 지점이 하필 공유 통로/교차로 위여도 로봇이 실제로 거기 서 있는
동안은 다른 로봇이 못 들어오게. **트레이드오프**: 운영자 결정이
오래 걸리면(사람이 개입하는 무기한 대기라 길어질 수 있음) 그 자원을
다른 로봇이 그만큼 오래 못 쓴다 - 지금은 "물리적으로 서 있는 동안은
반드시 점유를 지킨다"는 안전 우선 기본값으로 남겨뒀다.

기존 `wait_until_pose_reached()`(이슈 13번에서 긴급정지 감시를
추가했던 함수)는 이제 아무도 안 써서 `navigation_flow.py`에서 제거
(`_move_to()`가 이미 긴급정지/충돌위험을 다 보므로). `_is_valid_pose()`도
`_on_dock`/`_on_anomaly` 둘 다 `mission_flow.validate_mission()`으로
바뀌면서 안 쓰여 같이 제거.

### 17. 이상신호 이동 중 카메라 조기 포착 시 목적지 도달 전 정지 기능 추가

**대상**: `src/control_amr/control_amr/control_node.py` (`_move_to`,
`_traverse_route_with_crossings`, `_handle_anomaly`, `_on_anomaly_captured`),
`src/fleet/fleet/fleet_node.py` (`_on_anomaly_captured`)

이슈 16번까지는 CCTV 좌표를 스냅한 지점까지 항상 끝까지 이동했는데,
사용자 요청으로 "목적지로 가는 도중 로봇 자신의 카메라가 이상 상황을
먼저 포착하면 거기서 즉시 멈춘다"는 조기정지 기능을 추가했다. "카메라에
잡혔다"는 신호는 이 저장소에 비전 노드가 없어서 UI가 대신 보내주는
걸로 함(사용자 확인) - `/backend/anomaly_captured`(`{"robot": ns}`) →
Fleet이 `/fleet/<ns>/anomaly_captured`로 중계(`anomaly_resume`과 동일
패턴).

**구현**: `_move_to(pose, extra_interrupt=None)`에 `(reason, check)`
옵션 인자 추가 - `emergency_stopped`/`dock_pending`/`anomaly_pending`처럼
항상 감시하면 안 되고(순찰/도킹 이동 중엔 무의미) 특정 호출에서만
감시해야 하는 인터럽트용. `check()`가 True면 그 즉시(진행 중이던 홉
도중이어도) 취소하고 `navigation_interrupt_reason`을 `reason`으로 세운
채 False를 반환한다. `_traverse_route_with_crossings()`도 같은 옵션을
받아 그대로 `_move_to()`에 넘기고, 반환값을 기존 bool 대신
`'completed'`/`'stopped_early'`/`'failed'` 3가지 문자열로 바꿨다(도킹은
`extra_interrupt`를 안 써서 `'stopped_early'`가 절대 안 나옴 - `!=
'completed'`를 실패로 취급하는 기존 동작 그대로 유지).

`_handle_anomaly()`가 `extra_interrupt=('anomaly_captured', lambda:
self.anomaly_captured_pending)`를 넘긴다. 조기정지 시 카메라(로봇
정면) 각도는 멈춘 순간의 진행 방향 그대로 두고 별도로 안 돌린다(사용자
확인). 그 순간 쥐고 있던 크로싱이 있으면(마침 공유 통로 중간이었을
경우) 그대로 유지한 채 `anomaly_waiting`으로 넘어가고, 이후 재개/도킹
결정 때 정상적으로 반납된다(이슈 16번의 "물리적으로 서 있는 동안은
점유를 지킨다" 원칙과 동일).

### 18. 이상신호/도킹 경로의 point_id가 순찰 로봇의 point_id와 어긋나서 occupancy 뮤텍스가 실제로는 안 걸림 — 심각, 하드웨어 테스트 중 실제 재현

**대상**: `src/fleet/fleet/zone_router.py` (`build_missions`, `route_to_point`),
`src/fleet/fleet/fleet_node.py` (`_apply_zones`, `_on_dock_return`,
`_on_anomaly_trigger`)

`build_missions()`(순찰)와 `route_to_point()`(도킹/이상신호)가 각자
**독립적인** `_UnionFind`로 "엣지+교차로를 하나의 point_id로 합칠지"를
계산했다. `build_missions()`는 등록된 순찰 로봇 전체를 한 번에 놓고
계산하므로, 예를 들어 V_BC 엣지가 F_BC 교차로와 어느 로봇의 한
웨이포인트에서 동시에 필요하면 그 즉시 **전역적으로** `X_V_BC`와
`J_F_BC`를 합쳐서 이후 V_BC를 쓰는 모든 웨이포인트가 정규 id
`J_F_BC`로 통일된다. 그런데 `route_to_point()`는 도킹/이상신호 목적지
하나만 놓고 **그 호출 하나만의 지역적** union-find를 새로 계산한다 -
그 경로가 마침 F_BC 교차로를 안 지나가면(목적지가 V_BC 엣지 중간이면
당연히 안 지나감) 합쳐질 이유가 없어서 그냥 `X_V_BC`로 남는다.

**실패 시나리오 (실제 재현됨)**: 이상신호가 V_BC 엣지 중간(공유 구간)에
뜨자, 급파된 로봇은 `X_V_BC`를 요청해 정상적으로 grant받고 그 자리에
멈췄다. 그런데 같은 구간을 지나가던 **순찰 로봇은 자기 미션에서 그
구간이 `J_F_BC`로 태깅돼 있어서** `J_F_BC`를 요청했고, Fleet 입장에선
`X_V_BC`와 `J_F_BC`가 서로 무관한 자원이라 **둘 다 grant해줬다** -
물리적으로 완전히 같은 통로인데 occupancy 뮤텍스가 전혀 안 걸려서
순찰 로봇이 정지해 있던 이상신호 로봇을 그대로 지나쳐버렸다.

**수정**: `build_missions()`가 세 번째 반환값으로
`resource_canonical`(`{'X_<eid>'|'J_<nid>': point_id}`, 그 호출에서
실제로 계산된 정규 매핑)을 돌려준다. `fleet_node._apply_zones()`가
이걸 `self._resource_canonical`로 저장해뒀다가, `_on_dock_return()`/
`_on_anomaly_trigger()`가 `route_to_point(..., canonical_point_ids=
self._resource_canonical)`로 넘긴다. `route_to_point()`는 자기
지역적 union-find로 계산한 `local_id`를 이 매핑에 한 번 더 통과시켜서
(`canonical_point_ids.get(local_id, local_id)`) 있으면 정규 id로
치환한다 - 순찰 시스템이 이미 그 자원을 다른 것과 합쳐서 쓰고 있으면
도킹/이상신호도 정확히 같은 id를 쓰게 된다.

시뮬레이션으로 검증됨: 수정 전 이상신호 경로의 V_BC 중간 지점
point_id는 `X_V_BC`였는데, 수정 후엔 순찰 로봇(robot8)의 해당 구간
point_id와 정확히 같은 `J_F_BC`로 나온다.

**참고**: `build_missions()`/`route_to_point()`를 부르는 기존 테스트/리포트
스크립트(`test_zone_router.py`, `build_route_report.py`, `render_routes.py`)는
반환값이 2개에서 3개로 늘어나서 전부 `missions, crossing_log, _ =
...` 형태로 같이 갱신했다.

**남은 한계**: `route_to_point()`가 `start_node == end_node`(목적지에
이미 도착해 있어 홉이 0인 경우)면 여전히 `point_id: None`으로 무보호
직행 처리한다 - 도킹/이상신호 로봇이 그 순간 이미 어떤 크로싱을 쥐고
있었다면(예: 이동 도중 인터럽트됨) 그건 그대로 유지되니 대개 문제
없지만, 처음부터 목적지 바로 근처에서 출발한 경우는 이론상 여전히
무보호다. 지금까지 재현된 적은 없고 범위가 좁아 우선순위 낮게 남겨둠.

---

## 요약 — 지금 이대로 하드웨어 테스트 시나리오를 돌리면

- ~~**시나리오 4·5(이상신호)**: 문제 1번 때문에 최소 한 번은 로봇이 영구
  busy로 남을 가능성이 높음 (비전 노드가 없어서 100% 재현).~~ 문제 1번
  해결됨 — 실패해도 `anomaly_done`이 나가서 busy가 안 남는다.
- ~~**시나리오 6(인터럽트 우선순위) 및 교차/충돌/gate가 관여하는 모든 경로**:
  문제 2번 때문에 `control_node` 프로세스 종료로 끝날 수 있음.~~ 문제 2번
  해결됨 — abort돼도 프로세스는 살아서 다음 미션을 기다린다.
- 문제 5·8은 지금 구역 데이터(gate 없음)에서는 잠복 상태지만 gate
  데이터가 생기는 순간 조건 없이 재현되는 유형이라, 그 전에 고쳐두는
  게 안전함.
- ~~**robot3/robot8 실기 테스트에서 실제로 재현/발견된 네 문제(4·12·13·14)**:
  연속 웨이포인트에서 점유를 중간에 놓는 레이스, 0번 지점 무보호
  직행으로 인한 통로 충돌, 긴급정지 중 cancelTask() hang, 도킹
  이동도 같은 부류의 무보호 직행.~~ 전부 해결됨 - 자세한 내용은 위
  "2026-08-10 하드웨어 테스트에서 새로 발견된 문제" 참고.

우선순위 제안: ~~1, 2 → 9~~ (죽는 문제부터, 전부 해결됨) → ~~4, 12, 13,
14~~ (실기 테스트 중 발견/재현, 전부 해결됨) → 3 → 5, 6, 7, 8 → 10, 11.
