#!/usr/bin/env python3
"""Generate src/test/result/routes_report.html - a self-contained visual
report of the fixed corridor graph + DEFAULT_ZONES routes overlaid on the
real map, for visual verification and as a dated test record.

    python3 build_route_report.py
"""

import base64
import datetime
import math
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet.route_graph import RouteGraph
from fleet import zone_router
from fleet.default_zones import DEFAULT_ZONES

HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.join(HERE, '..', '..', '..')
MAP_PGM = os.path.join(REPO_ROOT, 'map', 'datacenter_map', 'datacenter_map_v4.pgm')
GRAPH_YAML = os.path.join(HERE, '..', '..', 'fleet', 'config', 'route_graph.yaml')
OUT_HTML = os.path.join(HERE, '..', 'result', 'routes_report.html')

RES = 0.05
OX, OY = -5.15, -0.659

# ---- compute (real code, not hand-transcribed) -----------------------
graph = RouteGraph.from_yaml(GRAPH_YAML)
missions, crossing_log, _ = zone_router.build_missions(graph, DEFAULT_ZONES)

im = Image.open(MAP_PGM).convert('L')
img_w, img_h = im.size
world_w, world_h = img_w * RES, img_h * RES
buf_path = os.path.join(HERE, '_map_tmp.png')
im.save(buf_path)
with open(buf_path, 'rb') as f:
    map_b64 = base64.b64encode(f.read()).decode('ascii')
os.remove(buf_path)


def to_svg(x, y):
    return (x - OX), ((OY + world_h) - y)


def rk(sx, sy):
    return (round(sx, 3), round(sy, 3))


def perp_offset(p0, p1, amount):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return (0.0, 0.0)
    nx, ny = -dy / length, dx / length
    return (nx * amount, ny * amount)


nodes_svg = {nid: to_svg(*xy) for nid, xy in graph.nodes.items()}
# zone_router.build_missions()가 이제 교차 지점을 두 종류로 낸다
# (point_id 접두사로 구분): 'X_' = 통로(엣지) 공유, 'J_' = 교차로(노드)
# 공유 - 서로 다른 엣지로 같은 교차로에 들어오는 경우까지 잡으려고
# 노드 단위 판정을 추가했다(zone_router.py의 "교차 지점 판정 단위"
# 참고). 리포트도 이 두 종류를 각각 다르게 그려야 한다 - 예전처럼
# crossing_log의 key를 전부 graph.edges 키로 취급하면 노드형 교차에서
# KeyError가 난다.
shared_edge_ids = {key for key, _, point_id in crossing_log if point_id.startswith('X_')}
shared_junction_ids = {key for key, _, point_id in crossing_log if point_id.startswith('J_')}

svg_parts = []

for eid, e in graph.edges.items():
    ax, ay = nodes_svg[e['a']]
    bx, by = nodes_svg[e['b']]
    cls = 'graph-edge graph-edge--shared' if eid in shared_edge_ids else 'graph-edge'
    svg_parts.append(f'<line class="{cls}" x1="{ax:.3f}" y1="{ay:.3f}" x2="{bx:.3f}" y2="{by:.3f}" />')

for nid, (nx, ny) in nodes_svg.items():
    cls = 'graph-node graph-node--shared' if nid in shared_junction_ids else 'graph-node'
    r = 0.06 if nid in shared_junction_ids else 0.035
    svg_parts.append(f'<circle class="{cls}" cx="{nx:.3f}" cy="{ny:.3f}" r="{r}" />')

for eid in shared_edge_ids:
    e = graph.edges[eid]
    ax, ay = nodes_svg[e['a']]
    bx, by = nodes_svg[e['b']]
    mx, my = (ax + bx) / 2, (ay + by) / 2
    point_id = next(r[2] for r in crossing_log if r[0] == eid)
    svg_parts.append(
        f'<text class="crossing-label" x="{mx:.3f}" y="{my - 0.13:.3f}" text-anchor="middle">'
        f'shared aisle · {point_id}</text>'
    )

