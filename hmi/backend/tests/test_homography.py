"""호모그래피(픽셀 → 맵 좌표) 변환 계층 검증 (Phase 3-1, 단계 A).

이 계층은 ROS/비전 없이 순수 파이썬이므로 여기서 전부 단위 검증한다:
  · 동차 변환 수식(원근 나눗셈 W) 정확도       → test_apply_*
  · bbox → 대표 픽셀(하단중앙/중심)             → test_bbox_anchor_*
  · YAML(ROS param 형식) 로드                   → test_load_*, test_real_calibration_*
  · 레지스트리/설정 게이트(pixel_to_map)        → test_pixel_to_map_*
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.config import settings
from app.services import homography as H

# ── 변환 수식 ────────────────────────────────────────────────────────────────

def test_apply_identity_returns_same_point() -> None:
    """항등행렬이면 (u, v) 가 그대로 나온다."""
    hom = H.Homography.from_flat([1, 0, 0, 0, 1, 0, 0, 0, 1])
    assert hom.apply(3.0, 7.0) == pytest.approx((3.0, 7.0))


def test_apply_affine_scale_translation() -> None:
    """마지막 행 [0,0,1] = 순수 아핀: x = 2u+10, y = 3v-5."""
    hom = H.Homography.from_flat([2, 0, 10, 0, 3, -5, 0, 0, 1])
    x, y = hom.apply(4.0, 2.0)
    assert (x, y) == pytest.approx((18.0, 1.0))


def test_apply_perspective_divides_by_w() -> None:
    """마지막 행이 비자명하면 W 로 나눈다. H=[[2,0,0],[0,2,0],[0,0,2]] → (u,v)."""
    hom = H.Homography.from_flat([2, 0, 0, 0, 2, 0, 0, 0, 2])
    assert hom.apply(5.0, 9.0) == pytest.approx((5.0, 9.0))
    # 스케일이 좌표에 의존하는 진짜 원근: W = u 로 나눔.
    hom2 = H.Homography.from_flat([1, 0, 0, 0, 1, 0, 1, 0, 0])
    x, y = hom2.apply(4.0, 8.0)  # X=4, Y=8, W=4 → (1, 2)
    assert (x, y) == pytest.approx((1.0, 2.0))


def test_apply_returns_none_when_w_zero() -> None:
    """W≈0(무한원점/카메라 뒤)이면 None 을 돌려 호출부가 건너뛰게 한다."""
    hom = H.Homography.from_flat([1, 0, 0, 0, 1, 0, 0, 0, 0])  # W = 0 항상
    assert hom.apply(1.0, 1.0) is None


def test_apply_returns_none_when_disabled() -> None:
    hom = H.Homography.from_flat([1, 0, 0, 0, 1, 0, 0, 0, 1], enabled=False)
    assert hom.apply(1.0, 1.0) is None


def test_from_flat_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        H.Homography.from_flat([1, 2, 3])


# ── bbox 앵커 ────────────────────────────────────────────────────────────────

def test_bbox_anchor_bottom_center_is_default() -> None:
    """[x1,y1,x2,y2] 의 하단중앙 = ((x1+x2)/2, max(y1,y2))."""
    assert H.bbox_anchor([10, 20, 30, 80]) == pytest.approx((20.0, 80.0))


def test_bbox_anchor_center() -> None:
    assert H.bbox_anchor([10, 20, 30, 80], anchor="center") == pytest.approx((20.0, 50.0))


def test_bbox_anchor_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        H.bbox_anchor([0, 0, 1, 1], anchor="top_left")


# ── YAML 로드 ────────────────────────────────────────────────────────────────

def test_load_ros_param_format(tmp_path: Path) -> None:
    f = tmp_path / "cam.yaml"
    f.write_text(
        "/**:\n"
        "  ros__parameters:\n"
        "    calibration_enabled: true\n"
        "    image_to_map_homography: [1,0,0, 0,1,0, 0,0,1]\n",
        encoding="utf-8",
    )
    hom = H.load_homography(f, camera_id="camX")
    assert hom.camera_id == "camX"
    assert hom.enabled is True
    assert hom.apply(2.0, 3.0) == pytest.approx((2.0, 3.0))


def test_load_respects_calibration_disabled(tmp_path: Path) -> None:
    f = tmp_path / "cam.yaml"
    f.write_text(
        "/**:\n  ros__parameters:\n    calibration_enabled: false\n"
        "    image_to_map_homography: [1,0,0, 0,1,0, 0,0,1]\n",
        encoding="utf-8",
    )
    hom = H.load_homography(f)
    assert hom.enabled is False
    assert hom.apply(1.0, 1.0) is None


def test_load_missing_key_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text("/**:\n  ros__parameters:\n    calibration_enabled: true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        H.load_homography(f)


# ── 실제 캘리브레이션 파일 (data/calibration/cctv1,2.yaml) ────────────────────

@pytest.mark.parametrize("camera_id", ["cctv1", "cctv2"])
def test_real_calibration_files_load_and_map(camera_id: str) -> None:
    """리포에 커밋된 실제 캘리브레이션이 로드되고 유한한 맵 좌표를 낸다."""
    path = settings.homography_files[camera_id]
    hom = H.load_homography(path, camera_id=camera_id)
    assert hom.enabled is True
    result = hom.apply(640.0, 360.0)  # 가상 이미지 중앙 근처
    assert result is not None
    x, y = result
    assert math.isfinite(x) and math.isfinite(y)


# ── 레지스트리 / pixel_to_map 게이트 ─────────────────────────────────────────

def test_pixel_to_map_uses_registry() -> None:
    H.reload()  # settings.homography_files 로 로드
    result = H.pixel_to_map("cctv1", 640.0, 360.0)
    assert result is not None and len(result) == 2


def test_pixel_to_map_unknown_camera_returns_none() -> None:
    H.reload()
    assert H.pixel_to_map("cctv_nonexistent", 1.0, 1.0) is None


def test_pixel_to_map_disabled_by_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "homography_enabled", False)
    assert H.pixel_to_map("cctv1", 640.0, 360.0) is None


def test_reload_with_custom_files(tmp_path: Path) -> None:
    f = tmp_path / "custom.yaml"
    f.write_text(
        "/**:\n  ros__parameters:\n    image_to_map_homography: [1,0,5, 0,1,7, 0,0,1]\n",
        encoding="utf-8",
    )
    H.reload({"camZ": str(f)})
    assert H.pixel_to_map("camZ", 0.0, 0.0) == pytest.approx((5.0, 7.0))
    H.reload()  # 원상복구(다른 테스트 격리)


# ── vision_bridge 연결부 (bbox → 맵 좌표) : rclpy 없이 순수 함수 검증 ─────────

def test_valid_bbox_accepts_positive_area() -> None:
    from app.vision_bridge import valid_bbox

    assert valid_bbox((10.0, 20.0, 30.0, 80.0)) is True


@pytest.mark.parametrize(
    "bbox",
    [None, (-1.0, -1.0, -1.0, -1.0), (0.0, 0.0, 0.0, 0.0), (30.0, 20.0, 10.0, 80.0)],
)
def test_valid_bbox_rejects_degenerate(bbox) -> None:
    """해제(-1)·미설정(0)·뒤집힌 좌표는 무효."""
    from app.vision_bridge import valid_bbox

    assert valid_bbox(bbox) is False


def test_detection_to_map_xy_cctv_returns_coords() -> None:
    """cctv1 유효 bbox → 유한한 맵 좌표(호모그래피 통과)."""
    from app.vision_bridge import detection_to_map_xy

    H.reload()
    result = detection_to_map_xy("cctv1", (600.0, 300.0, 680.0, 400.0))
    assert result is not None
    x, y = result
    assert math.isfinite(x) and math.isfinite(y)


def test_detection_to_map_xy_none_when_no_camera_or_bbox() -> None:
    """AMR 캠(camera_id None)·무효 bbox 는 좌표 없음(None)."""
    from app.vision_bridge import detection_to_map_xy

    assert detection_to_map_xy(None, (10.0, 10.0, 20.0, 20.0)) is None
    assert detection_to_map_xy("cctv1", None) is None
    assert detection_to_map_xy("cctv1", (-1.0, -1.0, -1.0, -1.0)) is None


def test_detection_to_map_xy_uses_bottom_center_anchor() -> None:
    """대표 픽셀은 bbox 하단중앙( (x1+x2)/2 , max(y1,y2) )을 써야 한다."""
    from app.vision_bridge import detection_to_map_xy

    H.reload({"camA": _identity_yaml()})
    # 항등 호모그래피면 맵좌표 = 픽셀좌표 = 하단중앙.
    assert detection_to_map_xy("camA", (10.0, 20.0, 30.0, 80.0)) == pytest.approx((20.0, 80.0))
    H.reload()  # 원상복구


def _identity_yaml() -> str:
    """테스트용 항등 호모그래피 YAML 파일을 만들어 경로를 돌려준다."""
    import tempfile

    fd = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    fd.write("/**:\n  ros__parameters:\n    image_to_map_homography: [1,0,0, 0,1,0, 0,0,1]\n")
    fd.close()
    return fd.name
