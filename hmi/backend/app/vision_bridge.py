"""비전(ROS `/detection/*`) ↔ 백엔드 브리지.

`app/robot_bridge.py`의 Ros2Bridge와 **같은 producer–consumer 구조**다:
  · 상행: ROS 콜백(rclpy 스레드) = 프로듀서. 값만 뽑아 loop.call_soon_threadsafe 로 큐에 넣기만.
    이벤트 루프의 컨슈머 코루틴이 큐를 비우며 detection.ingest(이벤트 생성)+WS 방송.
  · 하행: `/ui/start`(Bool) 발행 = 통합순찰 시작 시 CCTV 감지 on/off (rclpy 발행은 스레드 안전).

Phase 1 범위 (docs/… 인수인계서 §5): `/detection/cam_state` 구독 → 이벤트 파이프라인 연결.
카메라 이미지 피드(MJPEG)·차단기(CheckGate)는 Phase 2.

rclpy·patrol_interfaces 가 없으면(개발/CI) 임포트 실패로 끝난다 → 백엔드는 vision 없이 그대로 동작.
실행 전제: colcon build(patrol_interfaces, vision_detection) 후 install/setup.bash 를 source 해야 함.
"""

from __future__ import annotations

from typing import Any

from app import crud
from app.connection_manager import manager
from app.database import SessionLocal
from app.enums import EventType, WsMessageType
from app.logging_config import get_logger
from app.services import detection, dispatch, homography

log = get_logger("vision")

# ── 토픽 이름 ────────────────────────────────────────────────────────────────
CAM_STATE_TOPIC = "/detection/cam_state"
UI_START_TOPIC = "/ui/start"  # TODO(§7): 규칙상 백엔드 발행이면 /backend/start 로 통일 검토(팀 조율)

# CamState.state(0/1/2) → 백엔드 EventType. COOLANT=2 는 냉각수 누수 = LEAK.
_STATE_TO_TYPE: dict[int, str] = {
    0: EventType.FIRE.value,
    1: EventType.SMOKE.value,
    2: EventType.LEAK.value,
}

# CamState.camera_id → 이벤트 enrich (CamState 가 얇아서 여기서 채운다).
# ★조정 필요(§8): cctv↔zone, robot3/robot8↔amr_1/amr_2, AMR 캠 zone(로봇 현재존)은 추후.
VISION_CAMERA_MAP: dict[int, dict[str, Any]] = {
    0: {"source": "cctv", "camera_id": "cctv1", "zone_id": "Z01"},   # cctv1
    1: {"source": "cctv", "camera_id": "cctv2", "zone_id": "Z02"},   # cctv2
    2: {"source": "amr", "robot_id": "AMR-01", "zone_id": None},      # robot3 cam
    3: {"source": "amr", "robot_id": "AMR-02", "zone_id": None},      # robot8 cam
}
DEFAULT_CONFIDENCE = 0.9  # CamState.confidence 가 0(미설정)일 때 쓰는 기본값
INBOUND_QUEUE_SIZE = 500

# CamState bbox 픽셀 = (x1, y1, x2, y2). 아래 순수 함수는 rclpy 없이 pytest 로 검증된다.
Bbox = tuple[float, float, float, float]


def valid_bbox(bbox: Bbox | None) -> bool:
    """면적이 양(+)인 정상 bbox 인가. -1/0(해제·미설정) 은 무효로 걸러진다."""
    if bbox is None:
        return False
    x1, y1, x2, y2 = bbox
    return x2 > x1 and y2 > y1


def detection_to_map_xy(camera_id: str | None, bbox: Bbox | None) -> tuple[float, float] | None:
    """CCTV 픽셀 bbox → 맵 (x, y). 좌표를 못 구하면 None.

    None 이 되는 경우: camera_id 없음(AMR 캠은 여기 매핑 없음) / bbox 무효(해제·미설정) /
    해당 카메라 캘리브레이션 없음 / homography off / 원근 발산(W≈0).
    None 이면 호출부는 좌표 없이 이벤트만 만든다(현행 동작과 동일).
    """
    if not camera_id or not valid_bbox(bbox):
        return None
    u, v = homography.bbox_anchor(list(bbox), "bottom_center")  # 하단중앙 = 바닥 접지점
    return homography.pixel_to_map(camera_id, u, v)

