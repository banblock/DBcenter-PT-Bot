"""§2 구역·노드·경로 + §3 순찰 미션."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.bridge import get_bridge
from app.connection_manager import manager
from app.crud import ids
from app.database import get_db
from app.enums import MissionStatus, RobotState, WsMessageType
from app.errors import ApiError, E
from app.services import node_lock
from app.models import utcnow
from app.responses import ok
from app.schemas import (
    MissionReorderIn,
    NodeIn,
    NodeOut,
    NodeUpdate,
    PatrolReasonIn,
    PatrolStartIn,
    RouteIn,
    RouteOut,
    ZoneIn,
    ZoneOut,
)
from app.security import ActorDep, Permission, require

DbDep = Annotated[Session, Depends(get_db)]

zone_router = APIRouter(prefix="/zones", tags=["구역"])
node_router = APIRouter(prefix="/nodes", tags=["순찰 노드"])
route_router = APIRouter(prefix="/routes", tags=["순찰 경로"])
patrol_router = APIRouter(prefix="/patrol", tags=["순찰 미션"])

MasterEdit = Depends(require(Permission.MASTER_EDIT))
RobotControl = Depends(require(Permission.ROBOT_CONTROL))


# ══════════════════════════════════════════════════════════════════════════
# 구역
# ══════════════════════════════════════════════════════════════════════════
@zone_router.post("", summary="구역 생성", dependencies=[MasterEdit])
def create_zone(body: ZoneIn, db: DbDep):
    zone = crud.patrol.create_zone(
        db,
        zone_id=body.zone_id,
        name=body.name,
        polygon=body.polygon,
        risk_base=body.risk_base,
        camera_ids=body.camera_ids,
    )
    db.commit()
    return ok({"zone_id": zone.zone_id}, status_code=201)


@zone_router.get("", summary="구역 목록")
def list_zones(db: DbDep):
    return ok([ZoneOut.model_validate(z).model_dump(by_alias=False) for z in crud.patrol.list_zones(db)])


@zone_router.get("/{zone_id}", summary="구역 상세")
def get_zone(zone_id: str, db: DbDep):
    return ok(ZoneOut.model_validate(crud.patrol.get_zone(db, zone_id)).model_dump(by_alias=False))


@zone_router.put("/{zone_id}", summary="구역 수정", dependencies=[MasterEdit])
def update_zone(zone_id: str, body: ZoneIn, db: DbDep):
    zone = crud.patrol.update_zone(
        db,
        zone_id,
        name=body.name,
        polygon=body.polygon,
        risk_base=body.risk_base,
        camera_ids=body.camera_ids,
    )
    db.commit()
    return ok({"zone_id": zone.zone_id})


@zone_router.delete("/{zone_id}", summary="구역 삭제", dependencies=[MasterEdit])
def delete_zone(zone_id: str, db: DbDep):
    crud.patrol.delete_zone(db, zone_id)
    db.commit()
    return ok({"zone_id": zone_id, "deleted": True})


# ══════════════════════════════════════════════════════════════════════════
# 순찰 노드
# ══════════════════════════════════════════════════════════════════════════
@node_router.post("", summary="순찰 노드 생성", dependencies=[MasterEdit])
def create_node(body: NodeIn, db: DbDep):
    node = crud.patrol.create_node(
        db,
        node_id=body.node_id,
        map_id=body.map_id,
        zone_id=body.zone_id,
        name=body.name,
        x=body.x,
        y=body.y,
        theta=body.theta,
        dwell_sec=body.dwell_sec,
        is_blindspot=body.is_blindspot,
        manual_weight=body.manual_weight,
        inspect_targets=body.inspect_targets,
    )
    db.commit()
    return ok({"node_id": node.node_id}, status_code=201)


@node_router.get("", summary="순찰 노드 목록")
def list_nodes(
    db: DbDep,
    map_id: Annotated[str | None, Query()] = None,
    zone_id: Annotated[str | None, Query()] = None,
):
    nodes = crud.patrol.list_nodes(db, map_id=map_id, zone_id=zone_id)
    return ok([NodeOut.model_validate(n).model_dump() for n in nodes])


@node_router.get("/{node_id}", summary="순찰 노드 상세")
def get_node(node_id: str, db: DbDep):
    return ok(NodeOut.model_validate(crud.patrol.get_node(db, node_id)).model_dump())


@node_router.put("/{node_id}", summary="순찰 노드 수정", dependencies=[MasterEdit])
def update_node(node_id: str, body: NodeUpdate, db: DbDep):
    node = crud.patrol.update_node(db, node_id, **body.model_dump(exclude_unset=True))
    db.commit()
    return ok({"node_id": node.node_id})


@node_router.delete("/{node_id}", summary="순찰 노드 삭제", dependencies=[MasterEdit])
def delete_node(node_id: str, db: DbDep):
    crud.patrol.delete_node(db, node_id)
    db.commit()
    return ok({"node_id": node_id, "deleted": True})


# ══════════════════════════════════════════════════════════════════════════
# 순찰 경로
# ══════════════════════════════════════════════════════════════════════════
@route_router.post("", summary="순찰 경로 생성", dependencies=[MasterEdit])
def create_route(body: RouteIn, db: DbDep):
    route = crud.patrol.create_route(
        db,
        route_id=body.route_id,
        name=body.name,
        map_id=body.map_id,
        node_order=body.node_order,
        loop=body.loop,
    )
    db.commit()
    return ok({"route_id": route.route_id}, status_code=201)


@route_router.get("", summary="순찰 경로 목록")
def list_routes(db: DbDep):
    return ok([RouteOut.model_validate(r).model_dump(by_alias=False) for r in crud.patrol.list_routes(db)])


@route_router.get("/{route_id}", summary="순찰 경로 상세")
def get_route(route_id: str, db: DbDep):
    return ok(RouteOut.model_validate(crud.patrol.get_route(db, route_id)).model_dump(by_alias=False))


@route_router.put("/{route_id}", summary="순찰 경로 수정", dependencies=[MasterEdit])
def update_route(route_id: str, body: RouteIn, db: DbDep):
    route = crud.patrol.update_route(
        db, route_id, name=body.name, map_id=body.map_id, node_order=body.node_order, loop=body.loop
    )
    db.commit()
    return ok({"route_id": route.route_id})


@route_router.delete("/{route_id}", summary="순찰 경로 삭제", dependencies=[MasterEdit])
def delete_route(route_id: str, db: DbDep):
    crud.patrol.delete_route(db, route_id)
    db.commit()
    return ok({"route_id": route_id, "deleted": True})


@route_router.post("/{route_id}/validate", summary="경로 유효성 검증")
def validate_route(route_id: str, db: DbDep):
    return ok(crud.patrol.validate_route(db, route_id).model_dump())


# ══════════════════════════════════════════════════════════════════════════
# 순찰 미션
# ══════════════════════════════════════════════════════════════════════════
def _split_nodes(node_order: list[str], robot_ids: list[str]) -> dict[str, list[str]]:
    """노드 분배 (B-24) — 라운드로빈.

    구역 분할이나 최근접 배정이 더 좋지만, 그건 로봇 현재 위치와 코스트맵이
    필요하다. 여기서는 결정적(deterministic)이고 검증하기 쉬운 라운드로빈을 쓴다.
    dispatcher 서비스가 붙으면 이 함수만 교체하면 된다.
    """
    assignment: dict[str, list[str]] = {rid: [] for rid in robot_ids}
    for index, node_id in enumerate(node_order):
        assignment[robot_ids[index % len(robot_ids)]].append(node_id)
    return assignment


@patrol_router.post("/start", summary="순찰 시작", dependencies=[RobotControl])
async def start_patrol(body: PatrolStartIn, db: DbDep, actor: ActorDep):
    route = crud.patrol.get_route(db, body.route_id)
    node_order: list[str] = list(route.node_order_json or [])
    if not node_order:
        raise ApiError(E.ROUTE_EMPTY, f"경로에 노드가 없습니다: {route.route_id}")

    # 이미 뛰고 있는 로봇이 하나라도 있으면 409 (명세서 3-1)
    for robot_id in body.robot_ids:
        crud.robots.get(db, robot_id)
        running = crud.robots.active_mission_for(db, robot_id)
        if running is not None:
            raise ApiError(
                E.MISSION_ALREADY_RUNNING,
                f"{robot_id} 는 이미 미션 {running.mission_id} 을 수행 중입니다",
                data={"robot_id": robot_id, "mission_id": running.mission_id},
            )

    if body.apply_priority:
        # 점수 높은 노드를 앞으로. 점수가 같으면 원래 순서를 유지한다(안정 정렬).
        scores = {n.node_id: n.priority_score for n in crud.patrol.list_nodes(db)}
        node_order.sort(key=lambda nid: -scores.get(nid, 0.0))

    # 경로 겹침 사전 검사 (B-28) — 다른 로봇이 이미 점유 중인 노드를 배분 대상에서 뺀다.
    # 이렇게 하면 두 로봇이 같은 노드로 동시에 배정되는 일(중복/데드락)이 원천 차단된다.
    claim = node_lock.claim(db, body.robot_ids, node_order)
    if not claim.granted:
        raise ApiError(
            E.NODE_LOCKED,
            "요청한 경로의 모든 노드가 다른 로봇에 점유되어 있습니다",
            data={"conflicts": claim.conflicts_as_dicts()},
        )

    # granted 노드만 배분한다. 노드를 하나도 못 받은 로봇은 미션을 만들지 않는다
    # (점유 충돌로 배분할 노드가 부족한 경우 — conflicts 로 사유를 응답에 남긴다).
    assignment = {
        robot_id: nodes
        for robot_id, nodes in _split_nodes(claim.granted, body.robot_ids).items()
        if nodes
    }
    assigned_payload = []
    missions = []

    for robot_id, nodes in assignment.items():
        mission = crud.robots.create_mission(
            db,
            mission_id=ids.next_mission_id(),
            mission_type="PATROL",
            route_id=route.route_id,
            robot_id=robot_id,
            status=MissionStatus.RUNNING.value,
            node_order_json=nodes,
            current_node_id=nodes[0] if nodes else None,
            next_node_id=nodes[1] if len(nodes) > 1 else None,
            loop=(body.mode == "LOOP"),
            start_time=utcnow(),
        )
        robot = crud.robots.get(db, robot_id)
        robot.current_mission_id = mission.mission_id
        robot.status = RobotState.UNDOCKING.value
        robot.progress_step = 1
        missions.append(mission)
        assigned_payload.append({"robot_id": robot_id, "nodes": nodes})

        node_rows = {n.node_id: n for n in crud.patrol.list_nodes(db)}
        get_bridge().publish_command(
            robot_id,
            "START_PATROL",
            {
                "mission_id": mission.mission_id,
                "nodes": [
                    {
                        "node_id": nid,
                        "x": node_rows[nid].x,
                        "y": node_rows[nid].y,
                        "theta": node_rows[nid].theta,
                        "dwell_sec": node_rows[nid].dwell_sec,
                    }
                    for nid in nodes
                    if nid in node_rows
                ],
                "loop": body.mode == "LOOP",
            },
        )

    db.commit()
    for mission in missions:
        await manager.publish_async(
            WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)
        )

    return ok(
        {
            "mission_id": missions[0].mission_id if missions else None,
            "mission_ids": [m.mission_id for m in missions],
            "status": MissionStatus.RUNNING.value,
            "assigned": assigned_payload,
            "conflicts": claim.conflicts_as_dicts(),
        }
    )


def _transition(db: Session, mission_id: str, to: str, allowed_from: set[str]) -> object:
    mission = crud.robots.get_mission(db, mission_id)
    if mission.status not in allowed_from:
        raise ApiError(
            E.MISSION_NOT_RUNNING,
            f"{mission.status} 상태에서는 {to} 로 전이할 수 없습니다",
            data={"mission_id": mission_id, "status": mission.status},
        )
    mission.status = to
    return mission


@patrol_router.post("/{mission_id}/pause", summary="순찰 일시정지", dependencies=[RobotControl])
async def pause_patrol(mission_id: str, body: PatrolReasonIn, db: DbDep):
    mission = _transition(db, mission_id, MissionStatus.PREEMPTED.value, {MissionStatus.RUNNING.value})
    # 재개에 필요한 최소 정보만 저장 — 남은 노드와 현재 인덱스 (체크리스트 §1-4)
    order = list(mission.node_order_json or [])
    index = order.index(mission.current_node_id) if mission.current_node_id in order else 0
    mission.resume_context_json = {
        "remaining_nodes": order[index:],
        "current_index": index,
        "reason": body.reason,
    }
    if mission.robot_id:
        crud.robots.set_state(db, mission.robot_id, RobotState.PATROL_PAUSED.value)
        get_bridge().publish_command(
            mission.robot_id, "PAUSE", {"mission_id": mission_id, "reason": body.reason}
        )
    db.commit()
    await manager.publish_async(
        WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)
    )
    return ok({"mission_id": mission_id, "status": mission.status})


@patrol_router.post("/{mission_id}/resume", summary="순찰 재개", dependencies=[RobotControl])
async def resume_patrol(mission_id: str, db: DbDep):
    mission = _transition(db, mission_id, MissionStatus.RUNNING.value, {MissionStatus.PREEMPTED.value})
    context = mission.resume_context_json or {}
    resumed_node = (context.get("remaining_nodes") or [None])[0]
    mission.current_node_id = resumed_node or mission.current_node_id
    mission.resume_context_json = None
    if mission.robot_id:
        crud.robots.set_state(db, mission.robot_id, RobotState.RESUMING.value)
        get_bridge().publish_command(mission.robot_id, "RESUME", {"mission_id": mission_id})
    db.commit()
    await manager.publish_async(
        WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)
    )
    return ok({"mission_id": mission_id, "status": mission.status, "resumed_node_id": resumed_node})


@patrol_router.post("/{mission_id}/cancel", summary="순찰 취소", dependencies=[RobotControl])
async def cancel_patrol(mission_id: str, body: PatrolReasonIn, db: DbDep):
    mission = _transition(
        db,
        mission_id,
        MissionStatus.CANCELED.value,
        {MissionStatus.RUNNING.value, MissionStatus.PREEMPTED.value, MissionStatus.PENDING.value},
    )
    mission.end_time = utcnow()
    if mission.robot_id:
        robot = crud.robots.get(db, mission.robot_id)
        robot.current_mission_id = None
        robot.status = RobotState.IDLE.value
        get_bridge().publish_command(
            mission.robot_id, "CANCEL", {"mission_id": mission_id, "reason": body.reason}
        )
    db.commit()
    await manager.publish_async(
        WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, mission)
    )
    return ok({"mission_id": mission_id, "status": mission.status})


@patrol_router.get("/missions", summary="작업 큐 조회")
def list_missions(
    db: DbDep,
    status: Annotated[str | None, Query()] = None,
    robot_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    rows = crud.robots.list_missions(db, status=status, robot_id=robot_id, limit=limit)
    data = [crud.robots.mission_to_dict(db, m) for m in rows]
    return ok(data, message=f"{len(data)}건")


@patrol_router.put("/missions/reorder", summary="큐 수동 재정렬", dependencies=[RobotControl])
def reorder(body: MissionReorderIn, db: DbDep):
    applied = crud.robots.reorder_queue(db, body.order)
    db.commit()
    return ok({"applied": applied == len(body.order), "reordered": applied})


@patrol_router.get("/missions/{mission_id}", summary="미션 상세")
def get_mission(mission_id: str, db: DbDep):
    return ok(crud.robots.mission_to_dict(db, crud.robots.get_mission(db, mission_id)))
