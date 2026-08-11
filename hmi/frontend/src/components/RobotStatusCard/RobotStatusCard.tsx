import { BatteryCharging, BatteryMedium } from "lucide-react";
import { ALLOWED, CMD_LABEL, STATE_META, TRACKS } from "../../constants/dashboard";
import { useDashboard } from "../../hooks/useDashboard";
import type { Robot } from "../../types";
import { ageSec, stepIndex } from "../../utils/robot";
import "./RobotStatusCard.css";

export function RobotStatusCard({ robot }: { robot: Robot }) {
  const { pending, now, sendCommand } = useDashboard();

  const meta = STATE_META[robot.state] ?? { ko: robot.state, tone: "idle" as const };
  const track = TRACKS[robot.mission_type];
  const current = stepIndex(robot);
  const offline = robot.state === "OFFLINE";
  const battTone = robot.battery > 50 ? "ok" : robot.battery > 25 ? "low" : "crit";
  const pendingCmd = pending[robot.id];

  const sub =
    robot.mission_type === "ANOMALY"
      ? `${robot.event_id ?? ""} · ${robot.target_zone ?? ""} ${robot.event_type ?? ""}`
      : `${robot.route ?? "Route A"} · ${robot.zone ?? ""}`;

  const allowedCmds = ALLOWED[robot.state] ?? [];

  return (
    <section
      className={`panel amrcard mission-${robot.mission_type} tone-${meta.tone}${offline ? " offline" : ""}`}
    >
      <div className="misbar">
        <span className="mtag">
          {track.icon} {track.label}
        </span>
        <span className="msub">{sub}</span>
        <span className="mspacer" />
        <span className="age">{offline ? "--" : `${ageSec(robot.ts, now)}s`}</span>
      </div>

      <div className="amr-top">
        <div className="name">{robot.id}</div>
        <div className="badges2">
          <span className={`pill ${meta.tone}${robot.state === "ALERTING" ? " blink" : ""}`}>
            {meta.ko}
          </span>
          <span className={`battpill ${battTone}${robot.charging ? " charging" : ""}`}>
            {robot.charging ? <BatteryCharging size={12} /> : <BatteryMedium size={12} />}
            {robot.battery}%
            {robot.charging && <span className="chargebolt" title="충전 중">⚡</span>}
          </span>
        </div>
      </div>

      <div className="amr-body">
        <div className="amr-thumb">{robot.mission_type === "ANOMALY" ? "🚨" : "🤖"}</div>
        <div className="amr-info">
          현재 작업 <b>{robot.task ?? meta.ko}</b>
        </div>
      </div>

      {robot.mission_type === "ANOMALY" && robot.patrol_resume && (
        <div className="resumehint">
          ↩ 순찰 <b>{robot.patrol_resume.step}/5 · {robot.patrol_resume.zone}</b> 에서 중단됨 · 대응 후 복귀 예정
        </div>
      )}

      <ul className="steps2">
        {track.steps.map((st, i) => {
          const n = i + 1;
          let cls = "";
          let icon = "";
          if (n < current) {
            cls = "done";
            icon = "✓";
          } else if (n === current) {
            cls = st.alert ? "alert" : meta.tone === "warn" ? "warn" : "active";
            icon = st.alert ? "🔊" : "";
          }
          return (
            <li key={st.t} className={cls}>
              <span className="n">{n}</span>
              <span className="t">{st.t}</span>
              {n === current && robot.step_note && <span className="sub">{robot.step_note}</span>}
              {icon && <span className="ic">{icon}</span>}
            </li>
          );
        })}
      </ul>

      <div className="zonestrip">
        {robot.zones?.length ? (
          robot.zones.map((z) => (
            <span key={z.id} className={`zchip ${z.state}`} title={z.state}>
              <i /> {z.id}
            </span>
          ))
        ) : (
          <span className="zchip">점검 이력 없음</span>
        )}
      </div>

      <div className="cmdrow">
        {allowedCmds.length ? (
          allowedCmds.map((cmd) => (
            <button
              key={cmd}
              type="button"
              className={`button ${cmd === "stop_and_dock" ? "button--stop" : "button--ghost"} button--xs`}
              disabled={Boolean(pendingCmd)}
              onClick={() => sendCommand(robot.id, cmd)}
            >
              {CMD_LABEL[cmd]}
            </button>
          ))
        ) : (
          <span className="pending">사용 가능한 명령 없음</span>
        )}
        {pendingCmd && <span className="pending">요청 중… ({CMD_LABEL[pendingCmd]})</span>}
      </div>
    </section>
  );
}
