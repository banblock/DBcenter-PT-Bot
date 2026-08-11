# 로봇 연결 '교체구'(시임)

"""ROS2 브리지 시임(seam).

지금 단계에서 ROS2 노드는 아직 붙지 않았다. 그렇다고 라우터가 ROS 호출을
직접 품고 있으면 나중에 갈아끼울 자리가 흩어진다. 그래서 명령 하달 인터페이스를
여기 하나로 모으고, 기본 구현은 **로그만 남기는 NullBridge** 로 둔다.

실장비/테스트 노드가 준비되면 ``Ros2Bridge`` 를 구현해 ``set_bridge()`` 로 갈아끼우면
된다. 라우터 코드는 한 줄도 바뀌지 않는다.

명령 타입은 API 명세서 §10-2 표와 1:1 이다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from app.crud import ids
from app.logging_config import get_logger

log = get_logger("bridge")

CommandType = str  # START_PATROL|PAUSE|RESUME|CANCEL|GOTO|ANOMALY_HOLD|INSPECT|EVACUATE|ESTOP|RESET|DOCK


# [공부 메모] 이게 수업 때 배운 "의존성 역전(DIP)" 실물 예시라 리뷰 때 얘기하면 좋음.
#   - 라우터는 항상 get_bridge().publish_command(...) 만 부름 = "추상(Protocol)"에 의존.
#   - 실제 구현은 런타임에 set_bridge()로 끼움: NullBridge(로그만) / RobotBridge(loopback)
#     / Ros2Bridge(진짜 rclpy). → ROS가 있든 없든 라우터·테스트 코드는 안 바뀜.
#   - 기본이 NullBridge라서 로봇 없이 서버 켜도 아무것도 안 깨짐(그냥 로그만 남김).
class Bridge(Protocol):
    # ← "이런 메서드를 가진 놈이면 다 Bridge다"라는 계약(구조적 타이핑). 상속 아님.
    def publish_command(self, robot_id: str, command_type: CommandType, payload: dict) -> dict: ...

    @property
    def connected(self) -> bool: ...


class NullBridge:
    """ROS 없이 기동할 때 쓰는 기본 브리지.

    명령을 실제로 보내지 않는다. 그래서 응답의 ``accepted`` 는 항상 True 지만
    ``dispatched=False`` 를 함께 실어 보낸다 — 프론트/테스트가 "정말 로봇에
    전달됐는지"를 구분할 수 있어야 하기 때문이다. 여기서 True 로 뭉뚱그리면
    ROS 가 죽어 있어도 화면상으로는 성공처럼 보이게 된다.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []  # 테스트에서 하달 내역을 확인하는 용도

    @property
    def connected(self) -> bool:
        return False

    def publish_command(self, robot_id: str, command_type: CommandType, payload: dict) -> dict:
        # ← 여기 핵심: accepted=True 지만 dispatched=False. "접수는 했는데 실제 로봇엔
        #   안 보냄"을 정직하게 표시. 이걸 True로 뭉개면 로봇 죽어도 성공처럼 보임 = 위험.
        record = {
            "command_id": ids.next_command_id(),
            "robot_id": robot_id,
            "command_type": command_type,
            "payload": payload,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sent.append(record)
        log.info("[NullBridge] %s → %s %s", robot_id, command_type, payload)
        return {"command_id": record["command_id"], "accepted": True, "dispatched": False}


_bridge: Any = NullBridge()


def get_bridge() -> Bridge:
    return _bridge


def set_bridge(bridge: Bridge) -> None:
    """실제 ROS2 브리지(또는 테스트 더블)로 교체한다."""
    global _bridge
    _bridge = bridge
    log.info("브리지 교체 → %s", type(bridge).__name__)
