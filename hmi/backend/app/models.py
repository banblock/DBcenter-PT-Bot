"""ORM 모델 — 체크리스트 §2-11 테이블 명세 구현.

설계 메모
--------
* 체크리스트에 적힌 18개 테이블을 모두 만들고, API 명세서가 응답에 요구하는데
  체크리스트 컬럼 목록에는 빠져 있던 필드를 보강했다 (각 클래스 주석에 표시).
* ``tb_system_config`` 는 체크리스트에 없던 **추가 테이블**이다.
  `PUT /api/config/suppression-mode` 가 재기동 후에도 유지되어야 해서 넣었다.
* JSON 컬럼은 SQLAlchemy `JSON` 타입을 쓴다. SQLite 에선 TEXT 로 저장되지만
  파이썬 쪽에서는 list/dict 로 그대로 다룰 수 있다.
* 시각 컬럼은 전부 timezone-aware UTC 로 저장한다. 표시용 KST 변환은 프론트 몫.
* 문자열 Enum 은 DB 레벨에서 `CheckConstraint` 로 잠근다. SQLite 에도 적용된다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.enums import (
    AlignVerdict,
    EquipmentCheckState,
    EquipmentType,
    ErrorCode,
    EventStatus,
    EventType,
    EventVerdict,
    MissionStatus,
    ObservedState,
    RobotState,
    Severity,
    SuppressionStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """timezone 을 잃지 않는 DateTime.

    SQLite 에는 timezone 타입이 없다. `DateTime(timezone=True)` 로 선언해도 값을
    다시 읽으면 **naive datetime** 이 돌아온다. 그대로 `.isoformat()` 하면
    ``2026-08-06T10:08:11`` 처럼 오프셋 없는 문자열이 되고, 프론트의
    `Date.parse()` 는 이를 **로컬 시각**으로 해석해 KST 기준 9시간이 어긋난다.
    (실제로 이벤트 큐가 19:08 대신 10:08 로 표시되는 버그가 있었다.)

    그래서 저장할 땐 UTC 로 정규화하고, 읽을 땐 UTC tzinfo 를 다시 붙인다.
    비교 연산(`detected_at >= cutoff`)에서 aware/naive 가 섞이는 사고도 함께 막는다.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # naive 입력은 UTC 로 간주한다 — 서버는 UTC 로만 시각을 만든다.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _in(column: str, enum_cls: type) -> CheckConstraint:
    """`column IN ('A','B',...)` 체크 제약 생성기."""
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"ck_{column}")


class Base(DeclarativeBase):
    pass


# ══════════════════════════════════════════════════════════════════════════
# 1. 맵 · 좌표계
# ══════════════════════════════════════════════════════════════════════════
class Map(Base):
    """SLAM 으로 만든 맵 1장. width/height 는 명세서 1-3 응답에 필요해 보강."""

    __tablename__ = "tb_maps"

    map_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(512))
    yaml_path: Mapped[str | None] = mapped_column(String(512))
    resolution: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)  # m/pixel
    origin_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    origin_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    origin_theta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # px (보강)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # px (보강)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # 현재 로드된 맵 (보강)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    nodes: Mapped[list["Node"]] = relationship(back_populates="map", cascade="all, delete-orphan")
    markers: Mapped[list["ArucoMarker"]] = relationship(
        back_populates="map", cascade="all, delete-orphan"
    )


