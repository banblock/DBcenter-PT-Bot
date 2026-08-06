/**
 * UI 상태 — 테마 · 역할 · 토스트 · 모달 · 알림 설정.
 *
 * 서버 데이터가 아닌 '이 브라우저의 상태'만 담는다. 로봇/이벤트처럼 서버가
 * 주는 것은 각자 store 로 간다.
 */

import { create } from "zustand";
import { isRole, type Permission, type Role, can } from "../auth/permissions";

export type Theme = "light" | "dark";

/** 체크리스트 §3-12 는 다크를 요구한다. 기존 화면을 유지하려고 기본은 light. */
export const DEFAULT_THEME: Theme = "light";
export const DEFAULT_ROLE: Role = "OPERATOR";

const STORAGE_KEYS = {
  theme: "amr.theme",
  role: "amr.role",
  operator: "amr.operator",
  muted: "amr.alertMuted",
} as const;

export type ToastTone = "info" | "success" | "warn" | "danger";

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  description?: string;
  /** ms. 0 이면 수동으로 닫을 때까지 유지. */
  duration: number;
}

export interface CriticalAlert {
  eventId: string;
  title: string;
  description?: string;
  zoneId?: string | null;
  thumbnailUrl?: string | null;
  confidence?: number;
  at: number;
}

interface UiState {
  theme: Theme;
  role: Role;
  operator: string;
  /** CRITICAL 알림 소리 음소거 (F-56) */
  alertMuted: boolean;

  toasts: Toast[];
  /** CRITICAL 전체 모달 (F-51). 한 번에 하나만 띄운다. */
  criticalAlert: CriticalAlert | null;
  /** 사용자가 닫은 이벤트 — 같은 건으로 다시 모달을 띄우지 않는다. */
  dismissedAlertIds: string[];

  selectedRobotId: string | null;
  focusedEventId: string | null;

  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  setRole: (role: Role) => void;
  setOperator: (operator: string) => void;
  setAlertMuted: (muted: boolean) => void;

  pushToast: (toast: Omit<Toast, "id" | "duration"> & { duration?: number }) => string;
  dismissToast: (id: string) => void;
  clearToasts: () => void;

  showCriticalAlert: (alert: CriticalAlert) => void;
  dismissCriticalAlert: () => void;

  selectRobot: (robotId: string | null) => void;
  focusEvent: (eventId: string | null) => void;

  can: (permission: Permission) => boolean;
}

function readStorage<T>(key: string, fallback: T, parse: (raw: string) => T | null): T {
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return parse(raw) ?? fallback;
  } catch {
    return fallback; // 사파리 프라이빗 모드 등에서 localStorage 접근이 막힐 수 있다
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 저장 실패는 무시 — 기능 자체는 계속 동작해야 한다 */
  }
}

/** <html data-theme="..."> 를 갱신. CSS 토큰이 이 속성으로 테마를 고른다. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
}

let toastSeq = 0;

export const useUiStore = create<UiState>((set, get) => ({
  theme: readStorage<Theme>(STORAGE_KEYS.theme, DEFAULT_THEME, (raw) =>
    raw === "light" || raw === "dark" ? raw : null,
  ),
  role: readStorage<Role>(STORAGE_KEYS.role, DEFAULT_ROLE, (raw) => (isRole(raw) ? raw : null)),
  operator: readStorage(STORAGE_KEYS.operator, "operator", (raw) => raw || null),
  alertMuted: readStorage(STORAGE_KEYS.muted, false, (raw) => raw === "true"),

  toasts: [],
  criticalAlert: null,
  dismissedAlertIds: [],
  selectedRobotId: null,
  focusedEventId: null,

  setTheme: (theme) => {
    writeStorage(STORAGE_KEYS.theme, theme);
    applyTheme(theme);
    set({ theme });
  },

  toggleTheme: () => get().setTheme(get().theme === "light" ? "dark" : "light"),

  setRole: (role) => {
    writeStorage(STORAGE_KEYS.role, role);
    set({ role });
  },

  setOperator: (operator) => {
    writeStorage(STORAGE_KEYS.operator, operator);
    set({ operator });
  },

  setAlertMuted: (muted) => {
    writeStorage(STORAGE_KEYS.muted, String(muted));
    set({ alertMuted: muted });
  },

  pushToast: ({ tone, title, description, duration }) => {
    toastSeq += 1;
    const id = `toast-${Date.now()}-${toastSeq}`;
    // 위험 알림은 오래 띄운다. 정보성은 짧게.
    const resolved = duration ?? (tone === "danger" ? 8000 : 4000);
    set((state) => ({
      // 화면을 토스트로 뒤덮지 않도록 최근 5개만 유지
      toasts: [...state.toasts, { id, tone, title, description, duration: resolved }].slice(-5),
    }));
    return id;
  },

  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  clearToasts: () => set({ toasts: [] }),

  showCriticalAlert: (alert) => {
    // 이미 닫은 이벤트는 다시 띄우지 않는다 (같은 사건의 후속 프레임 방지)
    if (get().dismissedAlertIds.includes(alert.eventId)) return;
    set({ criticalAlert: alert });
  },

  dismissCriticalAlert: () =>
    set((state) => ({
      criticalAlert: null,
      dismissedAlertIds: state.criticalAlert
        ? [...state.dismissedAlertIds, state.criticalAlert.eventId].slice(-50)
        : state.dismissedAlertIds,
    })),

  selectRobot: (robotId) => set({ selectedRobotId: robotId }),
  focusEvent: (eventId) => set({ focusedEventId: eventId }),

  can: (permission) => can(get().role, permission),
}));

/** 훅 밖(서비스 계층)에서 쓰는 헬퍼 */
export const uiActions = {
  toast: (tone: ToastTone, title: string, description?: string) =>
    useUiStore.getState().pushToast({ tone, title, description }),
  identity: () => {
    const { role, operator } = useUiStore.getState();
    return { role, operator };
  },
};
