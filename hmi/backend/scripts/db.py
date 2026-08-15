"""터미널 SQLite 뷰어 — sqlite3 CLI 가 없어도 DB 를 들여다본다.

사용:
    ./.venv/bin/python scripts/db.py                 # 요약(로봇·미션·이벤트 최근)
    ./.venv/bin/python scripts/db.py tables          # 테이블 목록 + 행 수
    ./.venv/bin/python scripts/db.py "SELECT * FROM tb_robots"   # 임의 SQL

서버가 켜져 있어도 읽기 전용(mode=ro)으로 열어 WAL 포함 최신 커밋 데이터를 본다.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "amr.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (행 없음)")
        return
    cols = rows[0].keys()
    widths = {c: max(len(str(c)), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  " + " | ".join(str(c).ljust(widths[c]) for c in cols))
    print("  " + "-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + " | ".join(str(r[c]).ljust(widths[c]) for c in cols))


def tables(con: sqlite3.Connection) -> None:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    print(f"테이블 {len(rows)}개:")
    for r in rows:
        n = con.execute(f"SELECT COUNT(*) FROM {r['name']}").fetchone()[0]
        print(f"  {r['name']:<24} {n:>6} 행")


def summary(con: sqlite3.Connection) -> None:
    print("=== 로봇 (tb_robots) ===")
    _print_rows(con.execute(
        "SELECT robot_id, status, online, battery, current_node_id, current_mission_id FROM tb_robots ORDER BY robot_id"
    ).fetchall())
    print("\n=== 미션 최근 5건 (tb_missions) ===")
    _print_rows(con.execute(
        "SELECT mission_id, mission_type, robot_id, status, progress FROM tb_missions ORDER BY created_at DESC LIMIT 5"
    ).fetchall())
    print("\n=== 이벤트 최근 5건 (tb_events) ===")
    _print_rows(con.execute(
        "SELECT event_id, type, severity, status, zone_id FROM tb_events ORDER BY detected_at DESC LIMIT 5"
    ).fetchall())


def main() -> None:
    if not DB.exists():
        print(f"DB 없음: {DB}  (먼저 scripts/init_db.py 실행)")
        return
    arg = " ".join(sys.argv[1:]).strip()
    con = connect()
    try:
        if not arg:
            summary(con)
        elif arg.lower() == "tables":
            tables(con)
        else:
            _print_rows(con.execute(arg).fetchall())
    finally:
        con.close()


if __name__ == "__main__":
    main()
