import { Droplets, Flame, Zap } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import "./StatsPanel.css";

export function StatsPanel() {
  const { stats, breakerMismatch } = useDashboard();

  return (
    <section className="panel stats-panel">
      <header className="panel-header">
        <h2>📊 이상 감지 현황</h2>
      </header>
      <div className="stat3">
        <article className="sc red">
          <Flame size={15} />
          <div className="k">화재/연기 감지</div>
          <div className="v">{stats.fire}</div>
          <div className="d">오늘 누적</div>
        </article>
        <article className="sc neutral">
          <Droplets size={15} />
          <div className="k">냉각수 누수</div>
          <div className="v">{stats.leak}</div>
          <div className="d">오늘 누적</div>
        </article>
        <article className="sc amber">
          <Zap size={15} />
          <div className="k">차단기 불일치</div>
          <div className="v">{breakerMismatch}</div>
          <div className="d">현재 미해결</div>
        </article>
      </div>
    </section>
  );
}
