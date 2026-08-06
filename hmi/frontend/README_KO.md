# 설비 안전 AMR 순찰·이상감지 관제 — React 전환본

`index_1.html` 단일 파일 대시보드(상태머신 기반 STATE 단일 진실 원천 + WS/데모 폴백)를 **Vite + React + TypeScript** 구조로 전환한 프로젝트입니다.

## 권장 배치 경로

```text
C:\Users\user\Desktop\취준생\두산ROCKY부트캠프\대면프로젝트 4차\frontend_amr_react
```

## 실행

PowerShell에서 다음 명령을 실행합니다.

```powershell
cd "C:\Users\user\Desktop\취준생\두산ROCKY부트캠프\대면프로젝트 4차\frontend_amr_react"
npm install
npm run dev
```

기본 개발 서버 포트는 `5175`입니다.

## 빌드

```powershell
npm run build
npm run preview
```

## 백엔드 연동

`VITE_MOCK=true`(기본값)면 WS 연결을 시도하지 않고 곧바로 내장 데모 시뮬레이터로 화면을 구동합니다.
`VITE_MOCK=false`면 실제 WS 서버에 연결을 시도하고, 1.5초 안에 열리지 않거나 끊기면 데모 시뮬레이터로 자동 폴백합니다(재연결도 2초 간격으로 재시도).

```env
VITE_MOCK=true
VITE_API_BASE=http://localhost:8001
VITE_WS_URL=ws://localhost:8001/ws/amr/status
```

실제 API를 연결할 때 `.env`의 `VITE_MOCK=false`로 변경하면 됩니다. WS 인바운드 프레임 형식(`{robots?, events?, stats?, log?}`)과 로봇 상태 Enum은 `src/types/index.ts` · `src/constants/dashboard.ts`(STATE_META/TRACKS/ALLOWED)를 참고하세요.
**이 값들은 `docs/fe-be-연동규격.md`의 API v1.0(로봇ID carter1/carter2, 6단계 progress_step)과 다릅니다** — 백엔드 연동 전 규격 재조율이 필요합니다.

## 주요 구조

```text
src/
├─ components/       # 화면 단위 React 컴포넌트 (Top/Alert/RobotCard/Map/Camera/Stats/Queue/Log)
├─ constants/        # STATE_META · TRACKS(미션별 5스텝) · ALLOWED(상태별 허용 명령) · CMD_LABEL
├─ context/          # 단일 진실 원천 STATE + WS 연결/데모 폴백 + 명령 전송
├─ hooks/            # Context 접근 훅
├─ lib/              # API 클라이언트, 데모 시뮬레이터(백엔드 미연결 시 폴백)
├─ styles/           # 디자인 토큰과 전역 스타일
├─ types/            # TypeScript 타입
├─ utils/            # stepIndex/fmt/ageSec 등 순수 함수
├─ App.tsx
└─ main.tsx
```

## 구현된 상호작용

- 통합 순찰 시작 · 도킹 스테이션 복귀 · 긴급정지 (허용된 로봇에만 일괄 전송, `ALLOWED` 상태표 기준)
- 로봇 카드별 미션 트랙(PATROL/ANOMALY) 5단계 스텝 렌더 + 존별 차단기 점검 이력(zonestrip)
- 상태별로 허용된 명령 버튼만 노출, 전송 후 서버 응답 전까지 "요청 중…" pending 표시(낙관적 업데이트 금지)
- WS 연결 시도 → 타임아웃/끊김 시 데모 시뮬레이터 자동 폴백, 상단 연결 상태 pill로 표시
- 이상 감지 현황(화재/연기·냉각수 누수·차단기 불일치) · 이벤트 큐 · 활동 로그(PC1/PC2/Fleet 태그) 실시간 갱신
- 존-2 위험 이벤트 발생 시 지도·카메라 피드가 함께 "이상감지" 상태로 전환
- **지도 클릭 → 이동 목표 전송**: 클릭 지점의 맵 좌표(x, y)와 로봇 선택 카드가 뜨고, "전송"을 누르면 `sendGoto(robotId, x, y)`가 실행되어 `POST /api/robots/{id}/goto {waypoints:[{x,y}], preempt:true}`를 호출(데모 모드에서는 시뮬레이터가 해당 로봇의 pose를 즉시 그 좌표로 옮김)
- 반응형 3열/2열/1열 대시보드
