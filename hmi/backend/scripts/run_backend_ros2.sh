#!/usr/bin/env bash
# 백엔드를 ROS2(rclpy) 연동 모드로 기동한다.
#
# 현재 venv 는 격리형이라 rclpy 가 안 보인다. venv 를 건드리지 않고, ROS2 를 source 한
# 뒤 PYTHONPATH 에 venv 패키지를 얹어 시스템 python3.10 으로 uvicorn 을 실행한다.
# (fastapi/sqlalchemy = venv, rclpy = /opt/ros/humble — 둘 다 python3.10 이라 함께 임포트됨)
#
# 사용:  bash scripts/run_backend_ros2.sh
# 주의: ROS setup.bash 는 미설정 변수를 참조하므로 `set -u`(nounset) 를 쓰면 안 된다.
set -eo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # → hmi/backend
cd "$BACKEND_DIR"

ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
# shellcheck disable=SC1090
source "$ROS_SETUP"

# 비전 통합용: colcon 워크스페이스(install/setup.bash)를 source 하면 patrol_interfaces(CamState/
# CheckGate)를 백엔드가 import/구독할 수 있다. 아직 빌드 안 했으면 건너뛴다.
#   빌드:  cd <repo> && colcon build --packages-select patrol_interfaces vision_detection
# colcon 을 repo 루트에서 돌리면 install/, src 안에서 돌리면 src/install/ 이 생긴다.
# 둘 다 후보로 잡아 먼저 존재하는 쪽을 쓴다(WS_SETUP 로 명시 지정하면 그걸 우선).
if [ -z "${WS_SETUP:-}" ]; then
  for _cand in "$BACKEND_DIR/../../install/setup.bash" "$BACKEND_DIR/../../src/install/setup.bash"; do
    if [ -f "$_cand" ]; then WS_SETUP="$_cand"; break; fi
  done
fi
if [ -n "${WS_SETUP:-}" ] && [ -f "$WS_SETUP" ]; then
  # shellcheck disable=SC1090
  source "$WS_SETUP"
  echo "▶ 워크스페이스 source: $WS_SETUP (patrol_interfaces 사용 가능)"
  export AMR_VISION_ENABLED="${AMR_VISION_ENABLED:-true}"
else
  echo "▶ 워크스페이스 미빌드(install/setup.bash 없음) → 비전 없이 기동. colcon build 후 재실행 시 자동 연동."
fi

# ── DDS 디스커버리 모드 (★백엔드↔비전·robot3 를 같은 모드로) ────────────────
# 백엔드·비전 노드·로봇이 같은 디스커버리 모드가 아니면 /detection/cam_state 를 못 받아
# AMR/CCTV 감지가 백엔드까지 안 온다(로그에 cam= 안 찍힘). 랩 discovery 서버(.35)가 살아
# 있으면 서버 모드로 맞추고(비전·robot3 와 동일), 꺼져 있으면 로컬 모드로 둔다(=.bashrc 로직).
#   - 이미 ROS_DISCOVERY_SERVER 를 잡고 들어왔으면 그 값을 존중(중복 source 안 함).
#   - DISCOVERY_SETUP / DISCOVERY_SERVER_IP 로 경로·서버 IP 를 덮어쓸 수 있다.
DISCOVERY_SETUP="${DISCOVERY_SETUP:-/etc/turtlebot4_discovery/setup.bash}"
DISCOVERY_SERVER_IP="${DISCOVERY_SERVER_IP:-192.168.101.35}"
if [ -n "${ROS_DISCOVERY_SERVER:-}" ]; then
  echo "▶ DDS 서버 모드(기존 env) — ROS_DISCOVERY_SERVER=$ROS_DISCOVERY_SERVER"
elif [ -f "$DISCOVERY_SETUP" ] && ping -c 1 -W 1 "$DISCOVERY_SERVER_IP" >/dev/null 2>&1; then
  # shellcheck disable=SC1090
  source "$DISCOVERY_SETUP"
  echo "▶ DDS 서버 모드 — ROS_DISCOVERY_SERVER=${ROS_DISCOVERY_SERVER:-?} (비전·robot3 와 동일 모드)"
else
  echo "▶ DDS 로컬 모드 — discovery 서버($DISCOVERY_SERVER_IP) 미응답/미설치. 한 PC 통합용."
fi

export PYTHONPATH="$BACKEND_DIR/.venv/lib/python3.10/site-packages:${PYTHONPATH:-}"
export AMR_BRIDGE_BACKEND=ros2

echo "▶ ROS_DISTRO=$ROS_DISTRO · AMR_BRIDGE_BACKEND=$AMR_BRIDGE_BACKEND · AMR_VISION_ENABLED=${AMR_VISION_ENABLED:-false} · ROS_DISCOVERY_SERVER=${ROS_DISCOVERY_SERVER:-(로컬)}"
echo "▶ uvicorn 기동 (rclpy 연동) — http://localhost:8000"
exec python3 -m uvicorn app.main:app --port "${PORT:-8000}" "$@"
