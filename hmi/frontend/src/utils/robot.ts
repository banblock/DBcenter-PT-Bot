import { TRACKS } from "../constants/dashboard";
import type { Robot } from "../types";

export function fmt(ts: number): string {
  return new Date(ts).toTimeString().slice(0, 8);
}

export function ageSec(ts: number, now: number): number {
  return Math.max(0, Math.round((now - ts) / 1000));
}

/** 서버가 step을 안 주면 현재 robot_state로 트랙 위치를 추정한다 */
export function stepIndex(robot: Robot): number {
  if (typeof robot.step === "number") return robot.step;
  const track = TRACKS[robot.mission_type].steps;
  const i = track.findIndex((st) => st.s.includes(robot.state));
  return i < 0 ? 0 : i + 1;
}
