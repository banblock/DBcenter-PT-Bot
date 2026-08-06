/**
 * WebSocket 연결 + 자동 재연결 (F-01).
 *
 * ## 재연결 정책
 * 체크리스트 F-01 이 요구하는 것은 **3초 백오프**다. 지수 백오프를 쓰지 않는 이유:
 * 관제 시스템에서 화면이 끊긴 채로 30초·60초를 기다리는 건 허용되지 않는다.
 * 백엔드 재기동은 보통 수 초 안에 끝나므로 고정 3초가 맞다.
 *
 * 다만 서버가 오래 죽어 있을 때 초당 요청이 무한히 쌓이는 것도 막아야 하므로,
 * 연속 실패가 `SLOW_AFTER_ATTEMPTS` 회를 넘으면 간격을 `SLOW_DELAY_MS` 로 늘린다.
 * 한 번이라도 연결에 성공하면 카운터는 0으로 리셋된다.
 *
 * ## 수명주기
 *   connect() → onopen → (구독 전송 → 서버가 SNAPSHOT 발신)
 *             → onmessage*
 *             → onclose → 3초 뒤 connect() 재시도
 *   close()   → 재연결하지 않고 완전히 종료 (컴포넌트 언마운트 시)
 *
 * ## keepalive
 * 프록시/방화벽이 유휴 연결을 끊는 걸 막기 위해 25초마다 ping 을 보낸다.
 * PONG 이 `PONG_TIMEOUT_MS` 안에 오지 않으면 죽은 연결로 보고 스스로 끊는다.
 * (브라우저 WebSocket 은 TCP 가 반쯤 죽어도 onclose 가 안 오는 경우가 있다.)
 */

import {
  parseEnvelope,
  type ClientMessage,
  type WsEnvelope,
  type WsTopic,
} from "./messages";

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export interface WebSocketClientOptions {
  url: string;
  topics?: WsTopic[];
  /** 재연결 간격 (ms). 체크리스트 F-01 기준 3000. */
  reconnectDelayMs?: number;
  /** 연속 실패가 이 횟수를 넘으면 간격을 늘린다. */
  slowAfterAttempts?: number;
  slowDelayMs?: number;
  /** keepalive ping 주기. 0 이면 비활성. */
  pingIntervalMs?: number;
  pongTimeoutMs?: number;
  onMessage: (message: WsEnvelope) => void;
  onStateChange?: (state: ConnectionState, detail: { attempt: number }) => void;
  /** 테스트 주입용. 기본은 전역 WebSocket. */
  socketFactory?: (url: string) => WebSocket;
}

const DEFAULTS = {
  reconnectDelayMs: 3000,
  slowAfterAttempts: 10,
  slowDelayMs: 15000,
  pingIntervalMs: 25000,
  pongTimeoutMs: 8000,
};

export class WebSocketClient {
  private socket: WebSocket | null = null;
  private state: ConnectionState = "closed";
  private attempt = 0;
  private disposed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;
  private topics: WsTopic[];
  private readonly options: Required<
    Omit<WebSocketClientOptions, "onStateChange" | "socketFactory" | "topics">
  > &
    Pick<WebSocketClientOptions, "onStateChange" | "socketFactory">;

  constructor(options: WebSocketClientOptions) {
    this.topics = options.topics ?? [];
    this.options = {
      url: options.url,
      reconnectDelayMs: options.reconnectDelayMs ?? DEFAULTS.reconnectDelayMs,
      slowAfterAttempts: options.slowAfterAttempts ?? DEFAULTS.slowAfterAttempts,
      slowDelayMs: options.slowDelayMs ?? DEFAULTS.slowDelayMs,
      pingIntervalMs: options.pingIntervalMs ?? DEFAULTS.pingIntervalMs,
      pongTimeoutMs: options.pongTimeoutMs ?? DEFAULTS.pongTimeoutMs,
      onMessage: options.onMessage,
      onStateChange: options.onStateChange,
      socketFactory: options.socketFactory,
    };
  }

