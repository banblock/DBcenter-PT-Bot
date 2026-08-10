# 2026-08-10 하드웨어 테스트 세션 정리

`fleet-control` 브랜치, robot3/robot8 실기로 점유(occupancy)·긴급정지·도킹·이상신호
시나리오를 순서대로 테스트하면서 발견/수정한 문제 전체 기록. 커밋 `a9473b6`
(오전 수정분, 이미 push됨) 이후부터 이 세션에서 만든 커밋 11개는 **아직
push 안 됨** — 로컬에만 있으니 직접 `git push origin fleet-control` 필요.

각 문제의 상세 원인/코드 diff 설명은 `docs/fleet_control_known_issues.md`
이슈 12~18번에 더 자세히 있음 - 이 문서는 세션 전체를 시간 순서로 훑는
용도.

---

## 타임라인 요약

| # | 테스트/발견 | 문제 | 원인 요지 | 커밋 |
|---|---|---|---|---|
| 1 | 점유 코드 최초 분석 | (분석만) | 0번 순찰 지점이 그래프 밖에서 무보호 직행 | - |
| 2 | robot8 localization 실행 | AMCL이 Active로 안 넘어감 | lifecycle 서비스 응답 타임아웃(DDS/시스템 부하) | 코드 아님, 환경 문제 |
| 3 | 첫 실기 순찰 | robot3·robot8 통로에서 충돌 | 0번 지점 무보호 직행(이슈 12) | `e245184` |
| 4 | 긴급정지 재현 | control_node hang | `cancelTask()` idle 상태 재취소 시도 → 재진입 hang | `e245184` |
| 5 | 재테스트 | robot3 이동 중에도 hang | `cancelTask()`를 콜백에서 직접 부르는 것 자체가 재진입 | `931768a` |
| 6 | 도킹 첫 테스트 | 도킹 이동이 통로 그래프 안 거침 | `/backend/dock`이 좌표 하나만 보냄 | `dbe2796` |
| 7 | 순찰 재개 후 도킹 재현 | 웨이포인트가 점유지 위에서 멈추면 점유 놓아버림 | 도착마다 무조건 release → 재요청 레이스(이슈 4) | `71b6864` |
| 8 | 긴급정지→해제 후 도킹 | 도킹 후 undock 없이 바로 재순찰 | 도킹 전 몰래 채워진 옛 미션이 `self.mission`에 남음 | `fad8a2b`(포함) |
| 9 | 이상신호 사전 점검 | 이상신호 중 긴급정지 무반응 회귀 예상 | `wait_until_pose_reached()`가 `emergency_stopped` 안 봄 | `fad8a2b` |
| 10 | 설계 재확인 | 이상신호가 자동 판정 후 자동 복귀 중 | 실제 설계는 운영자(HMI)가 재개/도킹 결정 | `54151c6` |
| 11 | CCTV 좌표 질문 | CCTV 좌표가 랙 안쪽일 수 있음 | 좌표 직행 대신 그래프 스냅 필요 | `8cf26fa` |
| 12 | 추가 요청 | 이상신호 이동도 그래프 경로 필요 | 좌표 스냅만 하고 직행은 그대로였음 | `8cf26fa` |
| 13 | 추가 요청 | 카메라 조기 포착 시 조기정지 필요 | 신규 기능 | `59c56e2` |
| 14 | 조기정지 재현 테스트 | 순찰 로봇이 정지 중인 이상신호 로봇을 그대로 지나침 | 도킹/이상신호 point_id가 순찰 point_id와 문자열이 달라 뮤텍스 우회(이슈 18) | `5808ecb` |

---

## 1. 점유(occupancy) 코드 최초 분석

세션 시작 시점에 "지금 하드웨어 테스트 중인데 점유상황에서 문제가 생겼다"는
보고를 받고 `fleet_node.py`/`control_node.py`/`zone_router.py`를 코드
리딩으로 먼저 분석. 이 시점에 발견한 구조적 공백:

