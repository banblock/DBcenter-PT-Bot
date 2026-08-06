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

import type { AppEvent, Command, EventState, InboundMessage, InboundRobotPatch, RobotState } from "../types";

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
export const WS_MONITOR_URL: string = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/monitor";
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
  route_name?: string | null;
  status?: string;
}
interface BackendEvent {
  event_id: string;
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
const zoneNameById = new Map<string, string>();
/** 이벤트는 스냅샷이 통째로 주므로, 단건 EVENT 도 누적해 전체 배열을 다시 만든다 */
const eventsById = new Map<string, BackendEvent>();
let cachedRouteId: string | null = null;

export function resetCaches(): void {
  missionsById.clear();
  activeMissionByRobot.clear();
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
    pose: r.pose ? { x: r.pose.x, y: r.pose.y } : undefined,
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

function indexMission(m: BackendMission): void {
  missionsById.set(m.mission_id, m);
  if (!m.robot_id) return;
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
  let env: { type?: unknown; payload?: unknown };
  try {
    env = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!env || typeof env.type !== "string") return null;
  const p = (env.payload ?? {}) as Record<string, unknown>;

  switch (env.type) {
    case "SNAPSHOT": {
      zoneNameById.clear();
      (p.zones as BackendZone[] | undefined)?.forEach((z) => zoneNameById.set(z.zone_id, z.name));
      missionsById.clear();
      activeMissionByRobot.clear();
      (p.missions as BackendMission[] | undefined)?.forEach(indexMission);
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
      eventsById.set(ev.event_id, ev);
      const out = eventToOut(ev);
      return {
        events: emitEvents(),
        log: { tag: "PC2", msg: `이벤트 ${out.text}`, hot: out.severity === "DANGER" },
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
async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
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

/** 카드 명령 버튼 (start/pause/resume/dock/estop/reset/ack) → REST. */
export async function backendCommand(robotId: string, cmd: Command): Promise<void> {
  switch (cmd) {
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
      const routeId = await firstRouteId();
      if (!routeId) throw new Error("등록된 순찰 경로가 없습니다");
      await req("POST", "/api/patrol/start", {
        route_id: routeId,
        robot_ids: [robotId],
        mode: "LOOP",
        apply_priority: true,
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
