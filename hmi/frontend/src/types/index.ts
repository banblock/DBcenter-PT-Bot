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

export type Command = "start" | "pause" | "resume" | "dock" | "estop" | "stop_and_dock" | "reset" | "ack";

export type ZoneState = "NORMAL" | "MISMATCH" | "RECHECK" | "SCANNING" | "STALE" | "UNREADABLE";

export interface ZoneChip {
  id: string;
  state: ZoneState;
}

export interface RobotPose {
  x: number;
  y: number;
  theta?: number;
}

/** 기능1: 사용자가 구역별로 지정하는 순찰 waypoint */
export interface Waypoint {
  x: number;
  y: number;
  theta: number;
}

export type WaypointMap = Record<string, Waypoint[]>;

/** 맵 선택: 백엔드 MapOut 을 화면에서 쓰는 모양 (image_url 은 절대 URL 로 변환됨) */
export interface MapInfo {
  map_id: string;
  name: string;
  /** 렌더에 바로 쓰는 절대 URL (API_BASE + image_url). 없으면 이미지 없음 */
  image_url: string | null;
  resolution: number; // m/pixel
  origin: [number, number, number]; // [x, y, theta] (m, rad)
  width: number; // px
  height: number; // px
  is_active: boolean;
}

/** 맵 픽셀 좌표계의 사각형 존 영역 (viewBox = 0 0 width height 와 동일) */
export interface ZoneRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type ZoneRectMap = Record<string, ZoneRect>;

/** 점유격자 — waypoint 를 자유공간(백색)에만 찍기 위한 데이터.
 *  data 는 row-major(위→아래·좌→우) 그레이스케일(0~255). freeMin 이상이면 자유공간. */
export interface MapGrid {
  available: boolean;
  width: number;
  height: number;
  freeMin: number;
  data: Uint8Array | null;
}

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
  /** 있으면 팝업에 실제 MJPEG 카메라 스트림(<img>)을 띄운다(팝업 열려 있을 때만 마운트).
   *  없으면 기존 스타일 박스로 렌더. AMR 화재 팝업이 AMR 캠 실피드를 보여줄 때 사용. */
  camStreamUrl?: string;
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
  cameraId?: string;
  /** source=amr 일 때 감지한 로봇 id (AMR-01 등). AMR 카메라 스트림 id 로도 쓴다. */
  robotId?: string;
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
  /** 새 CCTV 이벤트가 최초 수신된 순간에만 채운다(상태 갱신 재팝업 방지). */
  detectedCctvEvent?: Omit<AppEvent, "ts"> & { ts: string | number };
  /** 새 AMR 카메라 이벤트가 최초 수신된 순간에만 채운다(사람 출동 팝업 트리거). */
  detectedAmrEvent?: Omit<AppEvent, "ts"> & { ts: string | number };
}

export type LinkMode = "connecting" | "live" | "demo" | "down";
