/** 미션(작업 큐) store — 우측 중단 작업 큐 패널(F-39~F-43)이 쓴다. */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { MissionStatusPayload, MissionStatusValue } from "../services/ws/messages";

export type Mission = MissionStatusPayload & { updated_at: number };

/** 진행 중으로 보는 상태 — 완료 목록과 나누는 기준 */
export const ACTIVE_STATUSES: ReadonlySet<MissionStatusValue> = new Set([
  "PENDING",
  "RUNNING",
  "PREEMPTED",
]);

const MAX_MISSIONS = 100;

interface MissionStoreState {
  byId: Record<string, Mission>;
  order: string[];

  upsert: (payload: MissionStatusPayload) => void;
  upsertMany: (payloads: MissionStatusPayload[]) => void;
  reset: () => void;
}

export const useMissionStore = create<MissionStoreState>((set) => ({
  byId: {},
  order: [],

  upsert: (payload) =>
    set((state) => {
      const byId = {
        ...state.byId,
        [payload.mission_id]: {
          ...state.byId[payload.mission_id],
          ...payload,
          updated_at: Date.now(),
        },
      };
      const order = state.order.includes(payload.mission_id)
        ? state.order
        : [payload.mission_id, ...state.order].slice(0, MAX_MISSIONS);
      return { byId, order };
    }),

  upsertMany: (payloads) =>
    set(() => {
      const byId: Record<string, Mission> = {};
      const order: string[] = [];
      for (const payload of payloads.slice(0, MAX_MISSIONS)) {
        byId[payload.mission_id] = { ...payload, updated_at: Date.now() };
        order.push(payload.mission_id);
      }
      return { byId, order };
    }),

  reset: () => set({ byId: {}, order: [] }),
}));

export const useMissions = (): Mission[] =>
  useMissionStore(useShallow((state) => state.order.map((id) => state.byId[id]).filter(Boolean)));

export const useActiveMissions = (): Mission[] =>
  useMissionStore(
    useShallow((state) =>
      state.order
        .map((id) => state.byId[id])
        .filter((m): m is Mission => Boolean(m) && ACTIVE_STATUSES.has(m.status)),
    ),
  );

export const useMissionForRobot = (robotId: string): Mission | undefined =>
  useMissionStore((state) =>
    state.order
      .map((id) => state.byId[id])
      .find((m) => m?.robot_id === robotId && ACTIVE_STATUSES.has(m.status)),
  );