class ArucoMarker(Base):
    """위치 보정용 ArUco 마커 마스터 (B-14)."""

    __tablename__ = "tb_aruco_markers"
    __table_args__ = (UniqueConstraint("map_id", "marker_id", name="uq_marker_per_map"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marker_id: Mapped[int] = mapped_column(Integer, nullable=False)
    map_id: Mapped[str] = mapped_column(ForeignKey("tb_maps.map_id", ondelete="CASCADE"))
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    yaw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("tb_zones.zone_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    map: Mapped[Map] = relationship(back_populates="markers")


class PoseCorrection(Base):
    """ArUco 보정 이력 (B-15). error_m 이 임계 초과하면 SYSTEM_ALERT 로 승격."""

    __tablename__ = "tb_pose_corrections"

    corr_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("tb_robots.robot_id", ondelete="CASCADE"))
    marker_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)


# ══════════════════════════════════════════════════════════════════════════
# 2. 구역 · 노드 · 경로
# ══════════════════════════════════════════════════════════════════════════
class Zone(Base):
    """감시 구역. polygon 은 맵 좌표(m) 기준 [[x,y], ...]."""

    __tablename__ = "tb_zones"

    zone_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    polygon_json: Mapped[list] = mapped_column(JSON, default=list)
    risk_base: Mapped[int] = mapped_column(Integer, default=1)  # 1~5 기본 위험도
    camera_ids: Mapped[list] = mapped_column(JSON, default=list)  # ["CAM-01", ...]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    nodes: Mapped[list["Node"]] = relationship(back_populates="zone")


class Node(Base):
    """순찰 노드(웨이포인트).

    `priority_score` / `last_visited_at` 은 우선순위 재계산(B-57)의 입출력이라
    체크리스트 컬럼 목록에는 없지만 명세서 2-3 응답이 요구해서 보강했다.
    """

    __tablename__ = "tb_nodes"
    __table_args__ = (
        Index("ix_nodes_map_zone", "map_id", "zone_id"),
        CheckConstraint("manual_weight >= 0.0 AND manual_weight <= 1.0", name="ck_manual_weight"),
    )

    node_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    map_id: Mapped[str] = mapped_column(ForeignKey("tb_maps.map_id", ondelete="CASCADE"))
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("tb_zones.zone_id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    theta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dwell_sec: Mapped[int] = mapped_column(Integer, default=0)
    is_blindspot: Mapped[bool] = mapped_column(Boolean, default=False)  # CCTV 사각지대
    manual_weight: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0~1.0 수동 가중치
    inspect_targets: Mapped[list] = mapped_column(JSON, default=list)  # [equipment_id] (보강)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)  # (보강)
    visit_multiplier: Mapped[int] = mapped_column(Integer, default=1)  # (보강)
    last_visited_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # (보강)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    map: Mapped[Map] = relationship(back_populates="nodes")
    zone: Mapped[Zone | None] = relationship(back_populates="nodes")


class Route(Base):
    """순찰 경로 = 노드 순서 배열. map_id 는 경로 검증(B-20)에 필요해 보강."""

    __tablename__ = "tb_routes"

    route_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    map_id: Mapped[str | None] = mapped_column(ForeignKey("tb_maps.map_id", ondelete="CASCADE"))
    node_order_json: Mapped[list] = mapped_column(JSON, default=list)  # ["N-001", ...]
    loop: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# ══════════════════════════════════════════════════════════════════════════
# 3. 로봇 · 미션
# ══════════════════════════════════════════════════════════════════════════
class Robot(Base):
    """로봇 최신 상태 스냅샷. 5Hz 텔레메트리는 여기를 덮어쓰고 이력은 남기지 않는다
    (이력이 필요하면 별도 시계열 저장소를 붙일 것 — SQLite 로는 감당 못 한다)."""

    __tablename__ = "tb_robots"
    __table_args__ = (
        _in("status", RobotState),
        CheckConstraint("battery >= 0 AND battery <= 100", name="ck_battery_range"),
    )

    robot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default=RobotState.OFFLINE.value)
    battery: Mapped[int] = mapped_column(Integer, default=0)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    theta: Mapped[float] = mapped_column(Float, default=0.0)
    online: Mapped[bool] = mapped_column(Boolean, default=False)  # (보강) heartbeat 판정 결과
    progress_step: Mapped[int] = mapped_column(Integer, default=0)  # (보강) 5단계 스텝
    current_node_id: Mapped[str | None] = mapped_column(String(32))  # (보강)
    dock_id: Mapped[str | None] = mapped_column(String(32))  # (보강)
    checkpoint_json: Mapped[dict | None] = mapped_column(JSON)  # (보강) 긴급정지 복귀점
    last_seen: Mapped[datetime | None] = mapped_column(UtcDateTime)
    current_mission_id: Mapped[str | None] = mapped_column(String(48))

    @property
    def pose(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "theta": self.theta}


class Mission(Base):
    """순찰/이상대응 미션 1건.

    `resume_context_json` 은 급파로 순찰이 선점(PREEMPTED)될 때 남은 노드와 인덱스를
    담아두는 자리다. 복귀(B-39) 시 이 값만 있으면 중단 지점부터 이어갈 수 있다.
    """

    __tablename__ = "tb_missions"
    __table_args__ = (
        _in("status", MissionStatus),
        Index("ix_missions_robot_status", "robot_id", "status"),
        CheckConstraint("mission_type IN ('PATROL', 'ANOMALY')", name="ck_mission_type"),
        CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="ck_progress_range"),
    )

    mission_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    mission_type: Mapped[str] = mapped_column(String(16), default="PATROL")  # (보강)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("tb_routes.route_id", ondelete="SET NULL"))
    robot_id: Mapped[str | None] = mapped_column(ForeignKey("tb_robots.robot_id", ondelete="SET NULL"))
    event_id: Mapped[str | None] = mapped_column(String(48))  # (보강) ANOMALY 미션의 원인 이벤트
    status: Mapped[str] = mapped_column(String(16), default=MissionStatus.PENDING.value)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0~1.0
    node_order_json: Mapped[list] = mapped_column(JSON, default=list)  # (보강) 이 로봇 담당 노드
    current_node_id: Mapped[str | None] = mapped_column(String(32))  # (보강)
    next_node_id: Mapped[str | None] = mapped_column(String(32))  # (보강)
    resume_context_json: Mapped[dict | None] = mapped_column(JSON)
    loop: Mapped[bool] = mapped_column(Boolean, default=False)  # (보강)
    queue_order: Mapped[int] = mapped_column(Integer, default=0)  # (보강) 작업 큐 수동 재정렬
    start_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    end_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# ══════════════════════════════════════════════════════════════════════════
