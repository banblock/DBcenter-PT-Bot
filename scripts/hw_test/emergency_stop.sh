#!/usr/bin/env bash
# 시나리오 1: 전체 긴급정지 (stop=true)
# 등록된 모든 로봇에게 정지 신호가 나갑니다.
set -euo pipefail

echo "/backend/emergency_stop_all -> {\"stop\": true} 발행"
ros2 topic pub --once /backend/emergency_stop_all std_msgs/msg/String \
  "{data: '{\"stop\": true}'}"
