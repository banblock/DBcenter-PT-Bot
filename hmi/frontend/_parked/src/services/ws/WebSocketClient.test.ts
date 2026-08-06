/**
 * WS 클라이언트 테스트 (fe_ws_client).
 *
 * 체크리스트 테스트 항목:
 * * 백엔드 재기동 시 3초 백오프 재연결 (F-01)
 * * 정의 외 메시지 타입 무시 · 콘솔 에러 0 (F-02)
 * * 네트워크 차단 시 인디케이터 1초 내 반영 (F-05)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebSocketClient, type ConnectionState } from "./WebSocketClient";

/** 테스트용 가짜 소켓 — 서버 동작을 수동으로 흉내 낸다. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];

  onopen: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeSocket.CLOSED;
  }

  // ── 서버 쪽 동작 시뮬레이션 ────────────────────────────────────────────
  simulateOpen() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  simulateRaw(text: string) {
    this.onmessage?.({ data: text } as MessageEvent);
  }

  simulateClose() {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }

  get parsedSent(): unknown[] {
    return this.sent.map((s) => JSON.parse(s));
  }
}

function makeClient(overrides: Partial<ConstructorParameters<typeof WebSocketClient>[0]> = {}) {
  const messages: unknown[] = [];
  const states: ConnectionState[] = [];
  const client = new WebSocketClient({
    url: "ws://test/ws/monitor",
    topics: ["ROBOT_STATUS", "EVENT"],
    pingIntervalMs: 0, // keepalive 는 별도 테스트에서 다룬다
    onMessage: (m) => messages.push(m),
    onStateChange: (s) => states.push(s),
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    ...overrides,
  });
  return { client, messages, states };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  // 전역 WebSocket 상수(OPEN 등)를 클라이언트가 참조한다
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("연결 수명주기", () => {
  it("connect() 하면 소켓을 하나 만든다", () => {
    const { client, states } = makeClient();
    client.connect();
    expect(FakeSocket.instances).toHaveLength(1);
    expect(states).toContain("connecting");
  });

  it("open 되면 상태가 open 이 되고 구독을 전송한다 (F-04)", () => {
    const { client, states } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();

    expect(states).toContain("open");
    expect(client.getState()).toBe("open");
    expect(FakeSocket.instances[0].parsedSent[0]).toEqual({
      action: "subscribe",
      topics: ["ROBOT_STATUS", "EVENT"],
    });
  });

  it("close() 후에는 재연결하지 않는다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    client.close();
    FakeSocket.instances[0].simulateClose();

    vi.advanceTimersByTime(30_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(client.getState()).toBe("closed");
  });
});

describe("자동 재연결 (F-01)", () => {
  it("연결이 끊기면 정확히 3초 뒤 재시도한다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateClose();

    // 3초 되기 전에는 아직 재시도하지 않는다
    vi.advanceTimersByTime(2_900);
    expect(FakeSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(200);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("백엔드가 계속 죽어 있으면 3초 간격으로 반복 시도한다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateClose();

    for (let i = 2; i <= 5; i += 1) {
      vi.advanceTimersByTime(3_000);
      expect(FakeSocket.instances).toHaveLength(i);
      FakeSocket.instances[i - 1].simulateClose();
    }
  });

  it("재연결에 성공하면 구독을 다시 보낸다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateClose();

    vi.advanceTimersByTime(3_000);
    FakeSocket.instances[1].simulateOpen();

    expect(FakeSocket.instances[1].parsedSent[0]).toEqual({
      action: "subscribe",
      topics: ["ROBOT_STATUS", "EVENT"],
    });
  });

  it("장시간 실패하면 간격을 늘려 요청 폭주를 막는다", () => {
    const { client } = makeClient({ slowAfterAttempts: 3, slowDelayMs: 15_000 });
    client.connect();

    // 3회까지는 3초 간격
    for (let i = 0; i < 3; i += 1) {
      FakeSocket.instances[FakeSocket.instances.length - 1].simulateClose();
      vi.advanceTimersByTime(3_000);
    }
    const countAfterFast = FakeSocket.instances.length;

    // 4번째 실패부터는 3초가 지나도 재시도하지 않는다
    FakeSocket.instances[countAfterFast - 1].simulateClose();
    vi.advanceTimersByTime(3_000);
    expect(FakeSocket.instances).toHaveLength(countAfterFast);

    vi.advanceTimersByTime(12_000);
    expect(FakeSocket.instances).toHaveLength(countAfterFast + 1);
  });

  it("성공하면 실패 카운터가 초기화된다", () => {
    const { client, states } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateClose();
    vi.advanceTimersByTime(3_000);
    FakeSocket.instances[1].simulateOpen();

    states.length = 0;
    FakeSocket.instances[1].simulateClose();
    vi.advanceTimersByTime(3_000); // 다시 3초(느린 간격 아님)로 재시도
    expect(FakeSocket.instances).toHaveLength(3);
  });
});

describe("상태 인디케이터 반영 (F-05)", () => {
  it("연결이 끊긴 즉시(1초 훨씬 이내) reconnecting 으로 바뀐다", () => {
    const { client, states } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    states.length = 0;

    FakeSocket.instances[0].simulateClose();
    // 타이머를 전혀 진행시키지 않았는데도 이미 상태가 바뀌어 있어야 한다
    expect(states).toContain("reconnecting");
    expect(client.getState()).toBe("reconnecting");
  });

  it("같은 상태가 반복되면 콜백을 중복 호출하지 않는다", () => {
    const { client, states } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateOpen();
    expect(states.filter((s) => s === "open")).toHaveLength(1);
  });
});

describe("메시지 처리 (F-02)", () => {
  it("정상 봉투를 그대로 전달한다", () => {
    const { client, messages } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateMessage({
      type: "ROBOT_STATUS",
      timestamp: "2026-08-06 12:00:00",
      payload: { robot_id: "amr_1" },
    });

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ type: "ROBOT_STATUS" });
  });

  it("정의되지 않은 타입은 조용히 무시한다 — 콘솔 에러 0", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const { client, messages } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateMessage({ type: "PARTY_MODE", payload: {} });
    FakeSocket.instances[0].simulateMessage({ type: 42, payload: {} });
    FakeSocket.instances[0].simulateMessage({ noType: true });

    expect(messages).toHaveLength(0);
    expect(errorSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("잘못된 JSON 도 예외 없이 무시한다", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { client, messages } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();

    expect(() => FakeSocket.instances[0].simulateRaw("{ 깨진 JSON")).not.toThrow();
    expect(messages).toHaveLength(0);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("PONG 은 상위로 올리지 않는다 — keepalive 내부 신호다", () => {
    const { client, messages } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateMessage({ type: "PONG", payload: {} });
    expect(messages).toHaveLength(0);
  });

  it("timestamp 가 없어도 처리한다", () => {
    const { client, messages } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    FakeSocket.instances[0].simulateMessage({ type: "EVENT", payload: { event_id: "EV-1" } });
    expect(messages).toHaveLength(1);
  });
});

describe("구독 · 스냅샷 재요청", () => {
  it("subscribe() 로 토픽을 바꾸면 즉시 전송한다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    client.subscribe(["LOG"]);

    expect(FakeSocket.instances[0].parsedSent).toContainEqual({
      action: "subscribe",
      topics: ["LOG"],
    });
  });

  it("연결 전에 send 하면 false 를 돌려주고 던지지 않는다", () => {
    const { client } = makeClient();
    expect(client.send({ action: "ping" })).toBe(false);
  });

  it("requestSnapshot() 은 snapshot 액션을 보낸다", () => {
    const { client } = makeClient();
    client.connect();
    FakeSocket.instances[0].simulateOpen();
    expect(client.requestSnapshot()).toBe(true);
    expect(FakeSocket.instances[0].parsedSent).toContainEqual({ action: "snapshot" });
  });
});

describe("keepalive", () => {
  it("주기마다 ping 을 보낸다", () => {
    const { client } = makeClient({ pingIntervalMs: 25_000, pongTimeoutMs: 8_000 });
    client.connect();
    FakeSocket.instances[0].simulateOpen();

    vi.advanceTimersByTime(25_000);
    expect(FakeSocket.instances[0].parsedSent).toContainEqual({ action: "ping" });
  });

  it("PONG 이 오지 않으면 죽은 연결로 보고 재연결한다", () => {
    const { client } = makeClient({ pingIntervalMs: 25_000, pongTimeoutMs: 8_000 });
    client.connect();
    FakeSocket.instances[0].simulateOpen();

    vi.advanceTimersByTime(25_000); // ping 발신
    vi.advanceTimersByTime(8_000); // PONG 타임아웃 → 연결 정리 + 재연결 예약
    vi.advanceTimersByTime(3_000); // 백오프
    expect(FakeSocket.instances.length).toBeGreaterThan(1);
  });

  it("PONG 이 오면 연결을 끊지 않는다", () => {
    const { client } = makeClient({ pingIntervalMs: 25_000, pongTimeoutMs: 8_000 });
    client.connect();
    FakeSocket.instances[0].simulateOpen();

    vi.advanceTimersByTime(25_000);
    FakeSocket.instances[0].simulateMessage({ type: "PONG", payload: {} });
    vi.advanceTimersByTime(10_000);

    expect(FakeSocket.instances).toHaveLength(1);
    expect(client.getState()).toBe("open");
  });
});