# ── 차단기 검사 서비스 (Phase 2-2) ──────────────────────────────────────────
GATE_CHECK_SERVICE = "/backend/check_gate"  # 서버=detect_main_node, 백엔드=클라이언트
# 백엔드 robot_id(AMR-01) → CheckGate.robot_id(정수, 로봇 캠 번호 /robot3·/robot8 기준).
# UI/DB 로봇 ID 를 AMR-01/AMR-02 로 통일(2026-08-10 UI 통합). robot3/robot8 캠 번호는 유지.
ROBOT_NUM: dict[str, int] = {"AMR-01": 3, "AMR-02": 8}

# ── 카메라 이미지 피드 (Phase 2) ────────────────────────────────────────────
# camera_id(웹에서 /api/cameras/{id}/stream) → 비전이 발행하는 CompressedImage 토픽.
# 전부 JPEG(CompressedImage)라 백엔드는 디코딩 없이 msg.data(bytes)를 그대로 흘린다.
# robot3/robot8 캠 = AMR-01/AMR-02(2026-08-10 UI 통합). AMR 캠은 이상 감지 중에만 프레임이 온다.
VISION_IMAGE_TOPICS: dict[str, str] = {
    "cctv1": "/detection/cctv1/detection_image",
    "cctv2": "/detection/cctv2/detection_image",
    "AMR-01": "/detection/robot3_cam/detection_image",
    "AMR-02": "/detection/robot8_cam/detection_image",
}


