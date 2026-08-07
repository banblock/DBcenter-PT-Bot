// 데이터 모양 정의

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

/** 기능1: 사용자가 구역별로 지정하는 순찰 waypoint */
export interface Waypoint {
  x: number;
  y: number;
  theta: number;
}

export type WaypointMap = Record<string, Waypoint[]>;

/** 기능3·4: 카메라/CCTV 확인 팝업 */
export interface PopupAction {
  label: string;
  cls?: string;
  onClick?: () => void;
}

export interface PopupData {
  title: string;
  camLabel: string;
  camHot: boolean;
  evtHtml: string;
  actions: PopupAction[];
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
  /** 기능2: 도달한 waypoint 순번 (도달 시점에만 갱신) */
  atWaypoint?: number;
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
  /** 기능4·6: 이벤트 출처 구분 (차단기 / AMR / CCTV) */
  kind?: "GATE" | "AMR" | "CCTV";
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

/** WS 인바운드 프레임 — 서버는 필요한 필드만 채워 보낸다 (부분 갱신).
 *  ts 는 서버가 ISO 문자열로도 줄 수 있어 Robot 의 number 와 분리한다. */
export interface InboundRobotPatch extends Partial<Omit<Robot, "id" | "ts">> {
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
