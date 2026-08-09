#!/usr/bin/env python3
"""src/test/result/anomaly_report.html 생성 - 이상신호 급파 대상 판정
(anomaly_control.py)이 test_anomaly_control.py의 케이스들을 실제로 어떻게
처리하는지 표로 시각화하는 검증 리포트.

    python3 build_anomaly_report.py

결과는 전부 anomaly_control.eligible_candidates() /
resolve_self_location()의 실제 반환값이다 - 손으로 옮겨적지 않았다."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet import anomaly_control

sys.path.insert(0, os.path.dirname(__file__))
from test_anomaly_control import CANDIDATE_CASES, SELF_LOCATION_CASES, DEFAULT_LOCATION

HERE = os.path.dirname(__file__)
OUT_HTML = os.path.join(HERE, '..', 'result', 'anomaly_report.html')


def robot_tags(names, cls):
    if not names:
        return '<span class="empty">없음</span>'
    return ' '.join(f'<span class="tag {cls}">{n}</span>' for n in names)


# ---- CCTV 감지 급파 후보 케이스 -----------------------------------------
candidate_sections = []
for case in CANDIDATE_CASES:
    result = anomaly_control.eligible_candidates(
        case['missions'], case['anomaly_busy'], case['emergency_stopped'])
    ok = result == case['expected']

    candidate_sections.append(f'''
    <article class="panel case">
      <div class="case-head">
        <span class="tag {"tag--win" if ok else "tag--fail"}">{"검증됨" if ok else "실패"}</span>
        <h2>{case['title']}</h2>
      </div>
      <p class="why">{case['why']}</p>
      <div class="io">
        <div class="io-in">
          <div class="io-label">입력</div>
          <table>
            <tr><th>순찰 중 (missions)</th><td>{robot_tags(case['missions'], 'tag--reg')}</td></tr>
            <tr><th>이상신호 대응 중 (anomaly_busy)</th><td>{robot_tags(sorted(case['anomaly_busy']), 'tag--dispatched')}</td></tr>
            <tr><th>긴급정지 중 (emergency_stopped)</th><td>{robot_tags(sorted(case['emergency_stopped']), 'tag--emergency')}</td></tr>
          </table>
        </div>
        <div class="io-out">
          <div class="io-label">급파 후보 (eligible_candidates)</div>
          <table>
            <tr><td>{robot_tags(result, 'tag--dispatched')}</td></tr>
          </table>
        </div>
      </div>
    </article>''')

candidates_html = '\n'.join(candidate_sections)

# ---- AMR 자체 감지 위치 판정 케이스 --------------------------------------
location_rows = []
for case in SELF_LOCATION_CASES:
    result = anomaly_control.resolve_self_location(
        case['ns'], case['robot_pose'], DEFAULT_LOCATION)
    ok = result == case['expected']
    pose_label = ', '.join(f'{ns}:({x},{y})' for ns, (x, y) in case['robot_pose'].items())

    location_rows.append(f'''
      <li class="edge-case">
        <div class="edge-case-head">
          <span class="tag {"tag--win" if ok else "tag--fail"}">{"검증됨" if ok else "실패"}</span>
          <strong>{case['title']}</strong>
        </div>
        <p>{case['why']}</p>
        <p class="mono">대상: <b>{case['ns']}</b> · 알고 있는 위치: {pose_label or '없음'}
          → 결과: <b>{result}</b></p>
      </li>''')

locations_html = '\n'.join(location_rows)

generated = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

html = f'''<!doctype html>
<title>이상신호 급파 대상 판정 검증 — anomaly_control.py</title>
<meta name="description" content="Fleet Node가 AMR 자체 감지/CCTV 감지 두 경로에서 이상신호 급파 대상을 어떻게 판정하는지 실제 시나리오로 검증한 리포트.">
<style>
:root {{
  --paper: #f2f5f7;
  --panel: #ffffff;
  --ink: #1b242c;
  --muted: #5b6b78;
  --line: #c6d0d8;
  --reg: #5b6b78;
  --dispatched: #1d8a72;
  --emergency: #b3261e;
  --win: #1d8a72;
  --fail: #b3261e;
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
    --reg: #90a0ac;
    --dispatched: #55cbac;
    --emergency: #ff6b5c;
    --win: #55cbac;
    --fail: #ff6b5c;
  }}
}}
:root[data-theme="dark"] {{
  --paper: #10161c;
  --panel: #161e26;
  --ink: #e8edf1;
  --muted: #90a0ac;
  --line: #29343e;
  --reg: #90a0ac;
  --dispatched: #55cbac;
  --emergency: #ff6b5c;
  --win: #55cbac;
  --fail: #ff6b5c;
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
  gap: 24px;
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
.lede {{ max-width: 68ch; color: var(--muted); font-size: 15px; margin: 4px 0 0; }}
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
.panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; }}
.case-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
.case-head h2 {{ font-family: var(--mono); font-size: 15px; font-weight: 600; margin: 0; }}
.why {{ font-size: 13px; color: var(--muted); margin: 0 0 16px; }}
.io {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 640px) {{ .io {{ grid-template-columns: 1fr; }} }}
.io-label {{
  font-family: var(--mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 6px;
}}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{
  text-align: left;
  font-weight: 500;
  color: var(--muted);
  padding: 6px 8px 6px 0;
  vertical-align: top;
  width: 45%;
  border-bottom: 1px solid var(--line);
}}
td {{ padding: 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }}
tr:last-child th, tr:last-child td {{ border-bottom: none; }}
.empty {{ color: var(--muted); font-family: var(--mono); font-size: 12px; }}
.mono {{ font-family: var(--mono); font-size: 12.5px; color: var(--muted); margin: 4px 0 0; }}
.tag {{
  font-family: var(--mono);
  font-size: 10.5px;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-block;
  letter-spacing: 0.02em;
  white-space: nowrap;
  margin: 2px 3px 2px 0;
}}
.tag--reg {{ background: color-mix(in srgb, var(--reg) 18%, transparent); color: var(--reg); }}
.tag--dispatched {{ background: color-mix(in srgb, var(--dispatched) 18%, transparent); color: var(--dispatched); }}
.tag--emergency {{ background: color-mix(in srgb, var(--emergency) 18%, transparent); color: var(--emergency); }}
.tag--win {{ background: color-mix(in srgb, var(--win) 18%, transparent); color: var(--win); }}
.tag--fail {{ background: color-mix(in srgb, var(--fail) 18%, transparent); color: var(--fail); }}
h2.section-title {{ font-family: var(--mono); font-size: 16px; font-weight: 600; margin: 0 0 4px; }}
.edge-cases {{ list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }}
.edge-case-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }}
.edge-case p {{ margin: 0; font-size: 13.5px; color: var(--muted); }}
footer {{ border-top: 1px solid var(--line); padding-top: 16px; font-size: 12.5px; color: var(--muted); }}
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
    <div class="eyebrow">Fleet Node · 이상신호 급파 대상 판정 검증</div>
    <h1>이상신호, 어느 로봇을 보낼 것인가</h1>
    <p class="lede">
      이상신호는 HMI가 보내는 페이로드 모양으로 두 경로가 갈립니다:
      <code>{{"robot"}}</code>만 있으면 <b>AMR 자체 감지</b> - 그 로봇의
      최근 위치를 그대로 이상 위치로 써서 제자리에 세웁니다.
      <code>{{"x","y"}}</code>만 있으면 <b>CCTV 감지</b> - 순찰 중이고
      이상신호 대응 중도 긴급정지 중도 아닌 로봇 중 통로 그래프
      최단경로로 가장 가까운 로봇을 골라 급파합니다. 두 경로 모두
      긴급정지 중인 로봇은 대상에서 제외됩니다 - 도킹 복귀와 달리
      운영자의 명시적 override가 아니라 자동 판단이기 때문입니다. 아래는
      test_anomaly_control.py에 정의된 케이스를 실제 실행한 결과입니다.
    </p>
    <div class="meta">
      <span>급파 후보 케이스 {len(CANDIDATE_CASES)}개</span>
      <span>자체 감지 위치 케이스 {len(SELF_LOCATION_CASES)}개</span>
      <span>생성 시각 {generated}</span>
    </div>
  </header>

  <section>
    <h2 class="section-title" style="margin-bottom:12px;">CCTV 감지 - 급파 후보 판정 (eligible_candidates)</h2>
    <div class="cases" style="display:flex;flex-direction:column;gap:16px;">
      {candidates_html}
    </div>
  </section>

  <section class="panel">
    <h2 class="section-title">AMR 자체 감지 - 위치 판정 (resolve_self_location)</h2>
    <p class="why">그 로봇의 최근 amcl_pose를 그대로 이상 위치로 쓴다 - 위치를 모르면 기본값으로 대체.</p>
    <ul class="edge-cases">
      {locations_html}
    </ul>
  </section>

  <footer>
    <code>src/test/code/build_anomaly_report.py</code>가
    <code>test_anomaly_control.py</code>의 케이스를 그대로 불러와
    <code>anomaly_control.eligible_candidates()</code> /
    <code>resolve_self_location()</code>을 실행한 결과로 생성함 - 손으로
    옮겨 적은 숫자가 아님. 단위 테스트:
    <code>src/test/code/test_anomaly_control.py</code>
    (<code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_anomaly_control.py -v</code>).
  </footer>
</div>
'''

os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
with open(OUT_HTML, 'w') as f:
    f.write(html)
print('wrote', OUT_HTML)