- **0번 순찰 지점이 그래프 라우팅 대상이 아님**: `zone_router._route_zone()`이
  "로봇이 지금 어디서 출발하는지 모른다"는 이유로 미션의 0번 웨이포인트를
  point_id 없이 그냥 박아 넣고, Control Node는 그 좌표로 Nav2 직행. 로봇이
  도킹 스테이션에서 출발해 공유 통로를 가로질러 첫 순찰 지점까지 가는
  구간이 occupancy 보호를 전혀 못 받는 구조였음.

실측 로그(`ros2 topic echo /robot3/amcl_pose` 등)로 실제 좌표를 확인하며
이 분석을 뒷받침함.

## 2. 로컬라이제이션/DDS 문제 (코드 문제 아님)

robot8의 `turtlebot4_navigation localization.launch.py` 실행 중
`amcl.change_state` 서비스 응답이 타임아웃되는 걸 발견:
```
[amcl-2] [WARN] ... failed to send response to /robot8/amcl/change_state (timeout)
```
lifecycle_manager가 응답을 못 받아 amcl이 Active로 안 넘어가는 문제로 진단 -
DDS 디스커버리/시스템 부하 계열(체크리스트에 이미 기록된 지난 사고와 같은
부류). `[RTPS_TRANSPORT_SHM Error]` 경고도 같이 관찰됨(stale 공유메모리
세그먼트 가능성). **코드로 고칠 수 있는 문제가 아니라 환경 진단만
제공** - `docs/hardware_test_checklist.md`의 DDS 사전 점검 절차 참고하며
재시작으로 우회.

이 문제 때문에 로봇 위치를 amcl_pose로 못 받아오는 경우를 대비해
`fleet_node.py`에 `DEFAULT_ROBOT_START` 시드값(robot3/robot8 실측 좌표)을
추가해 `self._robot_pose` 초기값으로 미리 채워둠(`e245184`).

## 3. 0번 지점 그래프 라우팅 (이슈 12) — `e245184`

`zone_router._route_zone()`에 `start_pos` 파라미터를 추가해 0번 지점도
"현재 위치 → 0번 지점" 홉으로 취급, 그래프 경로/occupancy 태깅 대상이
되도록 리팩터링(`_emit_hop()` 헬퍼로 공통화). `fleet_node.py`가
`_apply_zones()`에서 `build_missions()` 호출 전에 `_ensure_robot_pubs()`를
먼저 실행해 amcl_pose 구독이 최대한 일찍 걸리도록 순서도 변경.

## 4~5. 긴급정지 `cancelTask()` 재진입 hang (이슈 13) — `e245184`, `931768a`

**1차 진단(틀렸음, 이후 정정)**: robot8이 크로싱 grant를 기다리며 유휴
상태일 때 긴급정지가 오면, `cancelTask()`가 이미 SUCCEEDED로 끝난 이전
목표를 다시 취소하려다 응답을 못 받아 블로킹된다고 진단 -
`result_future.done()`일 때만 `cancelTask()`를 부르도록 가드 추가.

**재테스트에서 반증**: robot3처럼 **진짜로 이동 중**이던 로봇도 똑같이
멈추는 게 확인됨 - 1차 수정으로는 해결 안 됨. 근본 원인을 다시 찾음:
`nav2_simple_commander`의 `isTaskComplete()`/`cancelTask()`가 둘 다
`rclpy.get_global_executor()`가 반환하는 **프로세스 전역 단일
SingleThreadedExecutor**를 스핀하는데, `_move_to()`의 폴링 루프가 그
executor를 스핀하는 도중 실행된 콜백(`_on_emergency_stop`)이 곧바로
`cancelTask()`를 부르면 같은 executor를 재진입해서 다시 스핀하게 되고,
`SingleThreadedExecutor`는 재진입을 지원하지 않아 그대로 멈춤.

**최종 수정**: `_on_emergency_stop()`에서 `cancelTask()`를 아예 직접 안
부르고 `emergency_stopped` 플래그만 세운다. `_move_to()`의 폴링 루프가
매 반복(~0.1초)마다 이 플래그를 콜백 스택 밖의 안전한 위치에서 확인해
취소하므로 "즉시 정지"는 유지되면서 재진입 경로가 사라짐.

