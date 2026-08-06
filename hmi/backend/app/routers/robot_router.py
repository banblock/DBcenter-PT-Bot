"""§4 로봇 제어."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crud
from app.bridge import get_bridge
from app.connection_manager import manager
from app.database import get_db
from app.enums import RobotState, WsMessageType
from app.errors import ApiError, E
from app.responses import ok
from app.schemas import EmergencyStopIn, EvacuateIn, GotoIn, ResumeIn
from app.security import ActorDep, Permission, require

router = APIRouter(prefix="/robots", tags=["로봇 제어"])
DbDep = Annotated[Session, Depends(get_db)]

RobotControl = Depends(require(Permission.ROBOT_CONTROL))
EStop = Depends(require(Permission.EMERGENCY_STOP))


@router.get("", summary="로봇 목록/현재 상태")
def list_robots(db: DbDep):
    return ok([crud.robots.to_dict(r) for r in crud.robots.list_all(db)])


@router.get("/{robot_id}", summary="로봇 상세")
def get_robot(robot_id: str, db: DbDep):
    return ok(crud.robots.to_dict(crud.robots.get(db, robot_id)))


@router.post("/emergency-stop-all", summary="전체 긴급정지", dependencies=[EStop])
async def emergency_stop_all(db: DbDep, actor: ActorDep):
    """전체 정지는 개별 정지의 반복이 아니라 별도 경로다.

    한 대에서 실패해도 나머지는 반드시 세워야 하므로, 예외를 잡아 계속 진행하고
    실패 목록을 응답에 담는다.
    """
    stopped, failed = [], []
    for robot in crud.robots.list_all(db):
        try:
            _do_estop(db, robot.robot_id, "OPERATOR_ALL")
            stopped.append(robot.robot_id)
        except Exception as exc:  # noqa: BLE001 - 한 대 실패가 전체를 막으면 안 된다
            failed.append({"robot_id": robot.robot_id, "error": str(exc)})
    db.commit()
    await manager.publish_async(
        WsMessageType.SYSTEM_ALERT.value,
        {"code": "EMERGENCY_STOP_ALL", "message": f"{actor.name} 전체 긴급정지", "severity": "CRITICAL"},
    )
    return ok({"stopped": stopped, "failed": failed})


def _do_estop(db: Session, robot_id: str, reason: str) -> dict:
    robot = crud.robots.get(db, robot_id)
    # 복귀 지점 보존 (체크리스트 checkpoint 보존 로직)
    checkpoint = {
        "interrupted_state": robot.status,
        "node_id": robot.current_node_id,
        "progress_step": robot.progress_step,
    }
    robot.checkpoint_json = checkpoint
    robot.status = RobotState.EMERGENCY_STOP.value
    get_bridge().publish_command(robot_id, "ESTOP", {"reason": reason})
    return checkpoint


@router.post("/{robot_id}/emergency-stop", summary="개별 긴급정지", dependencies=[EStop])
async def emergency_stop(robot_id: str, body: EmergencyStopIn, db: DbDep):
    checkpoint = _do_estop(db, robot_id, body.reason)
    db.commit()
    await manager.publish_async(
        WsMessageType.ROBOT_STATE_CHANGED.value,
        {
            "robot_id": robot_id,
            "from": checkpoint["interrupted_state"],
            "to": RobotState.EMERGENCY_STOP.value,
            "reason": body.reason,
            "checkpoint": checkpoint,
        },
    )
    return ok(
        {"robot_id": robot_id, "state": RobotState.EMERGENCY_STOP.value, "checkpoint": checkpoint}
    )


@router.post("/{robot_id}/resume", summary="동작 재개(긴급정지 해제)", dependencies=[RobotControl])
async def resume(robot_id: str, body: ResumeIn, db: DbDep):
    robot = crud.robots.get(db, robot_id)
    if robot.status not in (RobotState.EMERGENCY_STOP.value, RobotState.ERROR.value):
        raise ApiError(
            E.CONFLICT,
            f"{robot.status} 상태에서는 재개할 수 없습니다 (EMERGENCY_STOP/ERROR 만 가능)",
        )
    checkpoint = robot.checkpoint_json or {}
    if body.mode == "RETURN_HOME":
        robot.status = RobotState.DOCKING.value
        resumed_from = None
    else:
        robot.status = checkpoint.get("interrupted_state") or RobotState.IDLE.value
        robot.progress_step = checkpoint.get("progress_step", 0)
        resumed_from = checkpoint.get("node_id")
    robot.checkpoint_json = None
    get_bridge().publish_command(robot_id, "RESET", {"mode": body.mode})
    db.commit()
    await manager.publish_async(WsMessageType.ROBOT_STATUS.value, crud.robots.to_dict(robot))
    return ok({"state": robot.status, "resumed_from": resumed_from})


@router.post("/{robot_id}/goto", summary="지도 클릭 이동 (다지점)", dependencies=[RobotControl])
def goto(robot_id: str, body: GotoIn, db: DbDep):
    robot = crud.robots.get(db, robot_id)
    if robot.status == RobotState.OFFLINE.value:
        raise ApiError(E.ROBOT_OFFLINE, f"{robot_id} 가 오프라인이라 이동 명령을 보낼 수 없습니다")
    result = get_bridge().publish_command(
        robot_id,
        "GOTO",
        {"waypoints": [w.model_dump() for w in body.waypoints], "preempt": body.preempt},
    )
    db.commit()
    # eta 는 Nav2 가 계산해 command_ack 로 돌려준다. 아직 브리지가 없으면 null.
    return ok({**result, "eta_sec": None})


@router.post("/{robot_id}/dock", summary="도킹 복귀", dependencies=[RobotControl])
def dock(robot_id: str, db: DbDep):
    robot = crud.robots.get(db, robot_id)
    robot.status = RobotState.DOCKING.value
    get_bridge().publish_command(robot_id, "DOCK", {"dock_id": robot.dock_id})
    db.commit()
    return ok({"state": robot.status})


@router.post("/{robot_id}/evacuate", summary="구역 대피 (진압 인터락용)", dependencies=[RobotControl])
def evacuate(robot_id: str, body: EvacuateIn, db: DbDep):
    crud.robots.get(db, robot_id)
    crud.patrol.get_zone(db, body.zone_id)
    result = get_bridge().publish_command(robot_id, "EVACUATE", {"zone_id": body.zone_id})
    db.commit()
    return ok({"accepted": result["accepted"], "eta_sec": None})
