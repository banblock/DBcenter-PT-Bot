#!/usr/bin/env bash
# 이상신호 - 목적지로 가는 도중 로봇 자신의 카메라가 상황을 먼저 포착 (조기정지)
# 시나리오 5(CCTV 감지)로 급파한 로봇이 목적지에 도착하기 전에 실행해야
# 의미가 있음 - anomaly_moving 상태일 때 보낼 것.
# 사용법: ./anomaly_captured.sh robot3
set -euo pipefail

ns="${1:?사용법: $0 <robot_namespace>  (예: robot3)}"

echo "/backend/anomaly_captured -> {\"robot\": \"${ns}\"} 발행"
ros2 topic pub --once /backend/anomaly_captured std_msgs/msg/String \
  "{data: '{\"robot\": \"${ns}\"}'}"
