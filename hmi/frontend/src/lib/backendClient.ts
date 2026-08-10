// ★백엔드와 통역(핵심)

/**
 * 실백엔드 배선 계층 — 원본 대시보드의 데이터 소스를 실제 관제 백엔드로 잇는다.
 *
 * 설계 의도
 * --------
 * 원본 화면(도메인 타입 `Robot`/`AppEvent`/`Stats`)은 손대지 않는다. 대신 이 모듈이
 *   1) 백엔드 WS 봉투(SNAPSHOT/ROBOT_STATUS/EVENT/…)를 원본 `InboundMessage` 로 **번역**하고,
 *   2) 원본 명령(start/pause/…)을 API 명세서 REST 엔드포인트로 **옮긴다**.
 *
 * 백엔드 필드명(robot_id/state_ko/…)과 원본 프론트 필드명(id/task/…)이 달라서 번역이
 * 필요하다. 로봇→미션유형(PATROL/ANOMALY)·존 이름·이벤트 목록을 알아야 하므로 이 모듈은
 * 작은 캐시를 들고 있고, WS 재연결마다 `resetCaches()` 로 비운다.
 *
 * 인증: 백엔드는 `X-Role` 헤더가 없으면 OPERATOR 로 본다. OPERATOR 는 대시보드 명령
 * (순찰/정지/급파/도킹/ACK)에 필요한 권한을 모두 가지므로 헤더를 보내지 않는다.
 */

// ════════════════════════════════════════════════════════════════
// [공부 메모] ★ 프론트↔백엔드 "통역사" (두 번째로 중요한 파일)
//
// 왜 필요? 백엔드 필드명(robot_id, state_ko, progress_step...)이랑
//   원래 화면이 쓰던 이름(id, task, step...)이 다름. 화면 코드를 안 고치려고
//   이 파일이 중간에서 번역함. (수업 때 배운 'anti-corruption layer'가 이거였음!)
//
// 리뷰 때 볼 함수 2개:
//   - translateFrame(): 백엔드 WS 봉투 → 화면이 아는 InboundMessage 로 번역.
//                       모르는 타입은 null 반환 → 그냥 무시(화면 안 죽음).
//   - backendCommand()/backendGoto(): 버튼(start/pause/...) → 실제 REST 주소로.
//
// 포인트: 백엔드 스키마가 바뀌어도 이 파일만 고치면 됨. 화면 컴포넌트는 그대로.
// ════════════════════════════════════════════════════════════════
import type { AppEvent, Command, EventState, InboundMessage, InboundRobotPatch, MapGrid, MapInfo, RobotState, ZoneRect } from "../types";

// 백엔드 주소 결정 규칙:
//  1) VITE_API_BASE 가 있으면 그 값을 쓴다.
//  2) 단, 값이 localhost/127.0.0.1 인데 페이지는 다른 호스트(LAN IP 등)에서 열렸다면,
//     그 localhost 는 "접속 기기 자신"을 가리켜 백엔드에 못 붙는다 → 페이지 호스트로 치환.
//  3) 아무 설정도 없으면 페이지 호스트:8000 을 기본으로 쓴다(같은 PC/LAN 모두 동작).
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function pageIsLocal(): boolean {
  return typeof location !== "undefined" && LOCAL_HOSTS.has(location.hostname);
}

function rehost(url: string): string {
  // http(s)://localhost:8000/... → http(s)://<페이지호스트>:8000/...
  if (typeof location === "undefined" || pageIsLocal()) return url;
  return url.replace(/(\/\/)(localhost|127\.0\.0\.1|\[::1\])(?=[:/]|$)/, `$1${location.hostname}`);
}

function resolveApiBase(): string {
  const env = import.meta.env.VITE_API_BASE as string | undefined;
  if (env) return rehost(env);
  if (typeof location !== "undefined") return `${location.protocol}//${location.hostname}:8000`;
  return "http://localhost:8000";
}