class VisionBridge:
    """비전 ROS 노드 + 상행 프로듀서-컨슈머 관리자."""

    def __init__(self, loop: Any) -> None:  # pragma: no cover - rclpy 필요
        import asyncio
        import threading

        import rclpy
        from patrol_interfaces.msg import CamState
        from patrol_interfaces.srv import CheckGate
        from rclpy.node import Node
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import CompressedImage
        from std_msgs.msg import Bool

        self._loop = loop
        self._inbound: asyncio.Queue = asyncio.Queue(maxsize=INBOUND_QUEUE_SIZE)
        self._stop = threading.Event()
        # 카메라별 최신 JPEG 1장만 유지(오래된 건 버림). MJPEG 엔드포인트가 여기서 읽는다.
        self._frames: dict[str, bytes] = {}
        self._frames_lock = threading.Lock()

        producer = self._produce
        store = self._store_frame

        # 비전 이미지 발행 QoS(BEST_EFFORT)와 맞춰야 프레임을 받는다(RELIABLE 구독이면 못 받음).
        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        class _Node(Node):
            def __init__(self) -> None:
                super().__init__("vision_bridge")
                self.create_subscription(
                    CamState, CAM_STATE_TOPIC,
                    lambda m: producer(
                        int(m.camera_id), int(m.state),
                        (float(m.bbox_x1), float(m.bbox_y1), float(m.bbox_x2), float(m.bbox_y2)),
                        float(m.confidence),
                    ), 10,
                )
                self.start_pub = self.create_publisher(Bool, UI_START_TOPIC, 1)
                # 차단기 검사 서비스 클라이언트(백엔드가 호출).
                self.gate_client = self.create_client(CheckGate, GATE_CHECK_SERVICE)
                # 카메라 이미지 구독 — 콜백은 최신 JPEG bytes 만 저장(디코딩 안 함).
                for cam_id, topic in VISION_IMAGE_TOPICS.items():
                    self.create_subscription(
                        CompressedImage, topic,
                        lambda m, c=cam_id: store(c, bytes(m.data)), image_qos,
                    )

            def set_active(self, active: bool) -> None:
                self.start_pub.publish(Bool(data=bool(active)))

        if not rclpy.ok():
            rclpy.init()
        self._node = _Node()

        self._consumer_task = loop.create_task(self._consume())
        self._spin_thread = threading.Thread(target=self._spin, name="vision-spin", daemon=True)
        self._spin_thread.start()
        log.info("[vision_bridge] 기동 — 구독 %s, 발행 %s", CAM_STATE_TOPIC, UI_START_TOPIC)

    # ── 하행: 통합순찰 시작/중지 시 CCTV 감지 on/off ──────────────────────
    def set_detection_active(self, active: bool) -> None:  # pragma: no cover
        self._node.set_active(active)
        log.info("[vision_bridge] /ui/start=%s 발행", active)

    # ── 차단기 검사 (Phase 2-2) : /backend/check_gate 서비스 호출 ──────────
    async def check_gate(
        self, robot_id: int, gate_id: int, gate_state: bool, timeout: float = 6.0
    ) -> dict | None:  # pragma: no cover - rclpy 서비스 필요
        """비전(detect_main_node)에 차단기 대조를 요청한다.

        gate_state = DB 기준(백엔드가 작업지시까지 반영해 계산한 ON 여부 bool. ON=True/OFF=False).
        반환: {gate_state_equal, error_state} 또는 None(서비스 미준비/타임아웃).
        rclpy future 를 이벤트 루프에서 폴링해 기다린다(스핀 스레드가 응답을 처리해줌).
        """
        import asyncio
        import time

        from patrol_interfaces.srv import CheckGate

        client = self._node.gate_client
        # 서비스 준비 대기(논블로킹 폴링, 최대 1초) — 이벤트 루프를 막지 않는다.
        ready_deadline = time.monotonic() + 1.0
        while not client.service_is_ready() and time.monotonic() < ready_deadline:
            await asyncio.sleep(0.05)
        if not client.service_is_ready():
            log.warning("[vision_bridge] %s 서비스 미준비", GATE_CHECK_SERVICE)
            return None

        req = CheckGate.Request()
        req.robot_id = int(robot_id)
        req.gate_id = int(gate_id)
        req.gate_state = bool(gate_state)
        future = client.call_async(req)

        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if not future.done():
            log.warning("[vision_bridge] check_gate 응답 타임아웃 (robot=%s gate=%s)", robot_id, gate_id)
            return None

        res = future.result()
        return {"gate_state_equal": bool(res.gate_state_equal), "error_state": int(res.error_state)}

    # ── 카메라 이미지 (Phase 2) : ROS 콜백이 저장, MJPEG 엔드포인트가 읽음 ──
    def _store_frame(self, camera_id: str, jpeg: bytes) -> None:  # pragma: no cover
        with self._frames_lock:
            self._frames[camera_id] = jpeg

    def get_latest_jpeg(self, camera_id: str) -> bytes | None:  # pragma: no cover
        with self._frames_lock:
            return self._frames.get(camera_id)

    @staticmethod
    def camera_ids() -> list[str]:
        return list(VISION_IMAGE_TOPICS)

    # ── 프로듀서 (ROS 스레드) : 큐에 넣기만 ──────────────────────────────
    def _produce(
        self, camera_id: int, state: int, bbox: Bbox, confidence: float
    ) -> None:  # pragma: no cover
        self._loop.call_soon_threadsafe(self._enqueue, (camera_id, state, bbox, confidence))

    def _enqueue(self, item: tuple) -> None:  # pragma: no cover
        import asyncio

        try:
            self._inbound.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._inbound.get_nowait()
                self._inbound.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # ── 컨슈머 (이벤트 루프) : enrich → ingest → 방송 ────────────────────
    async def _consume(self) -> None:  # pragma: no cover
        import asyncio

        while not self._stop.is_set():
            try:
                camera_id, state, bbox, confidence = await self._inbound.get()
            except asyncio.CancelledError:
                break
            try:
                await self._handle(camera_id, state, bbox, confidence)
            except Exception:  # noqa: BLE001 - 한 프레임 실패가 컨슈머를 멈추면 안 된다
                log.exception("[vision_bridge] cam_state 처리 실패 (cam=%s state=%s)", camera_id, state)

    async def _handle(
        self, camera_id: int, state: int, bbox: Bbox | None = None, confidence: float = 0.0
    ) -> None:  # pragma: no cover
        meta = VISION_CAMERA_MAP.get(camera_id)
        etype = _STATE_TO_TYPE.get(state)
        if meta is None or etype is None:
            log.warning("[vision_bridge] 매핑 없음 무시 (cam=%s state=%s)", camera_id, state)
            return

        # 호모그래피: CCTV 픽셀 bbox → 맵 (x, y). AMR/캘리브레이션 없음/무효 bbox 면 None.
        cam_key = meta.get("camera_id")
        map_xy = detection_to_map_xy(cam_key, bbox)
        map_x, map_y = map_xy if map_xy is not None else (None, None)
        bbox_list = list(bbox) if valid_bbox(bbox) else None
        conf = confidence if confidence and confidence > 0 else DEFAULT_CONFIDENCE

        # CamState 는 "감지/해제" 구분이 없음 → Phase 1 은 '감지'로만 취급(해제 규칙은 §8 미정).
        db = SessionLocal()
        try:
            result = detection.ingest(
                db,
                source=meta["source"],
                type_=etype,
                confidence=conf,
                camera_id=cam_key,
                robot_id=meta.get("robot_id"),
                zone_id=meta.get("zone_id"),
                x=map_x,
                y=map_y,
                bbox=bbox_list,
            )
            db.commit()
            payload = crud.events.to_dict(result.event)
            event_id = result.event.event_id
            auto = dispatch.should_auto_dispatch(etype, map_xy, result.merged)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("[vision_bridge] 이벤트 생성 실패 (cam=%s)", camera_id)
            return
        finally:
            db.close()

        await manager.publish_async(WsMessageType.EVENT.value, payload)
        log.info(
            "[vision_bridge] cam=%s state=%s bbox=%s → 이벤트 %s (%s) map=(%s,%s)",
            camera_id, state, bbox_list is not None, event_id,
            "MERGED" if result.merged else "NEW", map_x, map_y,
        )
        # 정책(2026-08-09): 화재 감지 + 맵 좌표 확보 시 즉시 자동 급파(가까운 AMR 에 GOTO).
        if auto:
            await self._auto_dispatch(event_id)

    async def _auto_dispatch(self, event_id: str) -> None:  # pragma: no cover
        """화재 이벤트에 가장 가까운 가용 로봇을 골라 GOTO(맵 좌표)를 하달한다.

        REST 급파와 같은 dispatch 서비스를 쓴다(로직 이중화 방지). 가용 로봇이 없으면
        경고만 남기고 넘어간다(운영자가 나중에 수동 급파 가능). 실패해도 컨슈머는 멈추지 않는다.
        """
        db = SessionLocal()
        try:
            event = crud.events.get(db, event_id)
            robot_id, selection = dispatch.select_robot(db, event)
            if robot_id is None:
                log.warning("[vision_bridge] 자동 급파 보류 — 가용 로봇 없음 (event=%s)", event_id)
                return
            outcome = dispatch.assign_and_goto(db, event, robot_id, preempt=True)
            db.commit()
            await manager.publish_async(WsMessageType.EVENT.value, crud.events.to_dict(event))
            await manager.publish_async(
                WsMessageType.MISSION_STATUS.value, crud.robots.mission_to_dict(db, outcome.mission)
            )
            log.info(
                "[vision_bridge] 화재 자동 급파 → %s GOTO(x=%s,y=%s) event=%s %s",
                robot_id, event.x, event.y, event_id, selection,
            )
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("[vision_bridge] 자동 급파 실패 (event=%s)", event_id)
        finally:
            db.close()

    def _spin(self) -> None:  # pragma: no cover
        import rclpy
        from rclpy.executors import SingleThreadedExecutor

        # 전용 executor 로 스핀한다. rclpy.spin_once(node) 는 인자 없이 호출하면 프로세스
        # 전역 executor 를 쓰는데, robot_bridge 스핀 스레드도 같은 전역 executor 를 돌리므로
        # 두 스레드가 같은 콜백 제너레이터에 동시 진입해 "generator already executing" 로
        # 죽는다. 노드마다 executor 를 분리하면 충돌하지 않는다.
        executor = SingleThreadedExecutor()
        executor.add_node(self._node)
        try:
            while rclpy.ok() and not self._stop.is_set():
                executor.spin_once(timeout_sec=0.5)
        except Exception:  # noqa: BLE001
            log.exception("[vision_bridge] rclpy spin 종료")
        finally:
            executor.remove_node(self._node)

    def stop(self) -> None:  # pragma: no cover
        import rclpy

        global _current
        _current = None
        self._stop.set()
        self._consumer_task.cancel()
        self._spin_thread.join(timeout=2.0)
        try:
            self._node.destroy_node()
        except Exception:  # noqa: BLE001
            pass
        # rclpy.shutdown() 은 robot_bridge 등 다른 노드도 쓸 수 있어 여기서 호출하지 않는다.
        log.info("[vision_bridge] 종료")


# 실행 중인 VisionBridge 싱글턴 — 카메라 스트림 라우터가 프레임을 읽으려고 참조한다.
# (bridge.py 의 get_bridge/set_bridge 와 같은 패턴)
_current: "VisionBridge | None" = None


def get_vision_bridge() -> "VisionBridge | None":
    """설치돼 있으면 실행 중 VisionBridge, 아니면 None (비전 미연동 시)."""
    return _current


def build_vision_bridge(loop: Any) -> "VisionBridge":  # pragma: no cover
    """비전 브리지를 만든다. rclpy·patrol_interfaces 없으면 RuntimeError."""
    global _current
    try:
        import rclpy  # noqa: F401
        from patrol_interfaces.msg import CamState  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "rclpy 또는 patrol_interfaces 를 임포트할 수 없습니다. colcon build 후 "
            "install/setup.bash 를 source 했는지 확인하세요. (vision 통합 전제)"
        ) from exc
    _current = VisionBridge(loop)
    return _current
