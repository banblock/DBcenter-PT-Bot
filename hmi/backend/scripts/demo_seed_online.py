"""코드 리뷰 라이브 데모용 — 로봇을 '온라인/대기(IDLE)'로 올려둔다.

실장비(ROS 텔레메트리)가 없으면 로봇이 OFFLINE 로 남아 상태-의존 버튼(순찰 시작/도킹
등)이 화면에 안 뜬다. 이 스크립트로 amr_1·amr_2 를 online·IDLE·last_seen=now 로 만들면
프론트가 SNAPSHOT 을 받을 때 카드에 명령 버튼이 나타난다.

heartbeat 워치독이 3초 뒤 다시 OFFLINE 으로 내리지 않도록, 서버는 아래처럼 timeout 을
크게 잡아 띄운다:
    AMR_BRIDGE_BACKEND=loopback AMR_HEARTBEAT_TIMEOUT_SEC=99999 \
      ./.venv/bin/uvicorn app.main:app --port 8000

실행 순서:
    1) 이 스크립트 먼저:  ./.venv/bin/python -m scripts.demo_seed_online
    2) 그 다음 uvicorn 기동(위 명령) → 프론트 접속
"""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.enums import RobotState
from app.models import utcnow


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        from app import crud

        for robot_id, state, battery, node in (
            ("amr_1", RobotState.IDLE.value, 78, "N-001"),
            ("amr_2", RobotState.IDLE.value, 52, "N-004"),
        ):
            robot = crud.robots.ensure(db, robot_id)
            robot.status = state
            robot.online = True
            robot.battery = battery
            robot.current_node_id = node
            robot.last_seen = utcnow()
        db.commit()
        print("✅ amr_1 · amr_2 → online / IDLE 로 설정 완료.")
        print("   이제 AMR_HEARTBEAT_TIMEOUT_SEC=99999 로 uvicorn 을 띄우면 온라인이 유지됩니다.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