for nid in shared_junction_ids:
    nx, ny = nodes_svg[nid]
    point_id = next(r[2] for r in crossing_log if r[0] == nid)
    svg_parts.append(
        f'<text class="crossing-label" x="{nx:.3f}" y="{ny - 0.13:.3f}" text-anchor="middle">'
        f'shared junction · {point_id}</text>'
    )

ROBOT_LABEL_OFFSET = {'robot3': (0.16, -0.14), 'robot8': (0.16, 0.22)}

robots_svg = {}
for robot, wps in missions.items():
    svg_wps = []
    for wp in wps:
        sx, sy = to_svg(wp['x'], wp['y'])
        svg_wps.append({**wp, 'sx': sx, 'sy': sy})
    robots_svg[robot] = svg_wps

    seen_edges = {}
    for i in range(len(svg_wps) - 1):
        p0 = (svg_wps[i]['sx'], svg_wps[i]['sy'])
        p1 = (svg_wps[i + 1]['sx'], svg_wps[i + 1]['sy'])
        key = frozenset([rk(*p0), rk(*p1)])
        seen_edges[key] = seen_edges.get(key, 0) + 1
        rep = seen_edges[key]
        off = perp_offset(p0, p1, 0.045 * (rep - 1)) if rep > 1 else (0.0, 0.0)
        ax, ay = p0[0] + off[0], p0[1] + off[1]
        bx, by = p1[0] + off[0], p1[1] + off[1]
        svg_parts.append(
            f'<line class="route route--{robot}" x1="{ax:.3f}" y1="{ay:.3f}" '
            f'x2="{bx:.3f}" y2="{by:.3f}" marker-end="url(#arrow-{robot})" />'
        )

    seq = 0
    for wp in svg_wps:
        sx, sy = wp['sx'], wp['sy']
        if wp['has_gate']:
            r = 0.09
            svg_parts.append(
                f'<rect class="waypoint waypoint--gate waypoint--{robot}" '
                f'x="{sx - r:.3f}" y="{sy - r:.3f}" width="{2*r:.3f}" height="{2*r:.3f}" '
                f'transform="rotate(45 {sx:.3f} {sy:.3f})" />'
            )
        elif wp['origin'] == 'patrol':
            svg_parts.append(
                f'<circle class="waypoint waypoint--patrol waypoint--{robot}" '
                f'cx="{sx:.3f}" cy="{sy:.3f}" r="0.075" />'
            )
        else:
            svg_parts.append(
                f'<circle class="waypoint waypoint--transit waypoint--{robot}" '
                f'cx="{sx:.3f}" cy="{sy:.3f}" r="0.04" />'
            )
        if wp['origin'] == 'patrol':
            seq += 1
            dx, dy = ROBOT_LABEL_OFFSET[robot]
            svg_parts.append(
                f'<text class="waypoint-seq waypoint-seq--{robot}" '
                f'x="{sx + dx:.3f}" y="{sy + dy:.3f}">{seq}</text>'
            )

svg_body = '\n      '.join(svg_parts)

# ---- waypoint tables ----------------------------------------------------


def flags_html(wp):
    tags = []
    if wp['has_gate']:
        tags.append('<span class="tag tag--gate">차단기 점검</span>')
    if wp['point_id']:
        tags.append(f'<span class="tag tag--crossing">{wp["point_id"]}</span>')
    if wp['origin'] == 'transit' and not tags:
        tags.append('<span class="tag tag--transit">경유</span>')
    return ''.join(tags)


def table_rows(robot):
    rows = []
    seq = 0
    for wp in missions[robot]:
        if wp['origin'] == 'patrol':
            seq += 1
            idx = str(seq)
        else:
            idx = '·'
        rows.append(
            '<tr class="{cls}"><td>{idx}</td><td class="num">{x:.2f}</td>'
            '<td class="num">{y:.2f}</td><td class="num">{yaw:.0f}°</td>'
            '<td>{flags}</td></tr>'.format(
                cls='row--transit' if wp['origin'] == 'transit' else '',
                idx=idx, x=wp['x'], y=wp['y'], yaw=wp['yaw'], flags=flags_html(wp),
            )
        )
    return '\n          '.join(rows)


