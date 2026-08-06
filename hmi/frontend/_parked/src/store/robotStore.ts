/**
 * 로봇 상태 store.
 *
 * ## 리렌더 최소화
 * ROBOT_STATUS 는 5Hz × N대로 들어온다. 컴포넌트가 `robots` 배열 전체를 구독하면
 * 로봇 한 대의 배터리가 1% 바뀔 때마다 좌측 패널 전체가 다시 그려진다.
 * 그래서 저장은 `Record<robotId, Robot>` 로 하고, 카드는
 * `useRobot(id)` 로 자기 것만 구독한다.
 *
 * 또한 갱신 시 **값이 실제로 달라졌을 때만** 새 객체를 만든다. 같은 값이 반복
 * 도착하면 참조가 유지되어 리렌더가 일어나지 않는다.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  Pose,
  RobotStateValue,
  RobotStatusPayload,
} from "../services/ws/messages";

export interface Robot {
  robot_id: string;
  name: string;
  state: RobotStateValue;
  state_ko: string;
  progress_step: number;
  battery: number;
  pose: Pose;
  mission_id: string | null;
  current_node_id: string | null;
  online: boolean;
  last_seen: string | null;
  /** 마지막으로 프레임을 받은 시각 (카드의 age 표시용) */
  updated_at: number;
}

const EMPTY_POSE: Pose = { x: 0, y: 0, theta: 0 };

function toRobot(payload: RobotStatusPayload, previous?: Robot): Robot {
  return {
    robot_id: payload.robot_id,
    name: payload.name ?? previous?.name ?? payload.robot_id,
    state: payload.state ?? previous?.state ?? "OFFLINE",
    state_ko: payload.state_ko ?? previous?.state_ko ?? "",
    progress_step: payload.progress_step ?? previous?.progress_step ?? 0,
    battery: payload.battery ?? previous?.battery ?? 0,
    pose: payload.pose ?? previous?.pose ?? EMPTY_POSE,
    mission_id: payload.mission_id ?? previous?.mission_id ?? null,
    current_node_id: payload.current_node_id ?? previous?.current_node_id ?? null,
    online: payload.online ?? previous?.online ?? false,
    last_seen: payload.last_seen ?? previous?.last_seen ?? null,
    updated_at: Date.now(),
  };
}

/** updated_at 을 뺀 나머지가 같은지 — 값이 그대로면 리렌더를 만들지 않는다. */
function isSameRobot(a: Robot | undefined, b: Robot): boolean {
  if (!a) return false;
  return (
    a.state === b.state &&
    a.battery === b.battery &&
    a.progress_step === b.progress_step &&
    a.online === b.online &&
    a.mission_id === b.mission_id &&
    a.current_node_id === b.current_node_id &&
    a.pose.x === b.pose.x &&
    a.pose.y === b.pose.y &&
    a.pose.theta === b.pose.theta
  );
}

interface RobotStoreState {
  byId: Record<string, Robot>;
  order: string[];
  /** 상태 전이 이력 — 카드의 "직전 상태" 표시와 로그 생성에 쓴다 */
  lastTransition: Record<string, { from: RobotStateValue; to: RobotStateValue; at: number }>;

  upsert: (payload: RobotStatusPayload) => void;
  upsertMany: (payloads: RobotStatusPayload[]) => void;
  applyStateChange: (robotId: string, from: RobotStateValue, to: RobotStateValue) => void;
  markOffline: (robotId: string, lastSeen: string | null) => void;
  reset: () => void;
}

export const useRobotStore = create<RobotStoreState>((set) => ({
  byId: {},
  order: [],
  lastTransition: {},

  upsert: (payload) =>
    set((state) => {
      const previous = state.byId[payload.robot_id];
      const next = toRobot(payload, previous);
      if (isSameRobot(previous, next)) return state; // 참조 유지 → 리렌더 없음
      return {
        byId: { ...state.byId, [payload.robot_id]: next },
        order: state.order.includes(payload.robot_id)
          ? state.order
          : [...state.order, payload.robot_id].sort(),
      };
    }),

  upsertMany: (payloads) =>
    set((state) => {
      const byId = { ...state.byId };
      for (const payload of payloads) {
        byId[payload.robot_id] = toRobot(payload, state.byId[payload.robot_id]);
      }
      return { byId, order: Object.keys(byId).sort() };
    }),

  applyStateChange: (robotId, from, to) =>
    set((state) => {
      const previous = state.byId[robotId];
      if (!previous) return state;
      return {
        byId: { ...state.byId, [robotId]: { ...previous, state: to, updated_at: Date.now() } },
        lastTransition: { ...state.lastTransition, [robotId]: { from, to, at: Date.now() } },
      };
    }),

  markOffline: (robotId, lastSeen) =>
    set((state) => {
      const previous = state.byId[robotId];
      if (!previous) return state;
      return {
        byId: {
          ...state.byId,
          [robotId]: {
            ...previous,
            state: "OFFLINE",
            online: false,
            last_seen: lastSeen,
            updated_at: Date.now(),
          },
        },
      };
    }),

  reset: () => set({ byId: {}, order: [], lastTransition: {} }),
}));

// ── 선택자 ────────────────────────────────────────────────────────────────
/** 카드 1장이 쓰는 훅 — 이 로봇이 바뀔 때만 리렌더된다. */
export const useRobot = (robotId: string): Robot | undefined =>
  useRobotStore((state) => state.byId[robotId]);

/** ID 목록만 구독 — 로봇이 늘거나 줄 때만 리렌더된다. */
export const useRobotIds = (): string[] => useRobotStore(useShallow((state) => state.order));

/** 목록 전체가 필요한 화면(지도 등)용. */
export const useRobots = (): Robot[] =>
  useRobotStore(useShallow((state) => state.order.map((id) => state.byId[id]).filter(Boolean)));

export const useOfflineCount = (): number =>
  useRobotStore((state) => state.order.filter((id) => !state.byId[id]?.online).length);
