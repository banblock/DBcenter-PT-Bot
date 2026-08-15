"""CCTV 픽셀 좌표 → 맵(월드) 좌표 호모그래피 변환 (Phase 3-1).

비전이 감지한 이미지 픽셀 좌표(bbox)를 맵 좌표로 옮겨 이벤트 x/y 에 채우고, 그 좌표로
로봇을 급파(GOTO)하기 위한 **순수 변환 계층**이다. 이벤트→GOTO 하행은 이미 map 좌표
(event.x/event.y → waypoints)로 흐르므로, 여기서 픽셀→map 만 채우면 급파까지 그대로 이어진다.

수식 (image_to_map, 3×3 동차 호모그래피 H, 행 우선)
    [X, Y, W]ᵀ = H · [u, v, 1]ᵀ
    map_x = X / W ,  map_y = Y / W          (W 로 나누는 원근 보정)

행렬은 캘리브레이션 YAML(ROS param 형식)에서 카메라별로 1개씩 읽는다:
    /**:
      ros__parameters:
        calibration_enabled: true
        image_to_map_homography: [h00, h01, h02, h10, h11, h12, h20, h21, h22]

numpy 없이 순수 파이썬으로 3×3 곱을 한다 → `robot_bridge` 규격부처럼 ROS/의존성 없이
pytest 로 완전 검증된다. (Phase B 에서 CamState 에 bbox/픽셀이 추가되면 vision_bridge 가
이 모듈의 `pixel_to_map` 을 불러 event.x/y 를 채운다 — 그때까지 이 계층은 독립적으로 완성.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from app.config import settings
from app.logging_config import get_logger

log = get_logger("homography")

#: W(동차 좌표 스케일)가 이 값보다 작으면 변환이 발산/무의미(무한원점·카메라 뒤) → 버린다.
_W_EPS = 1e-9

# 이미지 픽셀에서 "맵 위 접지점"에 가장 가까운 대표점. bbox 하단 중앙(발 밑)이 바닥 평면과
# 만나므로, 호모그래피(바닥 평면 가정)에서 중심보다 하단중앙이 실제 위치에 더 정확하다.
_ANCHORS = ("bottom_center", "center")


@dataclass(frozen=True)
class Homography:
    """카메라 1대의 image→map 호모그래피. 순수 값 객체(불변)."""

    matrix: tuple[tuple[float, float, float], ...]  # 3×3, 행 우선
    camera_id: str | None = None
    enabled: bool = True

    @classmethod
    def from_flat(
        cls, values: Sequence[float], *, camera_id: str | None = None, enabled: bool = True
    ) -> "Homography":
        """행 우선 9개 값(h00..h22)으로 만든다."""
        vals = [float(v) for v in values]
        if len(vals) != 9:
            raise ValueError(f"호모그래피는 9개 값이어야 합니다 (받음: {len(vals)}) camera={camera_id}")
        rows = tuple(tuple(vals[i : i + 3]) for i in range(0, 9, 3))
        return cls(matrix=rows, camera_id=camera_id, enabled=enabled)

    def apply(self, u: float, v: float) -> tuple[float, float] | None:
        """픽셀 (u, v) → 맵 (x, y). 캘리브레이션 off 이거나 발산하면 None."""
        if not self.enabled:
            return None
        (a, b, c), (d, e, f), (g, h, i) = self.matrix
        w = g * u + h * v + i
        if abs(w) < _W_EPS:
            log.warning("[homography] W≈0 발산 무시 (camera=%s u=%.1f v=%.1f)", self.camera_id, u, v)
            return None
        x = (a * u + b * v + c) / w
        y = (d * u + e * v + f) / w
        return (x, y)


def bbox_anchor(bbox: Sequence[float], anchor: str = "bottom_center") -> tuple[float, float]:
    """bbox [x1, y1, x2, y2] → 대표 픽셀 좌표.

    · bottom_center: 하단 중앙( (x1+x2)/2 , max(y1,y2) ) — 바닥 접지점, 호모그래피 기본.
    · center       : 중심점.
    """
    if anchor not in _ANCHORS:
        raise ValueError(f"anchor 는 {_ANCHORS} 중 하나여야 합니다 (받음: {anchor!r})")
    x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0 if anchor == "center" else max(y1, y2)
    return (cx, cy)


def load_homography(path: str | Path, *, camera_id: str | None = None) -> Homography:
    """캘리브레이션 YAML(ROS param 형식 또는 평면형)에서 Homography 를 읽는다."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    params = _ros_params(doc)
    values = params.get("image_to_map_homography")
    if values is None:
        raise ValueError(f"'image_to_map_homography' 키가 없습니다: {p}")
    enabled = bool(params.get("calibration_enabled", True))
    return Homography.from_flat(values, camera_id=camera_id, enabled=enabled)


def _ros_params(doc: dict) -> dict:
    """ROS param YAML({'/**': {'ros__parameters': {...}}})이면 파라미터 dict 를, 아니면 doc 자체를."""
    if not isinstance(doc, dict):
        return {}
    for key in ("/**", "**"):
        node = doc.get(key)
        if isinstance(node, dict) and isinstance(node.get("ros__parameters"), dict):
            return node["ros__parameters"]
    # 첫 노드에 ros__parameters 가 있으면(노드명이 다를 때) 그걸 쓴다.
    for node in doc.values():
        if isinstance(node, dict) and isinstance(node.get("ros__parameters"), dict):
            return node["ros__parameters"]
    return doc  # 평면형: image_to_map_homography 가 최상위에 있는 경우


# ── 카메라별 레지스트리 (config 주도, 지연 로드) ──────────────────────────────
_store: dict[str, Homography] = {}
_loaded = False


def _ensure_loaded() -> None:
    """settings.homography_files 를 처음 접근할 때 한 번 읽어 캐시한다."""
    global _loaded
    if _loaded:
        return
    for camera_id, path in getattr(settings, "homography_files", {}).items():
        try:
            _store[camera_id] = load_homography(path, camera_id=camera_id)
            log.info("[homography] %s 캘리브레이션 로드 (%s)", camera_id, path)
        except (OSError, ValueError) as exc:
            log.warning("[homography] %s 캘리브레이션 로드 실패: %s", camera_id, exc)
    _loaded = True


def reload(files: dict[str, str] | None = None) -> None:
    """레지스트리를 비우고 다시 로드한다(테스트/캘리브레이션 갱신용)."""
    global _loaded
    _store.clear()
    _loaded = False
    if files is not None:
        for camera_id, path in files.items():
            try:
                _store[camera_id] = load_homography(path, camera_id=camera_id)
            except (OSError, ValueError) as exc:
                log.warning("[homography] %s 캘리브레이션 로드 실패: %s", camera_id, exc)
        _loaded = True
    else:
        _ensure_loaded()


def get_homography(camera_id: str) -> Homography | None:
    """카메라의 Homography(없으면 None)."""
    _ensure_loaded()
    return _store.get(camera_id)


def pixel_to_map(camera_id: str, u: float, v: float) -> tuple[float, float] | None:
    """카메라 픽셀 (u, v) → 맵 (x, y). 캘리브레이션이 없거나 꺼짐/발산이면 None.

    설정(AMR_HOMOGRAPHY_ENABLED)이 꺼져 있거나 해당 카메라 매트릭스가 없으면 None →
    호출부(vision_bridge)는 좌표 없이 이벤트만 만들면 된다(현재 동작과 동일).
    """
    if not getattr(settings, "homography_enabled", True):
        return None
    hom = get_homography(camera_id)
    if hom is None:
        return None
    return hom.apply(float(u), float(v))
