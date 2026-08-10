# 설정 한곳 모음

"""환경설정 단일 소스.

모든 튜닝 값은 여기 한 곳에서만 정의한다. 코드 어디에서도 매직 넘버를 두지 않고
`from app.config import settings` 로 가져다 쓴다. 환경변수 접두사는 `AMR_`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AMR_",
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # 리스트/딕트 필드를 JSON 으로 자동 디코딩하지 않는다. `AMR_CORS_ORIGINS=a,b,c`
        # 같은 콤마 구분 값은 아래 `_split_csv` (mode="before") 가 직접 파싱한다.
        # (pydantic-settings 2.x 는 기본적으로 복합 타입 env 값을 JSON 으로 먼저 파싱해
        #  콤마 구분 문자열에서 SettingsError 를 낸다.)
        enable_decoding=False,
    )

    # ── 서비스 ────────────────────────────────────────────────────────────
    app_name: str = "AMR 순찰·이상감지 관제 API"
    app_version: str = "1.0.0"
    api_prefix: str = "/api"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── CORS — 개발 중 Vite dev 서버(5173~5180) 허용 ──────────────────────
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:5175",
            "http://127.0.0.1:5175",
        ]
    )

    # ── 저장소 ────────────────────────────────────────────────────────────
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'amr.db'}"
    media_root: str = str(BASE_DIR / "media")
    sql_echo: bool = False

    # ── 로깅 ──────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_dir: str = str(BASE_DIR / "logs")
    log_to_file: bool = True
    log_request_body: bool = False

    # ── 로봇 / ROS2 브리지 ────────────────────────────────────────────────
    robot_ids: list[str] = Field(default_factory=lambda: ["AMR-01", "AMR-02"])
    #: 논리 robot_id → ROS-safe 네임스페이스. 실 robot_bridge_node 가 쓰는 값과 반드시 일치.
    #: 명령 토픽 = /backend{ns}/command (예: AMR-01 → /amr_1 → /backend/amr_1/command).
    topic_prefix_map: dict[str, str] = Field(
        default_factory=lambda: {"AMR-01": "/amr_1", "AMR-02": "/amr_2"}
    )
    heartbeat_timeout_sec: float = 3.0
    ros_enabled: bool = False  # 실장비 없이 기동할 때 False
    #: robot_bridge 백엔드 (§10). null=NullBridge(기본, 발행 안 함) | loopback=발행만 기록 |
    #: ros2=rclpy 실기 연동(PC2 ROS2 Humble 노드에서만).
    bridge_backend: str = "null"
    #: 비전 통합(vision_bridge) 사용 여부. ros2 모드일 때만 의미. 기본 False =
    #: patrol_interfaces/vision workspace 없이도 백엔드가 그대로 동작. (colcon build+source 후 True)
    vision_enabled: bool = False

    # ── 호모그래피 (Phase 3-1) : CCTV 픽셀 → 맵 좌표 변환 ─────────────────
    #: 변환 on/off. off 면 pixel_to_map 이 항상 None → 이벤트에 좌표 미기입(현행 동작).
    homography_enabled: bool = True
    #: camera_id(vision_bridge VISION_CAMERA_MAP 의 camera_id) → 캘리브레이션 YAML 경로.
    #: v2_1=cctv1, v2_2=cctv2 (2026-08-09 확정). image_to_map 3×3 호모그래피(ROS param 형식).
    homography_files: dict[str, str] = Field(
        default_factory=lambda: {
            "cctv1": str(BASE_DIR / "data" / "calibration" / "cctv1.yaml"),
            "cctv2": str(BASE_DIR / "data" / "calibration" / "cctv2.yaml"),
        }
    )
    #: 화재 감지 즉시 자동 급파(로봇에 GOTO 좌표 전송). 2026-08-09 정책 확정. False 면
    #: 이벤트/알림만 만들고 급파는 운영자(POST /events/{id}/dispatch)가 한다.
    vision_auto_dispatch: bool = True

    #: 인프로세스 데모 로봇 시뮬레이터 (실장비/ROS2 없이 로봇을 실제로 구동). AMR_DEMO_SIM=1 로 켠다.
    demo_sim: bool = False

    # ── 이벤트 / 검증 ─────────────────────────────────────────────────────
    dedup_window_sec: int = 10
    inspect_angle_count: int = 3
    confidence_threshold: float = 0.6
    verdict_confidence_threshold: float = 0.75
    max_recheck_count: int = 2
    battery_low_threshold: int = 20
    battery_resume_threshold: int = 80

    # ── 진압 ──────────────────────────────────────────────────────────────
    suppression_mode: str = "MANUAL"  # MANUAL | AUTO
    suppression_max_retry: int = 3
    ack_escalation_sec: int = 60

    # ── 순찰 우선순위 점수 가중치 (w1~w4) ─────────────────────────────────
    priority_weights: dict[str, float] = Field(
        default_factory=lambda: {"w1": 0.4, "w2": 0.3, "w3": 0.2, "w4": 0.1}
    )
    priority_decay_lambda: float = 0.15
    priority_default_window: str = "7d"

    # ── WebSocket ─────────────────────────────────────────────────────────
    broadcast_queue_size: int = 2000
    event_queue_size: int = 500
    ws_status_throttle_hz: float = 5.0

    @field_validator("cors_origins", "robot_ids", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """env 로 넘어온 `a,b,c` 또는 JSON 배열 문자열을 리스트로 받아준다."""
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    @field_validator("suppression_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("MANUAL", "AUTO"):
            raise ValueError("suppression_mode must be MANUAL or AUTO")
        return v

    def ensure_dirs(self) -> None:
        Path(self.media_root).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
