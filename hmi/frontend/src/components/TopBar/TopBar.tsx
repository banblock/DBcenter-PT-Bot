import { OctagonX, Play, PlugZap, ShieldCheck } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import "./TopBar.css";

export function TopBar() {
  const { linkMode, linkText, startAll, dockAll, estopAll } = useDashboard();

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
        <button type="button" className="button button--start" onClick={startAll}>
          <Play size={15} fill="currentColor" /> 통합 순찰 시작
        </button>
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
