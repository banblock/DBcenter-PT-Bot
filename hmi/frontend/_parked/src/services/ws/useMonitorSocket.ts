/**
 * WS 연결을 앱 수명주기에 묶는 훅. 앱 전체에서 **한 번만** 호출한다 (AppLayout).
 *
 * StrictMode 대응: 개발 모드에서 React 는 effect 를 두 번 실행한다. 소켓을
 * 그때마다 새로 열면 서버에 연결이 두 개 잡히고 SNAPSHOT 도 두 번 온다.
 * 모듈 스코프 싱글턴 + 참조 카운트로 실제 소켓은 하나만 유지한다.
 */

import { useEffect } from "react";
import { useConnectionStore } from "../../store/connectionStore";
import { WebSocketClient } from "./WebSocketClient";
import { dispatch } from "./dispatcher";
import { DEFAULT_TOPICS, type WsTopic } from "./messages";

export const WS_URL: string =
  (import.meta.env.VITE_WS_URL as string | undefined) ?? "ws://localhost:8000/ws/monitor";

let client: WebSocketClient | null = null;
let refCount = 0;

function ensureClient(topics: WsTopic[]): WebSocketClient {
  if (client) return client;
  client = new WebSocketClient({
    url: WS_URL,
    topics,
    reconnectDelayMs: 3000, // F-01: 3초 백오프
    onMessage: dispatch,
    onStateChange: (state, { attempt }) => {
      useConnectionStore.getState().setFromClientState(state, attempt);
    },
  });
  client.connect();
  return client;
}

export function useMonitorSocket(topics: WsTopic[] = DEFAULT_TOPICS): void {
  useEffect(() => {
    refCount += 1;
    const socket = ensureClient(topics);

    return () => {
      refCount -= 1;
      // StrictMode 의 즉시 재마운트에서 소켓을 닫지 않도록 참조가 0일 때만 정리
      if (refCount === 0) {
        socket.close();
        client = null;
      }
    };
    // topics 는 모듈 상수라 재구독을 유발하지 않는다. 바꾸려면 getClient().subscribe() 사용.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** 컴포넌트 밖에서 구독을 바꾸거나 스냅샷을 다시 받고 싶을 때. */
export function getMonitorSocket(): WebSocketClient | null {
  return client;
}

/** 테스트 격리용 — 싱글턴을 강제로 비운다. */
export function __resetMonitorSocket(): void {
  client?.close();
  client = null;
  refCount = 0;
}