# 4. 이상 이벤트
# ══════════════════════════════════════════════════════════════════════════
class Event(Base):
    """이상 이벤트 이력. 시스템에서 가장 많이 쌓이고 가장 많이 조회되는 테이블.

    인덱스 3종은 체크리스트가 명시한 조회 패턴 그대로다:
    최신순 정렬(detected_at), 구역·타입·상태 필터, 관측 조인.
    """

    __tablename__ = "tb_events"
    __table_args__ = (
        _in("type", EventType),
        _in("severity", Severity),
        _in("status", EventStatus),
        CheckConstraint("source IN ('cctv', 'amr', 'sensor', 'align')", name="ck_event_source"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_confidence_range"),
        Index("ix_events_detected_at", "detected_at"),
        Index("ix_events_zone_type_status", "zone_id", "type", "status"),
        Index("ix_events_status_severity", "status", "severity"),
    )

    event_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # cctv|amr|sensor|align
    camera_id: Mapped[str | None] = mapped_column(String(32))  # (보강) source=cctv
    robot_id: Mapped[str | None] = mapped_column(String(32))  # (보강) source=amr
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), default=Severity.WARN.value)
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.DETECTED.value)

    zone_id: Mapped[str | None] = mapped_column(ForeignKey("tb_zones.zone_id", ondelete="SET NULL"))
    node_id: Mapped[str | None] = mapped_column(String(32))
    x: Mapped[float | None] = mapped_column(Float)
    y: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    final_confidence: Mapped[float | None] = mapped_column(Float)  # (보강) 다수결 결과
    bbox_json: Mapped[list | None] = mapped_column(JSON)  # (보강) [x1,y1,x2,y2]

    hit_count: Mapped[int] = mapped_column(Integer, default=1)  # dedup 병합 횟수
    merged_into: Mapped[str | None] = mapped_column(String(48))  # (보강) MERGED 시 원본 event_id
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))  # (보강)

    assigned_robot_id: Mapped[str | None] = mapped_column(String(32))
    verdict: Mapped[str | None] = mapped_column(String(20))  # CONFIRMED|FALSE_POSITIVE|UNVERIFIED
    reviewer: Mapped[str | None] = mapped_column(String(64))
    memo: Mapped[str | None] = mapped_column(Text)

    detected_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # (보강)
    arrived_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # (보강) 통계용
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # (보강)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # (보강)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))  # (보강)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    media: Mapped[list["EventMedia"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    timeline: Mapped[list["EventTimeline"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventTimeline.at"
    )


class EventMedia(Base):
    """이벤트 근거 이미지/영상. 파일은 media_root 에 두고 DB 엔 경로만 (B-44)."""

    __tablename__ = "tb_event_media"

    media_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("tb_events.event_id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="IMAGE")  # IMAGE|VIDEO
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    angle_idx: Mapped[int | None] = mapped_column(Integer)  # 다각도 검증 시 각도 번호
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="media")


class EventTimeline(Base):
    """이벤트 진행 단계 이력 (명세서 5-10 의 `timeline`).

    체크리스트에 없던 **추가 테이블**. 타임라인을 로그 텍스트에서 역파싱하는 건
    깨지기 쉬워서 단계 전이를 그때그때 한 줄씩 적재하는 쪽을 택했다.
    """

    __tablename__ = "tb_event_timeline"
    __table_args__ = (Index("ix_timeline_event", "event_id", "at"),)

    tl_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("tb_events.event_id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(24), nullable=False)  # DETECTED|ASSIGNED|...
    actor: Mapped[str | None] = mapped_column(String(64))  # CAM-01 | dispatcher | operator_kim
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="timeline")


