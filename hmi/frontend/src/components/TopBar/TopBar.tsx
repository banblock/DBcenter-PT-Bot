import { OctagonX, Play, PlugZap, RotateCw, ShieldCheck } from "lucide-react";
import { WP_PER_ZONE, ZONES } from "../../constants/dashboard";
import { useDashboard } from "../../hooks/useDashboard";
import "./TopBar.css";

export function TopBar() {
  const { linkMode, linkText, waypointTotal, patrolStarted, estopped, startPatrol, dockAll, estopAll, resumeAll } =
    useDashboard();
  const need = ZONES.length * WP_PER_ZONE;
  const startDisabled = waypointTotal < need || patrolStarted;

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <span className="topbar__logo">
          <ShieldCheck size={19} />
        </span>
        <div>
          <h1>설비 안전 AMR 순찰·이상감지 관제</h1>
          <p>PC1(MSI) 명령 라우팅 · PC2(VIC) 비전 추론(YOLO) · Fleet 코디네이터 · 공통 데이터 계층 연동</p>
        </div>
      </div>

      <div className="topbar__actions">
        <span className={`link link--${linkMode}`}>
          <i />
          {linkText}
        </span>
        <button type="button" className="button button--start" onClick={startPatrol} disabled={startDisabled}>
          <Play size={15} fill="currentColor" /> 통합 순찰 시작 ({waypointTotal}/{need})
        </button>
        {estopped && (
          <button type="button" className="button button--ghost" onClick={resumeAll}>
            <RotateCw size={15} /> 전체 작업 재개
          </button>
        )}
        <button type="button" className="button button--ghost" onClick={dockAll}>
          <PlugZap size={15} /> 도킹 스테이션 복귀
        </button>
        <button type="button" className="button button--stop" onClick={estopAll}>
          <OctagonX size={16} /> 긴급정지
        </button>
      </div>
    </header>
  );
}
