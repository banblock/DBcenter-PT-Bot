/**
 * REST 엔드포인트 래퍼 — API 명세서 §1~§8 을 함수로 옮긴 것.
 *
 * 컴포넌트가 URL 문자열을 직접 쓰지 않게 한다. 경로가 바뀌면 여기만 고친다.
 * 모든 함수는 봉투가 벗겨진 `data` 를 반환하고, 실패는 `ApiError` 로 던진다.
 */

import { _delete, _get, _getPaged, _post, _put, type RequestOptions } from "../http/httpClient";
import type {
  EventPayload,
  MapSnapshot,
  MissionStatusPayload,
  NodeSnapshot,
  RobotStatusPayload,
  SuppressionStatusPayload,
  ZoneSnapshot,
} from "../ws/messages";

// ── §1 맵 ─────────────────────────────────────────────────────────────────
export const mapApi = {
  list: () => _get<MapSnapshot[]>("/api/map"),
  active: () => _get<MapSnapshot | null>("/api/map/active"),
  get: (mapId: string) => _get<MapSnapshot>(`/api/map/${mapId}`),
  listAruco: (mapId: string) => _get<unknown[]>(`/api/map/${mapId}/aruco`),
  startSlam: (robotId: string, mapName: string) =>
    _post<{ map_id: string; status: string }>("/api/map/slam/start", {
      robot_id: robotId,
      map_name: mapName,
    }),
  saveSlam: (robotId: string, mapId: string) =>
    _post<{ map_id: string }>("/api/map/slam/save", { robot_id: robotId, map_id: mapId }),
};

// ── §2 구역·노드·경로 ─────────────────────────────────────────────────────
export const zoneApi = {
  list: () => _get<ZoneSnapshot[]>("/api/zones"),
  create: (body: Partial<ZoneSnapshot> & { name: string }) =>
    _post<{ zone_id: string }>("/api/zones", body),
  update: (zoneId: string, body: unknown) => _put<{ zone_id: string }>(`/api/zones/${zoneId}`, body),
  remove: (zoneId: string) => _delete<{ deleted: boolean }>(`/api/zones/${zoneId}`),
};

export const nodeApi = {
  list: (params?: { map_id?: string; zone_id?: string }) =>
    _get<NodeSnapshot[]>("/api/nodes", params),
  create: (body: unknown) => _post<{ node_id: string }>("/api/nodes", body),
  update: (nodeId: string, body: unknown) => _put<{ node_id: string }>(`/api/nodes/${nodeId}`, body),
  remove: (nodeId: string) => _delete<{ deleted: boolean }>(`/api/nodes/${nodeId}`),
};

export interface Route {
  route_id: string;
  name: string;
  map_id: string | null;
  node_order: string[];
  loop: boolean;
}

export const routeApi = {
  list: () => _get<Route[]>("/api/routes"),
  get: (routeId: string) => _get<Route>(`/api/routes/${routeId}`),
  create: (body: unknown) => _post<{ route_id: string }>("/api/routes", body),
  update: (routeId: string, body: unknown) => _put<{ route_id: string }>(`/api/routes/${routeId}`, body),
  remove: (routeId: string) => _delete<{ deleted: boolean }>(`/api/routes/${routeId}`),
  validate: (routeId: string) =>
    _post<{ valid: boolean; errors: Array<{ node_id: string; reason: string }> }>(
      `/api/routes/${routeId}/validate`,
    ),
};

// ── §3 순찰 미션 ──────────────────────────────────────────────────────────
export const patrolApi = {
  start: (body: {
    route_id: string;
    robot_ids: string[];
    mode?: "LOOP" | "ONCE";
    apply_priority?: boolean;
  }) =>
    _post<{
      mission_id: string;
      mission_ids: string[];
      status: string;
      assigned: Array<{ robot_id: string; nodes: string[] }>;
    }>("/api/patrol/start", { mode: "LOOP", apply_priority: true, ...body }),

  pause: (missionId: string, reason = "MANUAL") =>
    _post<{ status: string }>(`/api/patrol/${missionId}/pause`, { reason }),
  resume: (missionId: string) => _post<{ status: string }>(`/api/patrol/${missionId}/resume`),
  cancel: (missionId: string, reason = "OPERATOR_CANCEL") =>
    _post<{ status: string }>(`/api/patrol/${missionId}/cancel`, { reason }),

  missions: (params?: { status?: string; robot_id?: string; limit?: number }) =>
    _get<MissionStatusPayload[]>("/api/patrol/missions", params),
  reorder: (order: string[]) => _put<{ applied: boolean }>("/api/patrol/missions/reorder", { order }),
};

