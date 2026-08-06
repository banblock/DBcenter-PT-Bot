import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ALLOWED, CMD_LABEL, STATE_META, TRACKS } from "../constants/dashboard";
import { apiClient, MOCK, WS_URL } from "../lib/apiClient";
import { startDemoSimulator, type DemoHandle } from "../lib/demoSimulator";
import type {
  AppEvent,
  Command,
  InboundMessage,
  LinkMode,
  LogEntry,
  LogTag,
  Robot,
  Stats,
} from "../types";

const WS_TIMEOUT_MS = 1500;
const RECONNECT_DELAY_MS = 2000;

interface DashboardState {
  robots: Record<string, Robot>;
  events: AppEvent[];
  stats: Stats;
}

/** 단일 진실 원천 — ref에 실 데이터를 두고, 버전 카운터로만 리렌더를 유발한다.
 *  컴포넌트는 이 값을 직접 변경하지 않고 오직 그린다 (applyMessage만 값을 바꾼다). */
function useLiveState() {
  const ref = useRef<DashboardState>({ robots: {}, events: [], stats: { fire: 0, leak: 0 } });
  const [, bump] = useReducer((c: number) => c + 1, 0);
  return { ref, bump };
}

interface DashboardContextValue {
  robots: Robot[];
  events: AppEvent[];
  stats: Stats;
  pending: Record<string, Command>;
  logs: LogEntry[];
  now: number;
  linkMode: LinkMode;
  linkText: string;
  topAlert: AppEvent | undefined;
  isZone2Hot: boolean;
  breakerMismatch: number;
  dismissedAlertId: string | null;
  addLog: (tag: LogTag, msg: string, hot?: boolean) => void;
  sendCommand: (robotId: string, cmd: Command) => void;
  sendGoto: (robotId: string, x: number, y: number) => void;
  startAll: () => void;
  dockAll: () => void;
  estopAll: () => void;
  dismissAlert: (id: string) => void;
}

const DashboardContext = createContext<DashboardContextValue | null>(null);

