# 로봇→DB/방송 연결 어댑터

"""robot_bridge → 백엔드 배선 (sink).

`app/robot_bridge.py` 의 `RobotBridge` 는 순수 로직(§10 규격)만 담고, DB 반영과 WS
브로드캐스트는 이 어댑터가 맡는다. 덕분에 브리지 본체는 ROS2/DB 없이 테스트되고,
운영에서는 이 sink 하나만 갈아 끼우면 실 데이터가 흐른다.

브로드캐스트는 `ConnectionManager.publish()` 로 큐에 넣는다 — ROS2 실행기 스레드에서
호출돼도 안전하도록(큐는 스레드 세이프) 설계돼 있다.
"""

from __future__ import annotations

from typing import Any

from app import crud
from app.connection_manager import manager
from app.database import SessionLocal
from app.enums import ROBOT_STATE_KO, WsMessageType
from app.logging_config import get_logger

log = get_logger("bridge")

#: robot_state 로 들어온 필드명 → Robot 컬럼명. 나머지(state_msg 등)는 텔레메트리에 안 넣는다.
_TELEMETRY_COLUMNS = {"state": "status", "x": "x", "y": "y", "theta": "theta", "battery": "battery"}


# [공부 메모] "sink" = 상행 데이터가 최종적으로 흘러 들어가는 곳(수챗구멍 느낌).
#   RobotBridge(§10 규칙)는 파싱만 하고, "실제 DB 저장 + 화면 방송"은 여기가 담당.
#   덕분에 RobotBridge는 DB/ROS 없이 테스트되고, 운영은 이 sink만 끼우면 실데이터가 흐름.
#   ※ 이 메서드들은 robot_bridge의 '컨슈머'가 이벤트 루프에서 불러줌 → DB 만져도 안전.
class BackendSink:
    """§10-1 구독 데이터를 DB + WS 로 흘려보내는 운영용 sink."""

    # ← 로봇이 올린 상태를 (1) DB에 저장(apply_telemetry) + (2) 화면에 방송(publish). 상행의 종착.
    def robot_status(self, robot_id: str, **fields: Any) -> None:
        mapped = {
            _TELEMETRY_COLUMNS[k]: v
            for k, v in fields.items()
            if k in _TELEMETRY_COLUMNS and v is not None
        }
        if not mapped:
            return
        db = SessionLocal()
        try:
            robot = crud.robots.apply_telemetry(db, robot_id, **mapped)
            db.commit()
            manager.publish(WsMessageType.ROBOT_STATUS.value, crud.robots.to_dict(robot))
        except Exception:  # noqa: BLE001 - 텔레메트리 한 프레임 실패가 브리지를 멈추면 안 된다
            db.rollback()
            log.exception("robot_status 반영 실패 (%s)", robot_id)
        finally:
            db.close()

    def pose_corrected(self, robot_id: str, payload: dict) -> None:
        manager.publish(WsMessageType.POSE_CORRECTED.value, {"robot_id": robot_id, **payload})

    def detection(self, robot_id: str, payload: dict) -> None:
        manager.publish(
            WsMessageType.DETECTION.value,
            {"source": "amr", "robot_id": robot_id, **payload},
        )

    def safety_event(self, robot_id: str, code: str, detail: str) -> None:
        db = SessionLocal()
        try:
            crud.robots.log_error(db, robot_id=robot_id, error_code=code, error_msg=detail)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("safety_event 기록 실패 (%s)", robot_id)
        finally:
            db.close()
        manager.publish(
            WsMessageType.SYSTEM_ALERT.value,
            {"code": code, "message": f"{robot_id}: {detail}", "severity": "CRITICAL"},
        )

    def checkpoint(self, robot_id: str, raw: str) -> None:
        """복귀점 문자열(``"STATE|node|step"``)을 파싱해 robot.checkpoint_json 에 보존."""
        parts = raw.split("|")
        checkpoint = {
            "interrupted_state": parts[0] if parts else None,
            "node_id": parts[1] if len(parts) > 1 else None,
            "progress_step": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
        }
        db = SessionLocal()
        try:
            robot = crud.robots.get(db, robot_id)
            robot.checkpoint_json = checkpoint
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("checkpoint 저장 실패 (%s)", robot_id)
        finally:
            db.close()

    def command_ack(self, robot_id: str, payload: dict) -> None:
        state = "수락" if payload.get("accepted") else "거부"
        eta = payload.get("eta_sec")
        msg = f"{robot_id} 명령 {payload.get('command_id')} {state}"
        if eta is not None:
            msg += f" (ETA {eta}s)"
        manager.publish(WsMessageType.LOG.value, {"level": "INFO", "actor": robot_id, "message": msg})
