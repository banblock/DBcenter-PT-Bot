"""테스트 공통 픽스처.

테스트는 **파일이 아닌 임시 SQLite 파일 DB** 를 쓴다. 인메모리(`:memory:`)를 쓰면
FastAPI 의 스레드풀에서 다른 커넥션이 열려 테이블이 안 보이는 문제가 생긴다.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# Settings 가 import 되기 전에 환경변수를 심어야 한다.
_TMP = Path(tempfile.mkdtemp(prefix="amr-test-"))
os.environ["AMR_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["AMR_MEDIA_ROOT"] = str(_TMP / "media")
os.environ["AMR_LOG_DIR"] = str(_TMP / "logs")
os.environ["AMR_LOG_TO_FILE"] = "false"
os.environ["AMR_LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.bridge import NullBridge, set_bridge  # noqa: E402
from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_schema() -> Iterator[None]:
    init_db(drop=True)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded(db: Session) -> Session:
    """시드 데이터가 들어간 DB. 멱등이라 여러 테스트가 공유해도 안전하다."""
    seed_all(db)
    return db


@pytest.fixture
def bridge() -> NullBridge:
    """매 테스트마다 새 브리지 — 하달 내역이 테스트 간에 섞이지 않게."""
    b = NullBridge()
    set_bridge(b)
    return b


@pytest.fixture
def client(seeded: Session, bridge: NullBridge) -> Iterator[TestClient]:
    """OPERATOR 권한 기본 클라이언트."""
    with TestClient(app, headers={"X-Role": "OPERATOR", "X-Operator": "tester"}) as c:
        yield c


@pytest.fixture
def admin_client(seeded: Session, bridge: NullBridge) -> Iterator[TestClient]:
    with TestClient(app, headers={"X-Role": "ADMIN", "X-Operator": "admin_tester"}) as c:
        yield c


@pytest.fixture
def viewer_client(seeded: Session, bridge: NullBridge) -> Iterator[TestClient]:
    with TestClient(app, headers={"X-Role": "VIEWER", "X-Operator": "viewer_tester"}) as c:
        yield c


def data_of(response) -> dict:
    """봉투를 벗겨 data 만 꺼낸다. 봉투 규격 자체도 함께 검증한다."""
    body = response.json()
    assert body["result"] == "SUCCESS", body
    return body["data"]
