/**
 * WS 메시지 스키마 — 백엔드 `app/enums.py` 와 1:1 대응.
 *
 * 여기 정의를 바꾸면 백엔드 `WsMessageType` / `WsTopic` 도 같이 바꿔야 한다.
 * 서버가 보내는 봉투는 항상 `{ type, timestamp, payload }` 형태다 (API 명세서 §9-3).
 *
 * ## 왜 런타임 가드까지 두는가
 * TS 타입은 컴파일 시점에만 존재한다. 서버가 구버전이거나 필드가 빠진 프레임을
 * 보내면 `payload.robots.map(...)` 에서 화면 전체가 터진다. 관제 화면이 프레임
 * 하나 때문에 죽으면 안 되므로, dispatcher 는 아래 가드를 통과한 것만 store 에 넣는다.
 */

// ══════════════════════════════════════════════════════════════════════════
// 열거값 (백엔드 §11 Enum 고정)
// ══════════════════════════════════════════════════════════════════════════
export const ROBOT_STATES = [
  "OFFLINE", "MAPPING", "IDLE", "PATROLLING", "PATROL_PAUSED", "DISPATCHING",
  "INSPECTING", "REPORTING", "RESUMING", "CHARGING", "EMERGENCY_STOP", "ERROR",
  "ALERTING", "UNDOCKING", "DOCKING",
] as const;
export type RobotStateValue = (typeof ROBOT_STATES)[number];

export const EVENT_STATUSES = [
  "DETECTED", "QUEUED", "MERGED", "UNASSIGNED", "ASSIGNED", "VERIFYING",
  "CONFIRMED", "FALSE_POSITIVE", "SUPPRESSING", "RESOLVED", "ESCALATED",
] as const;
export type EventStatusValue = (typeof EVENT_STATUSES)[number];

export const EVENT_TYPES = [
  "FIRE", "SMOKE", "LEAK", "PERSON", "INTRUSION", "BREAKER_ABNORMAL",
  "LOCK_ABNORMAL", "PANEL_OUT_OF_RANGE", "ALIGN_MISMATCH",
] as const;
export type EventTypeValue = (typeof EVENT_TYPES)[number];

export const SEVERITIES = ["INFO", "WARN", "CRITICAL"] as const;
export type SeverityValue = (typeof SEVERITIES)[number];

export const MISSION_STATUSES = [
  "PENDING", "RUNNING", "PREEMPTED", "DONE", "CANCELED", "FAILED",
] as const;
export type MissionStatusValue = (typeof MISSION_STATUSES)[number];

export const SUPPRESSION_STATUSES = [
  "REQUESTED", "INTERLOCK_CHECK", "BLOCKED", "APPROVED", "POWER_CUTTING",
  "SPRINKLER_ON", "COMPLETED", "FAILED", "ABORTED",
] as const;
export type SuppressionStatusValue = (typeof SUPPRESSION_STATUSES)[number];

// ══════════════════════════════════════════════════════════════════════════
// 구독 토픽 (클라이언트 → 서버, §9-2)
// ══════════════════════════════════════════════════════════════════════════
export const WS_TOPICS = [
  "ROBOT_STATUS", "EVENT", "MISSION_STATUS", "LOG", "SUPPRESSION_STATUS", "DETECTION",
] as const;
export type WsTopic = (typeof WS_TOPICS)[number];

/** 대시보드가 기본으로 구독하는 토픽. 서브페이지는 필요한 것만 좁혀 쓴다. */
export const DEFAULT_TOPICS: WsTopic[] = [...WS_TOPICS];

// ══════════════════════════════════════════════════════════════════════════
// 서버 → 클라이언트 메시지 타입 (§9-3)
// ══════════════════════════════════════════════════════════════════════════
export const WS_MESSAGE_TYPES = [
  "SNAPSHOT", "ROBOT_STATUS", "ROBOT_STATE_CHANGED", "ROBOT_OFFLINE",
  "MISSION_STATUS", "EVENT", "DETECTION", "ALIGN_RESULT", "POSE_CORRECTED",
  "PRIORITY_UPDATED", "SUPPRESSION_STATUS", "LOG", "SYSTEM_ALERT", "PONG",
  "SUBSCRIBED",
] as const;
export type WsMessageType = (typeof WS_MESSAGE_TYPES)[number];

