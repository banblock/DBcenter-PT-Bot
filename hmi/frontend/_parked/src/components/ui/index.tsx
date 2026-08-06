/**
 * 공통 UI 컴포넌트 — Button / Badge / Modal / Toast / Card / Skeleton /
 * EmptyState / ErrorState / Gauge / StepList / PermissionGate.
 *
 * 원칙
 * * 이 컴포넌트들은 도메인을 모른다. `Robot`, `Event` 같은 타입을 import 하지 않는다.
 * * 접근성: 모달은 Esc 로 닫히고 포커스를 가둔다. 아이콘 전용 버튼은 aria-label 필수.
 * * 권한으로 감춘 UI 는 `PermissionGate` 하나로 처리한다 — 화면마다 조건문을
 *   흩뿌리면 정책이 바뀔 때 빠뜨리는 곳이 생긴다.
 */

import {
  useCallback,
  useEffect,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { AlertTriangle, Inbox, X } from "lucide-react";
import { useUiStore, type Toast, type ToastTone } from "../../store/uiStore";
import { can, type Permission } from "../../auth/permissions";
import "./ui.css";

// ══════════════════════════════════════════════════════════════════════════
// Button
// ══════════════════════════════════════════════════════════════════════════
export type ButtonVariant = "primary" | "danger" | "success" | "ghost" | "subtle";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  /** 요청 진행 중 — 자동으로 disabled 되고 스피너가 붙는다 */
  loading?: boolean;
  icon?: ReactNode;
}

