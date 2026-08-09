"""REST 라우터 등록.

`ALL_ROUTERS` 순서가 곧 OpenAPI 문서의 섹션 순서다. API 명세서 §1~§8 순서를 지킨다.
main.py 는 이 목록만 순회하면 되고, 라우터를 추가할 때 main.py 를 건드리지 않는다.
"""

from fastapi import APIRouter

from app.routers import (
    cameras_router,
    equipment_router,
    event_router,
    map_router,
    patrol_router,
    robot_router,
    stats_router,
    suppression_router,
)

ALL_ROUTERS: list[APIRouter] = [
    map_router.router,                     # §1 맵 · SLAM
    patrol_router.zone_router,             # §2 구역
    patrol_router.node_router,             # §2 노드
    patrol_router.route_router,            # §2 경로
    patrol_router.patrol_router,           # §3 순찰 미션
    robot_router.router,                   # §4 로봇 제어
    event_router.router,                   # §5 이상 이벤트
    equipment_router.equipment_router,     # §6 설비 마스터
    equipment_router.work_order_router,    # §6 작업지시
    equipment_router.align_router,         # §6 대조 판정
    stats_router.priority_router,          # §7 순찰 우선순위
    stats_router.stats_router,             # §7 통계
    suppression_router.router,             # §8 화재진압
    suppression_router.config_router,      # §8 시스템 설정
    cameras_router.router,                 # §9 카메라 피드(MJPEG, 비전 통합)
]

__all__ = ["ALL_ROUTERS"]
