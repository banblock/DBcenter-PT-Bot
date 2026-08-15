#!/usr/bin/env bash
# 시나리오 5: 이상신호 - CCTV 감지 (좌표만 오면 가장 가까운 로봇을 급파)
# 사용법:
#   ./anomaly_cctv.sh              -> 기본 좌표(DEFAULT_ANOMALY) 사용
#   ./anomaly_cctv.sh -2.33 0.0313 -> 좌표 직접 지정
set -euo pipefail

x="${1:--2.33}"
y="${2:-0.0313}"

echo "/fleet/anomaly_trigger -> {\"x\": ${x}, \"y\": ${y}} 발행 (CCTV 감지 경로)"
ros2 topic pub --once /fleet/anomaly_trigger std_msgs/msg/String \
  "{data: '{\"x\": ${x}, \"y\": ${y}}'}"
