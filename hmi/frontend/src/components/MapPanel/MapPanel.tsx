import { Map, RotateCcw, Square, Trash2 } from "lucide-react";
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { dockPixelPosition, WP_PER_ZONE, ZONE_AMR, ZONE_META, ZONES } from "../../constants/dashboard";
import { useDashboard } from "../../hooks/useDashboard";
import { worldToPixel } from "../../lib/backendClient";
import type { ZoneRect } from "../../types";
import "./MapPanel.css";
import { heldWaypoint, includesUnknownCell } from "./mapRules";

type Mode = "zone" | "waypoint";
const MIN_ZONE_PX = 6; // 이보다 작게 드래그하면 클릭으로 간주하고 무시

// 차단기(BREAKER) 실측 위치 — 월드 좌표(m). 세 번째 값(theta)은 원 표시엔 불필요.
// 맵 위에 노란 원(반지름 0.24m)으로 표시해 실측 대조 대상 지점을 알린다.
const BREAKER_RADIUS_M = 0.24;
const BREAKERS: { id: string; label: string; x: number; y: number }[] = [
  { id: "EQ-BRK-01", label: "차단기 1", x: -0.253, y: 1.99 },
  { id: "EQ-BRK-02", label: "차단기 2", x: -4.52, y: -0.157 },
];

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

