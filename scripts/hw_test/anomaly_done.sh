#!/usr/bin/env bash
# 이상신호 대응 완료 신호 (시나리오 4/5 마무리에 사용)
# 사용법: ./anomaly_done.sh robot3
set -euo pipefail

ns="${1:?사용법: $0 <robot_namespace>  (예: robot3)}"

echo "/fleet/anomaly_done -> {\"robot\": \"${ns}\"} 발행"
ros2 topic pub --once /fleet/anomaly_done std_msgs/msg/String \
  "{data: '{\"robot\": \"${ns}\"}'}"
