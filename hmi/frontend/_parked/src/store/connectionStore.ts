/**
 * 연결 상태 — 헤더 우측 인디케이터(F-05)의 단일 소스.
 *
 * 인디케이터는 1초 안에 실제 상태를 반영해야 한다(체크리스트 테스트 항목).
 * 그래서 폴링하지 않고 WebSocketClient 의 상태 콜백이 직접 여기를 갱신한다.
 */

import { create } from "zustand";
import type { ConnectionState } from "../services/ws/WebSocketClient";

export type LinkMode = "connecting" | "live" | "reconnecting" | "down";

interface ConnectionStoreState {
  mode: LinkMode;
  /** 연속 재연결 시도 횟수. 0 = 정상 */
  attempt: number;
  /** 마지막으로 서버 프레임을 받은 시각 (epoch ms) */
  lastMessageAt: number | null;
  connectedAt: number | null;
  /** SNAPSHOT 을 한 번이라도 받았는가 — 화면 스켈레톤 해제 기준 */
  snapshotLoaded: boolean;
  subscribedTopics: string[];

  setFromClientState: (state: ConnectionState, attempt: number) => void;
  markMessage: () => void;
  markSnapshotLoaded: () => void;
  setSubscribedTopics: (topics: string[]) => void;
  reset: () => void;
}

/** WS 클라이언트 상태 → UI 표시 모드 */
const MODE_BY_STATE: Record<ConnectionState, LinkMode> = {
  connecting: "connecting",
  open: "live",
  reconnecting: "reconnecting",
  closed: "down",
};

export const LINK_LABEL: Record<LinkMode, string> = {
  connecting: "연결 중…",
  live: "LIVE",
  reconnecting: "재연결 중…",
  down: "연결 끊김",
};

export const useConnectionStore = create<ConnectionStoreState>((set) => ({
  mode: "connecting",
  attempt: 0,
  lastMessageAt: null,
  connectedAt: null,
  snapshotLoaded: false,
  subscribedTopics: [],

  setFromClientState: (state, attempt) =>
    set((prev) => ({
      mode: MODE_BY_STATE[state],
      attempt,
      connectedAt: state === "open" ? Date.now() : prev.connectedAt,
      // 끊기면 스냅샷을 다시 받아야 최신이다. 화면은 유지하되 플래그는 내린다.
      snapshotLoaded: state === "open" ? prev.snapshotLoaded : false,
    })),

  markMessage: () => set({ lastMessageAt: Date.now() }),
  markSnapshotLoaded: () => set({ snapshotLoaded: true }),
  setSubscribedTopics: (topics) => set({ subscribedTopics: topics }),

  reset: () =>
    set({
      mode: "connecting",
      attempt: 0,
      lastMessageAt: null,
      connectedAt: null,
      snapshotLoaded: false,
      subscribedTopics: [],
    }),
}));
