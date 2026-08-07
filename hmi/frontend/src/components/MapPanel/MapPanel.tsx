import { Map, RotateCcw, Trash2 } from "lucide-react";
import { useRef, useState, type MouseEvent } from "react";
import { WP_PER_ZONE, ZONES, ZONE_AMR } from "../../constants/dashboard";
import { useDashboard } from "../../hooks/useDashboard";
import "./MapPanel.css";

const VB_W = 900;
const VB_H = 460;
const ZONE_COLOR: Record<string, string> = { "존-1": "#2f6bff", "존-2": "#d6409f" };
// 각 존 사각형 경계(viewBox 900×460) — waypoint는 해당 존 영역 안에만 지정 가능
const ZONE_BOUNDS: Record<string, { x: number; y: number; w: number; h: number }> = {
  "존-1": { x: 70, y: 70, w: 290, h: 150 },
  "존-2": { x: 500, y: 210, w: 290, h: 170 },
};

export function MapPanel() {
  const {
    robots,
    isZone2Hot,
    waypoints,
    waypointTotal,
    patrolStarted,
    addWaypoint,
    undoWaypoint,
    clearWaypoints,
  } = useDashboard();
  const svgRef = useRef<SVGSVGElement>(null);
  const [zone, setZone] = useState<string>(ZONES[0]);
  const [notice, setNotice] = useState("");
  const need = ZONES.length * WP_PER_ZONE;

  function flash(msg: string) {
    setNotice(msg);
    window.setTimeout(() => setNotice(""), 1600);
  }

  function handleMapClick(event: MouseEvent<SVGSVGElement>) {
    if (patrolStarted) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const x = Math.round(((event.clientX - rect.left) / rect.width) * VB_W);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * VB_H);
    // 클릭 지점이 속한 존 — 각 존 영역 안에만 지정 가능
    const z = ZONES.find((zz) => {
      const b = ZONE_BOUNDS[zz];
      return x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h;
    });
    if (!z) {
      flash("waypoint는 존-1 / 존-2 영역 안에만 지정할 수 있습니다");
      return;
    }
    if ((waypoints[z]?.length ?? 0) >= WP_PER_ZONE) {
      flash(`${z}은 이미 waypoint ${WP_PER_ZONE}개가 지정되었습니다`);
      return;
    }
    setZone(z); // 클릭한 존으로 자동 전환·귀속
    addWaypoint(z, x, y);
  }

  const hint = notice
    ? notice
    : patrolStarted
      ? "순찰 진행 중 — waypoint 잠금"
      : waypointTotal >= need
        ? "지정 완료! ▶ 통합 순찰 시작을 누르세요"
        : `${zone}에 waypoint ${waypoints[zone]?.length ?? 0}/${WP_PER_ZONE} 지정됨 · ${zone} 영역 안을 클릭하세요`;

  return (
    <section className="panel map-panel">
      <header className="panel-header">
        <h2>
          <Map size={16} /> 시설 맵 / 실시간 모니터링
        </h2>
        <span className="panel-header__hint">순찰 waypoint 지정</span>
      </header>

      <div className="map-panel__legend">
        <span>
          <i className="sw sw--area" style={{ background: "#eaf0ff", borderColor: "#2f6bff" }} /> 존-1 · AMR-01
        </span>
        <span>
          <i className="sw sw--area" style={{ background: "#fbe6f4", borderColor: "#d6409f" }} /> 존-2 · AMR-02
        </span>
        <span>
          <i className="sw sw--area sw--danger" /> 이상 구역
        </span>
        <span>
          <i className="sw sw--area sw--dock" /> 도킹 스테이션
        </span>
      </div>

      {/* 기능1: waypoint 지정 바 */}
      <div
        className="map-panel__wpbar"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          flexWrap: "wrap",
          borderBottom: "1px solid var(--line-2, #eef0f4)",
          background: waypointTotal >= need && !patrolStarted ? "var(--green-soft, #e7f6ee)" : "var(--blue-soft, #eaf0ff)",
        }}
      >
        <span style={{ fontSize: 12.5, fontWeight: 800, color: "#1f4fd0" }}>🎯 순찰 waypoint 지정</span>
        {ZONES.map((z) => (
          <button
            key={z}
            type="button"
            onClick={() => !patrolStarted && setZone(z)}
            style={{
              border: `1px solid ${zone === z ? ZONE_COLOR[z] : "#d8dce6"}`,
              boxShadow: zone === z ? `0 0 0 2px ${ZONE_COLOR[z]}22` : "none",
              background: "#fff",
              color: zone === z ? ZONE_COLOR[z] : "#5b6270",
              borderRadius: 8,
              padding: "6px 10px",
              fontSize: 11.5,
              fontWeight: 700,
              cursor: patrolStarted ? "default" : "pointer",
            }}
          >
            {z} · {ZONE_AMR[z]}
          </button>
        ))}
        <span style={{ fontSize: 12, fontWeight: 800, color: "#1f4fd0", fontVariantNumeric: "tabular-nums" }}>
          {waypointTotal} / {need}
        </span>
        <button
          type="button"
          className="button button--ghost button--xs"
          onClick={() => undoWaypoint(zone)}
          disabled={patrolStarted}
        >
          <RotateCcw size={12} /> 취소
        </button>
        <button
          type="button"
          className="button button--ghost button--xs"
          onClick={clearWaypoints}
          disabled={patrolStarted}
        >
          <Trash2 size={12} /> 전체 지우기
        </button>
        <span style={{ fontSize: 11, color: "#5b6270", flex: 1, minWidth: 160 }}>{hint}</span>
      </div>

      <div className="map-panel__canvas">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          role="img"
          aria-label="AMR 시설 맵 — 클릭하여 waypoint 지정"
          onClick={handleMapClick}
          className="map-panel__svg"
          style={{ cursor: patrolStarted ? "default" : "crosshair" }}
        >
          <rect x="0" y="0" width="900" height="460" fill="#eef0f4" />
          <rect x="20" y="20" width="860" height="420" rx="10" fill="#ffffff" stroke="#dfe3ea" />

          <rect x="70" y="70" width="290" height="150" rx="14" fill="#eaf0ff" stroke="#2f6bff" strokeWidth="1.5" />
          <text x="215" y="150" textAnchor="middle" fill="#1f4fd0" fontSize="15" fontFamily="sans-serif" fontWeight="700">존-1</text>

          <rect
            x="500" y="210" width="290" height="170" rx="14"
            fill={isZone2Hot ? "#fdecec" : "#fbe6f4"}
            stroke={isZone2Hot ? "#e5484d" : "#d6409f"}
            strokeWidth="1.5"
          />
          <text x="645" y="240" textAnchor="middle" fill={isZone2Hot ? "#c8393e" : "#b3348a"} fontSize="15" fontFamily="sans-serif" fontWeight="700">존-2</text>
          <text x="645" y="272" textAnchor="middle" fontSize="20" opacity={isZone2Hot ? 1 : 0}>⚠</text>

          <rect x="370" y="26" width="64" height="34" rx="7" fill="#efeaff" stroke="#7b5cff" />
          <text x="402" y="48" textAnchor="middle" fill="#5b3fe0" fontSize="12" fontFamily="sans-serif" fontWeight="700">D1</text>
          <rect x="370" y="400" width="64" height="34" rx="7" fill="#efeaff" stroke="#7b5cff" />
          <text x="402" y="422" textAnchor="middle" fill="#5b3fe0" fontSize="12" fontFamily="sans-serif" fontWeight="700">D2</text>

          {/* 기능1: 사용자가 지정한 waypoint 마커 */}
          {ZONES.map((z) =>
            (waypoints[z] ?? []).map((w, idx) => (
              <g key={`${z}-${idx}`} transform={`translate(${w.x},${w.y})`}>
                <circle r="10" fill={ZONE_COLOR[z]} fillOpacity="0.16" stroke={ZONE_COLOR[z]} strokeWidth="2" />
                <text y="4" textAnchor="middle" fontSize="11" fontWeight="700" fill={ZONE_COLOR[z]}>
                  {idx + 1}
                </text>
              </g>
            )),
          )}

          {/* AMR 마커 (waypoint 도달 시점에 갱신) */}
          {robots.map((r) => {
            const pose = r.pose ?? { x: 402, y: 230 };
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
        </svg>

        <div className="map-panel__controls">
          <button type="button" title="확대">+</button>
          <button type="button" title="축소">−</button>
          <button type="button" title="현재 위치">◎</button>
          <button type="button" title="레이어">▤</button>
        </div>
      </div>
    </section>
  );
}
