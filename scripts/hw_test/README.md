# 하드웨어 테스트용 시나리오 스크립트

`docs/hardware_test_checklist.md`의 시나리오 0~5에 대응하는 실행 스크립트입니다.
매번 `ros2 topic pub` JSON을 손으로 치다가 따옴표 실수하는 걸 줄이려고
만들었습니다. 실제 로봇을 움직이는 명령이므로, 각 스크립트를 실행하기 전에
로봇 주변 안전 확인 후 실행하세요.

| 스크립트 | 체크리스트 시나리오 | 설명 |
|---|---|---|
| `watch_state.sh <ns>` | 0 | `/control/<ns>_State` 실시간 echo |
| `emergency_stop.sh` | 1 | 전체 긴급정지 |
| `emergency_release.sh` | 2 | 전체 긴급정지 해제 |
| `dock.sh [<ns> ...]` | 3 | 도킹 복귀 (인자 없으면 전체 대상) |
| `anomaly_self.sh <ns>` | 4 | 이상신호 - AMR 자체감지 |
| `anomaly_cctv.sh [x] [y]` | 5 | 이상신호 - CCTV 감지 (좌표 생략 시 기본값) |
| `anomaly_done.sh <ns>` | 4/5 마무리 | 이상신호 대응 완료 처리 |

사용 전 실행 권한 부여:
```
chmod +x scripts/hw_test/*.sh
```

시나리오 6(인터럽트 우선순위 충돌)은 위 스크립트들을 조합해서 타이밍을 맞춰
직접 실행하는 시나리오라 별도 스크립트 없음 — 체크리스트 참고.
