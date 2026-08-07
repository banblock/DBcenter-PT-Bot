import { ListTodo } from "lucide-react";
import { useState } from "react";
import { EVENT_SEVERITY_LABEL, EVENT_STATE_LABEL, EVENT_STATE_TONE } from "../../constants/dashboard";
import { useDashboard } from "../../hooks/useDashboard";
import { fmt } from "../../utils/robot";
import "./QueuePanel.css";

const SEVERITY_CLASS: Record<string, string> = { DANGER: "danger", WARN: "warn", INFO: "info" };

export function QueuePanel() {
  const { events } = useDashboard();
  const [expanded, setExpanded] = useState(false);
  const total = events.length;
  const shown = expanded ? events : events.slice(0, 6); // 접힘: 최신 6건 / 펼침: 과거 내역 포함 전체

  return (
    <section className="panel queue-panel">
      <header className="panel-header">
        <h2>
          <ListTodo size={16} /> 이벤트 큐{" "}
          <span className="panel-header__hint">{total ? `(총 ${total}건)` : "(최신 순)"}</span>
        </h2>
        {total > 6 && (
          <button type="button" className="queue-more" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "접기 ‹" : `과거 내역 (${total}) ›`}
          </button>
        )}
      </header>
      {total ? (
        <ul className={`queue-list${expanded ? " expanded" : ""}`}>
          {shown.map((e) => (
            <li key={e.id} className="queue-list__item">
              <span className={`sev ${SEVERITY_CLASS[e.severity] ?? "info"}`}>
                {EVENT_SEVERITY_LABEL[e.severity] ?? "정보"}
              </span>
              <p>{e.text}</p>
              <span className="etm">{fmt(e.ts)}</span>
              <span className={`estat ${EVENT_STATE_TONE[e.state] ?? "run"}`}>
                {EVENT_STATE_LABEL[e.state] ?? e.state}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="queue-list__empty">진행 중인 이벤트 없음</div>
      )}
    </section>
  );
}