## 6. 도킹 이동 그래프 라우팅 (이슈 14) — `dbe2796`

`/backend/dock`이 `DOCK_STATIONS`의 고정 좌표를 그대로 보내서, 도킹
이동도 0번 지점과 같은 부류로 통로 그래프/occupancy를 안 거치는 문제
발견. `zone_router.route_to_point(graph, start_pos, target, id_prefix)`
신규 - 순찰과 같은 `_emit_hop()` 파이프라인으로 시작 위치→목표 지점
경로를 계산하고, 1회성 이동이라 "실제로 공유되는지" 미리 알 수 없으므로
지나가는 모든 홉에 항상 point_id를 태깅. Control Node의 `_handle_dock()`을
재작성해서 각 홉마다 크로싱 요청/이동/해제를 하며 마지막 지점에서 실제
`navigator.dock()` 호출.

같은 세션에서 순찰 사이 10분 대기 중에도 도킹 트리거가 즉시 반영되도록
`mission_flow.wait_until_next_patrol(should_interrupt=...)` 추가(이슈 #3
부분 해결).

## 7. 연속 웨이포인트 점유 유지 (이슈 4, 원래 known issue) — `71b6864`

방금 만든 그래프 라우팅 덕분에 순찰/도킹 정지 지점이 공유 통로/교차로
위에 오는 경우가 실제로 생겼는데, `_run_current_mission()`/`_handle_dock()`이
웨이포인트 도착마다 무조건 point_id를 release하고 다음 홉에서 재요청하는
구조라, 로봇이 그 자리에 멈춰 서 있는 동안 점유를 놓아버리는 레이스가
있었다(원래 문서화돼 있던 issue 4번이 이번에 실제 발현 조건을 만남).
이미 쥔 point_id는 재요청 안 하고, 다음 웨이포인트도 같은 point_id면
release를 미루도록 수정. `_handle_dock()`은 마지막 지점의 크로싱을
`navigator.dock()` 액션이 끝날 때까지도 들고 있도록 추가 보강.

## 8. 도킹 후 undock 없이 바로 재순찰 — `fad8a2b`에 포함

도킹 완료 직후 로그에서 `docked` 발행과 거의 동시에 `moving to waypoint
1/5`가 찍히는 걸 발견. 원인: 도킹이 끝나기 전(`self.docked`가 아직
`False`인 동안)에도 Fleet이 같은 순찰 미션을 계속 재발행하고
`_on_mission()`이 그걸 그대로 받아버려서, `self.mission`이 도킹 완료
시점엔 이미 옛 순찰 미션으로 몰래 채워져 있었음. `_wait_for_mission()`을
불러도 이미 non-None이라 대기를 건너뛰고 undock 없이 바로 재개. 도킹/
이상신호 인터럽트 처리 직후 `self.mission = None`을 명시적으로 비우도록
수정.

## 9. 이상신호 중 긴급정지 무반응 회귀 — `fad8a2b`

이상신호 테스트 전 점검 중, 4~5번에서 콜백의 직접 `cancelTask()` 호출을
없앤 부작용으로 `_handle_anomaly()`가 쓰던 `wait_until_pose_reached()`가
`emergency_stopped`를 전혀 안 봐서(원래도 collision_risk만 봤음) 이상신호
이동 중 긴급정지가 완전히 무반응이 되는 회귀를 미리 발견. `pose` 인자를
추가해 긴급정지 시 취소 후 해제될 때까지 대기했다가 같은 목표로 재시도하는
패턴을 `_move_to()`와 동일하게 적용. `wait_until_next_patrol()`의
인터럽트 체크에 `anomaly_pending`도 추가.

## 10. 이상신호 설계 변경: 자동 판정 → 운영자(HMI) 결정 — `54151c6`

