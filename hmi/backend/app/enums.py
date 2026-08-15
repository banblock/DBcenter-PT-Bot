# 고정 단어들(상태·타입)

"""API 명세서 §11 의 Enum 고정값. 프론트 `src/services/ws/messages.ts` 와 1:1 대응한다.

여기 값을 바꾸면 프론트 타입도 함께 바꿔야 한다. 순서도 명세서 표기 순서를 지킨다.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - 표시용
        return self.value


class RobotState(StrEnum):
    OFFLINE = "OFFLINE"
    MAPPING = "MAPPING"
    IDLE = "IDLE"
    PATROLLING = "PATROLLING"
    PATROL_PAUSED = "PATROL_PAUSED"
    DISPATCHING = "DISPATCHING"
    INSPECTING = "INSPECTING"
    REPORTING = "REPORTING"
    RESUMING = "RESUMING"
    CHARGING = "CHARGING"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ERROR = "ERROR"
    ALERTING = "ALERTING"
    UNDOCKING = "UNDOCKING"
    DOCKING = "DOCKING"


#: 상태값 → UI 한글 표기 (체크리스트 §1-1)
ROBOT_STATE_KO: dict[str, str] = {
    "OFFLINE": "연결 끊김",
    "MAPPING": "맵 생성 중",
    "IDLE": "대기",
    "PATROLLING": "순찰 중",
    "PATROL_PAUSED": "순찰 일시정지",
    "DISPATCHING": "이상지점 이동 중",
    "INSPECTING": "점검 중",
    "REPORTING": "결과 전송 중",
    "RESUMING": "순찰 복귀 중",
    "CHARGING": "충전 중",
    "EMERGENCY_STOP": "긴급정지",
    "ERROR": "오류",
    "ALERTING": "현장 경보 중",
    "UNDOCKING": "출발 준비",
    "DOCKING": "복귀·도킹 중",
}


class EventStatus(StrEnum):
    DETECTED = "DETECTED"
    QUEUED = "QUEUED"
    MERGED = "MERGED"
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    VERIFYING = "VERIFYING"
    CONFIRMED = "CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    SUPPRESSING = "SUPPRESSING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class EventType(StrEnum):
    FIRE = "FIRE"
    SMOKE = "SMOKE"
    LEAK = "LEAK"
    PERSON = "PERSON"
    INTRUSION = "INTRUSION"
    BREAKER_ABNORMAL = "BREAKER_ABNORMAL"
    LOCK_ABNORMAL = "LOCK_ABNORMAL"
    PANEL_OUT_OF_RANGE = "PANEL_OUT_OF_RANGE"
    ALIGN_MISMATCH = "ALIGN_MISMATCH"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class MissionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PREEMPTED = "PREEMPTED"
    DONE = "DONE"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


class SuppressionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    INTERLOCK_CHECK = "INTERLOCK_CHECK"
    BLOCKED = "BLOCKED"
    APPROVED = "APPROVED"
    POWER_CUTTING = "POWER_CUTTING"
    SPRINKLER_ON = "SPRINKLER_ON"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class EquipmentType(StrEnum):
    BREAKER = "BREAKER"
    LOCK = "LOCK"
    BATTERY_PANEL = "BATTERY_PANEL"
    VALVE = "VALVE"


class ObservedState(StrEnum):
    ON = "ON"
    OFF = "OFF"
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"
    NORMAL = "NORMAL"
    ABNORMAL = "ABNORMAL"
    UNKNOWN = "UNKNOWN"


class ErrorCode(StrEnum):
    ERR_NAV = "ERR_NAV"
    ERR_LOCALIZATION = "ERR_LOCALIZATION"
    ERR_COLLISION = "ERR_COLLISION"
    ERR_CAMERA = "ERR_CAMERA"
    ERR_TIMEOUT = "ERR_TIMEOUT"
    ERR_BATTERY = "ERR_BATTERY"
    ERR_COMM = "ERR_COMM"
    ERR_SUPPRESSION = "ERR_SUPPRESSION"


class AlignVerdict(StrEnum):
    OK = "OK"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"


class EventVerdict(StrEnum):
    CONFIRMED = "CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    UNVERIFIED = "UNVERIFIED"


class EquipmentCheckState(StrEnum):
    """차단기 DB 점검 상태 (체크리스트 §1-1 '차단기 DB' 표)."""

    UNKNOWN = "UNKNOWN"
    SCANNING = "SCANNING"
    VERIFYING = "VERIFYING"
    NORMAL = "NORMAL"
    MISMATCH = "MISMATCH"
    RECHECK = "RECHECK"
    UNREADABLE = "UNREADABLE"
    STALE = "STALE"


class WsTopic(StrEnum):
    """WS 구독 토픽 (API 명세서 §9-2)."""

    ROBOT_STATUS = "ROBOT_STATUS"
    EVENT = "EVENT"
    MISSION_STATUS = "MISSION_STATUS"
    LOG = "LOG"
    SUPPRESSION_STATUS = "SUPPRESSION_STATUS"
    DETECTION = "DETECTION"


class WsMessageType(StrEnum):
    """서버 → 클라이언트 메시지 타입 (API 명세서 §9-3)."""

    SNAPSHOT = "SNAPSHOT"
    ROBOT_STATUS = "ROBOT_STATUS"
    ROBOT_STATE_CHANGED = "ROBOT_STATE_CHANGED"
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    MISSION_STATUS = "MISSION_STATUS"
    EVENT = "EVENT"
    DETECTION = "DETECTION"
    ALIGN_RESULT = "ALIGN_RESULT"
    POSE_CORRECTED = "POSE_CORRECTED"
    PRIORITY_UPDATED = "PRIORITY_UPDATED"
    SUPPRESSION_STATUS = "SUPPRESSION_STATUS"
    LOG = "LOG"
    SYSTEM_ALERT = "SYSTEM_ALERT"
    PONG = "PONG"
    SUBSCRIBED = "SUBSCRIBED"


#: 메시지 타입 → 이 타입을 받으려면 구독해야 하는 토픽.
#: 여기 없는 타입(SNAPSHOT/PONG/SYSTEM_ALERT/SUBSCRIBED)은 구독과 무관하게 항상 전달된다.
TYPE_TO_TOPIC: dict[str, str] = {
    WsMessageType.ROBOT_STATUS: WsTopic.ROBOT_STATUS,
    WsMessageType.ROBOT_STATE_CHANGED: WsTopic.ROBOT_STATUS,
    WsMessageType.ROBOT_OFFLINE: WsTopic.ROBOT_STATUS,
    WsMessageType.POSE_CORRECTED: WsTopic.ROBOT_STATUS,
    WsMessageType.MISSION_STATUS: WsTopic.MISSION_STATUS,
    WsMessageType.EVENT: WsTopic.EVENT,
    WsMessageType.ALIGN_RESULT: WsTopic.EVENT,
    WsMessageType.PRIORITY_UPDATED: WsTopic.EVENT,
    WsMessageType.DETECTION: WsTopic.DETECTION,
    WsMessageType.SUPPRESSION_STATUS: WsTopic.SUPPRESSION_STATUS,
    WsMessageType.LOG: WsTopic.LOG,
}