// ── §4 로봇 ───────────────────────────────────────────────────────────────
export const robotApi = {
  list: () => _get<RobotStatusPayload[]>("/api/robots"),
  get: (robotId: string) => _get<RobotStatusPayload>(`/api/robots/${robotId}`),
  goto: (robotId: string, waypoints: Array<{ x: number; y: number; theta?: number }>, preempt = true) =>
    _post<{ command_id: string; accepted: boolean; dispatched: boolean; eta_sec: number | null }>(
      `/api/robots/${robotId}/goto`,
      { waypoints: waypoints.map((w) => ({ theta: 0, ...w })), preempt },
    ),
  emergencyStop: (robotId: string, reason = "OPERATOR") =>
    _post<{ state: string; checkpoint: Record<string, unknown> }>(
      `/api/robots/${robotId}/emergency-stop`,
      { reason },
    ),
  emergencyStopAll: () =>
    _post<{ stopped: string[]; failed: unknown[] }>("/api/robots/emergency-stop-all"),
  resume: (robotId: string, mode: "RESUME_CHECKPOINT" | "RETURN_HOME" = "RESUME_CHECKPOINT") =>
    _post<{ state: string; resumed_from: string | null }>(`/api/robots/${robotId}/resume`, { mode }),
  dock: (robotId: string) => _post<{ state: string }>(`/api/robots/${robotId}/dock`),
  evacuate: (robotId: string, zoneId: string) =>
    _post<{ accepted: boolean }>(`/api/robots/${robotId}/evacuate`, { zone_id: zoneId }),
};

// ── §5 이벤트 ─────────────────────────────────────────────────────────────
export interface EventDetail {
  event: EventPayload;
  timeline: Array<{ at: string; stage: string; actor: string | null; detail: string | null }>;
  media: Array<{ media_id: number; kind: string; uri: string; angle_idx: number | null }>;
  observations: Array<Record<string, unknown>>;
}

