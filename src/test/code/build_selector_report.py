#!/usr/bin/env python3
"""src/test/result/selector_report.html 생성 - 이상신호 로봇 선정
(robot_selector.py)이 test_robot_selector.py의 SCENARIOS를 실제로 어떻게
판정하는지 지도 위에 시각화한 검증 리포트.

    python3 build_selector_report.py

숫자와 경로는 전부 robot_selector.select_nearest_robot() /
path_distances()의 실제 반환값이다 - 손으로 옮겨적지 않았다.
"""

import base64
import datetime
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet.route_graph import RouteGraph
from fleet import robot_selector

sys.path.insert(0, os.path.dirname(__file__))
from test_robot_selector import SCENARIOS, EDGE_CASES

HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.join(HERE, '..', '..', '..')
MAP_PGM = os.path.join(REPO_ROOT, 'map', 'datacenter_map', 'datacenter_map_v4.pgm')
GRAPH_YAML = os.path.join(HERE, '..', '..', 'fleet', 'config', 'route_graph.yaml')
OUT_HTML = os.path.join(HERE, '..', 'result', 'selector_report.html')

RES = 0.05
OX, OY = -5.15, -0.659

base_graph = RouteGraph.from_yaml(GRAPH_YAML)

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


ROBOT_COLOR_VAR = {'robot3': '--robot3', 'robot8': '--robot8'}


def robot_color(ns):
    return f'var({ROBOT_COLOR_VAR.get(ns, "--muted")})'


def render_graph_svg():
    parts = []
    for e in base_graph.edges.values():
        ax, ay = to_svg(*base_graph.nodes[e['a']])
        bx, by = to_svg(*base_graph.nodes[e['b']])
        parts.append(f'<line class="graph-edge" x1="{ax:.3f}" y1="{ay:.3f}" x2="{bx:.3f}" y2="{by:.3f}" />')
    for nx, ny in base_graph.nodes.values():
        sx, sy = to_svg(nx, ny)
        parts.append(f'<circle class="graph-node" cx="{sx:.3f}" cy="{sy:.3f}" r="0.035" />')
    return '\n      '.join(parts)


GRAPH_SVG = render_graph_svg()


def render_scenario(case):
    """실제 select_nearest_robot() / path_distances() 호출 결과로 한
    시나리오의 SVG 지도 + 결과 테이블을 만든다.

    그래프 사본을 두 개 따로 쓰는 이유: path_distances()는 로봇/이상신호
    좌표를 그래프에 insert_point()로 끼워 넣어서(합성 노드 생성) 그
    그래프 객체 자체를 변형한다. 경로를 SVG로 그리려면 그 합성 노드의
    좌표가 필요하니 `graph`(변형된 사본)에서 좌표를 찾아야 하고,
    base_graph(원본, 안 변형됨)에서 찾으면 SCENARIOS 좌표가 우연히
    기존 그래프 노드와 정확히 겹칠 때만 운 좋게 통과하고 실제 AMCL
    처럼 소수점 오차가 섞인 좌표에서는 KeyError가 난다. winner는 이
    graph와 무관하게 별도 사본으로 다시 계산하는데, path_distances가
    아니라 select_nearest_robot()을 실제로 한 번 더 호출해서 리포트의
    "누가 이겼는가"가 진짜 공개 API(fleet_node.py가 부르는 바로 그
    함수)의 반환값이 되도록 하기 위함이다."""
    graph = base_graph.copy()
    dists = robot_selector.path_distances(graph, case['anomaly'], case['poses'], case['candidates'])
    winner = robot_selector.select_nearest_robot(
        base_graph.copy(), case['anomaly'], case['poses'], case['candidates'])

    ax, ay = to_svg(case['anomaly']['x'], case['anomaly']['y'])
    parts = [
        f'<circle class="anomaly-halo" cx="{ax:.3f}" cy="{ay:.3f}" r="0.16" />',
        f'<circle class="anomaly-dot" cx="{ax:.3f}" cy="{ay:.3f}" r="0.05" />',
    ]

    rows = []
    for ns in case['candidates']:
        color = robot_color(ns)
        is_winner = (ns == winner)
        if ns in dists:
            nodes, dist = dists[ns]
            # SVG path의 "d" 속성: 첫 점은 이동(M), 나머지는 직선(L)으로
            # 이어서 그래프가 실제로 밟은 홉을 그대로 꺾은선으로 그린다
            # (직선 한 방이 아니라 다익스트라가 고른 노드 순서 그대로).
            path_pts = [to_svg(*graph.nodes[n]) for n in nodes]
            path_d = ' '.join(f'{"M" if i == 0 else "L"}{x:.3f},{y:.3f}' for i, (x, y) in enumerate(path_pts))
            cls = 'sel-path sel-path--win' if is_winner else 'sel-path sel-path--lose'
            parts.append(f'<path class="{cls}" d="{path_d}" style="stroke:{color}" />')
            dist_label = f'{dist:.2f}m'
        else:
            dist_label = '위치 모름'
        pose = case['poses'].get(ns)
        if pose:
            sx, sy = to_svg(*pose)
            r = 0.095 if is_winner else 0.075
            parts.append(f'<circle class="robot-pose{" robot-pose--win" if is_winner else ""}" '
                         f'cx="{sx:.3f}" cy="{sy:.3f}" r="{r:.3f}" style="fill:{color}" />')
            parts.append(f'<text class="robot-label" x="{sx:.3f}" y="{sy - 0.13:.3f}" '
                         f'text-anchor="middle" style="fill:{color}">{ns}</text>')
        rows.append(
            '<tr class="{cls}"><td><span class="dot" style="background:{color}"></span>{ns}</td>'
            '<td class="num">{dist}</td><td>{verdict}</td></tr>'.format(
                cls='row--win' if is_winner else '',
                color=color, ns=ns, dist=dist_label,
                verdict='<span class="tag tag--win">선정됨</span>' if is_winner else '선정 안 됨',
            )
        )

    svg_body = GRAPH_SVG + '\n      ' + '\n      '.join(parts)
    table_rows = '\n          '.join(rows)
    return svg_body, table_rows, winner


