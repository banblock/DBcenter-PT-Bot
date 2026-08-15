"""카메라 이미지 피드 (Phase 2) — 비전 JPEG 를 MJPEG 로 웹에 흘린다.

비전 브리지(vision_bridge)가 카메라별 최신 JPEG 1장을 들고 있고, 여기서 그걸
``multipart/x-mixed-replace`` 로 스트리밍한다. 브라우저는
``<img src="/api/cameras/cctv1/stream">`` 하나로 네이티브 렌더한다(디코딩·base64 없음).

비전이 이미 JPEG(CompressedImage)로 발행하므로 백엔드는 바이트 패스스루만 한다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.errors import ApiError, E, not_found
from app.responses import ok
from app.vision_bridge import get_vision_bridge

router = APIRouter(prefix="/cameras", tags=["카메라"])

STREAM_FPS = 12.0            # 웹으로 흘리는 프레임률 (비전 발행률과 무관하게 최신 프레임 재전송)
_BOUNDARY = "frame"
_IDLE_LOG = 5.0             # 프레임 없을 때 대기


@router.get("", summary="카메라 목록 / 비전 연결 상태")
async def list_cameras():
    bridge = get_vision_bridge()
    return ok(
        {
            "cameras": bridge.camera_ids() if bridge else [],
            "vision_connected": bridge is not None,
        }
    )


@router.get("/{camera_id}/stream", summary="카메라 MJPEG 스트림")
async def stream(camera_id: str):
    bridge = get_vision_bridge()
    if bridge is None:
        # 비전 미연동(colcon build/source 안 됨 또는 vision_enabled=false)
        raise ApiError(E.VISION_UNAVAILABLE)
    if camera_id not in bridge.camera_ids():
        raise not_found(E.NOT_FOUND, camera_id)

    async def frames():
        interval = 1.0 / STREAM_FPS
        header = b"--" + _BOUNDARY.encode() + b"\r\nContent-Type: image/jpeg\r\n"
        while True:
            jpeg = bridge.get_latest_jpeg(camera_id)
            if jpeg:
                yield header + b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(interval)

    return StreamingResponse(
        frames(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
    )
