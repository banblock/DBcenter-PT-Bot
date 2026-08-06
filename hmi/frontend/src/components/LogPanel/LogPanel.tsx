import { ScrollText } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import { fmt } from "../../utils/robot";
import "./LogPanel.css";

function tagClass(tag: string): string {
  if (tag === "PC1") return "pc1";
  if (tag === "PC2") return "pc2";
  if (tag === "Fleet") return "fleet";
  return "other";
}

export function LogPanel() {
  const { logs } = useDashboard();

  return (
    <section className="panel log-panel">
      <header className="panel-header">
        <h2>
          <ScrollText size={16} /> 활동 로그 <span className="panel-header__hint">/ 타임라인</span>
        </h2>
      </header>
      <div className="actlog">
        {logs.map((log) => {
          const cls = tagClass(log.tag);
          return (
            <div key={log.id} className="ali">
              <span className={`adot ${cls}`} />
              <span className="atm">{fmt(log.ts).slice(0, 5)}</span>
              <span className={`atag ${cls}`}>[{log.tag}]</span>
              <span className="amsg">{log.hot ? <b>{log.msg}</b> : log.msg}</span>
            </div>
          );
        })}
      </div>
      <div className="actlegend">
        <span>
          <i className="sq" style={{ background: "var(--blue)" }} /> PC1(MSI)
        </span>
        <span>
          <i className="sq" style={{ background: "var(--pink)" }} /> PC2(VIC)
        </span>
        <span>
          <i className="sq" style={{ background: "var(--purple)" }} /> Fleet
        </span>
      </div>
    </section>
  );
}
