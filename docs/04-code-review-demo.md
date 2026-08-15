# 코드 리뷰 데모 — 3계층 통신 시연 체크리스트 (프론트 ↔ 백엔드 ↔ 로봇단)

> 목적: 실로봇 없이, **버튼 클릭 → 백엔드 REST → 로봇단(§10) 발행**이 터미널 로그로
> 왕복하는 것과, **로봇 상태 → 백엔드 → UI**(WS)가 흐르는 것을 눈으로 보여준다.
> 아래는 전부 이 저장소에서 실제로 돌려 검증한 절차다(2026-08-07).

## 0. 3계층 데이터 흐름

```
┌─────────────────────────── 계층 1: 프론트엔드 (React, :5175) ───────────────────────────┐
│  src/App.tsx → 각 패널(TopBar·RobotStatusCard·MapPanel …)                                │
│  src/context/DashboardContext.tsx   ← 단일 진실 원천(상태) + 버튼 핸들러                 │
│  src/lib/backendClient.ts           ← 버튼→REST 번역 / WS 프레임→화면 번역               │
└───────────────┬──────────────────────────────────────────────▲──────────────────────────┘
       (하행) 버튼 클릭 → HTTP POST                     (상행) WS 프레임 → 화면 갱신
                │  fetch(http://localhost:8000/api/*)             │  ws://localhost:8000/ws/monitor
                ▼                                                 │
┌─────────────────────────── 계층 2: 백엔드 (FastAPI, :8000) ──────────────────────────────┐
│  app/routers/*        REST 엔드포인트(검증·상태코드·봉투)                                 │
│      │  get_bridge().publish_command(robot_id, TYPE, payload)                             │
│      ▼                                                                                    │
│  app/bridge.py (시임) → app/robot_bridge.py  ← §10 규격: envelope 생성·세션·ACK           │
│      │                                                    ▲                               │
│      │ publish(/backend/{robot_id}/command)  app/bridge_backend.py (BackendSink)          │
│      │                                        · 구독 데이터를 DB 반영 + WS 브로드캐스트    │
│  app/connection_manager.py  ── WS 브로드캐스트(SNAPSHOT/ROBOT_STATUS/EVENT/MISSION …) ────┘
└───────────────┬──────────────────────────────────────────────▲──────────────────────────┘
       (하행) /backend/{robot_id}/command (std_msgs/String, JSON)   (상행) /{robot_id}/robot_state·detection …
                │  §10-2 envelope                                 │  §10-1 구독
                ▼                                                 │
┌─────────────────────────── 계층 3: 로봇단 (ROS2 Humble) ─────────────────────────────────┐
│  robot_bridge (loopback=터미널 로그 / ros2=rclpy 실기)                                    │
│  실로봇/PC2 노드가 붙으면: 명령 수신 → 수행 → /{robot_id}/command_ack(§10-3) + 텔레메트리 │
│  ※ 지금은 ROS2 미설치 → loopback 이 "로봇으로 나갈 프레임"을 터미널에 찍어 대체          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## 1. 버튼 → 엔드포인트 → 로봇단 매핑 (src/lib/backendClient.ts 기준)

| UI 조작 | 프론트 함수 | 백엔드 REST | 로봇단 발행(§10-2) |
|---|---|---|---|
| 헤더 [통합 순찰 시작] | backendStartAll | POST /api/patrol/start | `START_PATROL` × 대상 로봇 |
| 헤더 [도킹 스테이션 복귀] | backendDock | POST /api/robots/{id}/dock | `DOCK` |
| 헤더 [긴급정지] | backendEstopAll | POST /api/robots/emergency-stop-all | `ESTOP` × 전체 |
| 카드 [일시정지] | backendCommand pause | POST /api/patrol/{mission}/pause | `PAUSE` |
| 카드 [복귀/재개] | backendCommand resume | POST /api/patrol/{mission}/resume | `RESUME` |
| 카드 [정지] | backendCommand estop | POST /api/robots/{id}/emergency-stop | `ESTOP` |
| 카드 [리셋] | backendCommand reset | POST /api/robots/{id}/resume | `RESET` |
| 지도 클릭 → 전송 | backendGoto | POST /api/robots/{id}/goto | `GOTO` |
| (상행) 탐지 수신 | — (로봇/CCTV가 호출) | POST /api/events/detect | — (EVENT를 WS로 방송) |

## 2. 터미널 준비 (3개 창)

```
[터미널 A · 백엔드]  cd hmi/backend
  # 로봇을 온라인/대기로 올려 상태-의존 버튼까지 뜨게 한다(선택)
  ./.venv/bin/python -m scripts.demo_seed_online
  # loopback 브리지 + heartbeat 크게 잡아 기동 (로그가 이 창에 뜬다)
  AMR_BRIDGE_BACKEND=loopback AMR_HEARTBEAT_TIMEOUT_SEC=99999 \
    ./.venv/bin/uvicorn app.main:app --port 8000

