# 아키텍처 개요 — AMR 순찰·이상감지 관제 시스템

> 기준 문서: `API 명세서 (v1.0)`, `세부 기능 체크리스트 (v1.0)`
> 이 문서와 명세서가 어긋나면 **명세서가 우선**한다. 명세서를 벗어난 결정은
> 본 문서의 "명세서에서 벗어난 결정" 절에 전부 모아 두었다.

## 1. 배치

```
┌──────────────────────────────────────────────────────────────────────┐
│ 관제 PC (PC1 / MSI)                                                   │
│                                                                       │
│  hmi/frontend  (React + Vite, :5175)                                  │
│        │  REST  http://localhost:8000/api/*                           │
│        │  WS    ws://localhost:8000/ws/monitor                        │
│        ▼                                                              │
│  hmi/backend   (FastAPI + SQLite, :8000)                              │
│        │                                                              │
│        ├── SQLite  data/amr.db      (이력·마스터·설정)                │
│        ├── media/                    (스냅샷 이미지·맵 파일)          │
│        └── ROS2 브리지 시임 (app/bridge.py) ── 미연결(NullBridge)     │
└──────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ ROS2 Humble (예정)
                        ┌──────────┴──────────┐
                        │  AMR ×2 (amr_1/2)   │   PC2(VIC) 비전 추론
                        └─────────────────────┘
```

## 2. 데이터 흐름 (3-Lane)

체크리스트 §0-1 의 3개 레인을 그대로 구현했다.

| Lane | 방향 | 경로 | 구현 위치 |
|---|---|---|---|
| **상행 (Telemetry)** | ROS/CCTV → UI | `bridge` → `broadcast_queue` → `consumer_loop` → `manager.broadcast()` → WS | `app/connection_manager.py` |
| **하행 (Command)** | UI → ROS | `POST /api/*` → router → DB 기록 → `bridge.publish_command()` | `app/routers/*`, `app/bridge.py` |
| **조회 (Query)** | UI ↔ DB | `GET /api/*` → CRUD → SQLite | `app/crud/*` |

### 큐를 2개로 나눈 이유

위치 텔레메트리는 5Hz × 로봇 수로 쏟아지고, 넘치면 버려도 된다(다음 프레임이 곧 온다).
반면 화재 이벤트는 **절대 버리면 안 된다**. 큐가 하나면 위치 데이터가 큐를 채웠을 때
이벤트가 드롭될 수 있으므로 분리했다.

| 큐 | 크기 | 포화 시 정책 |
|---|---|---|
| `telemetry_queue` | 2000 | 가장 오래된 프레임 drop + WARN 로그 |
| `event_queue` | 500 | **드롭 금지**. ERROR 로그만 남기고 소비 루프가 비우길 기다린다 |

## 3. 모듈 구조

```
hmi/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI 앱, lifespan, 예외 핸들러, 라우터 등록
│   │   ├── config.py               Settings 단일 소스 (env 접두사 AMR_)
│   │   ├── enums.py                명세서 §11 Enum 고정값 + WS 타입/토픽
│   │   ├── errors.py               에러 카탈로그 + ApiError
│   │   ├── responses.py            공통 응답 봉투 ok/fail/paginated
│   │   ├── middleware.py           요청 ID·요청 로깅·역할 헤더 검증
│   │   ├── logging_config.py       로거 계층 (amr.request / amr.ws / amr.db …)
│   │   ├── security.py             역할·권한 (로그인 없음, advisory)
│   │   ├── database.py             엔진/세션 + SQLite PRAGMA
│   │   ├── models.py               ORM 20개 테이블 + UtcDateTime
│   │   ├── schemas.py              Pydantic 요청/응답 규격
│   │   ├── connection_manager.py   WS 연결·구독 필터·큐·소비 루프
│   │   ├── ws.py                   /ws/monitor 엔드포인트 + SNAPSHOT 구성
│   │   ├── bridge.py               ROS2 명령 하달 시임 (NullBridge)
│   │   ├── seed.py                 설비 기준값·대조 룰·데모 맵 시드
│   │   ├── crud/                   저장 관심사 (ids, maps, patrol, robots,
│   │   │                            events, equipment, system)
│   │   ├── routers/                HTTP 관심사 (§1~§8 라우터 14개)
│   │   └── services/               도메인 판단 (align_engine, priority)
│   ├── scripts/init_db.py          마이그레이션 + 스키마 점검
│   └── tests/                      pytest 87건
│
└── frontend/src/
    ├── app/                        라우팅·앱 셸·권한 가드
    ├── pages/                      대시보드 + 서브페이지 5종
    ├── components/
    │   ├── ui/                     공통 컴포넌트 (도메인 무지)
    │   └── <Panel>/                대시보드 패널
    ├── store/                      zustand 슬라이스 7개
    ├── services/
    │   ├── ws/                     WebSocketClient · dispatcher · messages
    │   ├── http/                   httpClient (_get/_post/_put/_delete)
    │   └── api/                    엔드포인트 래퍼
    ├── auth/                       역할·권한 매트릭스
    ├── hooks/                      useCommands · useDashboard · useAsync
    └── constants/ utils/ styles/
```