function resolveWsUrl(): string {
  const env = import.meta.env.VITE_WS_URL as string | undefined;
  if (env) return rehost(env);
  if (typeof location !== "undefined") {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.hostname}:8000/ws/monitor`;
  }
  return "ws://localhost:8000/ws/monitor";
}

const API_BASE: string = resolveApiBase();
export const WS_MONITOR_URL: string = resolveWsUrl();
/** 기본은 실연동 시도. 명시적으로 VITE_MOCK=true 일 때만 데모 시뮬레이터로 시작한다. */
export const MOCK: boolean = import.meta.env.VITE_MOCK === "true";

// ══════════════════════════════════════════════════════════════════════════
// 백엔드 페이로드(부분) — 번역에 필요한 필드만 좁게 선언
// ══════════════════════════════════════════════════════════════════════════
interface BackendPose {
  x: number;
  y: number;
  theta?: number;
}
interface BackendRobot {
  robot_id: string;
  name?: string;
  state: string;
  state_ko?: string;
  progress_step?: number;
  battery?: number;
  pose?: BackendPose;
  mission_id?: string | null;
  current_node_id?: string | null;
  online?: boolean;
  last_seen?: string | null;
}
interface BackendMission {
  mission_id: string;
  mission_type?: "PATROL" | "ANOMALY";
  robot_id?: string | null;
  route_id?: string | null;
  route_name?: string | null;
  status?: string;
}
interface BackendEvent {
  event_id: string;
  source?: string;
  camera_id?: string | null;
  type: string;
  severity: string;
  status: string;
  zone_id?: string | null;
  confidence?: number;
  assigned_robot_id?: string | null;
  detected_at?: string;
}
interface BackendZone {
  zone_id: string;
  name: string;
}

// ══════════════════════════════════════════════════════════════════════════
// 캐시 — WS 재연결마다 리셋
// ══════════════════════════════════════════════════════════════════════════
const missionsById = new Map<string, BackendMission>();
/** 로봇이 지금 물고 있는 미션 (RUNNING/PENDING/PREEMPTED) — pause/resume/ack 에 필요 */
const activeMissionByRobot = new Map<string, BackendMission>();
/** 개별 복귀 후 재순찰할 때 활성 맵과 대조할 로봇별 최근 경로 목록(최신순). */
const routeHistoryByRobot = new Map<string, string[]>();
const zoneNameById = new Map<string, string>();
/** 이벤트는 스냅샷이 통째로 주므로, 단건 EVENT 도 누적해 전체 배열을 다시 만든다 */
const eventsById = new Map<string, BackendEvent>();
let cachedRouteId: string | null = null;

export function resetCaches(): void {
  missionsById.clear();
  activeMissionByRobot.clear();
  routeHistoryByRobot.clear();
  zoneNameById.clear();
  eventsById.clear();
  cachedRouteId = null;
}

const OPEN_MISSION = new Set(["RUNNING", "PENDING", "PREEMPTED"]);
const CLOSED_EVENT = new Set(["RESOLVED", "FALSE_POSITIVE", "MERGED"]);

// ══════════════════════════════════════════════════════════════════════════
// 매핑 표
// ══════════════════════════════════════════════════════════════════════════
const SEVERITY_MAP: Record<string, AppEvent["severity"]> = {
  CRITICAL: "DANGER",
  WARN: "WARN",
  INFO: "INFO",
};

const EVENT_STATE_MAP: Record<string, EventState> = {
  DETECTED: "DETECTED",
  QUEUED: "DETECTED",
  UNASSIGNED: "DETECTED",
  ASSIGNED: "ASSIGNED",
  VERIFYING: "ON_SITE",
  CONFIRMED: "ALERTING",
  SUPPRESSING: "ALERTING",
  ESCALATED: "ESCALATED",
  RESOLVED: "RESOLVED",
  FALSE_POSITIVE: "FALSE_ALARM",
  MERGED: "RESOLVED",
};

const EVENT_TYPE_KO: Record<string, string> = {
  FIRE: "화재",
  SMOKE: "연기",
  LEAK: "냉각수 누수",
  PERSON: "사람 감지",
  INTRUSION: "침입",
  BREAKER_ABNORMAL: "차단기 이상",
  LOCK_ABNORMAL: "잠금장치 이상",
  PANEL_OUT_OF_RANGE: "패널 범위 이탈",
  ALIGN_MISMATCH: "차단기 불일치",
};

// ══════════════════════════════════════════════════════════════════════════
// 번역 — 백엔드 → 원본 InboundMessage
// ══════════════════════════════════════════════════════════════════════════
function robotToPatch(r: BackendRobot): InboundRobotPatch {
  const mission =
    (r.mission_id ? missionsById.get(r.mission_id) : undefined) ??
    activeMissionByRobot.get(r.robot_id);
  return {
    id: r.robot_id,
    state: r.state as RobotState,
    mission_type: mission?.mission_type ?? "PATROL",
    step: r.progress_step,
    battery: r.battery ?? 0,
    task: r.state_ko,
    route: mission?.route_name ?? undefined,
    zone: r.current_node_id ?? undefined,
    pose: r.pose ? { x: r.pose.x, y: r.pose.y, theta: r.pose.theta } : undefined,
    ts: r.last_seen ?? Date.now(),
  };
}

type OutboundEvent = Omit<AppEvent, "ts"> & { ts: string | number };

function eventToOut(e: BackendEvent): OutboundEvent {
  const zone = e.zone_id ? zoneNameById.get(e.zone_id) ?? e.zone_id : undefined;
  const conf = typeof e.confidence === "number" ? ` (${Math.round(e.confidence * 100)}%)` : "";
  return {
    id: e.event_id,
    severity: SEVERITY_MAP[e.severity] ?? "INFO",
    state: EVENT_STATE_MAP[e.status] ?? "DETECTED",
    text: `${EVENT_TYPE_KO[e.type] ?? e.type}${zone ? ` · ${zone}` : ""}${conf}`,
    zone,
    assignee: e.assigned_robot_id ?? undefined,
    kind: e.source === "cctv" ? "CCTV" : e.source === "amr" ? "AMR" : undefined,
    cameraId: e.camera_id ?? undefined,
    ts: e.detected_at ?? Date.now(),
  };
}

/** 누적 이벤트 캐시에서 최신순 배열을 만든다 (원본 applyMessage 는 events 를 통째 교체한다). */
function emitEvents(): OutboundEvent[] {
  return [...eventsById.values()]
    .sort((a, b) => (b.detected_at ?? "").localeCompare(a.detected_at ?? ""))
    .slice(0, 50)
    .map(eventToOut);
}

function indexMission(m: BackendMission, preserveNewerRoute = false): void {
  missionsById.set(m.mission_id, m);
  if (!m.robot_id) return;
  if (m.route_id) {
    const history = routeHistoryByRobot.get(m.robot_id) ?? [];
    if (!history.includes(m.route_id)) {
      routeHistoryByRobot.set(m.robot_id, preserveNewerRoute ? [...history, m.route_id] : [m.route_id, ...history]);
    }
  }
  if (OPEN_MISSION.has(m.status ?? "")) {
    activeMissionByRobot.set(m.robot_id, m);
  } else if (activeMissionByRobot.get(m.robot_id)?.mission_id === m.mission_id) {
    activeMissionByRobot.delete(m.robot_id);
  }
}

/**
 * WS 프레임 문자열 → 원본 InboundMessage. 모르는/무관한 타입은 null 을 돌려 무시한다.
 * 관제 화면이 프레임 하나 때문에 죽지 않도록 파싱·필드 검증에 관대하게 처리한다.
 */
export function translateFrame(raw: string): InboundMessage | null {
  // ← 여기서 백엔드 봉투 {type, payload} 를 받아 화면용으로 바꿈.
  //   깨진 JSON이나 type 없으면 그냥 null(=무시). 화면이 죽으면 안 되니까 관대하게.
  let env: { type?: unknown; payload?: unknown };
  try {
    env = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!env || typeof env.type !== "string") return null;
  const p = (env.payload ?? {}) as Record<string, unknown>;

  // ← type별로 갈라서 번역. 화면이 안 쓰는 type(PONG 등)은 default에서 null → 무시.
  switch (env.type) {
    case "SNAPSHOT": {
      zoneNameById.clear();
      (p.zones as BackendZone[] | undefined)?.forEach((z) => zoneNameById.set(z.zone_id, z.name));
      missionsById.clear();
      activeMissionByRobot.clear();
      // 스냅샷 미션은 최신순이므로 로봇별 첫 경로만 보존한다.
      (p.missions as BackendMission[] | undefined)?.forEach((mission) => indexMission(mission, true));
      eventsById.clear();
      (p.events as BackendEvent[] | undefined)?.forEach((e) => eventsById.set(e.event_id, e));
      return {
        robots: (p.robots as BackendRobot[] | undefined)?.map(robotToPatch) ?? [],
        events: emitEvents(),
      };
    }

    case "ROBOT_STATUS": {
      if (typeof p.robot_id !== "string") return null;
      return { robots: [robotToPatch(p as unknown as BackendRobot)] };
    }

    case "ROBOT_STATE_CHANGED": {
      if (typeof p.robot_id !== "string" || typeof p.to !== "string") return null;
      return { robots: [{ id: p.robot_id, state: p.to as RobotState, ts: Date.now() }] };
    }

    case "ROBOT_OFFLINE": {
      if (typeof p.robot_id !== "string") return null;
      return { robots: [{ id: p.robot_id, state: "OFFLINE", ts: Date.now() }] };
    }

    case "MISSION_STATUS": {
      if (typeof p.mission_id !== "string") return null;
      indexMission(p as unknown as BackendMission);
      const routeName = typeof p.route_name === "string" ? ` · ${p.route_name}` : "";
      return { log: { tag: "PC1", msg: `미션 ${p.mission_id} ${String(p.status ?? "")}${routeName}` } };
    }

    case "EVENT": {
      if (typeof p.event_id !== "string") return null;
      const ev = p as unknown as BackendEvent;
      const isNewCctvDetection = ev.source === "cctv" && !eventsById.has(ev.event_id);
      eventsById.set(ev.event_id, ev);
      const out = eventToOut(ev);
      return {
        events: emitEvents(),
        log: { tag: "PC2", msg: `이벤트 ${out.text}`, hot: out.severity === "DANGER" },
        detectedCctvEvent: isNewCctvDetection ? out : undefined,
      };
    }

    case "LOG": {
      if (typeof p.message !== "string") return null;
      const actor = typeof p.actor === "string" ? p.actor : "PC1";
      return { log: { tag: actor, msg: p.message, hot: p.level === "CRITICAL" } };
    }

    case "SYSTEM_ALERT": {
      if (typeof p.message !== "string") return null;
      return { log: { tag: "PC1", msg: p.message, hot: p.severity === "CRITICAL" } };
    }

    // DETECTION/ALIGN_RESULT/PRIORITY_UPDATED/SUPPRESSION_STATUS/PONG/SUBSCRIBED …
    // 는 이 화면이 아직 소비하지 않는다 — 조용히 무시한다.
    default:
      return null;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// REST — 원본 명령을 API 명세서 엔드포인트로
// ══════════════════════════════════════════════════════════════════════════
async function req<T>(
  method: string,
  path: string,
  body?: unknown,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  const headers: Record<string, string> = { ...extraHeaders };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: Object.keys(headers).length ? headers : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let json: { result?: string; data?: unknown; message?: string } | null = null;
  try {
    json = await res.json();
  } catch {
    /* 본문 없는 응답 */
  }
  if (!res.ok || json?.result === "FAIL") {
    throw new Error(json?.message ?? `HTTP ${res.status}`);
  }
  return (json?.data ?? json) as T;
}

async function firstRouteId(): Promise<string | null> {
  if (cachedRouteId) return cachedRouteId;
  const routes = await req<Array<{ route_id: string }>>("GET", "/api/routes");
  cachedRouteId = routes[0]?.route_id ?? null;
  return cachedRouteId;
}

interface BackendRouteSummary {
  route_id: string;
  map_id: string;
}

/** 로봇의 최근 경로 중 현재 활성 맵에 속하는 첫 경로를 선택한다. */
async function latestRouteOnActiveMap(robotId: string): Promise<string | null> {
  try {
    const [activeMap, routes] = await Promise.all([
      req<BackendMap | null>("GET", "/api/map/active"),
      req<BackendRouteSummary[]>("GET", "/api/routes"),
    ]);
    if (!activeMap) return null;
    const routeById = new Map(routes.map((route) => [route.route_id, route]));
    for (const routeId of routeHistoryByRobot.get(robotId) ?? []) {
      if (routeById.get(routeId)?.map_id === activeMap.map_id) return routeId;
    }
    return routes.find((route) => route.map_id === activeMap.map_id)?.route_id ?? null;
  } catch {
    return null;
  }
}

/** 카드 명령 버튼 (start/pause/resume/dock/estop/reset/ack) → REST. */
// ← 버튼 뜻(cmd)을 실제 REST 주소로 매핑하는 표. 예) estop → POST .../emergency-stop.
//   여기가 하행(명령) 흐름에서 "화면 → HTTP" 로 넘어가는 경계. req()가 fetch 담당.
export async function backendCommand(robotId: string, cmd: Command): Promise<void> {
  switch (cmd) {
    case "stop_and_dock":
      // 좌측 상태카드 복합 명령: 즉시 정지시킨 뒤 도킹 복귀로 전환한다.
      await req("POST", `/api/robots/${robotId}/emergency-stop`, { reason: "STOP_AND_DOCK" });
      await req("POST", `/api/robots/${robotId}/dock`);
      return;
    case "estop":
      await req("POST", `/api/robots/${robotId}/emergency-stop`, { reason: "OPERATOR" });
      return;
    case "reset":
      await req("POST", `/api/robots/${robotId}/resume`, { mode: "RESUME_CHECKPOINT" });
      return;
    case "dock":
      await req("POST", `/api/robots/${robotId}/dock`);
      return;
    case "pause": {
      const mission = activeMissionByRobot.get(robotId);
      if (!mission) throw new Error("진행 중인 미션이 없습니다");
      await req("POST", `/api/patrol/${mission.mission_id}/pause`, { reason: "MANUAL" });
      return;
    }
    case "resume": {
      const mission = activeMissionByRobot.get(robotId);
      if (mission) {
        await req("POST", `/api/patrol/${mission.mission_id}/resume`);
        return;
      }
      await req("POST", `/api/robots/${robotId}/resume`, { mode: "RESUME_CHECKPOINT" });
      return;
    }
    case "start": {
      const routeId = await latestRouteOnActiveMap(robotId) ?? await firstRouteId();
      if (!routeId) throw new Error("등록된 순찰 경로가 없습니다");
      // 도킹 직후 시뮬레이터/장비 ACK가 늦어 RUNNING 미션이 잠시 남은 경우를 정리한다.
      await cancelActiveMissions(robotId);
      await req("POST", "/api/patrol/start", {
        route_id: routeId,
        robot_ids: [robotId],
        mode: "LOOP",
        apply_priority: false,
      });
      return;
    }
    case "ack": {
      const ev = [...eventsById.values()].find(
        (e) => e.assigned_robot_id === robotId && !CLOSED_EVENT.has(e.status),
      );
      if (!ev) throw new Error("확인할 경보가 없습니다");
      await req("POST", `/api/events/${ev.event_id}/ack`, { operator: "operator" });
      return;
    }
    default:
      return;
  }
}

/** 지도 클릭 이동 (F-goto). */
export async function backendGoto(robotId: string, x: number, y: number): Promise<void> {
  await req("POST", `/api/robots/${robotId}/goto`, {
    waypoints: [{ x, y, theta: 0 }],
    preempt: true,
  });
}

/** 헤더 [통합 순찰 시작] — 첫 경로로 대상 로봇 전체에 순찰을 건다. */
export async function backendStartAll(robotIds: string[]): Promise<void> {
  const routeId = await firstRouteId();
  if (!routeId) throw new Error("등록된 순찰 경로가 없습니다");
  await req("POST", "/api/patrol/start", {
    route_id: routeId,
    robot_ids: robotIds,
    mode: "LOOP",
    apply_priority: true,
  });
}

/** 헤더 [도킹 스테이션 복귀] — 로봇별 도킹. */
export async function backendDock(robotId: string): Promise<void> {
  await req("POST", `/api/robots/${robotId}/dock`);
}

/** 헤더 [긴급정지] — 전용 일괄 엔드포인트. */
export async function backendEstopAll(): Promise<void> {
  await req("POST", "/api/robots/emergency-stop-all");
}

/** 이상 감지 현황 카드 (F-stats) — WS 로 오지 않는 집계값이라 주기 조회한다. */
export interface StatsOverview {
  fire_smoke: number;
  leak: number;
  breaker_mismatch: number;
  unresolved: number;
}

export function fetchStatsOverview(): Promise<StatsOverview> {
  return req<StatsOverview>("GET", "/api/stats/overview");
}

// ══════════════════════════════════════════════════════════════════════════
// 맵 · 존 — 맵 선택 / 존(사각형) 저장
// ══════════════════════════════════════════════════════════════════════════
/** 맵/존 CRUD 는 백엔드에서 MASTER_EDIT(ADMIN) 를 요구한다. 폐쇄망 advisory 권한이라
 *  관제 단말이 ADMIN 헤더를 붙여 보낸다(security.py 주석 참고). */
const ADMIN_HEADER = { "X-Role": "ADMIN" };

interface BackendMap {
  map_id: string;
  name: string;
  image_url: string | null;
  resolution: number;
  origin: number[];
  width: number;
  height: number;
  is_active: boolean;
}

function toMapInfo(m: BackendMap): MapInfo {
  const [ox = 0, oy = 0, ot = 0] = m.origin ?? [];
  return {
    map_id: m.map_id,
    name: m.name,
    // image_url 은 백엔드 상대경로(/media/...) — 렌더에 바로 쓰도록 절대 URL 로 만든다.
    image_url: m.image_url ? `${API_BASE}${m.image_url}` : null,
    resolution: m.resolution,
    origin: [ox, oy, ot],
    width: m.width,
    height: m.height,
    is_active: m.is_active,
  };
}

export async function fetchMaps(): Promise<MapInfo[]> {
  const maps = await req<BackendMap[]>("GET", "/api/map");
  return maps.map(toMapInfo);
}

export async function activateMap(mapId: string): Promise<MapInfo> {
  const m = await req<BackendMap>("POST", `/api/map/${mapId}/activate`, {}, ADMIN_HEADER);
  return toMapInfo(m);
}

// ── 좌표 변환 (백엔드 crud/maps.py 와 반드시 동일한 공식) ──────────────────
// px = (x - ox) / res ,  py = height - (y - oy) / res   (py 는 이미지 위→아래)
export function worldToPixel(map: MapInfo, x: number, y: number): { px: number; py: number } {
  const [ox, oy] = map.origin;
  return { px: (x - ox) / map.resolution, py: map.height - (y - oy) / map.resolution };
}

export function pixelToWorld(map: MapInfo, px: number, py: number): { x: number; y: number } {
  const [ox, oy] = map.origin;
  return { x: px * map.resolution + ox, y: (map.height - py) * map.resolution + oy };
}

interface BackendGrid {
  available: boolean;
  width: number;
  height: number;
  free_min?: number;
  data_b64?: string;
}

/** 활성 맵의 점유격자를 불러온다. PGM 이 없으면 available=false (제약 없이 동작). */
export async function fetchMapGrid(mapId: string): Promise<MapGrid> {
  const g = await req<BackendGrid>("GET", `/api/map/${mapId}/grid`);
  let data: Uint8Array | null = null;
  if (g.available && g.data_b64) {
    const bin = atob(g.data_b64);
    data = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) data[i] = bin.charCodeAt(i);
  }
  return {
    available: !!g.available,
    width: g.width,
    height: g.height,
    freeMin: g.free_min ?? 250,
    data,
  };
}

interface BackendZoneFull {
  zone_id: string;
  name: string;
  polygon: number[][];
  risk_base: number;
  camera_ids: string[];
}

export async function fetchZones(): Promise<BackendZoneFull[]> {
  return req<BackendZoneFull[]>("GET", "/api/zones");
}

/** 픽셀 사각형 → 월드 폴리곤(m). 시계방향 4꼭짓점(TL·TR·BR·BL). */
function rectToPolygon(map: MapInfo, rect: ZoneRect): number[][] {
  const corners: Array<[number, number]> = [
    [rect.x, rect.y],
    [rect.x + rect.w, rect.y],
    [rect.x + rect.w, rect.y + rect.h],
    [rect.x, rect.y + rect.h],
  ];
  return corners.map(([px, py]) => {
    const { x, y } = pixelToWorld(map, px, py);
    return [Number(x.toFixed(4)), Number(y.toFixed(4))];
  });
}

/** 월드 폴리곤(m) → 픽셀 사각형(바운딩 박스). 저장된 존을 화면에 복원할 때 사용. */
export function polygonToRect(map: MapInfo, polygon: number[][]): ZoneRect | null {
  if (!polygon || polygon.length < 3) return null;
  const pts = polygon.map(([x, y]) => worldToPixel(map, x, y));
  const xs = pts.map((p) => p.px);
  const ys = pts.map((p) => p.py);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  return { x: minX, y: minY, w: Math.max(...xs) - minX, h: Math.max(...ys) - minY };
}

// ── 순찰: 사용자가 그린 waypoint 로 경로를 만들어 시작 ──────────────────────
export interface PatrolPlan {
  amr: string;
  zoneId: string;
  zoneName: string;
  rect: ZoneRect | null;
  risk?: number;
  waypointsPixel: Array<{ x: number; y: number }>;
}

async function cancelActiveMissions(robotId: string): Promise<void> {
  try {
    const missions = await req<Array<{ mission_id: string; status: string }>>(
      "GET",
      `/api/patrol/missions?robot_id=${encodeURIComponent(robotId)}`,
    );
    for (const m of missions) {
      if (["RUNNING", "PREEMPTED", "PENDING"].includes(m.status)) {
        await req("POST", `/api/patrol/${m.mission_id}/cancel`, { reason: "RESTART" }).catch(() => {});
      }
    }
  } catch {
    /* 미션 조회 실패는 무시 — 시작을 막지 않는다 */
  }
}

/**
 * 각 존의 waypoint(픽셀)를 월드좌표 노드로 만들어, 존별로 경로를 생성하고
 * 담당 AMR 에 순찰(LOOP)을 건다. 재시작이 항상 되도록 기존 미션은 먼저 취소한다.
 * 존-1→AMR-01, 존-2→AMR-02 매핑을 그대로 지킨다(라운드로빈 분배 안 씀).
 */
export async function startPatrolFromWaypoints(map: MapInfo, plans: PatrolPlan[]): Promise<void> {
  const stamp = Date.now().toString(36);
  let started = 0;
  for (const plan of plans) {
    if (!plan.waypointsPixel.length) continue;
    // 1) 존을 먼저 저장해 노드 FK(zone_id) 를 보장
    if (plan.rect) {
      await saveZoneRect(map, plan.zoneId, plan.zoneName, plan.rect, plan.risk ?? 1).catch(() => {});
    }
    // 2) 재시작이 되도록 이 로봇의 기존 미션 취소
    await cancelActiveMissions(plan.amr);
    // 3) waypoint → 월드 노드 생성
    const nodeIds: string[] = [];
    for (let i = 0; i < plan.waypointsPixel.length; i++) {
      const wp = plan.waypointsPixel[i];
      const { x, y } = pixelToWorld(map, wp.x, wp.y);
      const res = await req<{ node_id: string }>(
        "POST",
        "/api/nodes",
        {
          map_id: map.map_id,
          zone_id: plan.zoneId,
          name: `${plan.zoneName}-WP${i + 1}`,
          x: Number(x.toFixed(4)),
          y: Number(y.toFixed(4)),
        },
        ADMIN_HEADER,
      );
      nodeIds.push(res.node_id);
    }
    // 4) 경로 생성
    const route = await req<{ route_id: string }>(
      "POST",
      "/api/routes",
      { map_id: map.map_id, name: `${plan.zoneName} 순찰 ${stamp}`, node_order: nodeIds, loop: true },
      ADMIN_HEADER,
    );
    // 5) 순찰 시작 (waypoint 순서 유지 위해 apply_priority=false)
    await req("POST", "/api/patrol/start", {
      route_id: route.route_id,
      robot_ids: [plan.amr],
      mode: "LOOP",
      apply_priority: false,
    });
    started += 1;
  }
  if (!started) throw new Error("지정된 waypoint 가 없습니다");
}

/** 존(사각형)을 백엔드에 저장(upsert). 없으면 생성(POST), 있으면 수정(PUT). */
export async function saveZoneRect(
  map: MapInfo,
  zoneId: string,
  name: string,
  rect: ZoneRect,
  riskBase = 1,
): Promise<void> {
  const polygon = rectToPolygon(map, rect);
  try {
    await req("PUT", `/api/zones/${zoneId}`, { name, polygon, risk_base: riskBase }, ADMIN_HEADER);
  } catch {
    // 아직 없는 존이면 PUT 이 404 → 생성으로 폴백.
    await req(
      "POST",
      "/api/zones",
      { zone_id: zoneId, name, polygon, risk_base: riskBase },
      ADMIN_HEADER,
    );
  }
}