[터미널 B · 프론트]  cd hmi/frontend && npm install && npm run dev    # http://localhost:5175
  # .env 없으면 기본이 라이브 모드 → 백엔드가 떠 있으면 자동 연결.
  # 상단 pill 이 "WS 연결됨"(초록) 이면 계층1↔2 연결 성공.

[터미널 C · 상행/보조]  cd hmi/backend   # curl 로 로봇 텔레메트리 흉내낼 때만 사용
```

건강 확인: `curl -s http://localhost:8000/health` → `"ros_bridge":"RobotBridge"` 면 loopback 활성.

## 3. 데모 체크리스트 (클릭 → 기대 로그 → 증명되는 계층)

| # | 브라우저에서 | 터미널 A(백엔드)에서 봐야 할 것 | 증명 |
|---|---|---|---|
| 1 | 상단 pill "WS 연결됨" 확인 | `WS 접속 — 현재 1개 연결` | 1↔2 WS 연결 |
| 2 | [통합 순찰 시작] 클릭 | `[robot_bridge] amr_1 ← START_PATROL CMD-...` (+ amr_2) 그리고 `[loopback] /backend/amr_1/command ← {..."command_type":"START_PATROL"...}` | 1→2→3 하행 |
| 3 | [긴급정지] 클릭 | `[robot_bridge] amr_1 ← ESTOP ...`, `amr_2 ← ESTOP ...` (양쪽) | 1→2→3 하행(항상 동작) |
| 4 | 지도 클릭 → 로봇 선택 → 전송 | `[robot_bridge] amr_1 ← GOTO ...` + envelope 에 waypoints | 1→2→3 하행 |
| 5 | 카드 [정지]/[일시정지] 등 | 해당 command_type envelope 로그 | 1→2→3 하행 |
| 6 | (상행) 터미널 C 에서 아래 curl | 화면 우측 "이벤트 큐"에 새 이벤트 등장 + 로그 패널 갱신 | 3→2→1 상행(WS) |

3-6 상행 curl (로봇/CCTV 탐지 흉내):
```
curl -s -X POST http://localhost:8000/api/events/detect \
  -H 'Content-Type: application/json' \
  -d '{"source":"amr","robot_id":"amr_2","type":"SMOKE","confidence":0.91,"zone_id":"Z02"}'
```
→ 백엔드가 EVENT 를 WS 로 방송 → 브라우저 이벤트 큐/카메라 패널이 실시간으로 바뀐다.

### 실제 확인된 로그 예시(검증본)
```
[loopback] /backend/amr_1/command ← {"command_id":"CMD-...","command_type":"START_PATROL",
  "payload":{"mission_id":"MSN-...","nodes":[{"node_id":"N-001","x":2.0,"y":2.0,"theta":0.0,"dwell_sec":0}, ...],"loop":true},
  "issued_at":"2026-08-07T04:31:49+09:00"}
[robot_bridge] amr_1 ← START_PATROL CMD-...
```
envelope 가 명세서 §10-2 형식({command_id, command_type, payload, issued_at})과 정확히 일치.

## 4. 로봇단(§10) 단독 왕복 데모 — ROS 없이 계약 전체를 한 번에

REST/브라우저 없이 로봇단 계약(하행 발행 · 상행 ACK · 텔레메트리 · 재접속 복구)을
터미널 하나로 보여준다:
```
cd hmi/backend && ./.venv/bin/python -m scripts.demo_robot_bridge
```
출력: GOTO 발행 → command_ack(eta_sec) 수신 → robot_state/pose/battery/detection 수신 →
다중 AMR 세션 격리 → 끊김 후 재접속 시 미확정 명령 재발행. (전부 §10-1/2/3 준수)

## 5. 자동화 근거(테스트) — 말이 아니라 통과로

```
cd hmi/backend && ./.venv/bin/python -m pytest -q          # 131 passed
./.venv/bin/python -m pytest tests/test_robot_bridge.py -q # 로봇단 §10 계약 13건
./.venv/bin/python -m pytest tests/test_websocket.py -q    # WS 계약(SNAPSHOT/구독/ping) 11건
```
- test_robot_bridge.py: 단일 목표 하달·도착 확인 / 다중 AMR / 끊김→재접속 복구 / §10-1 파싱.
- test_websocket.py: 접속 즉시 SNAPSHOT, 구독 필터, 모르는 메시지 무시(관제 안 죽음).

## 6. ★진짜 ROS2 3계층 데모 (loopback 아님 — 실제 DDS 토픽)

이 머신엔 ROS2 Humble(`/opt/ros/humble`)이 실제로 깔려 있어, 백엔드가 rclpy 로 실제
토픽을 발행/구독하고 **별도 테스트 로봇 노드**가 진짜 ROS2 로 주고받는다. loopback 이
"나갈 문자열을 로그로 찍는" 흉내였다면, 이건 실제 DDS 통신이다. (아래 절차 검증 완료)