scenario_sections = []
for case in SCENARIOS:
    svg_body, table_rows, winner = render_scenario(case)
    scenario_sections.append(f'''
    <article class="panel scenario">
      <h2>{case['label']}</h2>
      <p class="why">{case['why']}</p>
      <figure class="diagram-figure">
        <svg viewBox="0 0 {world_w:.3f} {world_h:.3f}" role="img"
             aria-label="{case['label']} 시나리오 지도. 후보 로봇 위치와 이상신호 좌표, 각 로봇의 통로 그래프 경로를 표시.">
          <image href="data:image/png;base64,{map_b64}" x="0" y="0" width="{world_w:.3f}" height="{world_h:.3f}"
                 style="image-rendering: pixelated; opacity: 0.9" />
        {svg_body}
        </svg>
      </figure>
      <table>
        <thead><tr><th>후보 로봇</th><th>그래프 경로 거리</th><th>결과</th></tr></thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </article>''')

scenarios_html = '\n'.join(scenario_sections)

# ---- 예외 케이스 (지도로 그리기엔 기하가 없는 경우) -------------------
# test_robot_selector.py의 EDGE_CASES를 그대로 재실행한다 - 여기서
# 숫자를 다시 손으로 적으면 테스트가 나중에 바뀌었을 때 리포트만 예전
# 값을 계속 보여주는 채로 남을 수 있다.

edge_case_rows = []
for ec in EDGE_CASES:
    result = robot_selector.select_nearest_robot(
        base_graph.copy(), ec['anomaly'], ec['poses'], ec['candidates'])
    ok = result == ec['expected']
    edge_case_rows.append(f'''
      <li class="edge-case">
        <div class="edge-case-head">
          <span class="tag {"tag--win" if ok else "tag--fail"}">{"검증됨" if ok else "실패"}</span>
          <strong>{ec['title']}</strong>
        </div>
        <p>{ec['detail']} 결과: <code>{result}</code></p>
      </li>''')
edge_cases_html = '\n'.join(edge_case_rows)

generated = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

