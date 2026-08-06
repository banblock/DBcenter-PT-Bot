export type RobotState =
  | "OFFLINE"
  | "MAPPING"
  | "IDLE"
  | "UNDOCKING"
  | "PATROLLING"
  | "PATROL_PAUSED"
  | "DISPATCHING"
  | "INSPECTING"
  | "ALERTING"
  | "REPORTING"
  | "RESUMING"
  | "DOCKING"
  | "CHARGING"
  | "EMERGENCY_STOP"
  | "ERROR"
  | "ASSIGNED";

export type StateTone = "off" | "info" | "idle" | "ok" | "warn" | "danger";

export interface StateMetaEntry {
  ko: string;
  tone: StateTone;
}

export type MissionType = "PATROL" | "ANOMALY";

export interface TrackStep {
  t: string;
  s: RobotState[];
  alert?: boolean;
}

export interface Track {
  label: string;
  icon: string;
  steps: TrackStep[];
}

export type Command = "start" | "pause" | "resume" | "dock" | "estop" | "reset" | "ack";

export type ZoneState = "NORMAL" | "MISMATCH" | "RECHECK" | "SCANNING" | "STALE" | "UNREADABLE";

export interface ZoneChip {
  id: string;
  state: ZoneState;
}

export interface RobotPose {
  x: number;
  y: number;
}

export interface Robot {
  id: string;
  state: RobotState;
  mission_type: MissionType;
  step?: number;
  step_note?: string | null;
  battery: number;
  task?: string;
  route?: string;
  zone?: string;
  event_id?: string | null;
  event_type?: string;
  target_zone?: string;
  patrol_resume?: { step: number; zone: string } | null;
  zones?: ZoneChip[];
  pose?: RobotPose;
  ts: number;
}

export type EventSeverity = "DANGER" | "WARN" | "INFO";

export type EventState =
  | "DETECTED"
  | "ASSIGNED"
  | "EN_ROUTE"
  | "ON_SITE"
  | "ALERTING"
  | "ACK_WAIT"
  | "RESOLVED"
  | "FALSE_ALARM"
  | "ESCALATED";

export interface AppEvent {
  id: string;
  severity: EventSeverity;
  state: EventState;
  text: string;
  zone?: string;
  assignee?: string;
  ts: number;
}

export interface Stats {
  fire: number;
  leak: number;
}

export type LogTag = "PC1" | "PC2" | "Fleet" | string;

export interface LogEntry {
  id: string;
  ts: number;
  tag: LogTag;
  msg: string;
  hot?: boolean;
}

/** WS 인바운드 프레임 — 서버는 필요한 필드만 채워 보낸다 (부분 갱신) */
export interface InboundRobotPatch extends Partial<Omit<Robot, "id">> {
  id: string;
  ts?: string | number;
}

export interface InboundMessage {
  robots?: InboundRobotPatch[];
  events?: Array<Omit<AppEvent, "ts"> & { ts: string | number }>;
  stats?: Partial<Stats>;
  log?: { tag: LogTag; msg: string; hot?: boolean };
}

export type LinkMode = "connecting" | "live" | "demo" | "down";
