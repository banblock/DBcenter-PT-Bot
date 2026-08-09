# Fleet ↔ Control 통합 — 하드웨어 테스트 전 인수인계

이 세션(Claude Code)이 `/clear` 되기 직전에 작성한 문서입니다. 실제 로봇으로
테스트를 시작하기 전에 읽어주세요. 새 세션에서 다시 시작할 때 "HANDOFF.md 읽고
이어서 진행해줘"라고 하면 됩니다.

## 지금 어디에 있나

- 브랜치: **`fleet-control`** (로컬에만 있음, `origin`에 아직 안 올라감)
- `origin/deploy`에서 분기해서, `fleet` 브랜치와 `lee` 브랜치를 둘 다 병합해둔
  상태 (`--no-ff` 머지, 충돌 없었음 — 두 브랜치가 건드린 디렉터리가
  `src/fleet/`와 `src/control_amr/`로 겹치지 않았음)
- **커밋되지 않은 변경사항이 있습니다** (아래 "커밋 안 된 부분" 참고) — 사용자가
  의도적으로 커밋을 보류함

```
git log --oneline -6  (fleet-control 브랜치)
29ea37e Merge branch 'lee' into fleet-control
e093679 Merge branch 'fleet' into fleet-control
329236c first full funtion            (lee 쪽, control_amr 스캐폴딩)
ff2682d merge 찬용                     (lee 쪽)
a297d2d 긴급정지 해제·도킹 복귀·이상신호 두 경로 처리 구현  (fleet 쪽, origin에 푸시됨)
```

## 커밋 안 된 부분 (⚠️ 중요)

다음 두 파일은 워킹트리에만 있고 커밋되지 않았습니다:

- `src/control_amr/control_amr/control_node.py` — 수정본 (123줄 추가)
- `docs/control_emergency_dock_integration.md` — 신규 문서 (untracked)

**`git status`로 이게 그대로 남아있는지 먼저 확인하세요.** 다른 브랜치로
전환하거나 `git checkout .` 같은 걸 하면 이 변경사항이 날아갑니다. 커밋하려면
"방금 확인한 문서 기준으로 커밋해도 될까요?"라고 사용자에게 먼저 확인.

## 이번 세션에서 한 일 (시간 순)

1. **Fleet Node 쪽 (`fleet` 브랜치, origin에 이미 푸시됨, 커밋 `a297d2d`)**
   - 긴급정지 전체 해제(`stop:false`) 구현 — 이전엔 한 번 걸리면 안 풀렸음
   - 도킹 복귀(`/backend/dock`) 신규 구현 — `DOCK_STATIONS` 고정 좌표
     (`dock_control.py`), 긴급정지 중인 로봇도 도킹 대상에 포함하고 도킹과 함께
     긴급정지를 암묵 해제 (운영자의 명시적 override로 취급)
   - 이상신호 두 경로 구분 구현(`anomaly_control.py`) — AMR 자체감지({"robot"}만
     오면 그 로봇 자신의 위치를 이상 위치로 사용) vs CCTV 감지({"x","y"}만 오면
     최근접 로봇 급파), 두 경로 모두 긴급정지 중인 로봇은 후보에서 제외
   - 각 판정 로직은 rclpy 의존 없는 순수 함수로 분리해서 pytest로 검증 + HTML
     리포트로 시각화 (`src/test/code/test_*.py`, `src/test/result/*.html`)
   - `docs/fleet_node_pipeline.drawio` 갱신 (섹션 ⑨ 긴급정지 해제 반영, 섹션 ⑩
     도킹 복귀 신규 추가, 섹션 ⑦ 이상신호 두 경로 반영)
   - AMR 자체감지 + CCTV감지 동시 발생 시 중복 급파 가능성을 분석했으나,
     **사용자가 "그런 상황을 안 만들기로 했다"고 결정 → 코드로는 처리 안 함**
     (HMI/운영 쪽에서 그 상황 자체를 안 만들도록 하기로 함, 참고용 기록)

