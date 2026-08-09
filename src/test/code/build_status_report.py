#!/usr/bin/env python3
"""src/test/result/status_report.html 생성 - 로봇 상태 퍼블리시
(robot_status.py)가 test_robot_status.py의 SCENARIOS/EDGE_CASES를 실제로
어떻게 판정하는지 타임라인 + 표로 시각화한 검증 리포트.

    python3 build_status_report.py

상태값과 변경 여부는 전부 robot_status.detect_changes() /
compute_status()의 실제 반환값이다 - 손으로 옮겨적지 않았다.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet import robot_status

sys.path.insert(0, os.path.dirname(__file__))
from test_robot_status import SCENARIOS, EDGE_CASES

HERE = os.path.dirname(__file__)
OUT_HTML = os.path.join(HERE, '..', 'result', 'status_report.html')

STATUS_TAG_CLASS = {
    robot_status.IDLE: 'tag--idle',
    robot_status.PATROLLING: 'tag--patrol',
    robot_status.DISPATCHING: 'tag--dispatch',
    robot_status.EMERGENCY_STOP: 'tag--emergency',
}


def status_badge(status):
    return f'<span class="tag {STATUS_TAG_CLASS.get(status, "")}">{status}</span>'


# ---- 시나리오 타임라인 ------------------------------------------------
# detect_changes()를 SCENARIOS에 정의된 순서 그대로 재생한다 -
# test_robot_status.py의 test_scenarios_follow_expected_transitions()와
# 완전히 같은 호출 시퀀스라서, 여기서 나오는 상태/변경 여부가 곧 그
# 테스트가 실제로 검증한 값이다.

scenario_sections = []
for case in SCENARIOS:
    ns = case['robot']
    previous = {}
    step_cards = []
    for i, step in enumerate(case['steps']):
        changes = robot_status.detect_changes(
            [ns], step['missions'], step['anomaly_busy'], step['emergency_stopped'], previous)
        status = previous[ns]
        changed = any(c_ns == ns for c_ns, _ in changes)
        assert status == step['expected_status'] and changed == step['expected_change'], (
            'report와 test_robot_status.py의 SCENARIOS 결과가 어긋남 - '
            '테스트가 실패 중이거나 시나리오 정의가 바뀌었는데 여기 반영이 안 됨')
        arrow = '' if i == 0 else '<span class="arrow">&rarr;</span>'
        publish_note = '퍼블리시함' if changed else '변화 없음 (퍼블리시 생략)'
        step_cards.append(f'''
        {arrow}
        <div class="step {"step--changed" if changed else "step--same"}">
          <div class="step-when">{step['when']}</div>
          {status_badge(status)}
          <div class="step-note">{publish_note}</div>
        </div>''')
    scenario_sections.append(f'''
    <article class="panel scenario">
      <h2>{case['label']}</h2>
      <p class="why">{case['why']}</p>
      <div class="timeline">{''.join(step_cards)}</div>
    </article>''')

scenarios_html = '\n'.join(scenario_sections)

# ---- 예외/우선순위 케이스 ----------------------------------------------
edge_case_rows = []
for ec in EDGE_CASES:
    result = robot_status.compute_status(
        ec['ns'], ec['missions'], ec['anomaly_busy'], ec['emergency_stopped'])
    ok = result == ec['expected']
    edge_case_rows.append(f'''
      <li class="edge-case">
        <div class="edge-case-head">
          <span class="tag {"tag--win" if ok else "tag--fail"}">{"검증됨" if ok else "실패"}</span>
          <strong>{ec['title']}</strong>
        </div>
        <p>{ec['detail']} 결과: {status_badge(result)}</p>
      </li>''')
edge_cases_html = '\n'.join(edge_case_rows)

# ---- UI팀 robot_state 15개 상태 어휘 대조표 ----------------------------
# 2026-08-06 스크린샷(~/Downloads/Screenshot from 2026-08-06 12-44-06.png)
# 원본 표를 그대로 옮기되, Fleet이 실제로 판정하는 상태와 아닌 상태를
# 구분해서 표시한다 - "이 리포트가 UI 스펙 전체를 구현했다"는 오해를
# 막기 위한 목적이 크다.
VOCAB_ROWS = [
    (robot_status.OFFLINE, '연결 끊김', 'Control Node (heartbeat 필요, Fleet 미구독)'),
    (robot_status.MAPPING, '맵 생성 중 (SLAM)', 'Control Node (Fleet 관여 없음)'),
    (robot_status.IDLE, '대기', 'Fleet ✓ (미션 없음)'),
    (robot_status.PATROLLING, '순찰 중', 'Fleet ✓ (미션 보유, 이상신호 대응 아님)'),
    (robot_status.PATROL_PAUSED, '순찰 일시정지', 'Fleet 예정 (남은 작업 4번 - UI 명령 연동)'),
    (robot_status.DISPATCHING, '이상지점 이동 중', 'Fleet ✓ (anomaly_busy 등록 시점)'),
    (robot_status.INSPECTING, '점검 중 (차단기 점검)', 'Control Node (nav 도착 피드백 필요)'),
    (robot_status.REPORTING, '결과 전송 중', 'Control Node (검증 완료 시점 필요)'),
    (robot_status.RESUMING, '순찰 복귀 중', 'Control Node (중단 지점 도달 필요)'),
    (robot_status.CHARGING, '충전 중', 'Control Node (배터리 정보 필요, Fleet 미구독)'),
    (robot_status.EMERGENCY_STOP, '긴급정지', 'Fleet ✓ (emergency_stopped 등록/해제 시점)'),
    (robot_status.ERROR, '오류', 'Control Node (내비게이션 실패 등)'),
    (robot_status.ALERTING, '현장 경보 중', 'Control Node (관제 ACK 필요)'),
    (robot_status.UNDOCKING, '출발 준비', 'Control Node (도킹 상태 필요, Fleet 미구독)'),
    (robot_status.DOCKING, '복귀·도킹 중', 'Control Node (도킹 상태 필요, Fleet 미구독)'),
]
vocab_rows_html = '\n          '.join(
    f'<tr class="{"row--owned" if "Fleet ✓" in owner else ""}">'
    f'<td>{status_badge(value)}</td><td>{label}</td><td>{owner}</td></tr>'
    for value, label, owner in VOCAB_ROWS
)

generated = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

html = f'''<!doctype html>
<title>로봇 상태 퍼블리시 검증 — robot_status.py</title>
<meta name="description" content="Fleet Node가 IDLE/PATROLLING/DISPATCHING 3개 상태를 판정해 /control/<ns>_State에 퍼블리시하는 로직을 실제 시나리오로 검증한 리포트.">
<style>
:root {{
  --paper: #f2f5f7;
  --panel: #ffffff;
  --ink: #1b242c;
  --muted: #5b6b78;
  --line: #c6d0d8;
  --idle: #5b6b78;
  --patrol: #2e6fa8;
  --dispatch: #b3691e;
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
    --idle: #90a0ac;
    --patrol: #6fb3e8;
    --dispatch: #e8a166;
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
  --idle: #90a0ac;
  --patrol: #6fb3e8;
  --dispatch: #e8a166;
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
.scenario h2 {{ font-family: var(--mono); font-size: 15px; font-weight: 600; margin: 0 0 6px; }}
.why {{ font-size: 13px; color: var(--muted); margin: 0 0 16px; }}
.timeline {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
.arrow {{ color: var(--muted); font-size: 18px; }}
.step {{
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  min-width: 160px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.step--changed {{ border-color: var(--win); }}
.step-when {{ font-size: 12px; color: var(--muted); }}
.step-note {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}
.step--changed .step-note {{ color: var(--win); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
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
td {{ padding: 8px 8px 8px 0; border-bottom: 1px solid var(--line); vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
tr.row--owned td {{ color: var(--ink); font-weight: 600; }}
.tag {{
  font-family: var(--mono);
  font-size: 10.5px;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-block;
  letter-spacing: 0.02em;
  white-space: nowrap;
}}
.tag--idle {{ background: color-mix(in srgb, var(--idle) 18%, transparent); color: var(--idle); }}
.tag--patrol {{ background: color-mix(in srgb, var(--patrol) 18%, transparent); color: var(--patrol); }}
.tag--dispatch {{ background: color-mix(in srgb, var(--dispatch) 18%, transparent); color: var(--dispatch); }}
.tag--emergency {{ background: color-mix(in srgb, var(--emergency) 18%, transparent); color: var(--emergency); }}
.tag--win {{ background: color-mix(in srgb, var(--win) 18%, transparent); color: var(--win); }}
.tag--fail {{ background: color-mix(in srgb, var(--fail) 18%, transparent); color: var(--fail); }}
h2.section-title {{ font-family: var(--mono); font-size: 16px; font-weight: 600; margin: 0 0 4px; }}
.edge-cases {{ list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }}
.edge-case-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }}
.edge-case p {{ margin: 0; font-size: 13.5px; color: var(--muted); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
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
    <div class="eyebrow">Fleet Node · 로봇 상태 퍼블리시 검증</div>
    <h1>Fleet은 로봇 상태를 어디까지 아는가</h1>
    <p class="lede">
      UI팀이 준 robot_state 15개 상태 중 Fleet Node가 지금 실제로
      판정하는 건 EMERGENCY_STOP / IDLE / PATROLLING / DISPATCHING
      4개뿐입니다. 미션 배정 여부, 이상신호 대응 여부, 긴급정지 여부만으로
      판정하고, 1Hz 폴링으로 변화가 있을 때만
      <code>/control/&lt;robot&gt;_State</code>에 퍼블리시합니다. 긴급정지는
      해제되기 전까지 다른 상태를 전부 덮어쓰고 유지되며, 해제되면
      emergency_stopped 세트에서 빠져 다음 순위(이상신호 대응/미션 보유
      여부)로 자연스럽게 떨어집니다. 아래는 test_robot_status.py에 정의된
      시나리오를 robot_status.py로 실제 실행한 결과입니다.
    </p>
    <div class="meta">
      <span>Fleet이 판정하는 상태 {len(robot_status.FLEET_KNOWN_STATES)}개 / UI 전체 어휘 {len(VOCAB_ROWS)}개</span>
      <span>시나리오 {len(SCENARIOS)}개 + 우선순위 케이스 {len(EDGE_CASES)}개</span>
      <span>생성 시각 {generated}</span>
    </div>
  </header>

  <section class="scenarios">
    {scenarios_html}
  </section>

  <section class="panel">
    <h2 class="section-title">우선순위 케이스</h2>
    <p class="why">compute_status()의 우선순위 규칙(이상신호 대응 &gt; 미션 보유 &gt; 둘 다 아님)이 의도대로 동작하는지 확인.</p>
    <ul class="edge-cases">
      {edge_cases_html}
    </ul>
  </section>

  <section class="panel">
    <h2 class="section-title">UI팀 robot_state 어휘 대조표</h2>
    <p class="why">
      2026-08-06 스크린샷 원본 표 기준. 굵게 표시된 3개가 Fleet이 지금
      퍼블리시하는 상태고, 나머지는 Control Node가 같은
      <code>/control/&lt;robot&gt;_State</code> 토픽에 이어서 퍼블리시할
      몫이거나(순차적 소유권 이양) Fleet의 다음 작업(도킹 복귀, 1시간
      자동 순찰 재개 등 UI 명령 연동)으로 예정된 상태입니다.
    </p>
    <table>
      <thead><tr><th>상태값</th><th>UI 한글 표기</th><th>담당</th></tr></thead>
      <tbody>
        {vocab_rows_html}
      </tbody>
    </table>
  </section>

  <footer>
    <code>src/test/code/build_status_report.py</code>가
    <code>test_robot_status.py</code>의 <code>SCENARIOS</code>/<code>EDGE_CASES</code>를
    그대로 불러와 <code>robot_status.detect_changes()</code> /
    <code>compute_status()</code>를 실행한 결과로 생성함 - 손으로 옮겨
    적은 숫자가 아님. 단위 테스트: <code>src/test/code/test_robot_status.py</code>
    (<code>PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_robot_status.py -v</code>).
  </footer>
</div>
'''

os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
with open(OUT_HTML, 'w') as f:
    f.write(html)
print('wrote', OUT_HTML)
