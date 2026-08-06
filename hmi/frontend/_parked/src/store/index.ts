/**
 * 전역 상태 store — 진입점.
 *
 * ## 구조
 * 도메인별로 슬라이스를 나눈다. 하나의 거대한 store 를 두면 5Hz 텔레메트리가
 * 들어올 때마다 무관한 화면까지 리렌더된다.
 *
 *   connectionStore — WS 연결 상태 (인디케이터)
 *   robotStore      — 로봇 최신 상태 (고빈도)
 *   eventStore      — 이상 이벤트 (실시간 상황판용, 최근 N건)
 *   missionStore    — 작업 큐
 *   facilityStore   — 맵·구역·노드·탐지·진압·우선순위 (저빈도/정적)
 *   logStore        — 활동 로그 롤링 버퍼
 *   uiStore         — 테마·역할·토스트·모달 (서버와 무관)
 *
 * ## 규칙
 * * 컴포넌트는 **선택자 훅**으로 필요한 조각만 구독한다. store 전체를 구독하지 않는다.
 * * 서버 데이터의 유일한 입구는 `services/ws/dispatcher.ts` 와 `services/api/*` 다.
 *   컴포넌트가 store 를 직접 쓰는 건 UI 상태(uiStore)뿐이다.
 * * 낙관적 업데이트는 하지 않는다. 명령은 pending 만 표시하고, 실제 상태는
 *   서버가 WS 로 알려줄 때 바뀐다 (관제 화면이 사실과 다르면 안 된다).
 */

export * from "./connectionStore";
export * from "./eventStore";
export * from "./facilityStore";
export * from "./logStore";
export * from "./missionStore";
export * from "./robotStore";
export * from "./uiStore";

import { useConnectionStore } from "./connectionStore";
import { useEventStore } from "./eventStore";
import { useFacilityStore } from "./facilityStore";
import { useLogStore } from "./logStore";
import { useMissionStore } from "./missionStore";
import { useRobotStore } from "./robotStore";

/** 전체 초기화 — 테스트와 '연결 재설정' 동작에서 쓴다. */
export function resetAllStores(): void {
  useConnectionStore.getState().reset();
  useRobotStore.getState().reset();
  useEventStore.getState().reset();
  useMissionStore.getState().reset();
  useFacilityStore.getState().reset();
  useLogStore.getState().clear();
}