function toEpoch(ts: string | number | undefined): number {
  if (typeof ts === "number") return ts;
  if (typeof ts === "string") return Date.parse(ts) || Date.now();
  return Date.now();
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const { ref: stateRef, bump } = useLiveState();
  const [pending, setPending] = useState<Record<string, Command>>({});
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [linkMode, setLinkMode] = useState<LinkMode>("connecting");
  const [linkText, setLinkText] = useState("연결 중…");
  const [dismissedAlertId, setDismissedAlertId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const logSeq = useRef(0);
  const linkModeRef = useRef<LinkMode>("connecting");
  linkModeRef.current = linkMode;
  const demoRef = useRef<DemoHandle | null>(null);

  const addLog = useCallback((tag: LogTag, msg: string, hot?: boolean) => {
    logSeq.current += 1;
    const entry: LogEntry = { id: `${Date.now()}-${logSeq.current}`, ts: Date.now(), tag, msg, hot };
    setLogs((prev) => [entry, ...prev].slice(0, 40));
  }, []);

  const applyMessage = useCallback(
    (msg: InboundMessage) => {
      const state = stateRef.current;
      msg.robots?.forEach((patch) => {
        const prevRobot = state.robots[patch.id];
        const merged: Robot = {
          ...prevRobot,
          ...patch,
          ts: toEpoch(patch.ts),
        } as Robot;
        state.robots = { ...state.robots, [patch.id]: merged };

        if (prevRobot && prevRobot.state !== merged.state) {
          addLog(
            "Fleet",
            `${merged.id} ${STATE_META[prevRobot.state]?.ko ?? prevRobot.state} → ${STATE_META[merged.state]?.ko ?? merged.state}`,
          );
        }
        if (prevRobot && prevRobot.mission_type !== merged.mission_type) {
          addLog(
            "PC1",
            `${merged.id} 미션 전환 · ${TRACKS[prevRobot.mission_type].label} → ${TRACKS[merged.mission_type].label}`,
            true,
          );
        }
        setPending((prev) => {
          if (!(patch.id in prev)) return prev;
          const next = { ...prev };
          delete next[patch.id];
          return next;
        });
      });

      if (msg.events) {
        state.events = msg.events.map((e) => ({ ...e, ts: toEpoch(e.ts) }));
      }
      if (msg.stats) {
        state.stats = { ...state.stats, ...msg.stats };
      }
      bump();

      if (msg.log) addLog(msg.log.tag, msg.log.msg, msg.log.hot);
    },
    [addLog, bump, stateRef],
  );

  /* 카드 age / 카메라 타임스탬프 갱신용 1초 틱 */
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  /* WS 연결 — 타임아웃 시 데모 시뮬레이터로 폴백, 이후 끊기면 재연결 시도 */
  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: number | null = null;
    let ws: WebSocket | null = null;

    function startDemo() {
      if (cancelled || demoRef.current) return;
      setLinkMode("demo");
      setLinkText("데모 모드 (WS 미연결)");
      demoRef.current = startDemoSimulator({ applyMessage, addLog });
    }

    function connect() {
      if (cancelled) return;
      let settled = false;
      const fallback = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          try {
            ws?.close();
          } catch {
            /* noop */
          }
          startDemo();
        }
      }, WS_TIMEOUT_MS);

      try {
        ws = new WebSocket(WS_URL);
      } catch {
        window.clearTimeout(fallback);
        startDemo();
        return;
      }

      ws.onopen = () => {
        settled = true;
        window.clearTimeout(fallback);
        setLinkMode("live");
        setLinkText("WS 연결됨");
        addLog("PC1", "관제 WebSocket 연결");
      };
      ws.onmessage = (ev) => {
        try {
          applyMessage(JSON.parse(ev.data));
        } catch (e) {
          console.warn("bad frame", e);
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        if (!settled) {
          settled = true;
          window.clearTimeout(fallback);
          startDemo();
        } else {
          setLinkMode("down");
          setLinkText("WS 끊김 · 재연결");
          reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
      ws.onerror = () => {
        /* onclose가 뒤이어 처리한다 */
      };
    }

    if (MOCK) startDemo();
    else connect();

    return () => {
      cancelled = true;
      demoRef.current?.stop();
      demoRef.current = null;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        /* noop */
      }
    };
  }, [applyMessage, addLog]);

  const sendCommand = useCallback(
    (robotId: string, cmd: Command) => {
      setPending((prev) => ({ ...prev, [robotId]: cmd }));
      addLog("PC1", `${robotId} 명령 전송 · ${CMD_LABEL[cmd]}`);

      (async () => {
        try {
          if (linkModeRef.current === "live") {
            await apiClient.post(`/api/robots/${robotId}/command`, { cmd });
          } else {
            demoRef.current?.command(robotId, cmd);
          }
        } catch (e) {
          setPending((prev) => {
            const next = { ...prev };
            delete next[robotId];
            return next;
          });
          addLog("PC1", `${robotId} 명령 실패 · ${(e as Error).message}`, true);
        }
      })();

      // 낙관적 업데이트 금지 — 서버(또는 데모)가 상태로 답할 때까지 pending만 표시
      window.setTimeout(() => {
        setPending((prev) => {
          if (prev[robotId] !== cmd) return prev;
          addLog("PC1", `${robotId} 명령 응답 지연 · 상태 미변경`, true);
          const next = { ...prev };
          delete next[robotId];
          return next;
        });
      }, 5000);
    },
    [addLog],
  );

  const sendGoto = useCallback(
    (robotId: string, x: number, y: number) => {
      addLog("PC1", `${robotId} 이동 목표 전송 · (${x}, ${y})`);
      (async () => {
        try {
          if (linkModeRef.current === "live") {
            await apiClient.post(`/api/robots/${robotId}/goto`, {
              waypoints: [{ x, y }],
              preempt: true,
            });
          } else {
            demoRef.current?.goto(robotId, x, y);
          }
        } catch (e) {
          addLog("PC1", `${robotId} 이동 목표 전송 실패 · ${(e as Error).message}`, true);
        }
      })();
    },
    [addLog],
  );

  const startAll = useCallback(() => {
    Object.values(stateRef.current.robots).forEach((r) => {
      if (ALLOWED[r.state]?.includes("start")) sendCommand(r.id, "start");
    });
  }, [sendCommand, stateRef]);

  const dockAll = useCallback(() => {
    Object.values(stateRef.current.robots).forEach((r) => {
      if (ALLOWED[r.state]?.includes("dock")) sendCommand(r.id, "dock");
    });
  }, [sendCommand, stateRef]);

  const estopAll = useCallback(() => {
    Object.values(stateRef.current.robots).forEach((r) => sendCommand(r.id, "estop"));
  }, [sendCommand, stateRef]);

  const dismissAlert = useCallback((id: string) => setDismissedAlertId(id), []);

  const state = stateRef.current;
  const robots = useMemo(
    () => Object.values(state.robots).sort((a, b) => a.id.localeCompare(b.id)),
    [state.robots],
  );
  const topAlert = state.events.find(
    (e) => e.severity === "DANGER" && !["RESOLVED", "FALSE_ALARM"].includes(e.state),
  );
  const isZone2Hot = state.events.some(
    (e) => e.zone === "존-2" && e.severity === "DANGER" && !["RESOLVED", "FALSE_ALARM"].includes(e.state),
  );
  const breakerMismatch = robots
    .flatMap((r) => r.zones ?? [])
    .filter((z) => z.state === "MISMATCH" || z.state === "UNREADABLE").length;

  const value: DashboardContextValue = {
    robots,
    events: state.events,
    stats: state.stats,
    pending,
    logs,
    now,
    linkMode,
    linkText,
    topAlert,
    isZone2Hot,
    breakerMismatch,
    dismissedAlertId,
    addLog,
    sendCommand,
    sendGoto,
    startAll,
    dockAll,
    estopAll,
    dismissAlert,
  };

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboardContext(): DashboardContextValue {
  const context = useContext(DashboardContext);
  if (!context) throw new Error("useDashboardContext must be used inside DashboardProvider");
  return context;
}
