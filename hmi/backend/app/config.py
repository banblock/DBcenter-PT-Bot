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
    robot_ids: list[str] = Field(default_factory=lambda: ["amr_1", "amr_2"])
    topic_prefix_map: dict[str, str] = Field(
        default_factory=lambda: {"amr_1": "/amr01", "amr_2": "/amr02"}
    )
    heartbeat_timeout_sec: float = 3.0
    ros_enabled: bool = False  # 실장비 없이 기동할 때 False

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
