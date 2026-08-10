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
//
// UI 리뉴얼(기능 1~6): waypoint 사전 지정 / 도달 현황 / AMR·CCTV 팝업 /
//   긴급정지·복귀·재개 / 차단기(CheckGate) 확인 흐름을 추가했다. 신규 상태
//   (waypoints/patrolStarted/estopped/popup)와 액션도 이 허브에서만 관리한다.
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
import {
  CMD_LABEL,
  STATE_META,
  TRACKS,
  WP_PER_ZONE,
  ZONE_META,
  ZONES,
} from "../constants/dashboard";
import {
  MOCK,
  WS_MONITOR_URL,
  activateMap,
  backendCommand,
  backendDock,
  backendEstopAll,
  backendGoto,
  startPatrolFromWaypoints,
  fetchMapGrid,
  fetchMaps,
  fetchStatsOverview,
  fetchZones,
  polygonToRect,
  resetCaches,
  saveZoneRect,
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
  MapGrid,
  MapInfo,
  PopupData,
  Robot,
  Stats,
  WaypointMap,
  ZoneRect,
  ZoneRectMap,
} from "../types";

const WS_TIMEOUT_MS = 1500;
const RECONNECT_DELAY_MS = 2000;
const WP_NEED = ZONES.length * WP_PER_ZONE;

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
  latestCctvDetection: AppEvent | null;
  // 맵 선택
  maps: MapInfo[];
  activeMap: MapInfo | null;
  mapGrid: MapGrid | null;
  selectMap: (mapId: string) => void;
  // 존(사각형) 사전 설정 — waypoint 지정 전에 드래그로 그린다
  zoneRects: ZoneRectMap;
  setZoneRect: (zone: string, rect: ZoneRect) => void;
  commitZoneRect: (zone: string, rect: ZoneRect) => void;
  // 기능1: waypoint 지정
  waypoints: WaypointMap;
  waypointTotal: number;
  patrolStarted: boolean;
  addWaypoint: (zone: string, x: number, y: number) => void;
  undoWaypoint: (zone: string) => void;
  clearWaypoints: () => void;
  startPatrol: () => void;
  // 기능5: 긴급정지 흐름
  estopped: boolean;
  estopAll: () => void;
  resumeAll: () => void;
  dockAll: () => void;
  // 기능3·4: 팝업
  popup: PopupData | null;
  openPopup: (p: PopupData) => void;
  closePopup: () => void;
  // 공통
  addLog: (tag: LogTag, msg: string, hot?: boolean) => void;
  sendCommand: (robotId: string, cmd: Command) => void;
  sendGoto: (robotId: string, x: number, y: number) => void;
  dismissAlert: (id: string) => void;
}

const DashboardContext = createContext<DashboardContextValue | null>(null);

function toEpoch(ts: string | number | undefined): number {
  if (typeof ts === "number") return ts;
  if (typeof ts === "string") return Date.parse(ts) || Date.now();
  return Date.now();
}

function emptyWaypoints(): WaypointMap {
  return ZONES.reduce((acc, z) => ({ ...acc, [z]: [] }), {} as WaypointMap);
}

/** 맵 크기에 맞춘 기본 존 사각형 — 저장된 존이 없을 때 초기값(픽셀 좌표). */
function defaultZoneRects(map: MapInfo | null): ZoneRectMap {
  const w = map?.width ?? 113;
  const h = map?.height ?? 66;
  return {
    "존-1": { x: w * 0.08, y: h * 0.15, w: w * 0.34, h: h * 0.5 },
    "존-2": { x: w * 0.56, y: h * 0.35, w: w * 0.34, h: h * 0.5 },
  };
}

