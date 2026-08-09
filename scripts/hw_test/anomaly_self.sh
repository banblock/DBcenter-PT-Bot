#!/usr/bin/env bash
# 시나리오 4: 이상신호 - AMR 자체감지 (로봇 자신의 위치를 이상 위치로 사용)
# 사용법: ./anomaly_self.sh robot3
set -euo pipefail

ns="${1:?사용법: $0 <robot_namespace>  (예: robot3)}"

echo "/fleet/anomaly_trigger -> {\"robot\": \"${ns}\"} 발행 (AMR 자체감지 경로)"
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String \
  "{data: '{\"robot\": \"${ns}\"}'}"
