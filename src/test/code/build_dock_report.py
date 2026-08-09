#!/usr/bin/env python3
"""src/test/result/dock_report.html 생성 - 도킹 복귀 대상 로봇 판정
(dock_control.py)이 test_dock_control.py의 CASES를 실제로 어떻게
처리하는지 표로 시각화하는 검증 리포트.

    python3 build_dock_report.py

결과는 전부 dock_control.resolve_dock_targets()의 실제 반환값이다 -
손으로 옮겨적지 않았다."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet import dock_control

sys.path.insert(0, os.path.dirname(__file__))
from test_dock_control import CASES

HERE = os.path.dirname(__file__)
OUT_HTML = os.path.join(HERE, '..', 'result', 'dock_report.html')


def robot_tags(names, cls):
    if not names:
        return '<span class="empty">없음</span>'
    return ' '.join(f'<span class="tag {cls}">{n}</span>' for n in names)


# ---- 케이스별 판정 결과 -------------------------------------------------
# test_dock_control.py의 test_resolve_dock_targets()와 완전히 같은 호출이다
# - 여기서 나오는 결과가 곧 그 테스트가 검증한 값.

case_sections = []
for case in CASES:
    dispatched, unknown, released_from_emergency, skipped_no_station = (
        dock_control.resolve_dock_targets(
            case['requested'], case['registered_robots'], case['emergency_stopped']))

    if case['requested'] is None:
        ok = (set(dispatched) == set(case['expected']['dispatched']) and
              set(skipped_no_station) == set(case['expected']['skipped_no_station']) and
              set(released_from_emergency) == set(case['expected']['released_from_emergency']))
    else:
        ok = (dispatched == case['expected']['dispatched'] and
              unknown == case['expected']['unknown'] and
              released_from_emergency == case['expected']['released_from_emergency'] and
              skipped_no_station == case['expected']['skipped_no_station'])

    requested_label = (
        '전체 (robots 필드 없음)' if case['requested'] is None
        else ', '.join(case['requested']))

    case_sections.append(f'''
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
            <tr><th>요청 로봇 (robots)</th><td>{requested_label}</td></tr>
            <tr><th>등록된 로봇</th><td>{robot_tags(sorted(case['registered_robots']), 'tag--reg')}</td></tr>
            <tr><th>긴급정지 중</th><td>{robot_tags(sorted(case['emergency_stopped']), 'tag--emergency')}</td></tr>
          </table>
        </div>
        <div class="io-out">
          <div class="io-label">판정 결과</div>
          <table>
            <tr><th>도킹 명령 발행 (dispatched)</th><td>{robot_tags(dispatched, 'tag--dispatched')}</td></tr>
            <tr><th>미등록 (unknown)</th><td>{robot_tags(unknown, 'tag--unknown')}</td></tr>
            <tr><th>긴급정지 암묵 해제 (released_from_emergency)</th><td>{robot_tags(released_from_emergency, 'tag--emergency')}</td></tr>
            <tr><th>도킹 스테이션 미설정 (skipped_no_station)</th><td>{robot_tags(skipped_no_station, 'tag--unknown')}</td></tr>
          </table>
        </div>
      </div>
    </article>''')

cases_html = '\n'.join(case_sections)

# ---- 도킹 스테이션 좌표 -------------------------------------------------
TODO_YAW_NOTE = '<span class="todo">실측 전 임시값</span>'


def _station_row(ns, s):
    yaw_note = f' {TODO_YAW_NOTE}' if s['yaw'] == 0.0 else ''
    return f'<tr><td>{ns}</td><td>{s["x"]}</td><td>{s["y"]}</td><td>{s["yaw"]}{yaw_note}</td></tr>'


station_rows = '\n          '.join(
    _station_row(ns, s) for ns, s in dock_control.DOCK_STATIONS.items())

generated = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

html = f'''<!doctype html>
<title>도킹 복귀 대상 판정 검증 — dock_control.py</title>
<meta name="description" content="Fleet Node가 /backend/dock 요청을 받아 실제로 도킹 명령을 내릴 로봇을 어떻게 걸러내는지 실제 시나리오로 검증한 리포트.">
<style>
:root {{
  --paper: #f2f5f7;
  --panel: #ffffff;
  --ink: #1b242c;
  --muted: #5b6b78;
  --line: #c6d0d8;
  --reg: #5b6b78;
  --dispatched: #1d8a72;
  --unknown: #b3691e;
  --emergency: #b3261e;
  --win: #1d8a72;
  --fail: #b3261e;
  --todo: #b3691e;
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
    --unknown: #e8a166;
    --emergency: #ff6b5c;
    --win: #55cbac;
    --fail: #ff6b5c;
    --todo: #e8a166;
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
  --unknown: #e8a166;
  --emergency: #ff6b5c;
  --win: #55cbac;
  --fail: #ff6b5c;
  --todo: #e8a166;
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
.todo {{ color: var(--todo); font-family: var(--mono); font-size: 11px; }}
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
.tag--unknown {{ background: color-mix(in srgb, var(--unknown) 18%, transparent); color: var(--unknown); }}
.tag--emergency {{ background: color-mix(in srgb, var(--emergency) 18%, transparent); color: var(--emergency); }}
.tag--win {{ background: color-mix(in srgb, var(--win) 18%, transparent); color: var(--win); }}
.tag--fail {{ background: color-mix(in srgb, var(--fail) 18%, transparent); color: var(--fail); }}
h2.section-title {{ font-family: var(--mono); font-size: 16px; font-weight: 600; margin: 0 0 4px; }}
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
    <div class="eyebrow">Fleet Node · 도킹 복귀 대상 판정 검증</div>
    <h1>도킹 복귀 명령, 누구에게 보낼 것인가</h1>
    <p class="lede">
      <code>/backend/dock</code> 요청이 오면 Fleet은 실제 이동 명령을
      내리기 전에 대상 로봇을 걸러냅니다: robots 필드로 특정 로봇을
      지정할 수도, 비워서 등록된 전체를 지정할 수도 있습니다. 등록되지
      않은 로봇이나 도킹 스테이션 좌표가 없는 로봇은 대상에서 빠지지만,
      긴급정지 중인 로봇은 운영자의 명시적 복귀 명령이라 오히려 대상에
      포함되고 도킹과 함께 긴급정지도 암묵적으로 해제됩니다. 실제
      이동(goToPose)과 도킹 액션 호출은 Control Node 몫이라 여기서는
      다루지 않습니다. 아래는
      test_dock_control.py에 정의된 케이스를
      dock_control.resolve_dock_targets()로 실제 실행한 결과입니다.
    </p>
    <div class="meta">
      <span>케이스 {len(CASES)}개</span>
      <span>등록된 도킹 스테이션 {len(dock_control.DOCK_STATIONS)}개</span>
      <span>생성 시각 {generated}</span>
    </div>
  </header>

  <section class="cases">
    {cases_html}
  </section>

  <section class="panel">
    <h2 class="section-title">로봇별 도킹 스테이션 대기 지점</h2>
    <p class="why">
      goToPose로 이동할 목표 좌표. 정밀한 도킹 자세가 아니라 실제
      irobot_create_msgs/action/Dock 액션(IR 비콘 기반 자동 접근)이
      맡을 수 있는 근방이면 된다.
    </p>
    <table>
      <thead><tr><th>로봇</th><th>x</th><th>y</th><th>yaw</th></tr></thead>
      <tbody>
        {station_rows}
      </tbody>
    </table>
  </section>

  <footer>
    <code>src/test/code/build_dock_report.py</code>가
    <code>test_dock_control.py</code>의 <code>CASES</code>를 그대로 불러와
    <code>dock_control.resolve_dock_targets()</code>를 실행한 결과로
    생성함 - 손으로 옮겨 적은 숫자가 아님. 단위 테스트:
    <code>src/test/code/test_dock_control.py</code>
    (<code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_dock_control.py -v</code>).
  </footer>
</div>
'''

os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
with open(OUT_HTML, 'w') as f:
    f.write(html)
print('wrote', OUT_HTML)
