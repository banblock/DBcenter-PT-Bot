#!/usr/bin/env bash
# 시나리오 0: /control/<ns>_State 실시간 확인
# 사용법: ./watch_state.sh robot3
set -euo pipefail

ns="${1:?사용법: $0 <robot_namespace>  (예: robot3)}"
topic="/control/${ns}_State"

echo "watching ${topic} (Ctrl+C로 종료)"
ros2 topic echo "${topic}"
