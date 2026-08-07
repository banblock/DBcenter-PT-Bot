# DB 연결

"""SQLite 엔진·세션·초기화.

SQLite 를 쓰는 이유는 관제 PC 1대에 얹는 단일 프로세스 구성이기 때문이다.
다만 아래 두 가지는 반드시 켜둔다.

* ``PRAGMA foreign_keys=ON``  — SQLite 는 기본이 OFF 라 FK 가 그냥 무시된다.
* ``PRAGMA journal_mode=WAL`` — 5Hz 텔레메트리 쓰기와 조회가 서로 막지 않도록.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.logging_config import get_logger
from app.models import Base

log = get_logger("db")

_is_sqlite = settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    future=True,
    # SQLite + FastAPI: WS 컨슈머 루프와 요청 핸들러가 다른 스레드에서 돈다.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def get_db() -> Iterator[Session]:
    """FastAPI 의존성. 요청 1건 = 세션 1개."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """스크립트·백그라운드 태스크용 컨텍스트 매니저 (커밋/롤백 자동)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(drop: bool = False) -> None:
    """테이블·인덱스 생성. 이미 있으면 건너뛴다(CREATE IF NOT EXISTS)."""
    if drop:
        log.warning("기존 테이블을 전부 삭제합니다 (drop=True)")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    log.info("DB 초기화 완료 — 테이블 %d개 (%s)", len(Base.metadata.tables), settings.database_url)
