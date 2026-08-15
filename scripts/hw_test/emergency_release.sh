#!/usr/bin/env bash
# 시나리오 2: 전체 긴급정지 해제 (stop=false)
# 현재 긴급정지 중인 로봇만 골라 해제 신호가 나갑니다.
set -euo pipefail

echo "/backend/emergency_stop_all -> {\"stop\": false} 발행"
ros2 topic pub --once /backend/emergency_stop_all std_msgs/msg/String \
  "{data: '{\"stop\": false}'}"
