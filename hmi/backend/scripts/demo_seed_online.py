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
from app.enums import MissionStatus, RobotState
from app.models import utcnow


DOCK_POSITION_RATIO = {
    "AMR-01": (0.15, 0.22),
    "AMR-02": (0.90, 0.82),
}


def main() -> None:
    from app.config import settings

    init_db()
    db = SessionLocal()
    try:
        from app import crud

        # config(AMR_ROBOT_IDS)에 정의된 로봇을 그대로 online/IDLE 로 올린다.
        default_batteries = [78, 52]
        active_map = crud.maps.get_active(db)
        for index, robot_id in enumerate(settings.robot_ids):
            battery = default_batteries[index] if index < len(default_batteries) else 60
            robot = crud.robots.ensure(db, robot_id)
            # 이전 데모의 활성 미션이 남아 있으면 다음 통합 순찰 시작이
            # MISSION_ALREADY_RUNNING 으로 실패한다. 데모 초기화 때 함께 정리한다.
            for mission in crud.robots.list_missions(db, robot_id=robot_id):
                if mission.status in {
                    MissionStatus.RUNNING.value,
                    MissionStatus.PENDING.value,
                    MissionStatus.PREEMPTED.value,
                }:
                    mission.status = MissionStatus.CANCELED.value
                    mission.end_time = utcnow()
            robot.status = RobotState.IDLE.value
            robot.online = True
            robot.battery = battery
            robot.current_node_id = None
            robot.current_mission_id = None
            ratio = DOCK_POSITION_RATIO.get(robot_id)
            if active_map is not None and ratio is not None:
                robot.x, robot.y = crud.maps.pixel_to_map(
                    active_map,
                    active_map.width * ratio[0],
                    active_map.height * ratio[1],
                )
            robot.last_seen = utcnow()
        db.commit()
        print(f"✅ {' · '.join(settings.robot_ids)} → online / IDLE 로 설정 완료.")
        print("   이제 AMR_HEARTBEAT_TIMEOUT_SEC=99999 로 uvicorn 을 띄우면 온라인이 유지됩니다.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
