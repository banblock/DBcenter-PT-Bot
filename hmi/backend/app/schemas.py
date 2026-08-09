# 요청/응답 모양 규칙

"""Pydantic 요청/응답 규격.

명세서의 JSON 예시를 그대로 타입으로 옮긴 것이다. 응답 모델은 봉투
(`{"result","data"}`) *안쪽* 의 `data` 만 기술한다 — 봉투는 responses.ok() 가 씌운다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import (
    AlignVerdict,
    EquipmentType,
    EventStatus,
    EventType,
    ObservedState,
    Severity,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════
# 맵 · ArUco
# ══════════════════════════════════════════════════════════════════════════
class SlamStartIn(BaseModel):
    robot_id: str
    map_name: str = Field(min_length=1, max_length=128)


class SlamSaveIn(BaseModel):
    robot_id: str
    map_id: str


class MapOut(ORMModel):
    map_id: str
    name: str
    image_url: str | None = None
    resolution: float
    origin: list[float]
    width: int
    height: int
    is_active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, m: Any) -> "MapOut":
        return cls(
            map_id=m.map_id,
            name=m.name,
            image_url=m.image_path,
            resolution=m.resolution,
            origin=[m.origin_x, m.origin_y, m.origin_theta],
            width=m.width,
            height=m.height,
            is_active=m.is_active,
            created_at=m.created_at,
        )


class ArucoIn(BaseModel):
    marker_id: int
    map_id: str
    x: float
    y: float
    yaw: float = 0.0
    zone_id: str | None = None


class ArucoOut(ORMModel):
    marker_id: int
    map_id: str
    x: float
    y: float
    yaw: float
    zone_id: str | None


# ══════════════════════════════════════════════════════════════════════════
# 구역 · 노드 · 경로
# ══════════════════════════════════════════════════════════════════════════
class ZoneIn(BaseModel):
    zone_id: str | None = None  # 생략 시 Z01, Z02... 자동 채번
    name: str = Field(min_length=1, max_length=128)
    polygon: list[list[float]] = Field(default_factory=list)
    risk_base: int = Field(default=1, ge=1, le=5)
    camera_ids: list[str] = Field(default_factory=list)

    @field_validator("polygon")
    @classmethod
    def _check_polygon(cls, v: list[list[float]]) -> list[list[float]]:
        # 빈 폴리곤은 허용(나중에 그린다). 그리려면 최소 삼각형이어야 한다.
        if v and len(v) < 3:
            raise ValueError("폴리곤은 꼭짓점이 3개 이상이어야 합니다")
        for point in v:
            if len(point) != 2:
                raise ValueError("폴리곤 꼭짓점은 [x, y] 형식이어야 합니다")
        return v


class ZoneOut(ORMModel):
    zone_id: str
    name: str
    polygon: list = Field(alias="polygon_json")
    risk_base: int
    camera_ids: list

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NodeIn(BaseModel):
    node_id: str | None = None  # 생략 시 N-001... 자동 채번
    map_id: str
    zone_id: str | None = None
    name: str = Field(min_length=1, max_length=128)
    x: float
    y: float
    theta: float = 0.0
    dwell_sec: int = Field(default=0, ge=0, le=3600)
    is_blindspot: bool = False
    manual_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    inspect_targets: list[str] = Field(default_factory=list)


class NodeUpdate(BaseModel):
    """PUT — 보낸 필드만 갱신한다."""

    zone_id: str | None = None
    name: str | None = None
    x: float | None = None
    y: float | None = None
    theta: float | None = None
    dwell_sec: int | None = Field(default=None, ge=0, le=3600)
    is_blindspot: bool | None = None
    manual_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    inspect_targets: list[str] | None = None


class NodeOut(ORMModel):
    node_id: str
    map_id: str
    zone_id: str | None
    name: str
    x: float
    y: float
    theta: float
    dwell_sec: int
    is_blindspot: bool
    manual_weight: float
    inspect_targets: list
    priority_score: float
    visit_multiplier: int
    last_visited_at: datetime | None


class RouteIn(BaseModel):
    route_id: str | None = None
    name: str = Field(min_length=1, max_length=128)
    map_id: str | None = None
    node_order: list[str] = Field(default_factory=list)
    loop: bool = True


class RouteOut(ORMModel):
    route_id: str
    name: str
    map_id: str | None
    node_order: list = Field(alias="node_order_json")
    loop: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RouteValidationError(BaseModel):
    node_id: str
    reason: Literal["UNREACHABLE", "DUPLICATED", "NOT_FOUND", "MAP_MISMATCH", "NO_ZONE"]


class RouteValidationOut(BaseModel):
    valid: bool
    errors: list[RouteValidationError] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 로봇 · 미션
# ══════════════════════════════════════════════════════════════════════════
class RobotOut(BaseModel):
    robot_id: str
    name: str
    state: str
    state_ko: str
    progress_step: int
    battery: int
    pose: dict[str, float]
    mission_id: str | None
    current_node_id: str | None
    online: bool
    last_seen: datetime | None


class Waypoint(BaseModel):
    x: float
    y: float
    theta: float = 0.0


class GotoIn(BaseModel):
    waypoints: list[Waypoint] = Field(min_length=1)
    preempt: bool = True


class EmergencyStopIn(BaseModel):
    reason: str = "OPERATOR"


class ResumeIn(BaseModel):
    mode: Literal["RESUME_CHECKPOINT", "RETURN_HOME"] = "RESUME_CHECKPOINT"


class EvacuateIn(BaseModel):
    zone_id: str


class PatrolStartIn(BaseModel):
    route_id: str
    robot_ids: list[str] = Field(min_length=1)
    mode: Literal["LOOP", "ONCE"] = "LOOP"
    apply_priority: bool = True


class PatrolReasonIn(BaseModel):
    reason: str = "MANUAL"


class MissionReorderIn(BaseModel):
    order: list[str] = Field(min_length=1)


class MissionOut(BaseModel):
    mission_id: str
    mission_type: str
    robot_id: str | None
    route_id: str | None
    route_name: str | None
    status: str
    progress: float
    current_node_id: str | None
    next_node_id: str | None
    start_time: datetime | None
    end_time: datetime | None


# ══════════════════════════════════════════════════════════════════════════
# 이상 이벤트
# ══════════════════════════════════════════════════════════════════════════
class DetectIn(BaseModel):
    source: Literal["cctv", "amr", "sensor"]
    type: EventType
    confidence: float = Field(ge=0.0, le=1.0)
    camera_id: str | None = None
    robot_id: str | None = None
    bbox: list[float] | None = None
    image_b64: str | None = None
    image_uri: str | None = None
    zone_id: str | None = None
    node_id: str | None = None
    x: float | None = None
    y: float | None = None
    detected_at: datetime | None = None

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != 4:
            raise ValueError("bbox 는 [x1, y1, x2, y2] 4개 값이어야 합니다")
        return v


class DispatchIn(BaseModel):
    robot_id: str | None = None  # 생략 시 자동 선정
    preempt: bool = True


class ObservationIn(BaseModel):
    robot_id: str
    angle_idx: int = Field(ge=1, le=16)
    equipment_id: str | None = None
    observed_state: ObservedState
    value: float | None = None
    unit: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    image_b64: str | None = None
    image_uri: str | None = None
    sensor: dict[str, float] | None = None


class VerdictIn(BaseModel):
    auto: bool = True
    verdict: Literal["CONFIRMED", "FALSE_POSITIVE", "UNVERIFIED"] | None = None


class ReinspectIn(BaseModel):
    extra_angles: int = Field(default=2, ge=1, le=8)
    reason: str = "LOW_CONFIDENCE"


class ReviewIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=64)
    verdict: Literal["CONFIRMED", "FALSE_POSITIVE", "UNVERIFIED"]
    memo: str | None = None


class AckIn(BaseModel):
    operator: str = Field(min_length=1, max_length=64)


class ResolveIn(BaseModel):
    operator: str = Field(min_length=1, max_length=64)
    memo: str | None = None


class EventOut(BaseModel):
    event_id: str
    source: str
    type: str
    severity: str
    status: str
    zone_id: str | None
    node_id: str | None
    x: float | None
    y: float | None
    confidence: float
    hit_count: int
    detected_at: datetime
    assigned_robot_id: str | None
    resolved_at: datetime | None
    thumbnail_url: str | None
    verdict: str | None


class TimelineOut(ORMModel):
    at: datetime
    stage: str
    actor: str | None
    detail: str | None


class MediaOut(ORMModel):
    media_id: int
    kind: str
    uri: str
    angle_idx: int | None


class ObservationOut(ORMModel):
    obs_id: int
    robot_id: str | None
    equipment_id: str | None
    angle_idx: int
    observed_state: str
    value: float | None
    unit: str | None
    confidence: float
    image_uri: str | None
    sensor_json: dict | None
    created_at: datetime


class EventQuery(BaseModel):
    """GET /api/events 쿼리 파라미터 묶음."""

    from_: datetime | None = None
    to: datetime | None = None
    zone_id: str | None = None
    type: EventType | None = None
    severity: Severity | None = None
    status: EventStatus | None = None
    source: str | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=500)


# ══════════════════════════════════════════════════════════════════════════
# 설비 · 작업지시 · align
# ══════════════════════════════════════════════════════════════════════════
class EquipmentIn(BaseModel):
    equipment_id: str = Field(min_length=1, max_length=32)
    type: EquipmentType
    name: str = Field(min_length=1, max_length=128)
    node_id: str | None = None
    zone_id: str | None = None
    normal_state: ObservedState | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None

    @field_validator("value_max")
    @classmethod
    def _check_range(cls, v: float | None, info: Any) -> float | None:
        vmin = info.data.get("value_min")
        if v is not None and vmin is not None and v < vmin:
            raise ValueError("value_max 는 value_min 이상이어야 합니다")
        return v


class EquipmentUpdate(BaseModel):
    name: str | None = None
    node_id: str | None = None
    zone_id: str | None = None
    normal_state: ObservedState | None = None
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None


class EquipmentOut(ORMModel):
    equipment_id: str
    type: str
    name: str
    node_id: str | None
    zone_id: str | None
    normal_state: str | None
    value_min: float | None
    value_max: float | None
    unit: str | None
    check_state: str
    last_observed_state: str | None
    last_value: float | None
    last_verdict: str | None
    last_severity: str | None
    last_checked_at: datetime | None


class WorkOrderIn(BaseModel):
    wo_id: str | None = None
    equipment_id: str
    work_type: str = Field(min_length=1, max_length=32)
    expected_state: ObservedState | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    status: Literal["SCHEDULED", "IN_PROGRESS", "DONE", "CANCELED"] = "SCHEDULED"
    requested_by: str | None = None


class WorkOrderOut(ORMModel):
    wo_id: str
    equipment_id: str
    work_type: str
    expected_state: str | None
    status: str
    start_ts: datetime | None
    end_ts: datetime | None


class AlignCheckIn(BaseModel):
    equipment_id: str
    observed_state: ObservedState
    event_id: str | None = None
    value: float | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GateCheckIn(BaseModel):
    """차단기 실측 대조 요청 (비전 CheckGate 서비스 호출)."""

    equipment_id: str
    robot_id: str            # amr_1 / amr_2 (백엔드 내부 로봇 id)
    gate_id: int = 0         # 비전 쪽 차단기 식별자(★매핑 확정 필요)


class AlignResultOut(ORMModel):
    align_id: int
    event_id: str | None
    equipment_id: str
    observed_state: str | None
    normal_state: str | None
    expected_state: str | None
    work_order_expected_state: str | None = None
    value: float | None
    verdict: str
    severity: str
    rule_id: str | None
    reason: str | None
    auto_created_event_id: str | None
    created_at: datetime


class AlignRuleIn(BaseModel):
    rule_id: str = Field(min_length=1, max_length=32)
    equipment_type: EquipmentType
    condition: dict[str, Any] = Field(default_factory=dict)
    verdict: AlignVerdict = AlignVerdict.MISMATCH
    severity: Severity = Severity.WARN
    message: str | None = None
    priority: int = 0
    enabled: bool = True


class AlignRuleUpdate(BaseModel):
    condition: dict[str, Any] | None = None
    verdict: AlignVerdict | None = None
    severity: Severity | None = None
    message: str | None = None
    priority: int | None = None
    enabled: bool | None = None


class AlignRuleOut(ORMModel):
    rule_id: str
    equipment_type: str
    condition: dict = Field(alias="condition_json")
    verdict: str
    severity: str
    message: str | None
    priority: int
    enabled: bool

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ══════════════════════════════════════════════════════════════════════════
# 우선순위 · 통계
# ══════════════════════════════════════════════════════════════════════════
class PriorityWeightIn(BaseModel):
    manual_weight: float = Field(ge=0.0, le=1.0)
    memo: str | None = None
    operator: str | None = None


class RecalculateIn(BaseModel):
    window: Literal["24h", "7d", "30d"] = "7d"


class PriorityNodeOut(BaseModel):
    node_id: str
    name: str
    zone_id: str | None
    event_count: int
    max_severity: str | None
    hours_since_last_patrol: float | None
    manual_weight: float
    score: float
    rank: int
    visit_multiplier: int


# ══════════════════════════════════════════════════════════════════════════
# 진압 · 설정
# ══════════════════════════════════════════════════════════════════════════
class SuppressionRequestIn(BaseModel):
    event_id: str | None = None
    zone_id: str
    actions: list[Literal["POWER_CUT", "SPRINKLER"]] = Field(min_length=1)
    sprinkler_duration_sec: int = Field(default=60, ge=1, le=1800)
    breaker_ids: list[str] = Field(default_factory=list)
    requested_by: str = Field(min_length=1, max_length=64)
    confirm_text: str = Field(min_length=1)


class SuppressionApproveIn(BaseModel):
    approver: str = Field(min_length=1, max_length=64)


class SuppressionAbortIn(BaseModel):
    operator: str = Field(min_length=1, max_length=64)
    reason: str | None = None


class SuppressionOut(ORMModel):
    suppression_id: str
    event_id: str | None
    zone_id: str | None
    actions: list = Field(alias="actions_json")
    status: str
    steps: list = Field(alias="steps_json")
    interlock: dict | None = Field(default=None, alias="interlock_json")
    requested_by: str | None
    approved_by: str | None
    started_at: datetime | None
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SuppressionModeIn(BaseModel):
    mode: Literal["MANUAL", "AUTO"]