### 구조 — 비동기 큐 프로듀서-컨슈머 (auto-dump-bot 계승, `app/robot_bridge.py:Ros2Bridge`)
- **상행(로봇→백엔드)**: ROS spin 스레드의 구독 콜백은 asyncio/DB/WS 를 직접 건드리지
  않고, ROS 메시지에서 값만 뽑아 `loop.call_soon_threadsafe(_enqueue)` 로 이벤트 루프의
  `asyncio.Queue` 에 넣기만 한다(프로듀서). 이벤트 루프의 **컨슈머 코루틴**이 큐를 비우며
  `bridge.on_*`(파싱·DB·브로드캐스트)를 실행한다. 고빈도 텔레메트리로 큐가 차면 오래된
  프레임을 버린다.
- **하행(백엔드→로봇)**: 라우터가 `publish_command → node.publish → Publisher.publish` 를
  직접 호출한다(rmw 가 스레드 안전 보장 — 상·하행 비대칭은 의도된 설계).

### 터미널 3개 + 브라우저
```
[A · 백엔드(ROS2 모드)]  cd hmi/backend
  bash scripts/run_backend_ros2.sh
  # ROS source + venv PYTHONPATH 합쳐 rclpy 연동으로 uvicorn 기동. venv 는 안 건드림.
  # 확인: curl -s localhost:8000/health → "ros_bridge":"RobotBridge","ros_connected":true

[B · 테스트 로봇 노드]  cd hmi/backend
  source /opt/ros/humble/setup.bash
  python3 -u scripts/fake_robot_node.py amr_1 amr_2
  # /backend/{id}/command 구독→출력, command_ack + robot_state/pose/battery 발행(1Hz).
  # 이 노드가 텔레메트리를 올리는 순간 로봇이 online/IDLE 로 바뀐다(seed 불필요).

[C · (선택) 원시 DDS 확인]  source /opt/ros/humble/setup.bash
  ros2 topic list | grep /amr_        # 백엔드가 만든 §10 토픽 18개 확인
  ros2 topic echo /backend/amr_1/command      # 버튼 누르기 전에 켜두면 원시 메시지가 실시간으로 뜸

[브라우저]  http://localhost:5175  (프론트는 그대로 npm run dev)
```

### 체크리스트 (검증된 실제 로그)
| 브라우저/REST | [A] 백엔드 로그 | [B] 로봇 노드 출력 | 증명 |
|---|---|---|---|
| 백엔드 기동 | `[robot_bridge] ROS2 브리지 기동 — 발행 2개, 구독 8토픽/로봇, 상행 큐 컨슈머 1` | — | 프로듀서-컨슈머 배선 |
| 로봇 노드 켜짐 | — | 기동 배너 | 3↔2 ROS 연결 |
| (자동) 텔레메트리 | robots online 됨 (`GET /api/robots` state=IDLE) | 1Hz robot_state/pose/battery 발행 | **3→2→1 상행** |
| **[통합 순찰 시작]** | `[robot_bridge] amr_1 ← START_PATROL CMD-x` | `📥 명령 수신: START_PATROL (CMD-x)` → `📤 command_ack 발행` | **1→2→3→2 왕복** |
| 지도 클릭/카드 버튼 | `[robot_bridge] amr_1 ← GOTO CMD-y` | `📥 GOTO CMD-y` → `📤 ack` | 1→2→3 |
| 명령 후 상태 | — | 로봇이 PATROLLING 텔레메트리 발행 | `GET /api/robots` 가 PATROLLING (왕복 최종 확인) |

핵심: 웹에서 누른 명령의 **command_id 가 백엔드 로그·로봇 노드 수신·ACK 에 동일하게 찍힌다**
= 같은 메시지가 실제 ROS2 토픽을 타고 왕복했다는 증거. 그리고 로봇이 되보낸 텔레메트리로
화면의 로봇 상태가 실제로 바뀐다.

### 주의
- 백엔드는 반드시 `scripts/run_backend_ros2.sh` 로 띄운다(그냥 uvicorn 은 rclpy 를 못 봄).
  `health` 의 `ros_connected:true` 를 꼭 확인.
- `fake_robot_node.py` 는 ROS 만 source 하면 되고 앱 의존성은 필요 없다(순수 rclpy 노드).
- 포트 8000 이 이미 물려 있으면 새 서버가 바인딩 실패한다 → `pkill -f "uvicorn app.main"` 먼저.

## 7. (참고) loopback vs ros2 요약
| 모드 | 실행 | 로봇단 | 용도 |
|---|---|---|---|
| `null`(기본) | `uvicorn app.main:app` | `[NullBridge] … → START_PATROL {…}` 로그만 | 기존 동작 |
| `loopback` | `AMR_BRIDGE_BACKEND=loopback uvicorn …` | `[loopback] /backend/amr_1/command ← {envelope}` 로그 | ROS 없이 §10 규격 확인 |
| `ros2` | `bash scripts/run_backend_ros2.sh` + fake_robot_node | **실제 DDS 토픽 왕복** | 진짜 3계층 데모(§6) |

## 8. 데모 후 DB 초기화(선택)
데모로 생긴 미션/상태를 지우고 처음부터:
```
cd hmi/backend && ./.venv/bin/python scripts/init_db.py --drop   # 드롭+재생성+시드
./.venv/bin/python -m scripts.demo_seed_online                   # 로봇 다시 온라인/IDLE
```