class Observation(Base):
    """AMR 다각도 관측 1건 (B-41). 다수결 판정(B-42)의 입력."""

    __tablename__ = "tb_observations"
    __table_args__ = (
        _in("observed_state", ObservedState),
        Index("ix_observations_event", "event_id"),
        UniqueConstraint("event_id", "angle_idx", name="uq_observation_angle"),
    )

    obs_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("tb_events.event_id", ondelete="CASCADE"))
    robot_id: Mapped[str | None] = mapped_column(String(32))
    equipment_id: Mapped[str | None] = mapped_column(
        ForeignKey("tb_equipment.equipment_id", ondelete="SET NULL")
    )
    angle_idx: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..N
    observed_state: Mapped[str] = mapped_column(String(16), default=ObservedState.UNKNOWN.value)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))  # (보강)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    image_uri: Mapped[str | None] = mapped_column(String(512))  # (보강)
    sensor_json: Mapped[dict | None] = mapped_column(JSON)  # {temp_c, gas_ppm, current_a, humidity}
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="observations")


# ══════════════════════════════════════════════════════════════════════════
# 5. 설비 마스터 · 작업지시 · 대조(align)
# ══════════════════════════════════════════════════════════════════════════
class Equipment(Base):
    """설비 마스터 — '정상은 무엇인가'의 기준값을 들고 있는 테이블.

    align 엔진은 `관측값 vs normal_state vs 작업지시 expected_state` 3자를 비교한다.
    last_* 컬럼은 설비 점검 화면(F-63)이 매번 조인하지 않고 읽도록 캐시해 둔 값이다.
    """

    __tablename__ = "tb_equipment"
    __table_args__ = (
        _in("type", EquipmentType),
        _in("check_state", EquipmentCheckState),
        Index("ix_equipment_zone_type", "zone_id", "type"),
        CheckConstraint(
            "value_min IS NULL OR value_max IS NULL OR value_min <= value_max",
            name="ck_value_range",
        ),
    )

    equipment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("tb_nodes.node_id", ondelete="SET NULL"))
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("tb_zones.zone_id", ondelete="SET NULL"))

    normal_state: Mapped[str | None] = mapped_column(String(16))  # 기준 상태
    value_min: Mapped[float | None] = mapped_column(Float)
    value_max: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))  # (보강)

    # ── 최근 점검 결과 캐시 (보강 — 명세서 6-2 응답 필드) ────────────────
    check_state: Mapped[str] = mapped_column(String(16), default=EquipmentCheckState.UNKNOWN.value)
    last_observed_state: Mapped[str | None] = mapped_column(String(16))
    last_value: Mapped[float | None] = mapped_column(Float)
    last_verdict: Mapped[str | None] = mapped_column(String(16))
    last_severity: Mapped[str | None] = mapped_column(String(12))
    last_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    recheck_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class WorkOrder(Base):
    """작업지시. '지금은 차단기가 OFF 여야 정상'처럼 기준값을 한시적으로 뒤집는다."""

    __tablename__ = "tb_work_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SCHEDULED', 'IN_PROGRESS', 'DONE', 'CANCELED')", name="ck_wo_status"
        ),
        Index("ix_wo_equipment_status", "equipment_id", "status"),
    )

    wo_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("tb_equipment.equipment_id", ondelete="CASCADE")
    )
    work_type: Mapped[str] = mapped_column(String(32), nullable=False)  # MAINTENANCE|INSPECTION|...
    expected_state: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="SCHEDULED")
    start_ts: Mapped[datetime | None] = mapped_column(UtcDateTime)
    end_ts: Mapped[datetime | None] = mapped_column(UtcDateTime)
    requested_by: Mapped[str | None] = mapped_column(String(64))  # (보강)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AlignRule(Base):
    """대조 판정 룰. 재배포 없이 DB 로 추가/수정한다 (B-52).

    `condition_json` 은 {필드: 기대값} 형태의 AND 매칭이다. 매칭되는 룰 중
    `priority` 가 가장 높은(숫자가 큰) 하나가 최종 판정이 된다.
    """

    __tablename__ = "tb_align_rules"
    __table_args__ = (
        _in("verdict", AlignVerdict),
        _in("severity", Severity),
    )

    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    equipment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    condition_json: Mapped[dict] = mapped_column(JSON, default=dict)
    verdict: Mapped[str] = mapped_column(String(16), default=AlignVerdict.MISMATCH.value)
    severity: Mapped[str] = mapped_column(String(12), default=Severity.WARN.value)
    message: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # (보강) 룰 충돌 시 우선순위
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AlignResult(Base):
    """대조 판정 결과 1건 (명세서 6-4)."""

    __tablename__ = "tb_align_results"
    __table_args__ = (
        _in("verdict", AlignVerdict),
        Index("ix_align_equipment_created", "equipment_id", "created_at"),
    )

    align_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("tb_events.event_id", ondelete="SET NULL")
    )
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("tb_equipment.equipment_id", ondelete="CASCADE")
    )
    observed_state: Mapped[str | None] = mapped_column(String(16))
    normal_state: Mapped[str | None] = mapped_column(String(16))
    expected_state: Mapped[str | None] = mapped_column(String(16))  # 작업지시 기준
    value: Mapped[float | None] = mapped_column(Float)  # (보강)
    verdict: Mapped[str] = mapped_column(String(16), default=AlignVerdict.UNVERIFIED.value)
    severity: Mapped[str] = mapped_column(String(12), default=Severity.INFO.value)
    rule_id: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    auto_created_event_id: Mapped[str | None] = mapped_column(String(48))  # (보강)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# ══════════════════════════════════════════════════════════════════════════
