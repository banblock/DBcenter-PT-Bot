// ★화면의 뇌(핵심)

// ════════════════════════════════════════════════════════════════
// [공부 메모] ★★ 프론트에서 제일 중요한 파일. 리뷰 30분이면 여기부터 시작 ★★
//
// 한 줄 정의: "화면의 뇌". 모든 상태(로봇/이벤트/통계)를 딱 여기 한 곳에서만
//           들고 있음 = SSOT(단일 진실 원천). 컴포넌트들은 여기서 받아 "그리기만".
//
// 리뷰 때 꼭 짚을 3가지 (아래 코드에 ★로 표시해둠):
//  1) applyMessage(): 서버가 준 데이터로만 화면 상태를 바꾼다.
//  2) 낙관적 업데이트 금지: 버튼 눌러도 화면 미리 안 바꿈. pending만 표시하고
//     서버가 새 상태로 답할 때만 바꿈. (로봇이랑 화면 어긋나면 사고니까)
//  3) WS 연결 상태머신: connecting→live→down(재연결) / demo(백엔드 없을 때 폴백)
//
// 헷갈렸던 점: 왜 useState 안 쓰고 useRef+useReducer(카운터)를 같이 쓰지?
//  → 로봇 pose가 1초에 여러 번 옴. 매번 setState 하면 리렌더 폭발.
//    그래서 실데이터는 ref(stateRef)에 담고, 화면 갱신은 "버전 카운터+1"로만 유발.
// ════════════════════════════════════════════════════════════════
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
import {
  MOCK,
  WS_MONITOR_URL,
  backendCommand,
  backendDock,
  backendEstopAll,
  backendGoto,
  backendStartAll,
  fetchStatsOverview,
  resetCaches,
  translateFrame,
} from "../lib/backendClient";
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
  /** 차단기 불일치 수 — /api/stats/overview 집계값 (WS 로 오지 않아 주기 조회) */
  const [breaker, setBreaker] = useState(0);
  const logSeq = useRef(0);
  const linkModeRef = useRef<LinkMode>("connecting");
  linkModeRef.current = linkMode;
  const demoRef = useRef<DemoHandle | null>(null);

  const addLog = useCallback((tag: LogTag, msg: string, hot?: boolean) => {
    logSeq.current += 1;
    const entry: LogEntry = { id: `${Date.now()}-${logSeq.current}`, ts: Date.now(), tag, msg, hot };
    setLogs((prev) => [entry, ...prev].slice(0, 40));
  }, []);

  // ★①  applyMessage = "화면 상태를 바꾸는 유일한 문". 서버(WS)나 데모가 준
  //     메시지로만 여기서 stateRef를 갱신함. 버튼 핸들러는 여기 직접 안 건드림.
  //     robots는 patch(부분 갱신)로 병합, events는 통째 교체.
  //     마지막에 bump()로 "버전+1" → 이때만 리렌더.
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
        // ★②와 짝: 이 로봇에 대한 새 상태가 실제로 도착했으니 pending("요청 중…") 해제.
        //   즉 "버튼 눌렀을 때"가 아니라 "서버가 답했을 때" pending이 풀림.
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

  /* 이상 감지 현황 카드 — 마운트 시 1회 + 30초마다. 데모 모드에선 시뮬레이터가 채운다. */
  useEffect(() => {
    if (MOCK) return;
    let cancelled = false;
    async function load() {
      try {
        const overview = await fetchStatsOverview();
        if (cancelled) return;
        applyMessage({ stats: { fire: overview.fire_smoke, leak: overview.leak } });
        setBreaker(overview.breaker_mismatch);
      } catch {
        /* 백엔드 미연결 — 통계는 그대로 두고 화면은 계속 동작한다 */
      }
    }
    load();
    const id = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [applyMessage]);

  // ★③ 연결 상태머신. 상단 pill 색이 여기서 정해짐.
  //    connect() 시도 → 1.5초 안에 안 열리면 startDemo()로 폴백(백엔드 없어도 화면 돎).
  //    열리면 "live", 끊기면 "down"+2초 후 재연결. 데모면 "demo".
  //    핵심: 백엔드가 죽어 있어도 관제 화면 자체는 절대 안 죽게 설계.
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
        ws = new WebSocket(WS_MONITOR_URL);
      } catch {
        window.clearTimeout(fallback);
        startDemo();
        return;
      }

      ws.onopen = () => {
        settled = true;
        window.clearTimeout(fallback);
        resetCaches(); // 재연결 시 미션/존/이벤트 캐시를 비우고 새 SNAPSHOT 으로 다시 채운다
        setLinkMode("live");
        setLinkText("WS 연결됨");
        addLog("PC1", "관제 WebSocket 연결");
      };
      ws.onmessage = (ev) => {
        // 백엔드 봉투(SNAPSHOT/ROBOT_STATUS/EVENT/…)를 원본 InboundMessage 로 번역해 반영.
        // 모르는/무관한 타입은 translateFrame 이 null 을 돌려주고 조용히 무시된다.
        const frame = translateFrame(ev.data);
        if (frame) applyMessage(frame);
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

  // ★② 낙관적 업데이트 금지의 실제 코드. 버튼 → 여기.
  //    (1) pending에 표시만 함(로봇 상태는 안 바꿈!)  (2) 백엔드로 명령 전송
  //    실제 상태 변경은? applyMessage(★①)가 서버 응답 받을 때만. 여기선 절대 안 바꿈.
  //    5초 안에 응답 없으면 pending 풀고 "응답 지연" 경고(멈춘 것처럼 안 보이게).
  const sendCommand = useCallback(
    (robotId: string, cmd: Command) => {
      setPending((prev) => ({ ...prev, [robotId]: cmd }));
      addLog("PC1", `${robotId} 명령 전송 · ${CMD_LABEL[cmd]}`);

      (async () => {
        try {
          if (linkModeRef.current === "live") {
            await backendCommand(robotId, cmd);
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
            await backendGoto(robotId, x, y);
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
    const robots = Object.values(stateRef.current.robots);
    const targets = robots.filter((r) => ALLOWED[r.state]?.includes("start")).map((r) => r.id);
    if (linkModeRef.current === "live") {
      if (targets.length === 0) {
        addLog("PC1", "순찰을 시작할 수 있는 로봇이 없습니다", true);
        return;
      }
      addLog("PC1", `통합 순찰 시작 요청 · ${targets.join(", ")}`);
      backendStartAll(targets).catch((e) =>
        addLog("PC1", `순찰 시작 실패 · ${(e as Error).message}`, true),
      );
    } else {
      targets.forEach((id) => sendCommand(id, "start"));
    }
  }, [sendCommand, addLog, stateRef]);

  const dockAll = useCallback(() => {
    const robots = Object.values(stateRef.current.robots);
    if (linkModeRef.current === "live") {
      robots
        .filter((r) => r.state !== "OFFLINE")
        .forEach((r) =>
          backendDock(r.id).catch((e) =>
            addLog("PC1", `${r.id} 도킹 실패 · ${(e as Error).message}`, true),
          ),
        );
    } else {
      robots.forEach((r) => {
        if (ALLOWED[r.state]?.includes("dock")) sendCommand(r.id, "dock");
      });
    }
  }, [sendCommand, addLog, stateRef]);

  const estopAll = useCallback(() => {
    if (linkModeRef.current === "live") {
      addLog("PC1", "전체 긴급정지 요청", true);
      backendEstopAll().catch((e) =>
        addLog("PC1", `긴급정지 실패 · ${(e as Error).message}`, true),
      );
    } else {
      Object.values(stateRef.current.robots).forEach((r) => sendCommand(r.id, "estop"));
    }
  }, [sendCommand, addLog, stateRef]);

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
  // 실연동 시엔 집계 엔드포인트 값을 쓰고, 데모 모드에선 로봇 존 칩에서 센다.
  const breakerFromZones = robots
    .flatMap((r) => r.zones ?? [])
    .filter((z) => z.state === "MISMATCH" || z.state === "UNREADABLE").length;
  const breakerMismatch = breaker || breakerFromZones;

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
