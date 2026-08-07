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

export PYTHONPATH="$BACKEND_DIR/.venv/lib/python3.10/site-packages:${PYTHONPATH:-}"
export AMR_BRIDGE_BACKEND=ros2

echo "▶ ROS_DISTRO=$ROS_DISTRO · AMR_BRIDGE_BACKEND=$AMR_BRIDGE_BACKEND"
echo "▶ uvicorn 기동 (rclpy 연동) — http://localhost:8000"
exec python3 -m uvicorn app.main:app --port "${PORT:-8000}" "$@"