  // ── 공개 API ────────────────────────────────────────────────────────────
  connect(): void {
    if (this.disposed) return;
    this.clearReconnectTimer();
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");

    let socket: WebSocket;
    try {
      socket = this.options.socketFactory
        ? this.options.socketFactory(this.options.url)
        : new WebSocket(this.options.url);
    } catch {
      // 잘못된 URL 등 생성 자체가 실패한 경우도 재시도 경로로 보낸다.
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;
    socket.onopen = this.handleOpen;
    socket.onmessage = this.handleMessage;
    socket.onclose = this.handleClose;
    socket.onerror = this.handleError;
  }

  /** 완전 종료. 이후 재연결하지 않는다. */
  close(): void {
    this.disposed = true;
    this.clearReconnectTimer();
    this.stopKeepalive();
    this.detachAndClose();
    this.setState("closed");
  }

  send(message: ClientMessage): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  /** 구독 토픽 변경 (F-04). 연결 전에 불러도 되고, 열리면 자동으로 재전송된다. */
  subscribe(topics: WsTopic[]): void {
    this.topics = topics;
    this.send({ action: "subscribe", topics });
  }

  /** SNAPSHOT 재요청 — 오래 끊겼다 붙었을 때 화면을 통째로 다시 맞춘다. */
  requestSnapshot(): boolean {
    return this.send({ action: "snapshot" });
  }

  getState(): ConnectionState {
    return this.state;
  }

  get isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  // ── 내부 ────────────────────────────────────────────────────────────────
  private handleOpen = (): void => {
    this.attempt = 0;
    this.setState("open");
    // 재연결 직후에도 구독을 다시 알려야 한다. 서버는 연결마다 구독을 새로 관리한다.
    if (this.topics.length > 0) {
      this.send({ action: "subscribe", topics: this.topics });
    }
    this.startKeepalive();
  };

  private handleMessage = (event: MessageEvent): void => {
    const envelope = parseEnvelope(
      typeof event.data === "string" ? event.data : String(event.data),
    );
    // 파싱 실패·미정의 타입은 여기서 걸러진다. 예외를 던지지 않는다 (F-02).
    if (envelope === null) return;

    if (envelope.type === "PONG") {
      this.clearPongTimer();
      return;
    }
    this.options.onMessage(envelope);
  };

  private handleClose = (): void => {
    this.stopKeepalive();
    if (this.disposed) return;
    this.scheduleReconnect();
  };

  private handleError = (): void => {
    // onerror 뒤에는 반드시 onclose 가 온다. 여기서 재연결을 걸면 이중 예약이 된다.
  };

  private scheduleReconnect(): void {
    if (this.disposed || this.reconnectTimer !== null) return;
    this.attempt += 1;
    this.setState("reconnecting");
    const delay =
      this.attempt > this.options.slowAfterAttempts
        ? this.options.slowDelayMs
        : this.options.reconnectDelayMs;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private startKeepalive(): void {
    if (this.options.pingIntervalMs <= 0) return;
    this.stopKeepalive();
    this.pingTimer = setInterval(() => {
      if (!this.send({ action: "ping" })) return;
      // PONG 이 안 오면 반쯤 죽은 연결 — 스스로 끊어 재연결 경로를 태운다.
      this.clearPongTimer();
      this.pongTimer = setTimeout(() => {
        this.detachAndClose();
        this.scheduleReconnect();
      }, this.options.pongTimeoutMs);
    }, this.options.pingIntervalMs);
  }

  private stopKeepalive(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    this.clearPongTimer();
  }

  private clearPongTimer(): void {
    if (this.pongTimer !== null) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }

  /** 콜백을 떼고 소켓을 닫는다 — 닫는 과정에서 onclose 가 또 돌지 않게. */
  private detachAndClose(): void {
    const socket = this.socket;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    this.socket = null;
    try {
      socket.close();
    } catch {
      /* 이미 닫힌 소켓 */
    }
  }

  private setState(next: ConnectionState): void {
    if (this.state === next) return;
    this.state = next;
    this.options.onStateChange?.(next, { attempt: this.attempt });
  }
}
