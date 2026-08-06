/**
 * 활동 로그 / 타임라인 store — 우측 하단 로그 패널(F-44~F-46).
 *
 * 롤링 버퍼다. 관제 화면은 며칠씩 켜져 있으므로 무한히 쌓으면 메모리가 샌다.
 * 체크리스트 이미지의 "최신 상단 · 최대 40건 롤링"을 기준으로 두되,
 * 필터링 여지를 주려고 조금 여유 있게 잡았다.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

const MAX_LOGS = 200;

export type LogLevel = "INFO" | "WARN" | "CRITICAL";

export interface LogEntry {
  id: string;
  at: number;
  level: LogLevel;
  /** 출처 태그 — PC1 / PC2 / Fleet / 로봇 ID 등 */
  actor: string;
  message: string;
  refId?: string;
}

export interface LogFilter {
  level: LogLevel | "ALL";
  actor: string | "ALL";
  keyword: string;
}

interface LogStoreState {
  entries: LogEntry[];
  filter: LogFilter;

  append: (entry: Omit<LogEntry, "id" | "at"> & { at?: number }) => void;
  setFilter: (patch: Partial<LogFilter>) => void;
  clear: () => void;
}

let seq = 0;

export const useLogStore = create<LogStoreState>((set) => ({
  entries: [],
  filter: { level: "ALL", actor: "ALL", keyword: "" },

  append: ({ level, actor, message, refId, at }) => {
    seq += 1;
    const entry: LogEntry = {
      id: `log-${Date.now()}-${seq}`,
      at: at ?? Date.now(),
      level,
      actor,
      message,
      refId,
    };
    set((state) => ({ entries: [entry, ...state.entries].slice(0, MAX_LOGS) }));
  },

  setFilter: (patch) => set((state) => ({ filter: { ...state.filter, ...patch } })),
  clear: () => set({ entries: [] }),
}));

/** 필터가 적용된 목록. 필터 조건이 바뀌거나 로그가 들어올 때만 재계산된다. */
export const useFilteredLogs = (): LogEntry[] =>
  useLogStore(
    useShallow((state) => {
      const { level, actor, keyword } = state.filter;
      const needle = keyword.trim().toLowerCase();
      return state.entries.filter((entry) => {
        if (level !== "ALL" && entry.level !== level) return false;
        if (actor !== "ALL" && entry.actor !== actor) return false;
        if (needle && !entry.message.toLowerCase().includes(needle)) return false;
        return true;
      });
    }),
  );

/** 필터 드롭다운 채우기용 — 실제 등장한 actor 만 보여준다. */
export const useLogActors = (): string[] =>
  useLogStore(useShallow((state) => [...new Set(state.entries.map((e) => e.actor))].sort()));

export const logActions = {
  info: (actor: string, message: string, refId?: string) =>
    useLogStore.getState().append({ level: "INFO", actor, message, refId }),
  warn: (actor: string, message: string, refId?: string) =>
    useLogStore.getState().append({ level: "WARN", actor, message, refId }),
  critical: (actor: string, message: string, refId?: string) =>
    useLogStore.getState().append({ level: "CRITICAL", actor, message, refId }),
};
