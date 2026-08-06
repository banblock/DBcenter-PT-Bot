import { TriangleAlert, X } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import { fmt } from "../../utils/robot";
import "./AlertBanner.css";

export function AlertBanner() {
  const { topAlert, dismissedAlertId, dismissAlert } = useDashboard();
  if (!topAlert || topAlert.id === dismissedAlertId) return null;

  return (
    <section className="alert-banner">
      <span className="alert-banner__icon">
        <TriangleAlert size={16} />
      </span>
      <div className="alert-banner__body">
        <strong>긴급 알림</strong> · {topAlert.text} · {fmt(topAlert.ts)} · 담당{" "}
        <strong>{topAlert.assignee ?? "배정 중"}</strong>
      </div>
      <button
        type="button"
        className="alert-banner__close"
        onClick={() => dismissAlert(topAlert.id)}
        aria-label="닫기"
        title="닫기"
      >
        <X size={14} />
      </button>
    </section>
  );
}
