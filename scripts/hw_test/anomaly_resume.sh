#!/usr/bin/env bash
# 이상신호 대응 - 운영자 "재개" 결정 (시나리오 4/5 마무리, anomaly_waiting 대기 해제)
# 도킹으로 보내고 싶으면 이거 대신 dock.sh를 쓸 것 - 재개/도킹 둘 중
# 하나만 보내면 됨.
# 사용법: ./anomaly_resume.sh robot3
set -euo pipefail

ns="${1:?사용법: $0 <robot_namespace>  (예: robot3)}"

echo "/backend/anomaly_resume -> {\"robot\": \"${ns}\"} 발행"
ros2 topic pub --once /backend/anomaly_resume std_msgs/msg/String \
  "{data: '{\"robot\": \"${ns}\"}'}"