html = f'''<!doctype html>
<title>이상신호 로봇 선정 검증 — robot_selector.py</title>
<meta name="description" content="통로 그래프 최단 경로 기준 이상신호 대응 로봇 선정 로직을 실제 시나리오 지도 위에 시각화한 검증 리포트.">
<style>
:root {{
  --paper: #f2f5f7;
  --panel: #ffffff;
  --ink: #1b242c;
  --muted: #5b6b78;
  --line: #c6d0d8;
  --robot3: #2e6fa8;
  --robot8: #96507e;
  --win: #1d8a72;
  --fail: #b3261e;
  --anomaly: #c0392b;
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
    --win: #55cbac;
    --fail: #ff6b5c;
    --anomaly: #ff6b5c;
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
  --win: #55cbac;
  --fail: #ff6b5c;
  --anomaly: #ff6b5c;
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
  max-width: 1080px;
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
  max-width: 68ch;
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
.scenarios {{ display: grid; grid-template-columns: 1fr; gap: 20px; }}
@media (min-width: 760px) {{ .scenarios {{ grid-template-columns: 1fr 1fr; }} }}
.scenario h2 {{
  font-family: var(--mono);
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 6px;
}}
.why {{ font-size: 13px; color: var(--muted); margin: 0 0 14px; }}
.diagram-figure {{ margin: 0; }}
.diagram-figure svg {{ width: 100%; height: auto; display: block; }}
.graph-edge {{ stroke: var(--line); stroke-width: 0.018; stroke-dasharray: 0.05 0.045; }}
.graph-node {{ fill: var(--muted); opacity: 0.6; }}
.sel-path {{ fill: none; stroke-linecap: round; stroke-linejoin: round; }}
.sel-path--win {{ stroke-width: 0.05; opacity: 0.95; }}
.sel-path--lose {{ stroke-width: 0.03; opacity: 0.4; stroke-dasharray: 0.07 0.06; }}
.robot-pose {{ stroke: var(--panel); stroke-width: 0.015; }}
.robot-pose--win {{ stroke-width: 0.022; }}
.robot-label {{ font-family: var(--mono); font-size: 0.15px; font-weight: 600; }}
.anomaly-halo {{ fill: var(--anomaly); opacity: 0.18; }}
.anomaly-dot {{ fill: var(--anomaly); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 14px; }}
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
td {{ padding: 7px 8px 7px 0; border-bottom: 1px solid var(--line); font-family: var(--mono); }}
td.num {{ font-variant-numeric: tabular-nums; }}
tr:last-child td {{ border-bottom: none; }}
tr.row--win td {{ color: var(--ink); font-weight: 600; }}
.dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }}
.tag {{
  font-family: var(--mono);
  font-size: 10.5px;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-block;
  letter-spacing: 0.02em;
}}
.tag--win {{ background: color-mix(in srgb, var(--win) 18%, transparent); color: var(--win); }}
.tag--fail {{ background: color-mix(in srgb, var(--fail) 18%, transparent); color: var(--fail); }}
h2.section-title {{
  font-family: var(--mono);
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px;
}}
.edge-cases {{ list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }}
.edge-case-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }}
.edge-case p {{ margin: 0; font-size: 13.5px; color: var(--muted); }}
.edge-case code {{ font-family: var(--mono); color: var(--ink); }}
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
    <div class="eyebrow">Fleet Node · 이상신호 로봇 선정 검증</div>
    <h1>가장 가까운 로봇을 어떻게 고르는가</h1>
    <p class="lede">
      이상신호가 감지되면 로봇을 배정해야 합니다. "가까움"은 직선 거리가
      아니라 통로 그래프(route_graph.yaml) 위의 실제 이동 경로 거리로
      판정합니다 - 이 지도는 랙 사이 외길 통로 구조라 직선상 가까워
      보여도 실제로는 돌아가야 하는 경우가 있기 때문입니다. 아래는
      test_robot_selector.py에 정의된 시나리오를 robot_selector.py로
      실제 실행한 결과입니다.
    </p>
    <div class="meta">
      <span>그래프: 노드 {len(base_graph.nodes)}개 / 엣지 {len(base_graph.edges)}개</span>
      <span>시나리오 {len(SCENARIOS)}개 + 예외 케이스 {len(EDGE_CASES)}개</span>
      <span>생성 시각 {generated}</span>
    </div>
  </header>

  <section class="scenarios">
    {scenarios_html}
  </section>

  <section class="panel">
    <h2 class="section-title">예외 케이스</h2>
    <p class="why">기하로 그리기 어려운 입력 - 위치 정보 누락, 후보 없음 - 에 대한 안전망이 의도대로 동작하는지 확인.</p>
    <ul class="edge-cases">
      {edge_cases_html}
    </ul>
  </section>

  <footer>
    <code>src/test/code/build_selector_report.py</code>가
    <code>test_robot_selector.py</code>의 <code>SCENARIOS</code>를 그대로
    불러와 <code>robot_selector.select_nearest_robot()</code> /
    <code>path_distances()</code>를 실행한 결과로 생성함 - 손으로 그린
    그림이 아님. 단위 테스트: <code>src/test/code/test_robot_selector.py</code>
    (<code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_robot_selector.py -v</code>).
  </footer>
</div>
'''

os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
with open(OUT_HTML, 'w') as f:
    f.write(html)
print('wrote', OUT_HTML)
