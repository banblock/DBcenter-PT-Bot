import { Map, Navigation, X } from "lucide-react";
import { useRef, useState, type MouseEvent } from "react";
import { useDashboard } from "../../hooks/useDashboard";
import "./MapPanel.css";

const VB_W = 900;
const VB_H = 460;

interface TargetPoint {
  x: number;
  y: number;
}

export function MapPanel() {
  const { robots, isZone2Hot, sendGoto } = useDashboard();
  const svgRef = useRef<SVGSVGElement>(null);
  const [target, setTarget] = useState<TargetPoint | null>(null);
  const [targetRobotId, setTargetRobotId] = useState<string>("");

  const targetRobot = robots.find((r) => r.id === targetRobotId) ?? robots[0];

  function handleMapClick(event: MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const x = Math.round(((event.clientX - rect.left) / rect.width) * VB_W);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * VB_H);
    setTarget({ x, y });
    setTargetRobotId((prev) => (robots.some((r) => r.id === prev) ? prev : (robots[0]?.id ?? "")));
  }

  function handleSend() {
    if (!target || !targetRobot) return;
    sendGoto(targetRobot.id, target.x, target.y);
    setTarget(null);
  }

  return (
    <section className="panel map-panel">
      <header className="panel-header">
        <h2>
          <Map size={16} /> 시설 맵 / 실시간 모니터링
        </h2>
        <span className="panel-header__hint">지도를 클릭해 이동 목표 지정</span>
      </header>

      <div className="map-panel__legend">
        <span>
          <i className="sw" /> 정상 경로
        </span>
        <span>
          <i className="sw sw--dash" /> 예정 경로
        </span>
        <span>
          <i className="sw sw--area sw--danger" /> 이상 구역
        </span>
        <span>
          <i className="sw sw--area sw--dock" /> 도킹 스테이션
        </span>
      </div>

      <div className="map-panel__canvas">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          role="img"
          aria-label="AMR 시설 맵 — 클릭하여 이동 목표 지정"
          onClick={handleMapClick}
          className="map-panel__svg"
        >
          <rect x="0" y="0" width="900" height="460" fill="#eef0f4" />
          <rect x="20" y="20" width="860" height="420" rx="10" fill="#ffffff" stroke="#dfe3ea" />

          <rect x="70" y="70" width="250" height="150" rx="14" fill="#e7f6ee" stroke="#1faa6b" strokeWidth="1.5" />
          <text x="195" y="150" textAnchor="middle" fill="#1a8f5c" fontSize="15" fontFamily="sans-serif" fontWeight="700">존-1</text>

          <rect x="520" y="60" width="290" height="110" rx="14" fill="#f3f4f7" stroke="#c9cfda" strokeWidth="1.5" />
          <text x="665" y="118" textAnchor="middle" fill="#7d8697" fontSize="15" fontFamily="sans-serif" fontWeight="700">존-3</text>

          <rect
            x="470" y="210" width="240" height="170" rx="14"
            fill={isZone2Hot ? "#fdecec" : "#f3f4f7"}
            stroke={isZone2Hot ? "#e5484d" : "#c9cfda"}
            strokeWidth="1.5"
          />
          <text x="590" y="240" textAnchor="middle" fill={isZone2Hot ? "#c8393e" : "#7d8697"} fontSize="15" fontFamily="sans-serif" fontWeight="700">존-2</text>
          <text x="590" y="272" textAnchor="middle" fontSize="20" opacity={isZone2Hot ? 1 : 0}>⚠</text>

          <rect x="370" y="26" width="64" height="34" rx="7" fill="#efeaff" stroke="#7b5cff" />
          <text x="402" y="48" textAnchor="middle" fill="#5b3fe0" fontSize="12" fontFamily="sans-serif" fontWeight="700">D1</text>
          <rect x="370" y="400" width="64" height="34" rx="7" fill="#efeaff" stroke="#7b5cff" />
          <text x="402" y="422" textAnchor="middle" fill="#5b3fe0" fontSize="12" fontFamily="sans-serif" fontWeight="700">D2</text>

          <polyline points="402,60 402,120 320,120 250,145" fill="none" stroke="#1faa6b" strokeWidth="3" />
          <polyline points="250,145 150,170 150,200" fill="none" stroke="#a9b0be" strokeWidth="2.5" strokeDasharray="6 6" />
          <polyline points="402,400 402,340 470,320" fill="none" stroke="#1faa6b" strokeWidth="3" />
          <polyline
            points="470,320 520,300 560,320 600,300"
            fill="none" stroke="#e08a00" strokeWidth="2.5" strokeDasharray="6 6"
            opacity={isZone2Hot ? 1 : 0}
          />

          {robots.map((r) => {
            const pose = r.pose ?? { x: 450, y: 230 };
            const danger = r.mission_type === "ANOMALY";
            return (
              <g key={r.id} transform={`translate(${pose.x},${pose.y})`}>
                <circle r="12" fill={danger ? "#e5484d" : "#2f6bff"} stroke="#fff" strokeWidth="2.5" />
                <rect x="-36" y="16" width="72" height="18" rx="9" fill="#fff" stroke={danger ? "#f6bcbd" : "#c6d5ff"} />
                <text x="0" y="29" textAnchor="middle" fontSize="10.5" fontFamily="sans-serif" fontWeight="700" fill={danger ? "#e5484d" : "#2f6bff"}>
                  {r.id}
                </text>
              </g>
            );
          })}

          {target && targetRobot?.pose && (
            <line
              x1={targetRobot.pose.x} y1={targetRobot.pose.y}
              x2={target.x} y2={target.y}
              stroke="#7b5cff" strokeWidth="2" strokeDasharray="5 5"
            />
          )}
          {target && (
            <g transform={`translate(${target.x},${target.y})`} className="map-panel__target">
              <circle r="14" className="map-panel__target-ring" />
              <circle r="5" fill="#7b5cff" stroke="#fff" strokeWidth="2" />
            </g>
          )}
        </svg>

        <div className="map-panel__controls">
          <button type="button" title="확대">+</button>
          <button type="button" title="축소">−</button>
          <button type="button" title="현재 위치">◎</button>
          <button type="button" title="레이어">▤</button>
        </div>

        {target && (
          <div
            className="map-panel__goto"
            style={{ left: `${(target.x / VB_W) * 100}%`, top: `${(target.y / VB_H) * 100}%` }}
          >
            <div className="map-panel__goto-head">
              <span>
                <Navigation size={12} /> 이동 목표 좌표
              </span>
              <button type="button" onClick={() => setTarget(null)} aria-label="닫기">
                <X size={13} />
              </button>
            </div>
            <div className="map-panel__goto-coord">
              x: {target.x} · y: {target.y}
            </div>
            <label className="map-panel__goto-select">
              로봇
              <select value={targetRobot?.id ?? ""} onChange={(e) => setTargetRobotId(e.target.value)}>
                {robots.map((r) => (
                  <option key={r.id} value={r.id}>{r.id}</option>
                ))}
              </select>
            </label>
            <button type="button" className="button button--start button--xs map-panel__goto-send" onClick={handleSend} disabled={!targetRobot}>
              전송
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