export function Button({
  variant = "ghost",
  size = "md",
  block = false,
  loading = false,
  icon,
  disabled,
  children,
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={[
        "ui-button",
        `ui-button--${variant}`,
        `ui-button--${size}`,
        block ? "ui-button--block" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="ui-button__spinner" aria-hidden /> : icon}
      {children}
    </button>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Badge / StatusDot
// ══════════════════════════════════════════════════════════════════════════
export type BadgeTone = "neutral" | "info" | "ok" | "warn" | "danger" | "accent";

export interface BadgeProps {
  tone?: BadgeTone;
  outline?: boolean;
  /** 경보 상태 강조 점멸 */
  blink?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  title?: string;
}

export function Badge({ tone = "neutral", outline, blink, icon, children, title }: BadgeProps) {
  return (
    <span
      className={[
        "ui-badge",
        `ui-badge--${tone}`,
        outline ? "ui-badge--outline" : "",
        blink ? "ui-badge--blink" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      title={title}
    >
      {icon}
      {children}
    </span>
  );
}

export function StatusDot({
  tone = "off",
  pulse = false,
  label,
}: {
  tone?: "ok" | "warn" | "danger" | "off";
  pulse?: boolean;
  label?: string;
}) {
  return (
    <i
      className={["ui-dot", `ui-dot--${tone}`, pulse ? "ui-dot--pulse" : ""].filter(Boolean).join(" ")}
      role={label ? "img" : undefined}
      aria-label={label}
    />
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Card
// ══════════════════════════════════════════════════════════════════════════
export interface CardProps {
  title?: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  className?: string;
  children: ReactNode;
}

export function Card({ title, icon, hint, actions, flush, className = "", children }: CardProps) {
  return (
    <section className={`ui-card ${className}`}>
      {(title || actions) && (
        <header className="ui-card__header">
          {title && (
            <h2 className="ui-card__title">
              {icon}
              {title}
            </h2>
          )}
          {hint && <span className="ui-card__hint">{hint}</span>}
          {actions && <span style={{ marginLeft: "auto" }}>{actions}</span>}
        </header>
      )}
      <div className={`ui-card__body${flush ? " ui-card__body--flush" : ""}`}>{children}</div>
    </section>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Modal
// ══════════════════════════════════════════════════════════════════════════
export interface ModalProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  size?: "md" | "lg";
  /** CRITICAL 경보 — 최상위 레이어 + 강조 테두리 (F-51) */
  critical?: boolean;
  /** 스크림 클릭으로 닫히지 않게 (진압 확인 등 실수 방지) */
  disableScrimClose?: boolean;
  children: ReactNode;
}

export function Modal({
  open,
  title,
  onClose,
  footer,
  size = "md",
  critical = false,
  disableScrimClose = false,
  children,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<Element | null>(null);

  // Esc 로 닫기 (체크리스트 §3-12 키보드 단축키)
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      // 포커스를 모달 안에 가둔다 — 뒤쪽 버튼으로 탭이 새어나가면 안 된다
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    dialogRef.current?.querySelector<HTMLElement>("button, input, [href]")?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      (previouslyFocused.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      {critical && <div className="ui-critical-frame" aria-hidden />}
      <div
        className={`ui-modal__scrim${critical ? " ui-modal__scrim--critical" : ""}`}
        onMouseDown={(event) => {
          if (!disableScrimClose && event.target === event.currentTarget) onClose();
        }}
      >
        <div
          ref={dialogRef}
          className={[
            "ui-modal",
            size === "lg" ? "ui-modal--lg" : "",
            critical ? "ui-modal--critical" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          role="dialog"
          aria-modal="true"
          aria-label={typeof title === "string" ? title : undefined}
        >
          <header className="ui-modal__header">
            {critical && <AlertTriangle size={20} color="var(--danger)" aria-hidden />}
            <h3 className="ui-modal__title">{title}</h3>
            <button type="button" className="ui-modal__close" onClick={onClose} aria-label="닫기">
              <X size={18} />
            </button>
          </header>
          <div className="ui-modal__body">{children}</div>
          {footer && <footer className="ui-modal__footer">{footer}</footer>}
        </div>
      </div>
    </>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Toast
// ══════════════════════════════════════════════════════════════════════════
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  const dismiss = useCallback(() => onDismiss(toast.id), [onDismiss, toast.id]);

  useEffect(() => {
    if (toast.duration <= 0) return; // 0 = 수동 닫기 전용
    const timer = setTimeout(dismiss, toast.duration);
    return () => clearTimeout(timer);
  }, [toast.duration, dismiss]);

  return (
    <div
      className={`ui-toast ui-toast--${toast.tone}`}
      role={toast.tone === "danger" ? "alert" : "status"}
    >
      <div className="ui-toast__body">
        <div className="ui-toast__title">{toast.title}</div>
        {toast.description && <div className="ui-toast__description">{toast.description}</div>}
      </div>
      <button type="button" className="ui-toast__close" onClick={dismiss} aria-label="알림 닫기">
        <X size={14} />
      </button>
    </div>
  );
}

/** 앱 루트에 한 번만 둔다. */
export function ToastHost() {
  const toasts = useUiStore((state) => state.toasts);
  const dismissToast = useUiStore((state) => state.dismissToast);
  if (toasts.length === 0) return null;
  return (
    <div className="ui-toast-host" aria-live="polite">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={dismissToast} />
      ))}
    </div>
  );
}

export function useToast() {
  const pushToast = useUiStore((state) => state.pushToast);
  return useCallback(
    (tone: ToastTone, title: string, description?: string) =>
      pushToast({ tone, title, description }),
    [pushToast],
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Skeleton / EmptyState / ErrorState
// ══════════════════════════════════════════════════════════════════════════
export function Skeleton({
  width = "100%",
  height = 14,
  radius,
  count = 1,
}: {
  width?: string | number;
  height?: string | number;
  radius?: string;
  count?: number;
}) {
  return (
    <div style={{ display: "grid", gap: 8 }} aria-hidden data-testid="skeleton">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="ui-skeleton" style={{ width, height, borderRadius: radius }} />
      ))}
    </div>
  );
}

export function EmptyState({
  title = "표시할 항목이 없습니다",
  description,
  icon,
  action,
}: {
  title?: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="ui-state" role="status">
      <span className="ui-state__icon">{icon ?? <Inbox size={28} />}</span>
      <span className="ui-state__title">{title}</span>
      {description && <span className="ui-state__description">{description}</span>}
      {action && <span className="ui-state__action">{action}</span>}
    </div>
  );
}

export function ErrorState({
  title = "불러오지 못했습니다",
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="ui-state ui-state--error" role="alert">
      <span className="ui-state__icon">
        <AlertTriangle size={28} />
      </span>
      <span className="ui-state__title">{title}</span>
      {description && <span className="ui-state__description">{description}</span>}
      {onRetry && (
        <span className="ui-state__action">
          <Button size="sm" variant="ghost" onClick={onRetry}>
            다시 시도
          </Button>
        </span>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Gauge
// ══════════════════════════════════════════════════════════════════════════
export interface GaugeProps {
  value: number;
  max?: number;
  /** 이 값 아래면 warn, 그 절반 아래면 danger */
  warnBelow?: number;
  label?: string;
  showLabel?: boolean;
}

export function Gauge({ value, max = 100, warnBelow = 50, label, showLabel = true }: GaugeProps) {
  const ratio = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  const percent = ratio * 100;
  const tone = percent > warnBelow ? "ok" : percent > warnBelow / 2 ? "warn" : "danger";
  return (
    <div className="ui-gauge">
      <div
        className="ui-gauge__track"
        role="meter"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-label={label ?? "게이지"}
      >
        <div className={`ui-gauge__fill ui-gauge__fill--${tone}`} style={{ width: `${percent}%` }} />
      </div>
      {showLabel && <span className="ui-gauge__label">{label ?? `${Math.round(percent)}%`}</span>}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// StepList (F-14)
// ══════════════════════════════════════════════════════════════════════════
export interface Step {
  label: string;
  /** 이 스텝이 경보 단계인가 (강조 색 + 아이콘) */
  alert?: boolean;
  note?: string;
}

export function StepList({
  steps,
  current,
  tone = "active",
}: {
  steps: Step[];
  /** 1-based. 0 이면 아무 것도 진행 중이 아님 */
  current: number;
  tone?: "active" | "warn";
}) {
  return (
    <ol className="ui-steps">
      {steps.map((step, index) => {
        const n = index + 1;
        const done = n < current;
        const isCurrent = n === current;
        const modifier = done
          ? "done"
          : isCurrent
            ? step.alert
              ? "alert"
              : tone
            : "";
        return (
          <li
            key={step.label}
            className={`ui-steps__item${modifier ? ` ui-steps__item--${modifier}` : ""}`}
            aria-current={isCurrent || undefined}
          >
            <span className="ui-steps__n">{n}</span>
            <span>{step.label}</span>
            {isCurrent && step.note && (
              <span style={{ color: "var(--ink-faint)", fontSize: 12 }}>· {step.note}</span>
            )}
            {done && <span className="ui-steps__mark">✓</span>}
            {isCurrent && step.alert && <span className="ui-steps__mark">🔊</span>}
          </li>
        );
      })}
    </ol>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// PermissionGate
// ══════════════════════════════════════════════════════════════════════════
export interface PermissionGateProps {
  permission: Permission;
  /** 권한이 없을 때: 감춤(hide) vs 비활성(disable). 기본은 감춤. */
  mode?: "hide" | "disable";
  /** mode="hide" 일 때 대신 보여줄 것 */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * 권한 기반 노출 제어 (체크리스트 §3-12 "권한별 버튼 비활성").
 *
 * `disable` 모드는 버튼을 남기되 못 누르게 한다 — "이 기능이 있긴 하다"를
 * 보여줘야 하는 경우에 쓴다. 존재 자체를 알 필요가 없으면 `hide`.
 */
export function PermissionGate({
  permission,
  mode = "hide",
  fallback = null,
  children,
}: PermissionGateProps) {
  const role = useUiStore((state) => state.role);
  const allowed = can(role, permission);

  if (allowed) return <>{children}</>;
  if (mode === "hide") return <>{fallback}</>;

  return (
    <span
      className="ui-permission-disabled"
      title={`이 작업에는 ${permission} 권한이 필요합니다`}
      style={{ opacity: 0.45, pointerEvents: "none", display: "contents" }}
      aria-disabled="true"
      data-testid="permission-disabled"
    >
      {children}
    </span>
  );
}

/** 조건부 렌더가 필요한 곳에서 쓰는 훅 */
export function usePermission(permission: Permission): boolean {
  const role = useUiStore((state) => state.role);
  return can(role, permission);
}
