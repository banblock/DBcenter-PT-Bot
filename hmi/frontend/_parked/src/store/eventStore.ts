/**
 * 이상 이벤트 store.
 *
 * 실시간 스트림(WS)과 이력 조회(REST)는 성격이 다르다.
 * * `byId`/`order` — 실시간 상황판. 최근 MAX_LIVE 건만 메모리에 둔다.
 * * 이력 화면은 REST 페이지네이션을 쓰고 이 store 를 거치지 않는다.
 *   (수만 건을 클라이언트 메모리에 들고 있을 이유가 없다.)
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { EventPayload, EventStatusValue, SeverityValue } from "../services/ws/messages";

/** 실시간으로 들고 있을 최대 건수. 넘치면 오래된 것부터 버린다. */
const MAX_LIVE = 200;

/** 여기 있는 상태는 '끝난 이벤트' — 미확인 카운트·경보에서 제외된다. */
export const CLOSED_STATUSES: ReadonlySet<EventStatusValue> = new Set([
  "RESOLVED",
  "FALSE_POSITIVE",
  "MERGED",
]);

export type AppEvent = EventPayload & { received_at: number };

interface EventStoreState {
  byId: Record<string, AppEvent>;
  /** 최신순 event_id 배열 */
  order: string[];

  upsert: (payload: EventPayload) => { isNew: boolean; event: AppEvent };
  upsertMany: (payloads: EventPayload[]) => void;
  remove: (eventId: string) => void;
  reset: () => void;
}

export const useEventStore = create<EventStoreState>((set, get) => ({
  byId: {},
  order: [],

  upsert: (payload) => {
    const existing = get().byId[payload.event_id];
    const event: AppEvent = {
      ...existing,
      ...payload,
      received_at: existing?.received_at ?? Date.now(),
    };
    set((state) => {
      const order = state.order.includes(payload.event_id)
        ? state.order
        : [payload.event_id, ...state.order].slice(0, MAX_LIVE);
      const byId = { ...state.byId, [payload.event_id]: event };
      // order 에서 밀려난 항목은 byId 에서도 지운다 (메모리 누수 방지)
      if (state.order.length >= MAX_LIVE) {
        const keep = new Set(order);
        for (const id of Object.keys(byId)) {
          if (!keep.has(id)) delete byId[id];
        }
      }
      return { byId, order };
    });
    return { isNew: existing === undefined, event };
  },

  upsertMany: (payloads) =>
    set(() => {
      const sorted = [...payloads].sort((a, b) =>
        (b.detected_at ?? "").localeCompare(a.detected_at ?? ""),
      );
      const byId: Record<string, AppEvent> = {};
      const order: string[] = [];
      for (const payload of sorted.slice(0, MAX_LIVE)) {
        byId[payload.event_id] = { ...payload, received_at: Date.now() };
        order.push(payload.event_id);
      }
      return { byId, order };
    }),

  remove: (eventId) =>
    set((state) => {
      const byId = { ...state.byId };
      delete byId[eventId];
      return { byId, order: state.order.filter((id) => id !== eventId) };
    }),

  reset: () => set({ byId: {}, order: [] }),
}));

// ── 선택자 ────────────────────────────────────────────────────────────────
export const useEvent = (eventId: string | null): AppEvent | undefined =>
  useEventStore((state) => (eventId ? state.byId[eventId] : undefined));

export const useEvents = (): AppEvent[] =>
  useEventStore(useShallow((state) => state.order.map((id) => state.byId[id]).filter(Boolean)));

export const useOpenEvents = (): AppEvent[] =>
  useEventStore(
    useShallow((state) =>
      state.order
        .map((id) => state.byId[id])
        .filter((e): e is AppEvent => Boolean(e) && !CLOSED_STATUSES.has(e.status)),
    ),
  );

/** 헤더 경보바에 상시 노출할 최상위 위험 1건 */
export const useTopAlert = (): AppEvent | undefined =>
  useEventStore((state) => {
    for (const id of state.order) {
      const event = state.byId[id];
      if (event && event.severity === "CRITICAL" && !CLOSED_STATUSES.has(event.status)) {
        return event;
      }
    }
    return undefined;
  });

/** 미확인(ACK 안 한) 건수 뱃지 (F-55) */
export const useUnacknowledgedCount = (): number =>
  useEventStore(
    (state) =>
      state.order.filter((id) => {
        const event = state.byId[id];
        return event && !event.acknowledged_at && !CLOSED_STATUSES.has(event.status);
      }).length,
  );

export function severityTone(severity: SeverityValue): "danger" | "warn" | "info" {
  return severity === "CRITICAL" ? "danger" : severity === "WARN" ? "warn" : "info";
}
