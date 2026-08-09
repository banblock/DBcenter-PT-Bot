#!/usr/bin/env bash
# 시나리오 3: 도킹 복귀 (실제 실행 미검증 - 가장 주의 깊게 지켜볼 것)
# 사용법:
#   ./dock.sh robot3          -> robot3만 도킹
#   ./dock.sh robot3 robot8   -> robot3, robot8 둘 다 도킹
#   ./dock.sh                 -> 인자 없으면 등록된 로봇 전체 도킹
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "대상 로봇 지정 없음 -> 등록된 로봇 전체 도킹"
  payload='{}'
else
  robots=$(printf '"%s",' "$@")
  robots="[${robots%,}]"
  echo "대상 로봇: $*"
  payload="{\"robots\": ${robots}}"
fi

echo "/backend/dock -> ${payload} 발행"
ros2 topic pub --once /backend/dock std_msgs/msg/String \
  "{data: '${payload}'}"
