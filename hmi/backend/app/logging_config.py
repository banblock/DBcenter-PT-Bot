"""로깅 구조.

계층
----
* ``amr``            — 루트 로거. 모든 앱 로거는 이 아래에 붙는다.
* ``amr.request``    — HTTP 요청/응답 1줄 요약 (미들웨어)
* ``amr.ws``         — WebSocket 연결/구독/브로드캐스트
* ``amr.db``         — DB 초기화·마이그레이션·시드
* ``amr.<router>``   — 라우터별 도메인 로그

포맷은 `시각 | 레벨 | 로거 | 요청ID | 메시지`. 요청ID 는 미들웨어가 contextvar 로 심는다.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from pathlib import Path

from app.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-16s | %(request_id)-8s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging() -> None:
    """앱 부팅 시 1회 호출. 두 번 불려도 핸들러가 중복되지 않는다."""
    root = logging.getLogger("amr")
    if root.handlers:
        return

    root.setLevel(settings.log_level.upper())
    root.propagate = False
    fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)
    id_filter = _RequestIdFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(id_filter)
    root.addHandler(console)

    if settings.log_to_file:
        Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
        # 10MB × 5개 롤링 — 관제실 장시간 운전에서 디스크가 차지 않도록.
        file_handler = logging.handlers.RotatingFileHandler(
            Path(settings.log_dir) / "amr-api.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(id_filter)
        root.addHandler(file_handler)

    # uvicorn 자체 access 로그는 우리 요청 로그와 중복이라 끈다.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    """``get_logger("ws")`` → ``amr.ws``"""
    return logging.getLogger(f"amr.{name}")