robot3_rows = table_rows('robot3')
robot8_rows = table_rows('robot8')

crossing_summary = crossing_log[0] if crossing_log else None


def crossing_summary_text(row):
    key, robots, point_id = row
    # point_id 접두사('X_'=엣지, 'J_'=교차로)로 문구를 다르게 낸다 -
    # build_route_report.py 상단 nodes_svg/shared_edge_ids 주석 참고.
    noun = '교차로' if point_id.startswith('J_') else '통로'
    return (
        f'{noun} <strong>{key}</strong> (point_id <strong>{point_id}</strong>)를 '
        + '와 '.join(robots) + ' 둘 다 사용합니다 — 한 번에 하나만 점유하도록 조정됩니다.'
    )


generated = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
node_count = len(graph.nodes)
edge_count = len(graph.edges)

# ---- assemble HTML -------------------------------------------------------

html = f'''<!doctype html>
<title>Fleet 경로 검증 — datacenter_map_v4</title>
<meta name="description" content="datacenter_map_v4 지도 위에 로봇별 순찰 경로와 공유 통로(교차 지점) 검증 결과를 시각화.">
<style>
:root {{
  --paper: #f2f5f7;
  --panel: #ffffff;
  --ink: #1b242c;
  --muted: #5b6b78;
  --line: #c6d0d8;
  --robot3: #2e6fa8;
  --robot8: #96507e;
  --gate: #1d8a72;
  --crossing: #c07c1e;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", system-ui, sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper: #10161c;
    --panel: #161e26;
    --ink: #e8edf1;
    --muted: #90a0ac;
    --line: #29343e;
    --robot3: #6fb3e8;
    --robot8: #db92c4;
    --gate: #55cbac;
    --crossing: #e8a63f;
  }}
}}
:root[data-theme="dark"] {{
  --paper: #10161c;
  --panel: #161e26;
  --ink: #e8edf1;
  --muted: #90a0ac;
  --line: #29343e;
  --robot3: #6fb3e8;
  --robot8: #db92c4;
  --gate: #55cbac;
  --crossing: #e8a63f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  line-height: 1.55;
}}
.wrap {{
  max-width: 920px;
  margin: 0 auto;
  padding: 48px 24px 72px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}}
.eyebrow {{
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}}
h1 {{
  font-family: var(--mono);
  font-size: clamp(24px, 4vw, 34px);
  font-weight: 600;
  margin: 6px 0 0;
  text-wrap: balance;
  letter-spacing: -0.01em;
}}
.lede {{
  max-width: 65ch;
  color: var(--muted);
  font-size: 15px;
  margin: 4px 0 0;
}}
.meta {{
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}}
.panel {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 20px;
}}
.diagram-figure {{ margin: 0; }}
.diagram-figure svg {{
  width: 100%;
  height: auto;
  display: block;
  color: var(--ink);
}}
.diagram-figure figcaption {{
  margin-top: 14px;
  font-size: 13px;
  color: var(--muted);
}}
.graph-edge {{ stroke: var(--line); stroke-width: 0.018; stroke-dasharray: 0.05 0.045; }}
.graph-edge--shared {{ stroke: var(--crossing); stroke-width: 0.03; stroke-dasharray: none; opacity: 0.55; }}
.graph-node {{ fill: var(--muted); opacity: 0.7; }}
.graph-node--shared {{ fill: var(--crossing); opacity: 0.85; }}
.crossing-label {{
  font-family: var(--mono);
  font-size: 0.135px;
  fill: var(--crossing);
  font-weight: 600;
}}
.route {{ stroke-width: 0.03; fill: none; stroke-linecap: round; }}
.route--robot3 {{ stroke: var(--robot3); }}
.route--robot8 {{ stroke: var(--robot8); }}
.waypoint--robot3 {{ fill: var(--robot3); }}
.waypoint--robot8 {{ fill: var(--robot8); }}
.waypoint--gate {{ fill: var(--gate) !important; stroke: var(--panel); stroke-width: 0.012; }}
.waypoint--transit {{ opacity: 0.55; }}
.waypoint-seq {{
  font-family: var(--mono);
  font-size: 0.16px;
  font-weight: 600;
}}
.waypoint-seq--robot3 {{ fill: var(--robot3); }}
.waypoint-seq--robot8 {{ fill: var(--robot8); }}
.legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  color: var(--muted);
}}
.legend-item {{ display: flex; align-items: center; gap: 8px; }}
.swatch {{ width: 14px; height: 14px; border-radius: 50%; flex: none; }}
.swatch--gate {{ border-radius: 3px; transform: rotate(45deg); }}
.swatch--transit {{ width: 8px; height: 8px; }}
.swatch--edge {{ width: 18px; height: 2px; border-radius: 0; background: var(--line); }}
.swatch--crossing {{ width: 18px; height: 3px; border-radius: 0; background: var(--crossing); opacity: 0.7; }}
h2 {{
  font-family: var(--mono);
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px;
}}
.tables {{ display: grid; grid-template-columns: 1fr; gap: 20px; }}
@media (min-width: 680px) {{ .tables {{ grid-template-columns: 1fr 1fr; }} }}
.table-card h2 {{ display: flex; align-items: center; gap: 10px; }}
.robot-dot {{ width: 10px; height: 10px; border-radius: 50%; flex: none; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }}
th {{
  font-family: var(--mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  text-align: left;
  font-weight: 500;
  padding: 0 8px 8px 0;
  border-bottom: 1px solid var(--line);
}}
td {{ padding: 6px 8px 6px 0; border-bottom: 1px solid var(--line); font-family: var(--mono); }}
td.num {{ font-variant-numeric: tabular-nums; text-align: right; }}
tr.row--transit td {{ color: var(--muted); }}
tr:last-child td {{ border-bottom: none; }}
.tag {{
  font-family: var(--mono);
  font-size: 10.5px;
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-block;
  margin-right: 4px;
  letter-spacing: 0.02em;
}}
.tag--gate {{ background: color-mix(in srgb, var(--gate) 18%, transparent); color: var(--gate); }}
.tag--crossing {{ background: color-mix(in srgb, var(--crossing) 18%, transparent); color: var(--crossing); }}
.tag--transit {{ background: color-mix(in srgb, var(--muted) 18%, transparent); color: var(--muted); }}
.callout {{
  border: 1px solid var(--crossing);
  border-left-width: 4px;
  border-radius: 8px;
  padding: 14px 18px;
  font-size: 14px;
  background: color-mix(in srgb, var(--crossing) 8%, var(--panel));
}}
.callout strong {{ font-family: var(--mono); }}
footer {{
  border-top: 1px solid var(--line);
  padding-top: 16px;
  font-size: 12.5px;
  color: var(--muted);
}}
footer code {{
  font-family: var(--mono);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 5px;
}}
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Fleet Node · 경로 검증</div>
    <h1>datacenter_map_v4 경로 그래프</h1>
    <p class="lede">
      DEFAULT_ZONES를 고정 통로 그래프(노드=교차로, 엣지=한 번에 한 대만
      지날 수 있는 통로) 위에서 홉 단위로 라우팅한 결과입니다. 두 로봇의
      경로가 같은 통로를 필요로 하거나(엣지 공유) 서로 다른 통로로 같은
      교차로에 들어오면(노드 공유), 점유 조정이 필요한 교차 지점으로
      표시됩니다.
    </p>
    <div class="meta">
      <span>지도: {world_w:.2f}m × {world_h:.2f}m @ {RES:.2f}m/px</span>
      <span>그래프: 노드 {node_count}개 / 엣지 {edge_count}개</span>
      <span>구역: {len(DEFAULT_ZONES)}개</span>
      <span>생성 시각 {generated}</span>
    </div>
  </header>

  <section class="panel">
    <figure class="diagram-figure">
      <svg viewBox="0 0 {world_w:.3f} {world_h:.3f}" role="img"
           aria-label="datacenter_map_v4 지도 위에 고정 통로 그래프와 robot3, robot8의 순찰 경로를 표시하고, 공유되는 통로 하나를 교차 지점으로 강조한 그림.">
        <defs>
          <marker id="arrow-robot3" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--robot3)" />
          </marker>
          <marker id="arrow-robot8" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--robot8)" />
          </marker>
        </defs>
        <image href="data:image/png;base64,{map_b64}" x="0" y="0" width="{world_w:.3f}" height="{world_h:.3f}"
               style="image-rendering: pixelated; opacity: 0.9" />
      {svg_body}
      </svg>
      <figcaption>
        회색 점선 격자: 고정 통로 그래프. 파란색 = robot3(zoneAB), 자주색 =
        robot8(zoneCD). 채워진 점은 UI가 지정한 순찰 지점(방문 순서 번호
        표시), 작은 점은 두 지점을 잇기 위해 라우터가 지나간 경유
        노드입니다. 마름모는 차단기 점검 지점. 강조된 통로는 두 로봇이
        함께 사용해서 <code>/fleet/occupancy_request</code>로 점유가
        조정됩니다.
      </figcaption>
    </figure>

    <div class="legend">
      <span class="legend-item"><span class="swatch" style="background:var(--robot3)"></span>robot3 경로</span>
      <span class="legend-item"><span class="swatch" style="background:var(--robot8)"></span>robot8 경로</span>
      <span class="legend-item"><span class="swatch swatch--transit" style="background:var(--muted)"></span>경유 노드</span>
      <span class="legend-item"><span class="swatch swatch--gate" style="background:var(--gate)"></span>차단기 점검 지점</span>
      <span class="legend-item"><span class="swatch--edge"></span>통로 그래프</span>
      <span class="legend-item"><span class="swatch--crossing"></span>공유(경합) 통로</span>
      <span class="legend-item"><span class="swatch" style="background:var(--crossing)"></span>공유(경합) 교차로</span>
    </div>
  </section>

  <section class="tables">
    <div class="panel table-card">
      <h2><span class="robot-dot" style="background:var(--robot3)"></span>robot3 — zoneAB</h2>
      <table>
        <thead><tr><th>순번</th><th>x</th><th>y</th><th>yaw</th><th>플래그</th></tr></thead>
        <tbody>
          {robot3_rows}
        </tbody>
      </table>
    </div>
    <div class="panel table-card">
      <h2><span class="robot-dot" style="background:var(--robot8)"></span>robot8 — zoneCD</h2>
      <table>
        <thead><tr><th>순번</th><th>x</th><th>y</th><th>yaw</th><th>플래그</th></tr></thead>
        <tbody>
          {robot8_rows}
        </tbody>
      </table>
    </div>
  </section>

  <section class="callout">
    <strong>교차 지점 {len(crossing_log)}개 감지됨.</strong>
    {crossing_summary_text(crossing_summary) if crossing_summary else '이 구성에서는 공유되는 통로/교차로가 없습니다.'}
  </section>

  <footer>
    <code>src/test/code/build_route_report.py</code>가 실제
    <code>route_graph.yaml</code> + <code>zone_router.build_missions()</code>
    출력값으로 생성함 — 손으로 그린 그림이 아님. 단위 테스트:
    <code>src/test/code/test_route_graph.py</code>,
    <code>src/test/code/test_zone_router.py</code>.
  </footer>
</div>
'''

os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
with open(OUT_HTML, 'w') as f:
    f.write(html)
print('wrote', OUT_HTML)
