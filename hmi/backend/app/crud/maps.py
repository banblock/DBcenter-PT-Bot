"""맵 · ArUco 마커 CRUD + 좌표 변환 유틸 (B-11~B-15)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.crud import ids
from app.errors import E, not_found


# ── 좌표 변환 (B-13) ──────────────────────────────────────────────────────
# 명세서 1-3 각주의 공식과 정확히 일치해야 한다. 프론트 mapTransform.ts 와 쌍.
def map_to_pixel(m: models.Map, x: float, y: float) -> tuple[float, float]:
    px = (x - m.origin_x) / m.resolution
    py = m.height - (y - m.origin_y) / m.resolution
    return px, py


def pixel_to_map(m: models.Map, px: float, py: float) -> tuple[float, float]:
    x = px * m.resolution + m.origin_x
    y = (m.height - py) * m.resolution + m.origin_y
    return x, y


# ── 점유격자 로드 (waypoint 를 이동 가능 영역에만 찍기 위한 데이터) ──────────
def _media_fs_path(url_path: str | None) -> Path | None:
    """DB 에 저장된 ``/media/...`` URL 경로를 실제 파일시스템 경로로 바꾼다."""
    if not url_path or "/media/" not in url_path:
        return None
    rel = url_path.split("/media/", 1)[1]
    return Path(settings.media_root) / rel


def _parse_pgm_p5(raw: bytes) -> tuple[int, int, bytes]:
    """이진 PGM(P5) → (width, height, grayscale bytes[row-major, top-origin]).

    헤더는 공백으로 구분된 토큰이며 ``#`` 주석 줄이 섞일 수 있다. 픽셀 데이터는
    이미지 위→아래·좌→우 순서로, SVG image 및 프론트 좌표계와 정확히 일치한다.
    """
    if not raw.startswith(b"P5"):
        raise ValueError("PGM(P5) 형식이 아닙니다")
    idx = 2
    tokens: list[bytes] = []
    while len(tokens) < 3:  # width, height, maxval
        while idx < len(raw) and raw[idx : idx + 1].isspace():
            idx += 1
        if idx < len(raw) and raw[idx : idx + 1] == b"#":  # 주석 줄 스킵
            while idx < len(raw) and raw[idx : idx + 1] not in (b"\n", b"\r"):
                idx += 1
            continue
        start = idx
        while idx < len(raw) and not raw[idx : idx + 1].isspace():
            idx += 1
        tokens.append(raw[start:idx])
    width, height, _maxval = (int(t) for t in tokens)
    idx += 1  # maxval 뒤의 단일 공백 1개
    data = raw[idx : idx + width * height]
    return width, height, data


def load_occupancy(m: models.Map) -> tuple[int, int, bytes] | None:
    """맵의 PGM 점유격자를 읽어 (width, height, grayscale bytes) 로 돌려준다.

    PGM 이 없으면(예: 데모 맵) None. image_path 의 확장자를 .pgm 으로 바꿔 찾는다.
    """
    png = _media_fs_path(m.image_path)
    if png is None:
        return None
    pgm = png.with_suffix(".pgm")
    if not pgm.exists():
        return None
    try:
        return _parse_pgm_p5(pgm.read_bytes())
    except (ValueError, OSError):
        return None


# ── 맵 ────────────────────────────────────────────────────────────────────
def create(db: Session, *, name: str, map_id: str | None = None, **kwargs) -> models.Map:
    obj = models.Map(map_id=map_id or ids.next_map_id(), name=name, **kwargs)
    db.add(obj)
    db.flush()
    return obj


def get(db: Session, map_id: str) -> models.Map:
    obj = db.get(models.Map, map_id)
    if obj is None:
        raise not_found(E.MAP_NOT_FOUND, map_id)
    return obj


def get_or_none(db: Session, map_id: str) -> models.Map | None:
    return db.get(models.Map, map_id)


def list_all(db: Session) -> list[models.Map]:
    return list(db.execute(select(models.Map).order_by(models.Map.created_at.desc())).scalars())


def get_active(db: Session) -> models.Map | None:
    return db.execute(select(models.Map).where(models.Map.is_active.is_(True))).scalars().first()


def set_active(db: Session, map_id: str) -> models.Map:
    """활성 맵은 항상 1개. 나머지는 전부 내린다."""
    obj = get(db, map_id)
    db.execute(update(models.Map).values(is_active=False))
    obj.is_active = True
    db.flush()
    return obj


def delete(db: Session, map_id: str) -> None:
    db.delete(get(db, map_id))
    db.flush()


# ── ArUco 마커 ────────────────────────────────────────────────────────────
def upsert_marker(
    db: Session, *, marker_id: int, map_id: str, x: float, y: float, yaw: float, zone_id: str | None
) -> models.ArucoMarker:
    """같은 맵에 같은 marker_id 를 다시 등록하면 좌표를 덮어쓴다 (재측량 반영)."""
    get(db, map_id)  # 맵 존재 확인
    obj = db.execute(
        select(models.ArucoMarker).where(
            models.ArucoMarker.map_id == map_id, models.ArucoMarker.marker_id == marker_id
        )
    ).scalars().first()
    if obj is None:
        obj = models.ArucoMarker(marker_id=marker_id, map_id=map_id)
        db.add(obj)
    obj.x, obj.y, obj.yaw, obj.zone_id = x, y, yaw, zone_id
    db.flush()
    return obj


def list_markers(db: Session, map_id: str) -> list[models.ArucoMarker]:
    get(db, map_id)
    return list(
        db.execute(
            select(models.ArucoMarker)
            .where(models.ArucoMarker.map_id == map_id)
            .order_by(models.ArucoMarker.marker_id)
        ).scalars()
    )


def add_pose_correction(
    db: Session, *, robot_id: str, marker_id: int, before: dict, after: dict, error_m: float
) -> models.PoseCorrection:
    obj = models.PoseCorrection(
        robot_id=robot_id,
        marker_id=marker_id,
        before_json=before,
        after_json=after,
        error_m=error_m,
    )
    db.add(obj)
    db.flush()
    return obj