export const eventApi = {
  query: (
    params: {
      from?: string;
      to?: string;
      zone_id?: string;
      type?: string;
      severity?: string;
      status?: string;
      source?: string;
      page?: number;
      size?: number;
    },
    options?: RequestOptions,
  ) => _getPaged<EventPayload>("/api/events", params, options),

  get: (eventId: string) => _get<EventDetail>(`/api/events/${eventId}`),
  detect: (body: unknown) => _post<{ event_id: string; status: string }>("/api/events/detect", body),
  dispatch: (eventId: string, robotId?: string) =>
    _post<{ assigned_robot_id: string; status: string }>(`/api/events/${eventId}/dispatch`, {
      robot_id: robotId ?? null,
      preempt: true,
    }),
  ack: (eventId: string, operator: string) =>
    _post<{ acknowledged_at: string }>(`/api/events/${eventId}/ack`, { operator }),
  resolve: (eventId: string, operator: string, memo?: string) =>
    _post<{ status: string }>(`/api/events/${eventId}/resolve`, { operator, memo }),
  review: (eventId: string, body: { reviewer: string; verdict: string; memo?: string }) =>
    _post<{ status: string }>(`/api/events/${eventId}/review`, body),
  reinspect: (eventId: string, extraAngles = 2, reason = "LOW_CONFIDENCE") =>
    _post<{ status: string }>(`/api/events/${eventId}/reinspect`, {
      extra_angles: extraAngles,
      reason,
    }),
  verdict: (eventId: string, auto = true) =>
    _post<{ verdict: string; final_confidence: number }>(`/api/events/${eventId}/verdict`, { auto }),

  /** CSV 내보내기 (F-49) — Blob 을 받아 브라우저 다운로드를 띄운다. */
  exportCsv: async (params: Record<string, unknown>): Promise<void> => {
    const blob = await _get<Blob>("/api/events/export", { format: "csv", ...params });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `events-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  },
};

// ── §6 설비 · align ───────────────────────────────────────────────────────
export interface Equipment {
  equipment_id: string;
  type: string;
  name: string;
  node_id: string | null;
  zone_id: string | null;
  normal_state: string | null;
  value_min: number | null;
  value_max: number | null;
  unit: string | null;
  check_state: string;
  last_observed_state: string | null;
  last_value: number | null;
  last_verdict: string | null;
  last_severity: string | null;
  last_checked_at: string | null;
}

export const equipmentApi = {
  list: (params?: { zone_id?: string; type?: string }) => _get<Equipment[]>("/api/equipment", params),
  get: (equipmentId: string) => _get<Equipment>(`/api/equipment/${equipmentId}`),
  create: (body: unknown) => _post<{ equipment_id: string }>("/api/equipment", body),
  update: (equipmentId: string, body: unknown) =>
    _put<{ equipment_id: string }>(`/api/equipment/${equipmentId}`, body),
  remove: (equipmentId: string) => _delete<{ deleted: boolean }>(`/api/equipment/${equipmentId}`),
};

export const workOrderApi = {
  list: (params?: { equipment_id?: string; status?: string }) =>
    _get<Array<Record<string, unknown>>>("/api/work-orders", params),
  create: (body: unknown) => _post<{ wo_id: string }>("/api/work-orders", body),
};

export const alignApi = {
  check: (body: { equipment_id: string; observed_state: string; value?: number; event_id?: string }) =>
    _post<Record<string, unknown>>("/api/align/check", body),
  results: (params?: { event_id?: string; equipment_id?: string; from?: string; to?: string }) =>
    _get<Array<Record<string, unknown>>>("/api/align/results", params),
  rules: () => _get<Array<Record<string, unknown>>>("/api/align/rules"),
  createRule: (body: unknown) => _post<{ rule_id: string }>("/api/align/rules", body),
  updateRule: (ruleId: string, body: unknown) => _put<{ rule_id: string }>(`/api/align/rules/${ruleId}`, body),
  removeRule: (ruleId: string) => _delete<{ deleted: boolean }>(`/api/align/rules/${ruleId}`),
};

// ── §7 우선순위 · 통계 ────────────────────────────────────────────────────
export interface PriorityNode {
  node_id: string;
  name: string;
  zone_id: string | null;
  event_count: number;
  max_severity: string | null;
  hours_since_last_patrol: number | null;
  manual_weight: number;
  score: number;
  rank: number;
  visit_multiplier: number;
}

export const priorityApi = {
  nodes: (window: "24h" | "7d" | "30d" = "7d") =>
    _get<PriorityNode[]>("/api/priority/nodes", { window }),
  setWeight: (nodeId: string, manualWeight: number, memo?: string) =>
    _put<{ before_score: number; after_score: number }>(`/api/priority/nodes/${nodeId}`, {
      manual_weight: manualWeight,
      memo,
    }),
  recalculate: (window: "24h" | "7d" | "30d" = "7d") =>
    _post<{ updated: number }>("/api/priority/recalculate", { window }),
  log: (params?: { node_id?: string; limit?: number }) =>
    _get<Array<Record<string, unknown>>>("/api/priority/log", params),
};

export interface EventStats {
  total: number;
  false_positive_rate: number;
  avg_response_sec: { detect_to_arrive: number; detect_to_confirm: number };
  groups: Array<{ key: string | null; count: number }>;
  group_by: string;
}

export const statsApi = {
  events: (params?: { from?: string; to?: string; group_by?: "zone" | "type" | "day" }) =>
    _get<EventStats>("/api/stats/events", params),
  overview: () =>
    _get<{
      date: string;
      total_today: number;
      fire_smoke: number;
      leak: number;
      breaker_mismatch: number;
      unresolved: number;
    }>("/api/stats/overview"),
};

// ── §8 진압 · 설정 ────────────────────────────────────────────────────────
export const suppressionApi = {
  request: (body: {
    zone_id: string;
    actions: Array<"POWER_CUT" | "SPRINKLER">;
    event_id?: string;
    breaker_ids?: string[];
    sprinkler_duration_sec?: number;
    requested_by: string;
    confirm_text: string;
  }) =>
    _post<{ suppression_id: string; status: string; interlock: unknown }>(
      "/api/suppression/request",
      body,
    ),
  get: (suppressionId: string) => _get<SuppressionStatusPayload>(`/api/suppression/${suppressionId}`),
  approve: (suppressionId: string, approver: string) =>
    _post<{ status: string }>(`/api/suppression/${suppressionId}/approve`, { approver }),
  abort: (suppressionId: string, operator: string, reason?: string) =>
    _post<{ status: string }>(`/api/suppression/${suppressionId}/abort`, { operator, reason }),
  logs: (params?: { from?: string; to?: string }) =>
    _get<Array<Record<string, unknown>>>("/api/suppression/logs", params),
};

export const configApi = {
  get: () => _get<Record<string, unknown>>("/api/config"),
  setSuppressionMode: (mode: "MANUAL" | "AUTO") =>
    _put<{ mode: string }>("/api/config/suppression-mode", { mode }),
};