기존 구현은 로봇이 이상 위치에 도착하면 비전 노드 응답을 15초 기다렸다가
(비전 노드가 없어 항상 타임아웃) 자동으로 순찰을 재개했음. 사용자 확인
결과 실제 설계는: 로봇이 도착(자체감지면 제자리 정지)하면 자동 판정 없이
카메라로 상황을 계속 비추며 무기한 대기하고, HMI에서 운영자가 그 영상을
보고 "재개" 또는 "도킹" 중 하나를 최종 결정. `inspection_flow.py`의
`inspect_anomaly()`(비전 왕복) 제거. 신규 `/backend/anomaly_resume` →
`/fleet/<ns>/anomaly_resume` 경로 추가("재개" 결정), "도킹" 결정은
기존 `/backend/dock`을 그대로 재사용(`_handle_anomaly()`가 직접
`_handle_dock()` 호출).

## 11~12. CCTV 좌표 그래프 스냅 + 이상신호 이동도 그래프 라우팅 (이슈 16) — `8cf26fa`

"CCTV 이상상황 위치가 어디냐"는 질문에서 시작해, CCTV가 주는 좌표가
랙 안쪽처럼 로봇이 못 가는 지점일 수 있다는 게 확인됨(`-2.7, 1.5` 예시로
검증 - 실제로 가장 가까운 통로 요소에서 0.3~0.5m 떨어짐). `RouteGraph.nearest_point()`
(조회 전용 스냅) + `anomaly_control.snap_cctv_location()`(스냅 + 카메라
yaw 계산) 추가. 이어서 "이것도 그래프 경로로 이동해야 한다"는 요청으로
`_on_anomaly_trigger()`도 `zone_router.route_to_point()`를 쓰도록 확장.
Control Node의 도킹/이상신호 이동 로직이 완전히 같아져서
`_traverse_route_with_crossings()` 공통 헬퍼로 통합.

## 13. 카메라 조기 포착 조기정지 (이슈 17) — `59c56e2`

"목적지로 가는 도중 로봇 카메라가 이상 상황을 먼저 포착하면 그 자리에서
멈춰야 한다"는 요청. 비전 노드가 없어 이 신호는 UI가 대신 보내주는 걸로
설계(`/backend/anomaly_captured` → `/fleet/<ns>/anomaly_captured`).
`_move_to(pose, extra_interrupt=None)`에 `(reason, check)` 옵션 인자
추가 - 순찰/도킹 이동 중엔 무의미한 인터럽트를 이상신호 이동에서만
감시하도록. 조기정지 시 카메라 각도는 멈춘 순간의 진행 방향 그대로
유지(별도 회전 없음, 사용자 확인).

## 14. point_id 불일치로 occupancy 뮤텍스 우회 (이슈 18) — `5808ecb`

조기정지 기능까지 넣고 재테스트하다가 발견된 가장 심각한 버그: 이상
위치가 공유 구간이라 이상 감지 로봇은 정지를 잘 했는데, 다른 순찰
로봇이 그 자리를 그냥 지나침. 원인: `build_missions()`(순찰)와
`route_to_point()`(도킹/이상신호)가 "엣지+교차로를 하나의 point_id로
합칠지"를 각자 독립적인 `_UnionFind`로 계산해서, 물리적으로 같은 통로인데
서로 다른 point_id 문자열(예: 순찰은 `J_F_BC`, 이상신호는 `X_V_BC`)을
쓰는 경우가 생겨 Fleet이 둘을 무관한 자원으로 보고 둘 다 grant해준
것이었음.

`build_missions()`가 세 번째 반환값으로 `resource_canonical`(그 호출에서
실제로 계산된 정규 매핑)을 돌려주고, `fleet_node`가 이를 저장해뒀다가
도킹/이상신호 경로 계산 시 `route_to_point(..., canonical_point_ids=...)`로
넘겨서 지역적으로 계산한 id를 정규 id로 치환하도록 수정. 시뮬레이션으로
검증: 수정 전/후 이상신호 경로의 point_id가 각각 `X_V_BC` → `J_F_BC`로
바뀌어 순찰 로봇과 정확히 일치함을 확인.

---

## 변경된 파일 (커밋 `a9473b6` 이후 누적)

