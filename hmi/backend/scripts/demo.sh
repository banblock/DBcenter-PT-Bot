#!/usr/bin/env bash
# 코드 리뷰 데모 운영 명령 모음.  사용:  bash scripts/demo.sh <명령>
#
#   fresh            프로세스 정리 + DB 리셋(가장 자주 씀 — 데모 시작 전 1회)
#   kill             남은 서버/로봇 노드 강제 종료 (포트 8000 정리)
#   reset            DB 드롭+재생성+시드
#   seed             로봇을 online/IDLE 로 (loopback 데모용; ros2 는 로봇노드가 자동 온라인)
#   backend          백엔드 기동 (ROS2 연동 모드)  ← 실 3계층 데모
#   backend-loopback 백엔드 기동 (loopback: ROS 없이 §10 로그만)
#   robot [ids...]   테스트 로봇 ROS2 노드 (기본 AMR-01 AMR-02)
#   echo [ns]        ros2 topic echo /backend/<ns>/command (기본 amr_1) — 원시 DDS 확인
#   db               DB Browser(GUI) 로 amr.db 열기
#   db-cli [SQL]     터미널로 DB 보기 (요약 / tables / 임의 SQL)
set -eo pipefail

BE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BE"
PY="$BE/.venv/bin/python"
DB="$BE/data/amr.db"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"

case "${1:-help}" in
  kill)
    pkill -9 -f "uvicorn app.main" 2>/dev/null || true
    pkill -9 -f "fake_robot_node" 2>/dev/null || true
    sleep 1
    if ss -ltn 2>/dev/null | grep -q ":8000"; then echo "⚠ 8000 아직 점유"; else echo "✓ 포트 8000 정리됨"; fi ;;
  reset)
    printf 'yes\n' | "$PY" scripts/init_db.py --drop >/dev/null 2>&1
    echo "✓ DB 리셋 완료 (미션·이벤트 0)" ;;
  seed)
    "$PY" -m scripts.demo_seed_online ;;
  fresh)
    bash "$0" kill; bash "$0" reset
    echo "→ 준비 완료. 이제:  bash scripts/demo.sh backend   /   bash scripts/demo.sh robot" ;;
  backend)
    exec bash scripts/run_backend_ros2.sh ;;
  backend-loopback)
    exec env AMR_BRIDGE_BACKEND=loopback AMR_HEARTBEAT_TIMEOUT_SEC=99999 \
      "$BE/.venv/bin/uvicorn" app.main:app --port 8000 ;;
  robot)
    # shellcheck disable=SC1090
    source "$ROS_SETUP"; exec python3 -u scripts/fake_robot_node.py "${@:2}" ;;
  echo)
    # shellcheck disable=SC1090
    source "$ROS_SETUP"; exec ros2 topic echo "/backend/${2:-amr_1}/command" ;;
  db)
    sqlitebrowser "$DB" >/dev/null 2>&1 & echo "✓ DB Browser 실행: $DB (잠금 경고 뜨면 File→Open Read Only)" ;;
  db-cli)
    "$PY" scripts/db.py "${@:2}" ;;
  *)
    grep -E "^#   " "$0" | sed 's/^#   //' ;;
esac
