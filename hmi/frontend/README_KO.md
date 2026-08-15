# 설비 안전 AMR 순찰·이상감지 관제 프론트엔드

React 18 + TypeScript + Vite 기반 HMI입니다. 기본 개발 포트는 `5175`, 백엔드는 `8000`, 관제 WebSocket은 `/ws/monitor`입니다.

## 새 PC에서 실행

```bash
cd hmi/frontend
cp .env.example .env
npm ci
npm run dev -- --host 0.0.0.0 --port 5175
```

브라우저는 `http://localhost:5175` 또는 `http://<서버-IP>:5175`로 접속합니다. 다른 PC에서 접속할 때는 백엔드 `AMR_CORS_ORIGINS`에도 그 브라우저 오리진을 추가해야 합니다.

## 환경변수

```env
VITE_API_BASE=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/monitor
VITE_MOCK=false
```

- `VITE_MOCK=false`: FastAPI 백엔드와 연결합니다. ROS 없는 데모도 이 값을 사용하고 백엔드를 `AMR_DEMO_SIM=1`로 실행합니다.
- `VITE_MOCK=true`: 백엔드 없이 `src/lib/demoSimulator.ts`만 사용하는 프론트 단독 mock입니다.
- 페이지를 LAN IP로 열었는데 API/WS 값이 `localhost`이면 `backendClient.ts`가 페이지 호스트로 자동 치환합니다.
- Vite 환경변수는 빌드 시 반영되므로 `.env`를 바꾼 뒤 개발 서버 또는 빌드를 다시 시작해야 합니다.

실제 `.env`는 비밀값 및 PC별 주소가 들어갈 수 있으므로 전달·덮어쓰기하지 않습니다. `.env.example`을 복사한 뒤 대상 PC 설정에 맞춰 병합하십시오.

## 검증

```bash
npm test -- --run
npm run build
```

## 주요 구조

```text
src/
├─ components/MapPanel/       맵·존·waypoint·도킹·실좌표 표시
├─ components/CameraGrid/     CCTV 감지 팝업과 전체 정지/재개/복귀
├─ components/RobotStatusCard 개별 AMR 제어
├─ constants/dashboard.ts     상태/명령/도킹 위치/존 담당 로봇
├─ context/DashboardContext.tsx 상태 단일 진실 원천과 WS 연결
├─ lib/backendClient.ts       REST/WS 변환과 픽셀↔map 좌표 변환
├─ lib/demoSimulator.ts       프론트 단독 mock
└─ types/index.ts             공용 타입
```

전체 기능, ROS2 데이터 흐름, 다른 파트와의 병합 순서는 함께 전달되는 `AI-인수인계서.md`를 우선 참조하십시오.