const MESSAGE_TYPE_SET: ReadonlySet<string> = new Set(WS_MESSAGE_TYPES);

/** 정의된 타입인지 — dispatcher 가 모르는 타입을 조용히 버릴 때 쓴다 (F-02). */
export function isKnownMessageType(value: unknown): value is WsMessageType {
  return typeof value === "string" && MESSAGE_TYPE_SET.has(value);
}

// ══════════════════════════════════════════════════════════════════════════
// 페이로드
// ══════════════════════════════════════════════════════════════════════════
export interface Pose {
  x: number;
  y: number;
  theta: number;
}

export interface RobotStatusPayload {
  robot_id: string;
  name?: string;
  state: RobotStateValue;
  state_ko?: string;
  progress_step?: number;
  battery?: number;
  pose?: Pose;
  mission_id?: string | null;
  current_node_id?: string | null;
  online?: boolean;
  last_seen?: string | null;
}

export interface RobotStateChangedPayload {
  robot_id: string;
  from: RobotStateValue;
  to: RobotStateValue;
  reason?: string;
  checkpoint?: Record<string, unknown> | null;
}

export interface RobotOfflinePayload {
  robot_id: string;
  last_seen: string | null;
}

export interface MissionStatusPayload {
  mission_id: string;
  mission_type?: "PATROL" | "ANOMALY";
  robot_id: string | null;
  route_id?: string | null;
  route_name?: string | null;
  status: MissionStatusValue;
  progress?: number;
  current_node_id?: string | null;
  next_node_id?: string | null;
  start_time?: string | null;
  end_time?: string | null;
}

export interface EventPayload {
  event_id: string;
  source?: string;
  camera_id?: string | null;
  robot_id?: string | null;
  type: EventTypeValue;
  severity: SeverityValue;
  status: EventStatusValue;
  zone_id?: string | null;
  node_id?: string | null;
  x?: number | null;
  y?: number | null;
  confidence?: number;
  final_confidence?: number | null;
  hit_count?: number;
  merged_into?: string | null;
  thumbnail_url?: string | null;
  assigned_robot_id?: string | null;
  verdict?: string | null;
  detected_at?: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
}

export interface DetectionBox {
  label: string;
  conf: number;
  bbox: [number, number, number, number];
}

export interface DetectionPayload {
  source: string;
  camera_id?: string;
  robot_id?: string;
  boxes: DetectionBox[];
  frame_ts?: string;
}

export interface AlignResultPayload {
  align_id?: number;
  event_id: string | null;
  equipment_id: string;
  observed_state: string | null;
  normal_state: string | null;
  expected_state: string | null;
  work_order_expected_state?: string | null;
  verdict: "OK" | "MISMATCH" | "UNVERIFIED";
  severity: SeverityValue;
  rule_id?: string | null;
  reason?: string | null;
}

export interface PoseCorrectedPayload {
  robot_id: string;
  marker_id: number;
  error_m: number;
  before?: Pose;
  after?: Pose;
}

export interface PriorityUpdatedItem {
  node_id: string;
  score: number;
  rank: number;
  visit_multiplier?: number;
}

export interface SuppressionStatusPayload {
  suppression_id: string;
  event_id?: string | null;
  zone_id?: string | null;
  status: SuppressionStatusValue;
  actions?: string[];
  steps?: Array<{ step: string; status: string; at: string; result?: string }>;
  interlock?: { passed: boolean; blockers: Array<{ code: string; detail: string }> } | null;
}

export interface LogPayload {
  level: "INFO" | "WARN" | "CRITICAL";
  actor?: string;
  message: string;
  ref_id?: string;
}

export interface SystemAlertPayload {
  code: string;
  message: string;
  severity: SeverityValue;
}

export interface ZoneSnapshot {
  zone_id: string;
  name: string;
  polygon: number[][];
  risk_base: number;
  camera_ids: string[];
}

export interface NodeSnapshot {
  node_id: string;
  name: string;
  zone_id: string | null;
  x: number;
  y: number;
  theta: number;
  is_blindspot: boolean;
  priority_score: number;
}

export interface MapSnapshot {
  map_id: string;
  name: string;
  image_url: string | null;
  resolution: number;
  origin: [number, number, number] | number[];
  width: number;
  height: number;
}