export function MapPanel() {
  const {
    robots,
    isZone2Hot,
    maps,
    activeMap,
    mapGrid,
    selectMap,
    zoneRects,
    setZoneRect,
    commitZoneRect,
    waypoints,
    waypointTotal,
    patrolStarted,
    addWaypoint,
    undoWaypoint,
    clearWaypoints,
  } = useDashboard();

  const svgRef = useRef<SVGSVGElement>(null);
  const [mode, setMode] = useState<Mode>("zone");
  const [zone, setZone] = useState<string>(ZONES[0]);
  const [notice, setNotice] = useState("");
  const dragStart = useRef<{ px: number; py: number } | null>(null);
  const [draft, setDraft] = useState<ZoneRect | null>(null);

  // 맵 픽셀 좌표계 = SVG viewBox. 맵이 없으면 기본값으로 그린다.
  const W = activeMap?.width ?? 113;
  const H = activeMap?.height ?? 66;
  const U = W / 100; // 마커/글자 크기 스케일 (뷰박스가 작아서 상대 크기로 그린다)
  const need = ZONES.length * WP_PER_ZONE;
  const locked = patrolStarted;

  // 규칙2: AMR 마커는 도달한 waypoint 에만 스냅해 고정한다(실시간 위치 표기 안 함).
  const heldMarkerRef = useRef<Record<string, { x: number; y: number }>>({});
  // 기능2: 로봇이 지나간(도달한) waypoint 인덱스를 존별로 누적한다(순서 무관). 순찰이 끝나면 비운다.
  const visitedRef = useRef<Record<string, Set<number>>>({});

  function flash(msg: string) {
    setNotice(msg);
    window.setTimeout(() => setNotice(""), 1800);
  }

  function evtToPixel(event: ReactPointerEvent<SVGSVGElement>): { px: number; py: number } | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      px: clamp(((event.clientX - rect.left) / rect.width) * W, 0, W),
      py: clamp(((event.clientY - rect.top) / rect.height) * H, 0, H),
    };
  }

  function zoneAt(px: number, py: number): string | undefined {
    return ZONES.find((z) => {
      const r = zoneRects[z];
      return r && px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h;
    });
  }

  // 존 사각형을 이미지 경계[0,W]×[0,H] 안으로 강제.
  function clampRect(r: ZoneRect): ZoneRect {
    const x = clamp(r.x, 0, W);
    const y = clamp(r.y, 0, H);
    return { x, y, w: clamp(r.w, 0, W - x), h: clamp(r.h, 0, H - y) };
  }

  // 규칙2: 두 사각형이 겹치는지.
  function rectsOverlap(a: ZoneRect, b: ZoneRect): boolean {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }

  // 규칙3: 해당 픽셀이 자유공간(백색)인가 = 로봇 이동 가능. 그리드 없으면 제약 없음.
  function isNavigable(px: number, py: number): boolean {
    if (!mapGrid || !mapGrid.available || !mapGrid.data) return true;
    const gx = Math.floor(px);
    const gy = Math.floor(py);
    if (gx < 0 || gy < 0 || gx >= mapGrid.width || gy >= mapGrid.height) return false;
    return mapGrid.data[gy * mapGrid.width + gx] >= mapGrid.freeMin;
  }

  function handlePointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (locked || mode !== "zone") return;
    const p = evtToPixel(event);
    if (!p) return;
    (event.target as Element).setPointerCapture?.(event.pointerId);
    dragStart.current = p;
    setDraft({ x: p.px, y: p.py, w: 0, h: 0 });
  }

  function handlePointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (locked || mode !== "zone" || !dragStart.current) return;
    const p = evtToPixel(event);
    if (!p) return;
    const s = dragStart.current;
    setDraft({
      x: Math.min(s.px, p.px),
      y: Math.min(s.py, p.py),
      w: Math.abs(p.px - s.px),
      h: Math.abs(p.py - s.py),
    });
  }

  function handlePointerUp() {
    if (mode !== "zone" || !dragStart.current) return;
    const raw = draft;
    dragStart.current = null;
    setDraft(null);
    if (!raw || raw.w < MIN_ZONE_PX || raw.h < MIN_ZONE_PX) return;
    // 이미지 경계로 클램프
    const rect = clampRect(raw);
    if (rect.w < MIN_ZONE_PX || rect.h < MIN_ZONE_PX) {
      flash("존은 맵 내부에만 설정할 수 있습니다");
      return;
    }
    // 규칙1: 실제 맵(검정 박스) 밖(회색 미탐색)에는 존을 설정할 수 없다
    if (includesUnknownCell(mapGrid, rect)) {
      flash("실제 맵(검정 박스) 밖에는 존을 설정할 수 없습니다");
      return;
    }
    // 규칙2: 다른 존과 겹치면 거부
    const other = ZONES.find((z) => z !== zone);
    if (other && zoneRects[other] && rectsOverlap(rect, zoneRects[other])) {
      flash(`${zone} 이(가) ${other} 과(와) 겹칩니다 — 겹치지 않게 그려주세요`);
      return;
    }
    setZoneRect(zone, rect); // 즉시 반영
    commitZoneRect(zone, rect); // 백엔드 저장
    flash(`${zone} 영역 설정됨 · 이제 waypoint 를 지정하세요`);
  }

  function handleClickWaypoint(event: ReactPointerEvent<SVGSVGElement>) {
    if (locked || mode !== "waypoint") return;
    const p = evtToPixel(event);
    if (!p) return;
    const z = zoneAt(p.px, p.py);
    if (!z) {
      flash("waypoint 는 존 영역 안에만 지정할 수 있습니다");
      return;
    }
    if ((waypoints[z]?.length ?? 0) >= WP_PER_ZONE) {
      flash(`${z} 은 이미 waypoint ${WP_PER_ZONE}개가 지정되었습니다`);
      return;
    }
    // 규칙3: 자유공간(백색=로봇 이동 가능) 위에만 지정 가능
    if (!isNavigable(p.px, p.py)) {
      flash("로봇 이동 불가 영역입니다 — 백색(이동 가능) 구역을 클릭하세요");
      return;
    }
    setZone(z);
    addWaypoint(z, Math.round(p.px), Math.round(p.py));
  }

  const hint = notice
    ? notice
    : locked
      ? "순찰 진행 중 — 맵·존·waypoint 잠금"
      : mode === "zone"
        ? `존 설정 모드 · ${zone} 을(를) 선택하고 지도 위에 네모박스를 드래그하세요`
        : waypointTotal >= need
          ? "지정 완료! ▶ 통합 순찰 시작을 누르세요"
          : `${zone} waypoint ${waypoints[zone]?.length ?? 0}/${WP_PER_ZONE} · ${zone} 영역 안을 클릭하세요`;

  // 기능2: 순찰 중 로봇 실좌표가 waypoint 도달 반경 안이면 그 점을 "방문"으로 누적한다.
  //   순서 개념 없이(가까운 점 우선 순회) 지나간 점부터 색이 칠해진다. 순찰 종료 시 초기화.
  // 반경은 픽셀이 아니라 실제 거리(m)로 잡는다 — Nav2 목표 허용오차(~0.25m)와 pose 가
  //   띄엄띄엄(≈1Hz) 오는 사이 로봇이 이동하는 거리를 함께 견뎌야, waypoint 위를 지나갈 때
  //   놓치지 않고 채워진다. 맵 해상도(m/cell)로 픽셀(=셀) 반경으로 환산한다.
  const REACH_M = 0.4;
  const SNAP = activeMap?.resolution ? REACH_M / activeMap.resolution : Math.max(6, W * 0.06);
  if (!patrolStarted) {
    if (Object.keys(visitedRef.current).length) visitedRef.current = {};
  } else {
    for (const r of robots) {
      const z = ZONES.find((zz) => ZONE_AMR[zz] === r.id);
      if (!z || !r.pose) continue;
      const live = activeMap
        ? (() => {
            const c = worldToPixel(activeMap, r.pose.x, r.pose.y);
            return { x: clamp(c.px, 0, W), y: clamp(c.py, 0, H) };
          })()
        : { x: clamp(r.pose.x, 0, W), y: clamp(r.pose.y, 0, H) };
      const set = visitedRef.current[z] ?? (visitedRef.current[z] = new Set<number>());
      (waypoints[z] ?? []).forEach((w, i) => {
        if (Math.hypot(w.x - live.x, w.y - live.y) <= SNAP) set.add(i);
      });
    }
  }

  return (
    <section className="panel map-panel">
      <header className="panel-header">
        <h2>
          <Map size={16} /> 시설 맵 / 실시간 모니터링
        </h2>
        <div className="map-panel__mapselect">
          <label htmlFor="map-select">맵</label>
          <select
            id="map-select"
            value={activeMap?.map_id ?? ""}
            onChange={(e) => selectMap(e.target.value)}
            disabled={locked || maps.length === 0}
          >
            {maps.length === 0 && <option value="">맵 없음</option>}
            {maps.map((m) => (
              <option key={m.map_id} value={m.map_id}>
                {m.name} ({m.width}×{m.height})
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* 모드 전환 + 존/waypoint 컨트롤 바 */}
      <div className="map-panel__toolbar">
        <div className="map-panel__modes">
          <button
            type="button"
            className={`map-panel__mode ${mode === "zone" ? "is-active" : ""}`}
            onClick={() => !locked && setMode("zone")}
            disabled={locked}
          >
            <Square size={12} /> 존 설정
          </button>
          <button
            type="button"
            className={`map-panel__mode ${mode === "waypoint" ? "is-active" : ""}`}
            onClick={() => !locked && setMode("waypoint")}
            disabled={locked}
          >
            🎯 waypoint 지정
          </button>
        </div>

        {ZONES.map((z) => (
          <button
            key={z}
            type="button"
            className="map-panel__zonebtn"
            onClick={() => !locked && setZone(z)}
            style={{
              borderColor: zone === z ? ZONE_META[z].color : "#d8dce6",
              boxShadow: zone === z ? `0 0 0 2px ${ZONE_META[z].color}22` : "none",
              color: zone === z ? ZONE_META[z].color : "#5b6270",
            }}
            disabled={locked}
          >
            {z} · {ZONE_AMR[z]}
          </button>
        ))}

        <span className="map-panel__count">
          {waypointTotal} / {need}
        </span>
        <button
          type="button"
          className="button button--ghost button--xs"
          onClick={() => undoWaypoint(zone)}
          disabled={locked || mode !== "waypoint"}
        >
          <RotateCcw size={12} /> 취소
        </button>
        <button
          type="button"
          className="button button--ghost button--xs"
          onClick={clearWaypoints}
          disabled={locked || mode !== "waypoint"}
        >
          <Trash2 size={12} /> 전체 지우기
        </button>
        <span className="map-panel__hint">{hint}</span>
      </div>

      <div className="map-panel__canvas">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="AMR 시설 맵"
          className="map-panel__svg"
          style={{
            cursor: locked ? "default" : mode === "zone" ? "crosshair" : "pointer",
            aspectRatio: `${W} / ${H}`,
            touchAction: "none",
          }}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onClick={handleClickWaypoint}
        >
          {/* 맵 이미지(SLAM 점유격자) 또는 배경 */}
          <rect x="0" y="0" width={W} height={H} fill="#e9edf3" />
          {activeMap?.image_url && (
            <image
              href={activeMap.image_url}
              x="0"
              y="0"
              width={W}
              height={H}
              preserveAspectRatio="none"
              style={{ imageRendering: "pixelated" }}
            />
          )}

          {/* 고정 도킹 스테이션: 명칭은 박스 내부에만 간결하게 표시 */}
          {Object.values(ZONE_META).map((meta, index) => {
            const dock = dockPixelPosition(meta.amr, W, H);
            const color = meta.color;
            return (
              <g key={`dock-${meta.amr}`} transform={`translate(${dock.x},${dock.y})`}>
                <rect
                  x={-4.5 * U}
                  y={-2.7 * U}
                  width={9 * U}
                  height={5.4 * U}
                  rx={1.2 * U}
                  fill="#fff"
                  fillOpacity={0.94}
                  stroke={color}
                  strokeWidth={1.6}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  y={0.9 * U}
                  textAnchor="middle"
                  fontSize={2.6 * U}
                  fontWeight={800}
                  fill={color}
                  fontFamily="sans-serif"
                >
                  도킹{index + 1}
                </text>
              </g>
            );
          })}

          {/* 차단기 위치 — 노란 원(반지름 0.24m). 실측 대조 대상 지점을 UI에 표시.
              반지름은 실제 거리(m)라서 맵 해상도(m/cell)로 픽셀 반경으로 환산한다. */}
          {activeMap &&
            BREAKERS.map((b) => {
              const c = worldToPixel(activeMap, b.x, b.y);
              const rPx = BREAKER_RADIUS_M / activeMap.resolution;
              return (
                <g key={b.id} transform={`translate(${c.px},${c.py})`}>
                  <circle
                    r={rPx}
                    fill="#f5c518"
                    fillOpacity={0.35}
                    stroke="#f5c518"
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                  />
                  <text
                    y={-rPx - 0.6 * U}
                    textAnchor="middle"
                    fontSize={2.4 * U}
                    fontWeight={700}
                    fill="#a67c00"
                    fontFamily="sans-serif"
                    style={{ paintOrder: "stroke", stroke: "#fff", strokeWidth: 0.7 * U }}
                  >
                    {b.label}
                  </text>
                </g>
              );
            })}

          {/* 존 사각형 (저장/그리는 중) */}
          {ZONES.map((z) => {
            const r = zoneRects[z];
            if (!r) return null;
            const hot = z === "존-2" && isZone2Hot;
            const color = hot ? "#e5484d" : ZONE_META[z].color;
            return (
              <g key={z}>
                <rect
                  x={r.x}
                  y={r.y}
                  width={r.w}
                  height={r.h}
                  rx={1.2 * U}
                  fill={color}
                  fillOpacity={hot ? 0.16 : 0.1}
                  stroke={color}
                  strokeWidth={zone === z && mode === "zone" ? 2 : 1.4}
                  strokeDasharray={zone === z && mode === "zone" ? "3 2" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  x={r.x + 1.5 * U}
                  y={r.y + 3.5 * U}
                  fill={color}
                  fontSize={3 * U}
                  fontWeight={700}
                  fontFamily="sans-serif"
                  style={{ paintOrder: "stroke", stroke: "#fff", strokeWidth: 0.6 * U }}
                >
                  {z} {hot ? "⚠" : ""}
                </text>
              </g>
            );
          })}

          {/* 드래그 중인 존 미리보기 */}
          {draft && (
            <rect
              x={draft.x}
              y={draft.y}
              width={draft.w}
              height={draft.h}
              fill={ZONE_META[zone].color}
              fillOpacity={0.14}
              stroke={ZONE_META[zone].color}
              strokeWidth={2}
              strokeDasharray="3 2"
              vectorEffect="non-scaling-stroke"
            />
          )}

          {/* 지정한 waypoint 마커 — 번호 없음. 로봇이 도달하면 색이 채워져 진행 현황을 표시. */}
          {ZONES.map((z) =>
            (waypoints[z] ?? []).map((w, idx) => {
              const done = visitedRef.current[z]?.has(idx) ?? false;
              const color = ZONE_META[z].color;
              return (
                <g key={`${z}-${idx}`} transform={`translate(${w.x},${w.y})`}>
                  <circle
                    r={2.2 * U}
                    fill={color}
                    fillOpacity={done ? 1 : 0.12}
                    stroke={color}
                    strokeWidth={done ? 2 : 1.6}
                    vectorEffect="non-scaling-stroke"
                  />
                  {done && (
                    <text
                      y={0.9 * U}
                      textAnchor="middle"
                      fontSize={2.4 * U}
                      fontWeight={800}
                      fill="#fff"
                    >
                      ✓
                    </text>
                  )}
                </g>
              );
            }),
          )}

          {/* 규칙2: AMR 마커는 도달한 waypoint 에 스냅해 고정 — 실시간 위치는 표기하지 않는다.
              직전 도달 waypoint 에 마커를 고정해두고, 다음 waypoint 도달 반경 안에 들어오면
              그 지점으로 옮긴다(heldWaypoint). 첫 도달 전에는 마커를 그리지 않는다. */}
          {robots.map((r) => {
            // 실제(월드) 포즈를 픽셀로 환산 (스냅 판정용 — 화면에는 직접 쓰지 않는다)
            let live = { x: W / 2, y: H / 2 };
            if (r.pose && activeMap) {
              const c = worldToPixel(activeMap, r.pose.x, r.pose.y);
              live = { x: clamp(c.px, 0, W), y: clamp(c.py, 0, H) };
            } else if (r.pose) {
              live = { x: clamp(r.pose.x, 0, W), y: clamp(r.pose.y, 0, H) };
            }
            // 각 AMR은 담당 존 waypoint만 판정한다. 다른 AMR 경로 근처를 지나도 잘못
            // 스냅되지 않으며, 첫 waypoint 도달 전에는 마커 자체를 표시하지 않는다.
            // 스냅 반경은 위 "방문(done) 색칠"과 동일한 SNAP(도달 반경 0.4m)을 그대로 쓴다.
            const assignedZone = ZONES.find((z) => ZONE_AMR[z] === r.id);
            const robotWaypoints = assignedZone ? (waypoints[assignedZone] ?? []) : [];
            const atDock = ["IDLE", "DOCKING", "CHARGING"].includes(r.state);
            const held = atDock
              ? dockPixelPosition(r.id, W, H)
              : heldWaypoint(live, robotWaypoints, heldMarkerRef.current[r.id] ?? null, SNAP);
            if (held) heldMarkerRef.current[r.id] = held;
            else delete heldMarkerRef.current[r.id];
            if (!held) return null;
            const px = held.x;
            const py = held.y;
            const danger = r.mission_type === "ANOMALY";
            // 평상시 마커는 담당 존 색과 동일(존-1 파랑 / 존-2 핑크). 이상 대응 중이면 빨강.
            const zoneColor = assignedZone ? ZONE_META[assignedZone].color : "#1f4fd0";
            const fill = danger ? "#e5484d" : zoneColor;
            return (
              <g key={r.id} transform={`translate(${px},${py})`}>
                <circle r={2.8 * U} fill={fill} stroke="#fff" strokeWidth={2} vectorEffect="non-scaling-stroke" />
                <text
                  y={-3.6 * U}
                  textAnchor="middle"
                  fontSize={2.6 * U}
                  fontWeight={700}
                  fill={fill}
                  fontFamily="sans-serif"
                  style={{ paintOrder: "stroke", stroke: "#fff", strokeWidth: 0.9 * U }}
                >
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

        {/* ROS map 프레임의 실제 좌표 — 위치 텔레메트리가 올 때마다 실시간 갱신 */}
        <div className="map-panel__coordinates" aria-label="AMR 실제 좌표" aria-live="polite">
          <strong>실제 좌표 · map frame</strong>
          {robots.map((robot) => (
            <div key={`coordinate-${robot.id}`} className="map-panel__coordinate-row">
              <span>{robot.id}</span>
              {robot.pose ? (
                <code>
                  X {robot.pose.x.toFixed(3)}m · Y {robot.pose.y.toFixed(3)}m
                  {typeof robot.pose.theta === "number" ? ` · θ ${robot.pose.theta.toFixed(2)}rad` : ""}
                </code>
              ) : (
                <code>좌표 수신 대기</code>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