2. **`fleet` → `fleet-control` 머지, `lee`(Control, 다른 팀) → `fleet-control` 머지**
   - 둘 다 충돌 없이 깔끔하게 병합됨

3. **Control Node 쪽 갭 확인** — `lee` 브랜치의 `control_node.py`에 Fleet이 새로
   만든 `/fleet/<ns>/emergency_stop`, `/fleet/<ns>/dock` 토픽을 구독하는 코드가
   전혀 없어서 통합 테스트가 불가능한 상태였음을 확인

4. **`control_node.py`에 긴급정지/도킹 구독 직접 추가** (커밋 안 됨, 위 참고)
   - 자세한 내용은 `docs/control_emergency_dock_integration.md`에 전부 정리해둠
     (아래 "꼭 읽어야 할 문서" 참고)

## 꼭 읽어야 할 문서 (순서대로)

1. **`docs/control_emergency_dock_integration.md`** — 이번에 `control_node.py`에
   무엇을, 왜 추가했는지 전부 설명. Lee 팀 리뷰용으로 작성함. **하드웨어
   테스트하기 전에 반드시 읽을 것** — 특히 "4. 알려진 미구현/확인이 필요한 부분"
   섹션.
2. `docs/fleet_node_pipeline.drawio` — Fleet Node 쪽 전체 파이프라인 다이어그램
   (draw.io로 열기). 섹션 ⑨(긴급정지), ⑩(도킹복귀), ⑦(이상신호)이 이번 세션
   작업분.
3. `src/test/result/*.html` (status_report, dock_report, anomaly_report) — Fleet
   판정 로직 검증 리포트, 브라우저로 열어보면 됨.

## 하드웨어 테스트 전 확인해야 할 것 (`control_emergency_dock_integration.md`에서 발췌)

- **도킹 이후 재순찰(언도킹) 트리거가 없음** — 지금 로봇이 도킹하면 그 세션
  안에서는 계속 도킹 상태로 남습니다. Fleet 쪽에 로봇별 재순찰 신호가 아직 없어서
  의도된 동작입니다 (버그 아님, 다음 작업 항목).
- **`_handle_dock()`이 실제 `TurtleBot4Navigator`/`navigator.dock()`으로 실행
  검증된 적이 없습니다** — 컴파일 통과 + 코드 리딩으로만 확인함. 처음 테스트할 때
  주의 깊게 지켜볼 것.
- **`_move_to()` 인터럽트 우선순위**: `emergency_stop > collision_risk > dock >
  anomaly`로 정했는데, collision_risk를 emergency_stop보다 아래 둔 게 맞는지
  재검토 여지 있음 (문서에 명시해둠).
- **발견한 별개 이슈**: `state_flow.py`가 `/fleet/<ns>/state`에 상태를
  발행하는데, Fleet의 `robot_status.py`는 `/control/<ns>_State`를 씀 — 토픽명이
  달라서 서로 안 이어질 수 있음. 이번엔 안 건드림, 확인 필요.

## 세션 전반의 작업 스타일 (참고)

- 커밋은 사용자가 명시적으로 요청할 때만 함 (이번 세션 내내 그렇게 함)
- 브랜치/토픽 네이밍처럼 여러 팀에 영향 주는 결정은 먼저 물어보고 진행함
  (예: 도킹 대상 범위, 긴급정지 해제 시 상태 처리 등)
- `fleet` 브랜치는 이미 `origin`에 푸시돼 있음. `fleet-control`은 아직 로컬에만
  있음 — 푸시하려면 반드시 `git push -u origin fleet-control`처럼 명시적으로
  브랜치명을 지정할 것 (이 브랜치를 `git checkout -b`로 만들 때 실수로
  `origin/deploy`에 업스트림이 잡혔다가 수동으로 해제한 이력 있음 — 지금은
  업스트림 없는 상태이므로 `git push`만 치면 에러 남, 안전함)