# 6. 화재진압
# ══════════════════════════════════════════════════════════════════════════
class Suppression(Base):
    """진압 시퀀스 1건 + 감사 로그(B-70).

    `steps_json` 은 [{step, status, at, result}] 배열로 진행 단계를 통째로 들고 있다.
    누가·언제·왜·결과가 한 행에 다 남아야 사고 조사가 가능하다.
    """

    __tablename__ = "tb_suppressions"
    __table_args__ = (
        _in("status", SuppressionStatus),
        Index("ix_suppression_started", "started_at"),
    )

    suppression_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("tb_events.event_id", ondelete="SET NULL")
    )
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("tb_zones.zone_id", ondelete="SET NULL"))
    actions_json: Mapped[list] = mapped_column(JSON, default=list)  # ["POWER_CUT","SPRINKLER"]
    breaker_ids_json: Mapped[list] = mapped_column(JSON, default=list)  # (보강)
    sprinkler_duration_sec: Mapped[int] = mapped_column(Integer, default=60)  # (보강)
    status: Mapped[str] = mapped_column(String(20), default=SuppressionStatus.REQUESTED.value)
    steps_json: Mapped[list] = mapped_column(JSON, default=list)  # (보강)
    interlock_json: Mapped[dict | None] = mapped_column(JSON)  # (보강) {passed, blockers:[]}
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # (보강)
    mode: Mapped[str] = mapped_column(String(8), default="MANUAL")  # (보강)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    aborted_by: Mapped[str | None] = mapped_column(String(64))  # (보강)
    abort_reason: Mapped[str | None] = mapped_column(Text)  # (보강)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# ══════════════════════════════════════════════════════════════════════════
# 7. 우선순위 · 에러 · 시스템 설정
# ══════════════════════════════════════════════════════════════════════════
class PriorityLog(Base):
    """우선순위 점수 변경 이력 (B-61). '왜 이 노드를 더 자주 도는가'의 설명 가능성."""

    __tablename__ = "tb_priority_log"
    __table_args__ = (Index("ix_priority_node_created", "node_id", "created_at"),)

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("tb_nodes.node_id", ondelete="CASCADE"))
    before_score: Mapped[float] = mapped_column(Float, default=0.0)
    after_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(String(64))  # (보강) 수동 가중치 조정자
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ErrorLog(Base):
    """로봇/미션 오류 이력."""

    __tablename__ = "tb_error_log"
    __table_args__ = (
        _in("error_code", ErrorCode),
        Index("ix_error_robot_time", "robot_id", "error_time"),
    )

    error_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    robot_id: Mapped[str | None] = mapped_column(String(32))
    mission_id: Mapped[str | None] = mapped_column(String(48))
    error_code: Mapped[str] = mapped_column(String(24), nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text)
    error_time: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class SystemConfig(Base):
    """런타임 설정 KV — 체크리스트에 없던 **추가 테이블**.

    `PUT /api/config/suppression-mode` 처럼 재기동 후에도 유지돼야 하는 설정만 넣는다.
    .env 로 충분한 값(포트, 임계값 기본치 등)은 여기 넣지 않는다.
    """

    __tablename__ = "tb_system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow
    )


#: 마이그레이션/문서 생성 스크립트가 참조하는 테이블 목록
ALL_TABLES = [
    Map, Zone, Node, Route, ArucoMarker, PoseCorrection,
    Robot, Mission,
    Event, EventMedia, EventTimeline, Observation,
    Equipment, WorkOrder, AlignRule, AlignResult,
    Suppression, PriorityLog, ErrorLog, SystemConfig,
]
