"""§1 맵 · SLAM · ArUco."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crud, models
from app.bridge import get_bridge
from app.database import get_db
from app.enums import RobotState
from app.responses import ok
from app.schemas import ArucoIn, ArucoOut, MapOut, SlamSaveIn, SlamStartIn
from app.security import ActorDep, Permission, require

router = APIRouter(prefix="/map", tags=["맵 · SLAM"])
DbDep = Annotated[Session, Depends(get_db)]


@router.post("/slam/start", summary="SLAM 매핑 시작")
def slam_start(body: SlamStartIn, db: DbDep, actor: ActorDep = None):  # noqa: B008
    crud.robots.get(db, body.robot_id)
    new_map = crud.maps.create(db, name=body.map_name)
    crud.robots.set_state(db, body.robot_id, RobotState.MAPPING.value)
    get_bridge().publish_command(body.robot_id, "START_SLAM", {"map_id": new_map.map_id})
    db.commit()
    return ok({"map_id": new_map.map_id, "status": RobotState.MAPPING.value})


@router.post("/slam/save", summary="맵 저장")
def slam_save(body: SlamSaveIn, db: DbDep):
    m = crud.maps.get(db, body.map_id)
    # 실제 pgm/yaml 은 ROS map_saver 가 만든다. 여기서는 경로 규칙만 확정해 기록한다.
    m.image_path = m.image_path or f"/media/maps/{m.map_id}.pgm"
    m.yaml_path = m.yaml_path or f"/media/maps/{m.map_id}.yaml"
    crud.maps.set_active(db, m.map_id)
    crud.robots.set_state(db, body.robot_id, RobotState.IDLE.value)
    db.commit()
    return ok({"map_id": m.map_id, "image_path": m.image_path, "yaml_path": m.yaml_path})


@router.get("", summary="맵 목록")
def list_maps(db: DbDep):
    return ok([MapOut.from_model(m).model_dump() for m in crud.maps.list_all(db)])


@router.get("/active", summary="현재 활성 맵")
def active_map(db: DbDep):
    m = crud.maps.get_active(db)
    return ok(MapOut.from_model(m).model_dump() if m else None)


@router.get("/{map_id}", summary="맵 메타 조회")
def get_map(map_id: str, db: DbDep):
    return ok(MapOut.from_model(crud.maps.get(db, map_id)).model_dump())


@router.post(
    "/{map_id}/activate",
    summary="맵 활성화(현재 로드 맵 지정)",
    dependencies=[Depends(require(Permission.MASTER_EDIT))],
)
def activate_map(map_id: str, db: DbDep):
    """UI 에서 맵을 선택하면 그 맵을 현재 활성 맵으로 올린다(활성 맵은 항상 1장)."""
    m = crud.maps.set_active(db, map_id)
    db.commit()
    return ok(MapOut.from_model(m).model_dump())


@router.get("/{map_id}/grid", summary="점유격자(이동 가능 영역) 조회")
def get_occupancy_grid(map_id: str, db: DbDep):
    """맵의 PGM 점유격자를 base64 그레이스케일로 돌려준다.

    프론트는 이 값으로 'waypoint 를 백색(자유공간) 위에만 찍기'를 검증한다.
    - data_b64: row-major(위→아래·좌→우) 그레이스케일 바이트를 base64 로 인코딩
    - free_min: 이 값 이상이면 자유공간(백색). ROS 관례상 254(자유)/205(미탐색)/0(점유).
    PGM 이 없으면(데모 맵 등) available=false 로 응답한다(프론트는 제약 없이 동작).
    """
    m = crud.maps.get(db, map_id)
    occ = crud.maps.load_occupancy(m)
    if occ is None:
        return ok({"available": False, "width": m.width, "height": m.height})
    import base64

    width, height, data = occ
    return ok(
        {
            "available": True,
            "width": width,
            "height": height,
            "free_min": 250,  # 254(백색=자유)만 통과. 205(미탐색)/0(벽)은 제외.
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    )


@router.get("/{map_id}/aruco", summary="ArUco 마커 목록")
def list_aruco(map_id: str, db: DbDep):
    markers = crud.maps.list_markers(db, map_id)
    return ok([ArucoOut.model_validate(mk).model_dump() for mk in markers])


@router.post(
    "/aruco",
    summary="ArUco 마커 등록",
    dependencies=[Depends(require(Permission.MASTER_EDIT))],
)
def create_aruco(body: ArucoIn, db: DbDep):
    marker = crud.maps.upsert_marker(
        db,
        marker_id=body.marker_id,
        map_id=body.map_id,
        x=body.x,
        y=body.y,
        yaw=body.yaw,
        zone_id=body.zone_id,
    )
    db.commit()
    return ok({"marker_id": marker.marker_id})


@router.get("/{map_id}/transform", summary="좌표 변환 확인 (map ↔ pixel)")
def transform(map_id: str, db: DbDep, x: float = 0.0, y: float = 0.0):
    """프론트 좌표 변환식과 백엔드가 일치하는지 확인하는 진단용 엔드포인트."""
    m: models.Map = crud.maps.get(db, map_id)
    px, py = crud.maps.map_to_pixel(m, x, y)
    rx, ry = crud.maps.pixel_to_map(m, px, py)
    return ok({"map": {"x": x, "y": y}, "pixel": {"px": px, "py": py}, "roundtrip": {"x": rx, "y": ry}})