/** 접속 직후 1회. 이 한 프레임으로 화면 전체를 그릴 수 있어야 한다 (F-03). */
export interface SnapshotPayload {
  robots: RobotStatusPayload[];
  missions: MissionStatusPayload[];
  events: EventPayload[];
  zones: ZoneSnapshot[];
  nodes: NodeSnapshot[];
  map: MapSnapshot | null;
  suppression_mode?: "MANUAL" | "AUTO";
}

// ══════════════════════════════════════════════════════════════════════════
// 봉투
// ══════════════════════════════════════════════════════════════════════════
export interface WsEnvelope<T = unknown> {
  type: WsMessageType;
  timestamp: string;
  payload: T;
}

/** 타입별 페이로드 매핑 — dispatcher 핸들러가 이 표로 타입을 좁힌다. */
export interface WsPayloadMap {
  SNAPSHOT: SnapshotPayload;
  ROBOT_STATUS: RobotStatusPayload;
  ROBOT_STATE_CHANGED: RobotStateChangedPayload;
  ROBOT_OFFLINE: RobotOfflinePayload;
  MISSION_STATUS: MissionStatusPayload;
  EVENT: EventPayload;
  DETECTION: DetectionPayload;
  ALIGN_RESULT: AlignResultPayload;
  POSE_CORRECTED: PoseCorrectedPayload;
  PRIORITY_UPDATED: PriorityUpdatedItem[];
  SUPPRESSION_STATUS: SuppressionStatusPayload;
  LOG: LogPayload;
  SYSTEM_ALERT: SystemAlertPayload;
  PONG: Record<string, never>;
  SUBSCRIBED: { topics: WsTopic[] };
}

// ══════════════════════════════════════════════════════════════════════════
// 클라이언트 → 서버
// ══════════════════════════════════════════════════════════════════════════
export type ClientMessage =
  | { action: "subscribe"; topics: WsTopic[] }
  | { action: "snapshot" }
  | { action: "ping" };

// ══════════════════════════════════════════════════════════════════════════
// 런타임 가드
// ══════════════════════════════════════════════════════════════════════════
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 봉투 형태인지 확인. payload 안쪽은 타입별 가드가 따로 본다. */
export function parseEnvelope(raw: string): WsEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isObject(parsed)) return null;
  if (!isKnownMessageType(parsed.type)) return null;
  return {
    type: parsed.type,
    timestamp: typeof parsed.timestamp === "string" ? parsed.timestamp : new Date().toISOString(),
    payload: parsed.payload ?? {},
  };
}

/** 타입별 최소 필드 검증. 여기 통과 못 하면 store 에 넣지 않는다. */
export const payloadGuards: Partial<Record<WsMessageType, (p: unknown) => boolean>> = {
  SNAPSHOT: (p) => isObject(p) && Array.isArray(p.robots),
  ROBOT_STATUS: (p) => isObject(p) && typeof p.robot_id === "string",
  ROBOT_STATE_CHANGED: (p) => isObject(p) && typeof p.robot_id === "string",
  ROBOT_OFFLINE: (p) => isObject(p) && typeof p.robot_id === "string",
  MISSION_STATUS: (p) => isObject(p) && typeof p.mission_id === "string",
  EVENT: (p) => isObject(p) && typeof p.event_id === "string",
  DETECTION: (p) => isObject(p) && Array.isArray(p.boxes),
  ALIGN_RESULT: (p) => isObject(p) && typeof p.equipment_id === "string",
  POSE_CORRECTED: (p) => isObject(p) && typeof p.robot_id === "string",
  PRIORITY_UPDATED: (p) => Array.isArray(p),
  SUPPRESSION_STATUS: (p) => isObject(p) && typeof p.suppression_id === "string",
  LOG: (p) => isObject(p) && typeof p.message === "string",
  SYSTEM_ALERT: (p) => isObject(p) && typeof p.message === "string",
  SUBSCRIBED: (p) => isObject(p) && Array.isArray(p.topics),
};

export function isValidPayload(type: WsMessageType, payload: unknown): boolean {
  const guard = payloadGuards[type];
  return guard ? guard(payload) : true; // 가드 없는 타입(PONG 등)은 통과
}
