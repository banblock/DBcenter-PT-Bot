// 상태·명령 규칙표

import type { Command, MissionType, RobotState, StateMetaEntry, Track } from "../types";

/** 기능1: 구역 2개 · 각 구역당 waypoint 3개 · 구역↔담당 AMR 매핑 */
export const ZONES = ["존-1", "존-2"] as const;
export const ZONE_AMR: Record<string, string> = { "존-1": "AMR-01", "존-2": "AMR-02" };
export const WP_PER_ZONE = 3;

/** 상태별 한글 라벨 + 뱃지 톤 */
export const STATE_META: Record<RobotState, StateMetaEntry> = {
  OFFLINE: { ko: "연결 끊김", tone: "off" },
  MAPPING: { ko: "맵 생성 중", tone: "info" },
  IDLE: { ko: "대기", tone: "idle" },
  UNDOCKING: { ko: "출발 준비", tone: "info" },
  PATROLLING: { ko: "순찰 중", tone: "ok" },
  PATROL_PAUSED: { ko: "순찰 일시정지", tone: "warn" },
  DISPATCHING: { ko: "이상지점 이동 중", tone: "warn" },
  INSPECTING: { ko: "점검 중", tone: "info" },
  ALERTING: { ko: "현장 경보 중", tone: "danger" },
  REPORTING: { ko: "결과 전송 중", tone: "info" },
  RESUMING: { ko: "순찰 복귀 중", tone: "info" },
  DOCKING: { ko: "도킹 중", tone: "idle" },
  CHARGING: { ko: "충전 중", tone: "idle" },
  EMERGENCY_STOP: { ko: "긴급정지", tone: "danger" },
  ERROR: { ko: "오류", tone: "danger" },
  ASSIGNED: { ko: "배정됨", tone: "warn" },
};

/** 미션별 5스텝 트랙 — 카드의 스텝 리스트는 이 표에서만 나온다 */
export const TRACKS: Record<MissionType, Track> = {
  PATROL: {
    label: "순찰 미션",
    icon: "🛡",
    steps: [
      { t: "순찰 시작", s: ["UNDOCKING"] },
      { t: "경로 주행", s: ["PATROLLING"] },
      { t: "존 차단기 점검", s: ["INSPECTING"] },
      { t: "다음 구역 이동", s: ["PATROLLING"] },
      { t: "순찰 완료", s: ["DOCKING", "CHARGING", "IDLE"] },
    ],
  },
  ANOMALY: {
    label: "이상 대응 미션",
    icon: "🚨",
    steps: [
      { t: "이벤트 접수", s: ["ASSIGNED"] },
      { t: "현장 출동", s: ["DISPATCHING"] },
      { t: "현장 확인", s: ["INSPECTING"] },
      { t: "경보 발령", s: ["ALERTING"], alert: true },
      { t: "보고·순찰 복귀", s: ["REPORTING", "RESUMING"] },
    ],
  },
};

/** 상태별 허용 명령 — 불가능한 버튼은 카드에 렌더되지 않는다 */
export const ALLOWED: Record<RobotState, Command[]> = {
  OFFLINE: [],
  MAPPING: ["estop"],
  IDLE: ["start", "dock"],
  UNDOCKING: ["estop"],
  PATROLLING: ["pause", "estop", "dock"],
  PATROL_PAUSED: ["resume", "estop", "dock"],
  INSPECTING: ["pause", "estop"],
  DISPATCHING: ["estop"],
  ALERTING: ["ack", "estop"],
  REPORTING: ["estop"],
  RESUMING: ["pause", "estop"],
  DOCKING: ["estop"],
  CHARGING: ["start"],
  EMERGENCY_STOP: ["reset"],
  ERROR: ["reset"],
  ASSIGNED: ["estop"],
};

export const CMD_LABEL: Record<Command, string> = {
  start: "▶ 순찰",
  pause: "⏸ 일시정지",
  resume: "▶ 재개",
  dock: "🔌 복귀",
  estop: "⨯ 정지",
  reset: "↺ 리셋",
  ack: "✓ 경보 확인",
};

export const EVENT_SEVERITY_LABEL: Record<string, string> = {
  DANGER: "위험",
  WARN: "경고",
  INFO: "정보",
};

export const EVENT_STATE_LABEL: Record<string, string> = {
  DETECTED: "접수",
  ASSIGNED: "배정",
  EN_ROUTE: "출동 중",
  ON_SITE: "현장",
  ALERTING: "경보 중",
  ACK_WAIT: "확인 필요",
  RESOLVED: "완료",
  FALSE_ALARM: "오탐",
  ESCALATED: "상위 보고",
};

export const EVENT_STATE_TONE: Record<string, "run" | "chk" | "done"> = {
  RESOLVED: "done",
  FALSE_ALARM: "done",
  ACK_WAIT: "chk",
  ESCALATED: "chk",
};