/** 저장된 존 폴리곤(월드 m) → 맵 픽셀 사각형. 없는 존은 기본값으로 채운다. */
function zoneRectsFromBackend(
  map: MapInfo | null,
  raw: Array<{ zone_id: string; polygon: number[][] }>,
): ZoneRectMap {
  const defaults = defaultZoneRects(map);
  if (!map) return defaults;
  const out: ZoneRectMap = { ...defaults };
  for (const zone of ZONES) {
    const meta = ZONE_META[zone];
    const found = raw.find((z) => z.zone_id === meta.zoneId);
    const rect = found ? polygonToRect(map, found.polygon) : null;
    if (rect && rect.w > 0.5 && rect.h > 0.5) out[zone] = rect;
  }
  return out;
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const { ref: stateRef, bump } = useLiveState();
  const [pending, setPending] = useState<Record<string, Command>>({});
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [linkMode, setLinkMode] = useState<LinkMode>("connecting");
  const [linkText, setLinkText] = useState("연결 중…");
  const [dismissedAlertId, setDismissedAlertId] = useState<string | null>(null);
  const [latestCctvDetection, setLatestCctvDetection] = useState<AppEvent | null>(null);
  const [now, setNow] = useState(() => Date.now());
  /** 차단기 불일치 수 — /api/stats/overview 집계값 (WS 로 오지 않아 주기 조회) */
  const [breaker, setBreaker] = useState(0);

  // 기능1·3·5: 리뉴얼 UI 전용 상태
  const [waypoints, setWaypoints] = useState<WaypointMap>(emptyWaypoints);
  const [patrolStarted, setPatrolStarted] = useState(false);
  const [estopped, setEstopped] = useState(false);
  const [popup, setPopup] = useState<PopupData | null>(null);

  // 맵 선택 · 존(사각형) 상태
  const [maps, setMaps] = useState<MapInfo[]>([]);
  const [activeMap, setActiveMap] = useState<MapInfo | null>(null);
  const [mapGrid, setMapGrid] = useState<MapGrid | null>(null);
  const [zoneRects, setZoneRects] = useState<ZoneRectMap>(() => defaultZoneRects(null));
  /** 백엔드에서 받은 존 원본(월드 폴리곤) — 맵 전환 시 픽셀 사각형을 다시 계산한다. */
  const zonesRawRef = useRef<Array<{ zone_id: string; polygon: number[][] }>>([]);

  const logSeq = useRef(0);
  const linkModeRef = useRef<LinkMode>("connecting");
  linkModeRef.current = linkMode;
  const waypointsRef = useRef<WaypointMap>(waypoints);
  waypointsRef.current = waypoints;
  const patrolStartedRef = useRef(false);
  patrolStartedRef.current = patrolStarted;
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
      if (msg.detectedCctvEvent) {
        setLatestCctvDetection({
          ...msg.detectedCctvEvent,
          ts: toEpoch(msg.detectedCctvEvent.ts),
        });
      }
      bump();

      if (msg.log) addLog(msg.log.tag, msg.log.msg, msg.log.hot);
    },
    [addLog, bump, stateRef],
  );

  const openPopup = useCallback((p: PopupData) => setPopup(p), []);
  const closePopup = useCallback(() => setPopup(null), []);
  const setLink = useCallback((mode: LinkMode, text: string) => {
    setLinkMode(mode);
    setLinkText(text);
  }, []);
  const getWaypoints = useCallback(() => waypointsRef.current, []);

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

  /* 맵 목록 + 활성 맵 + 저장된 존을 초기 1회 로드. 데모 모드에선 건너뛴다. */
  useEffect(() => {
    if (MOCK) return;
    let cancelled = false;
    (async () => {
      try {
        const [list, rawZones] = await Promise.all([fetchMaps(), fetchZones().catch(() => [])]);
        if (cancelled) return;
        const active = list.find((m) => m.is_active) ?? list[0] ?? null;
        zonesRawRef.current = rawZones;
        setMaps(list);
        setActiveMap(active);
        setZoneRects(zoneRectsFromBackend(active, rawZones));
        if (active) {
          addLog("PC1", `맵 로드 · ${active.name} (${active.width}×${active.height}px)`);
          fetchMapGrid(active.map_id)
            .then((g) => !cancelled && setMapGrid(g))
            .catch(() => setMapGrid(null));
        }
      } catch {
        /* 백엔드 미연결 — 맵 없이도 화면은 계속 동작한다 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [addLog]);

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
      setLinkText("데모 모드 · 순찰 대기");
      demoRef.current = startDemoSimulator({
        applyMessage,
        addLog,
        getWaypoints,
        openPopup,
        closePopup,
        setLink,
      });
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
        } else if (!demoRef.current) {
          // 데모 폴백이 이미 돌고 있으면(demoRef 존재) 재연결로 "down" 라벨을 덮지 않는다.
          // 실제 live 였다가 끊긴 경우(demoRef 없음)에만 재연결을 시도한다.
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
  }, [applyMessage, addLog, getWaypoints, openPopup, closePopup, setLink]);

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

  /* 맵 선택 — 백엔드 활성 맵을 바꾸고, 저장된 존을 새 맵 좌표계로 다시 계산한다. */
  const selectMap = useCallback(
    (mapId: string) => {
      if (patrolStartedRef.current) return; // 순찰 중엔 맵 잠금
      (async () => {
        try {
          const updated = await activateMap(mapId);
          setMaps((prev) => prev.map((m) => ({ ...m, is_active: m.map_id === mapId })));
          setActiveMap(updated);
          setZoneRects(zoneRectsFromBackend(updated, zonesRawRef.current));
          setWaypoints(emptyWaypoints()); // 맵이 바뀌면 좌표 기준이 달라져 waypoint 초기화
          setMapGrid(null);
          fetchMapGrid(updated.map_id)
            .then(setMapGrid)
            .catch(() => setMapGrid(null));
          addLog("PC1", `맵 전환 · ${updated.name}`);
        } catch (e) {
          addLog("PC1", `맵 전환 실패 · ${(e as Error).message}`, true);
        }
      })();
    },
    [addLog],
  );

  /* 존 사각형 — 드래그 중엔 setZoneRect(로컬 즉시 반영), 드래그 끝에 commitZoneRect(백엔드 저장). */
  const setZoneRect = useCallback((zone: string, rect: ZoneRect) => {
    if (patrolStartedRef.current) return;
    setZoneRects((prev) => ({ ...prev, [zone]: rect }));
  }, []);

  const commitZoneRect = useCallback(
    (zone: string, rect: ZoneRect) => {
      if (patrolStartedRef.current) return;
      setZoneRects((prev) => ({ ...prev, [zone]: rect }));
      // 존이 바뀌면 그 존의 waypoint 는 영역 밖일 수 있으니 비운다.
      setWaypoints((prev) => ({ ...prev, [zone]: [] }));
      const map = activeMap;
      const meta = ZONE_META[zone];
      if (!map || !meta) return;
      saveZoneRect(map, meta.zoneId, zone, rect, meta.risk)
        .then(() => addLog("PC1", `${zone} 영역 저장 · ${meta.amr} 담당`))
        .catch((e) => addLog("PC1", `${zone} 영역 저장 실패 · ${(e as Error).message}`, true));
    },
    [activeMap, addLog],
  );

  /* 기능1: waypoint 지정 (구역별 최대 WP_PER_ZONE개) */
  const addWaypoint = useCallback((zone: string, x: number, y: number) => {
    if (patrolStartedRef.current) return;
    setWaypoints((prev) => {
      if ((prev[zone]?.length ?? 0) >= WP_PER_ZONE) return prev;
      return { ...prev, [zone]: [...(prev[zone] ?? []), { x, y, theta: 0 }] };
    });
  }, []);
  const undoWaypoint = useCallback((zone: string) => {
    if (patrolStartedRef.current) return;
    setWaypoints((prev) => ({ ...prev, [zone]: (prev[zone] ?? []).slice(0, -1) }));
  }, []);
  const clearWaypoints = useCallback(() => {
    if (patrolStartedRef.current) return;
    setWaypoints(emptyWaypoints());
  }, []);

  // ★ 기능1: 통합 순찰 시작 — 6개 waypoint를 다 채워야 활성.
  //   · demo: 지정한 waypoint를 그대로 순환하도록 시뮬레이터에 넘긴다.
  //   · live: 실백엔드는 route_id 기반(PatrolStartIn)이라 waypoint 좌표를 직접 받지 않는다.
  //           → backendStartAll(대상 로봇)로 첫 경로 순찰을 건다. (waypoint 픽셀→월드
  //             좌표 변환·경로화는 백엔드 합의 후 연동 — 인수인계서 §7 참고, 가정값)
  const startPatrol = useCallback(() => {
    const wp = waypointsRef.current;
    const total = ZONES.reduce((n, z) => n + (wp[z]?.length ?? 0), 0);
    if (total < WP_NEED || patrolStartedRef.current) return;
    setPatrolStarted(true);
    addLog("PC1", `통합 순찰 시작 · waypoint ${total}개 지정`);
    if (linkModeRef.current === "live") {
      const map = activeMap;
      if (!map) {
        setPatrolStarted(false);
        addLog("PC1", "순찰 시작 실패 · 활성 맵이 없습니다", true);
        return;
      }
      // 사용자가 그린 waypoint 로 존별 경로를 만들어 담당 AMR 에 순찰을 건다.
      const plans = ZONES.map((z) => ({
        amr: ZONE_META[z].amr,
        zoneId: ZONE_META[z].zoneId,
        zoneName: z,
        rect: zoneRects[z] ?? null,
        risk: ZONE_META[z].risk,
        waypointsPixel: (wp[z] ?? []).map((w) => ({ x: w.x, y: w.y })),
      }));
      startPatrolFromWaypoints(map, plans)
        .then(() => addLog("PC1", "순찰 경로 생성·시작 완료 · 로봇 주행 시작"))
        .catch((e) => {
          setPatrolStarted(false);
          addLog("PC1", `순찰 시작 실패 · ${(e as Error).message}`, true);
        });
    } else {
      demoRef.current?.startPatrol();
    }
  }, [addLog, stateRef, activeMap, zoneRects]);

  /* 기능5: 긴급정지 → 복귀/재개 */
  const estopAll = useCallback(() => {
    setEstopped(true);
    addLog("PC1", "전체 긴급정지 요청", true);
    if (linkModeRef.current === "live") {
      backendEstopAll().catch((e) => addLog("PC1", `긴급정지 실패 · ${(e as Error).message}`, true));
    } else {
      demoRef.current?.estopAll();
    }
  }, [addLog]);

  const resumeAll = useCallback(() => {
    setEstopped(false);
    addLog("PC1", "전체 작업 재개 요청");
    if (linkModeRef.current === "live") {
      Object.keys(stateRef.current.robots).forEach((id) =>
        backendCommand(id, "reset").catch((e) => addLog("PC1", `${id} 재개 실패 · ${(e as Error).message}`, true)),
      );
    } else {
      demoRef.current?.resumeAll();
    }
  }, [addLog, stateRef]);

  const dockAll = useCallback(() => {
    setEstopped(false);
    setPatrolStarted(false); // 도킹 복귀 → 통합 순찰 시작 재활성화 (지도 잠금 해제)
    addLog("PC1", "전체 도킹 스테이션 복귀 요청");
    if (linkModeRef.current === "live") {
      Object.values(stateRef.current.robots)
        .filter((r) => r.state !== "OFFLINE")
        .forEach((r) =>
          backendDock(r.id).catch((e) => addLog("PC1", `${r.id} 도킹 실패 · ${(e as Error).message}`, true)),
        );
    } else {
      demoRef.current?.dockAll();
    }
  }, [addLog, stateRef]);

  const dismissAlert = useCallback((id: string) => setDismissedAlertId(id), []);

  const state = stateRef.current;
  const robots = useMemo(
    () => Object.values(state.robots).sort((a, b) => a.id.localeCompare(b.id)),
    [state.robots],
  );
  const topAlert = state.events.find(
    (e) =>
      (e.severity === "DANGER" || e.kind === "GATE") &&
      !["RESOLVED", "FALSE_ALARM"].includes(e.state),
  );
  const isZone2Hot = state.events.some(
    (e) =>
      e.zone === "존-2" &&
      (e.severity === "DANGER" || e.kind === "GATE") &&
      !["RESOLVED", "FALSE_ALARM"].includes(e.state),
  );
  // 실연동 시엔 집계 엔드포인트 값을 쓰고, 데모 모드에선 로봇 존 칩 + GATE 이벤트에서 센다.
  const gateOpen = state.events.filter(
    (e) => e.kind === "GATE" && !["RESOLVED", "FALSE_ALARM"].includes(e.state),
  ).length;
  const breakerFromZones = robots
    .flatMap((r) => r.zones ?? [])
    .filter((z) => z.state === "MISMATCH" || z.state === "UNREADABLE").length;
  const breakerMismatch = breaker || breakerFromZones + gateOpen;
  const waypointTotal = ZONES.reduce((n, z) => n + (waypoints[z]?.length ?? 0), 0);

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
    latestCctvDetection,
    maps,
    activeMap,
    mapGrid,
    selectMap,
    zoneRects,
    setZoneRect,
    commitZoneRect,
    waypoints,
    waypointTotal,
    patrolStarted,
    addWaypoint,
    undoWaypoint,
    clearWaypoints,
    startPatrol,
    estopped,
    estopAll,
    resumeAll,
    dockAll,
    popup,
    openPopup,
    closePopup,
    addLog,
    sendCommand,
    sendGoto,
    dismissAlert,
  };

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboardContext(): DashboardContextValue {
  const context = useContext(DashboardContext);
  if (!context) throw new Error("useDashboardContext must be used inside DashboardProvider");
  return context;
}