### 계층 규칙

* **라우터**는 HTTP 관심사만 (검증·상태코드·봉투). DB 세션을 직접 만지지 않는다.
* **CRUD**는 저장 관심사만. "없으면 404" 같은 규칙이 여기 한 곳에 있다.
* **서비스**는 여러 테이블에 걸친 판단 (대조 판정, 우선순위 산정).
* 프론트 **컴포넌트**는 서버 데이터를 직접 fetch 하지 않는다. store 선택자만 읽는다.
  서버 데이터의 입구는 `services/ws/dispatcher.ts` 와 `services/api/*` 뿐이다.

## 4. 명세서에서 벗어난 결정

작업 중 명세서에 없거나 다르게 정한 것들. **변경하려면 알려 주면 되돌린다.**

| # | 결정 | 이유 |
|---|---|---|
| 1 | 백엔드 위치를 `code/backend/` 대신 **`hmi/backend/`** | 기존 `hmi/frontend/` 와 나란히 두는 편이 HMI 계층 경계가 명확 (사용자 승인) |
| 2 | **로그인 없음**. 권한은 `X-Role` 헤더 기반 advisory | 사용자 결정. 보안 경계가 아니라 오조작 방지용 — §5 참조 |
| 3 | 테이블 2개 추가: `tb_event_timeline`, `tb_system_config` | 타임라인을 로그 역파싱하지 않기 위해 / 진압 모드가 재기동 후에도 유지되어야 해서 |
| 4 | 컬럼 보강 (`tb_maps.width/height`, `tb_events.arrived_at` 등) | 명세서 응답 예시가 요구하는데 체크리스트 컬럼 목록에 없던 것들. `models.py` 에 `(보강)` 주석 |
| 5 | REST 경로 `POST /api/map/slam/start` 유지, `POST /api/robots/{id}/emergency-stop` | 체크리스트 F-09 는 `/api/robot/emergency-stop` 로 적었으나 명세서 4-3/4-4 의 `/api/robots/...` 를 따름 |
| 6 | 403 상태코드 추가 | 명세서 공통 에러는 400/404/409/500 뿐. 권한 거부를 400 으로 뭉개면 프론트가 구분 못 함 |
| 7 | 프론트 기본 테마 **light** 유지 | 체크리스트 §3-12 는 다크를 요구하지만 기존 화면을 바꾸지 않기 위해. 토글 제공, 기본값은 `uiStore.ts` 의 `DEFAULT_THEME` 한 줄 |
| 8 | 상태 `ASSIGNED` 를 로봇 상태에서 제외 | 명세서 §11 ROBOT_STATE 15종에 없음. 이벤트 상태(`EVENT_STATUS.ASSIGNED`)와 혼동을 피함 |
| 9 | 노드 분배는 라운드로빈 | B-24 의 "구역 분할 or 최근접"은 로봇 위치·코스트맵이 필요. dispatcher 서비스가 붙을 때 교체 |

## 5. 권한 모델의 한계 (반드시 읽을 것)

로그인을 두지 않기로 했으므로, 이 시스템의 권한은 **인증이 아니다**.

* 역할은 브라우저 localStorage 에 저장되고 사용자가 직접 바꿀 수 있다.
* 백엔드는 프론트가 보내는 `X-Role` 헤더를 그대로 믿는다. **위조 가능하다.**
* 즉 권한 계층은 "관리자만 누르는 버튼을 뷰어가 실수로 호출"하는 사고를 막을 뿐이다.

**실제 보안 경계는 망 분리와 물리적 접근 통제다.** 이 시스템이 외부망에 노출되는
순간 이 모델은 무효가 되며, 서버 측 인증(JWT 등)을 반드시 붙여야 한다.

## 6. 아직 붙지 않은 것

| 항목 | 현재 상태 | 붙일 자리 |
|---|---|---|
| ROS2 브리지 | `NullBridge` — 명령을 로그만 남기고 실제로 보내지 않음. 응답에 `dispatched:false` 로 정직하게 표시 | `app/bridge.py` 의 `set_bridge()` |
| 비전 추론(PC2) | 없음. `POST /api/events/detect` 로 결과를 받을 준비만 됨 | — |
| 진압 실장비 | 인터락 검사·상태 기록까지만. 실제 차단/살수 명령 없음 | `app/routers/suppression_router.py` |
| 인원 잔류 확인 | PERSON 이벤트 탐지 여부로만 판단 (`person_check: DETECTION_ONLY`) | 출입 통제 연동 또는 관리자 체크박스 |
| 지도 실이미지 렌더 | 손으로 그린 SVG 위에 마커만 표시. 실제 맵 이미지는 미렌더 | `MapPanel.tsx` (F-19) |
