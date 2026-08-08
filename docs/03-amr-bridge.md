# AMR Bridge Service 설계 — `robot_bridge.py` (명세서 §10)

> 기준: `API 명세서 (v1.0)` §9 WebSocket · §10 ROS2 인터페이스 · §11 Enum.
> 이 문서와 명세서가 어긋나면 **명세서가 우선**한다.

백엔드(PC1)와 AMR(ROS2 Humble) 사이의 통신 계층. 명세서 §10 이 규정한 토픽 계약을
그대로 구현하며, 코드는 세 조각으로 나뉜다.

| 파일 | 책임 |
|---|---|
| `app/robot_bridge.py` | §10 규격 로직 — 발행 envelope, ACK, 구독 파싱, 세션·재접속. **ROS2/DB 무의존** |
| `app/bridge_backend.py` | `BackendSink` — 구독 데이터를 DB 반영 + WS 브로드캐스트로 배선 |
| `app/bridge.py` | 기존 시임(`Bridge` 프로토콜 / `set_bridge()`) — 교체 지점 |

## 1. 통신 방식 (§10)

네임스페이스는 `robot_id` (`/amr_1/...`, `/amr_2/...`). 전송은 `std_msgs/String` JSON
직렬화를 기본으로 하고, 위치·배터리만 표준 ROS 메시지 타입을 쓴다.

### 1-1. 구독 — 로봇 → 백엔드 (§10-1)

| 토픽 | 타입 | 파싱 규칙 | → 백엔드 |
|---|---|---|---|
| `/{robot_id}/robot_state` | std_msgs/String | `"STATE:msg"` | `ROBOT_STATUS` 갱신·방송 |
| `/{robot_id}/amcl_pose` | geometry_msgs/PoseWithCovarianceStamped | x·y·yaw | pose 갱신 |
| `/{robot_id}/battery_state` | sensor_msgs/BatteryState | percentage(0~1)→0~100 | battery 갱신 |
| `/{robot_id}/detection` | std_msgs/String | JSON | `DETECTION` 방송 |
| `/{robot_id}/aruco_correction` | std_msgs/String | JSON | `POSE_CORRECTED` 방송 |
| `/{robot_id}/safety_event` | std_msgs/String | `"CODE:detail"` | ErrorLog + `SYSTEM_ALERT` |
| `/{robot_id}/checkpoint` | std_msgs/String | `"STATE\|node\|step"` | `checkpoint_json` 보존 |

### 1-2. 발행 — 백엔드 → 로봇 (§10-2)

토픽 `/backend/{robot_id}/command` (std_msgs/String, JSON) — 백엔드가 발행하므로 `/backend` prefix
(patrol_interfaces 규약). envelope:

```json
{ "command_id": "CMD-...", "command_type": "GOTO",
  "payload": { ... }, "issued_at": "2026-08-05T09:45:03+09:00" }
```

command_type 은 §10-2 표 **10종 고정** (`START_PATROL`·`PAUSE`·`RESUME`·`CANCEL`·`GOTO`·
`INSPECT`·`EVACUATE`·`ESTOP`·`RESET`·`DOCK`). 표에 없는 값은 발행이 거부된다. 기존
라우터(`robot_router`·`patrol_router`·`event_router`·`suppression_router`)가 이미 이
command_type 으로 `get_bridge().publish_command()` 를 호출하므로 배선 변경이 없다.

### 1-3. 명령 ACK (§10-3)

토픽 `/{robot_id}/command_ack` → `{command_id, accepted, reason, eta_sec}`. 도착·수락
확인이 오면 해당 명령을 inflight 에서 제거하고 `eta_sec` 을 잡는다(§4-2 goto 응답의
`eta_sec` 출처).

## 2. AMR 등록·세션 관리

`AmrSession(robot_id, connected, last_seen, last_state, inflight)` 을 robot_id 로 키잉한
레지스트리. 구독 프레임이 한 번이라도 오면(`_touch`) 해당 세션은 `connected=True`,
`last_seen` 갱신. 다중 AMR 은 네임스페이스로 완전히 분리돼 서로의 큐/상태를 침범하지 않는다.

## 3. 통신 끊김·재접속 정책

* **끊김**(`on_disconnect`): `connected=False`. **ACK 못 받은 inflight 명령은 버리지 않는다.**
* **재접속**(`on_reconnect`): `connected=True` 로 올리고 미확정 inflight 를 **다시 발행**해
  하달 중이던 목표를 복구한다. 이미 ACK 된 명령은 재전송 대상이 아니다.
* heartbeat 타임아웃(기본 3초, `settings.heartbeat_timeout_sec`) 은 기존
  `_heartbeat_watchdog` + `crud.robots.mark_offline_stale` 가 담당해 `ROBOT_OFFLINE` 를 방송한다.

## 4. 활성화

`settings.bridge_backend` (env `AMR_BRIDGE_BACKEND`):

| 값 | 동작 |
|---|---|
| `null` (기본) | `NullBridge` 유지 — 발행하지 않음. 기존 동작·테스트 불변 |
| `loopback` | `RobotBridge` + `BackendSink`, 발행은 로그로만. ROS2 없이 §10 규격 검증용 |
| `ros2` | `build_ros2_bridge()` — rclpy pub/sub 실기 연동 (PC2 ROS2 Humble 노드) |

## 5. 아직 붙지 않은 것 (실기 연동)

`build_ros2_bridge()` 는 rclpy import 를 시도하고, 없으면 명확한 오류를 던진다.
이 저장소에는 ROS2 가 없어 **실제 토픽 pub/sub 배선과 PC2 ROS2 노드 연동은 미완**이다.
`RobotBridge` 본체(§10 규격)는 완성·검증돼 있으므로, 실장비에서는 `_Ros2Node` 의
`create_subscription(...)` 으로 `bridge.on_*` 콜백만 연결하면 된다.
