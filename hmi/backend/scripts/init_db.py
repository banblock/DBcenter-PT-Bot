#!/usr/bin/env python3
"""DB 초기 마이그레이션 스크립트.

사용::

    python scripts/init_db.py            # 테이블 생성 + 시드
    python scripts/init_db.py --drop     # 전부 지우고 다시 (개발 전용)
    python scripts/init_db.py --no-seed  # 테이블만
    python scripts/init_db.py --check    # 현재 스키마 점검만

Alembic 을 쓰지 않는 이유: 단일 SQLite 파일이고 아직 스키마가 굳지 않았다.
운영 배포가 시작되면 이 스크립트를 Alembic 초기 리비전으로 옮겨야 한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine, init_db  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed_all  # noqa: E402


def check_schema() -> int:
    """모델 정의와 실제 DB 스키마가 어긋나지 않았는지 확인한다."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    expected = set(Base.metadata.tables)

    missing = sorted(expected - existing)
    extra = sorted(existing - expected - {"sqlite_sequence"})

    print(f"DB: {settings.database_url}")
    print(f"기대 테이블 {len(expected)}개 / 실제 {len(existing & expected)}개")

    for table in sorted(expected & existing):
        columns = {c["name"] for c in inspector.get_columns(table)}
        model_columns = set(Base.metadata.tables[table].columns.keys())
        if model_columns - columns:
            print(f"  ✗ {table}: 컬럼 누락 {sorted(model_columns - columns)}")
        else:
            index_count = len(inspector.get_indexes(table))
            print(f"  ✓ {table} ({len(columns)}컬럼, 인덱스 {index_count})")

    if missing:
        print(f"\n✗ 누락된 테이블: {missing}")
    if extra:
        print(f"\n⚠ 모델에 없는 테이블: {extra}")
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AMR 관제 DB 초기화")
    parser.add_argument("--drop", action="store_true", help="기존 테이블 삭제 후 재생성")
    parser.add_argument("--no-seed", action="store_true", help="시드 데이터 생략")
    parser.add_argument("--check", action="store_true", help="스키마 점검만 수행")
    args = parser.parse_args()

    setup_logging()

    if args.check:
        return check_schema()

    if args.drop:
        answer = input("기존 데이터가 전부 삭제됩니다. 계속하려면 'yes' 입력: ")
        if answer.strip().lower() != "yes":
            print("취소했습니다.")
            return 1

    init_db(drop=args.drop)

    if not args.no_seed:
        db = SessionLocal()
        try:
            counts = seed_all(db)
            print(f"시드 완료: {counts}")
        finally:
            db.close()

    return check_schema()


if __name__ == "__main__":
    raise SystemExit(main())