```
docs/fleet_control_known_issues.md             | 377 +++++++++++++++++++++---
docs/hardware_test_checklist.md                |  69 ++++-
scripts/hw_test/README.md                      |   3 +-
scripts/hw_test/anomaly_captured.sh            |  12 + (신규)
scripts/hw_test/anomaly_done.sh                |  10 - (삭제, 옛 흐름 잔재)
scripts/hw_test/anomaly_resume.sh              |  12 + (신규)
src/control_amr/control_amr/control_node.py    | 380 ++++++++++++++++--------
src/control_amr/control_amr/inspection_flow.py |  39 +-- (inspect_anomaly 제거)
src/control_amr/control_amr/mission_flow.py    |  10 +-
src/control_amr/control_amr/navigation_flow.py |  12 - (wait_until_pose_reached 제거)
src/fleet/fleet/anomaly_control.py             |  18 ++
src/fleet/fleet/fleet_node.py                  | 136 ++++++-
src/fleet/fleet/route_graph.py                 |  18 ++
src/fleet/fleet/zone_router.py                 | 245 ++++++++------
src/test/code/build_route_report.py            |   2 +-
src/test/code/render_routes.py                 |   2 +-
src/test/code/test_zone_router.py              |  16 +-
```

## 커밋 목록 (전부 로컬에만 있음 - push 필요)

```
e245184  robot3/robot8 실기 테스트에서 발견된 점유·긴급정지 문제 2건 수정
931768a  긴급정지 콜백의 cancelTask() 재진입 hang을 근본 수정
dbe2796  도킹 이동을 통로 그래프/occupancy 프로토콜로 라우팅
71b6864  순찰/도킹 웨이포인트가 점유 자원 위에서 멈출 때 점유를 놓지 않도록 수정
fad8a2b  이상신호 이동 중 긴급정지 무반응 회귀 수정, 10분 대기 중 이상신호 인터럽트 반영
54151c6  이상신호 대응을 비전 노드 자동 판정에서 운영자(HMI) 결정 방식으로 변경
351bfbb  하드웨어 테스트 체크리스트 시나리오 5·6, 토픽 목록 갱신
3cac9eb  hw_test 스크립트를 이상신호 운영자 결정 방식에 맞게 교체
8cf26fa  이상신호 이동도 통로 그래프 경로로 계산하고 카메라만 회전
59c56e2  이상신호 이동 중 카메라 조기 포착 시 목적지 도달 전 정지 기능
5808ecb  도킹/이상신호 경로의 point_id를 순찰 로봇과 같은 정규 id로 통일
```

## 남은 과제 / 아직 검증 안 된 것

- **`route_to_point()`의 홉 0(이미 도착) 케이스** — 여전히 `point_id: None`
  무보호. 재현된 적 없는 좁은 엣지케이스(이슈 18 "남은 한계" 참고).
- **`_request_crossing()`/`_wait_for_mission()`이 여전히 `dock_pending`/
  `anomaly_pending`을 안 봄** — 크로싱 grant 대기(최대 15초) 중에는
  인터럽트 반응이 그만큼 늦을 수 있음(이슈 #3 남은 부분).
- **이상신호 대응 중 크로싱을 무기한 들고 있는 트레이드오프** — 운영자
  결정이 오래 걸리면 그 자원을 다른 로봇이 그만큼 못 씀(이슈 16 참고,
  현재는 안전 우선으로 의도적으로 남겨둠).
- **로봇8 AMCL의 lifecycle 타임아웃 근본 원인 미해결** — DDS/시스템 부하
  진단만 하고 코드 수정은 아님. 재발하면 체크리스트의 DDS 사전 점검
  절차부터 다시 밟을 것.
- 이번 세션에서 만든 기능들(도킹 그래프 라우팅, 이상신호 재설계, 조기정지,
  point_id 통일) 모두 **시뮬레이션/코드 리딩으로는 검증했지만 하드웨어
  전체 시나리오 재테스트는 아직** — 커밋 push 후 체크리스트 시나리오
  3~6 전체를 처음부터 다시 돌려보는 걸 권장.
