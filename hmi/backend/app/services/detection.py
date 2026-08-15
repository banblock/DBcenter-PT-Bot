"""탐지 수신 공용 파이프라인 (dedup·병합·이벤트 생성).

REST(`POST /api/events/detect`)와 비전 브리지(`vision_bridge`, ROS `/detection/cam_state`)가
**같은 로직**을 쓰도록 여기 한 곳에 모은다. 두 경로가 각자 이벤트를 만들면 dedup/병합 규칙이
갈라져 반드시 어긋난다.

경계
----
· 이 함수는 DB 변경만 한다(dedup 검사 → 병합 또는 생성 + 미디어). **commit/broadcast 는 호출부**가
  한다(REST 는 async 핸들러, 비전은 컨슈머 코루틴 — 둘 다 이벤트 루프에서 `manager.publish_async`).
· base64 이미지 파일 저장은 REST 전용이라 여기 없다. 호출부가 image_uri 만 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app import crud, models
from app.enums import EventStatus


@dataclass
class IngestResult:
    event: models.Event  # 병합된 기존 이벤트 또는 새로 만든 이벤트
    merged: bool          # True 면 dedup 병합, False 면 신규 생성
    response: dict         # REST ok(...) 로 돌려줄 본문(케이스별 필드 유지)


def ingest(
    db: Session,
    *,
    source: str,
    type_: str,
    confidence: float,
    camera_id: str | None = None,
    robot_id: str | None = None,
    zone_id: str | None = None,
    node_id: str | None = None,
    x: float | None = None,
    y: float | None = None,
    bbox: list | None = None,
    detected_at: datetime | None = None,
    image_uri: str | None = None,
) -> IngestResult:
    """dedup 창 안 같은 zone+type 이면 병합, 아니면 신규 이벤트 생성 (B-31·B-32).

    commit 하지 않는다. 호출부가 commit + `manager.publish_async(EVENT, to_dict(result.event))`.
    """
    existing = crud.events.find_dedup_target(db, zone_id=zone_id, type_=type_)
    if existing is not None:
        merged = crud.events.merge_into(db, existing, confidence)
        crud.events.add_timeline(
            db, merged.event_id, stage="MERGED", actor=camera_id or robot_id,
            detail=f"중복 탐지 병합 (hit={merged.hit_count})",
        )
        return IngestResult(
            event=merged,
            merged=True,
            response={
                "event_id": merged.event_id,
                "status": EventStatus.MERGED.value,
                "merged_into": merged.event_id,
                "severity": merged.severity,
                "hit_count": merged.hit_count,
            },
        )

    event = crud.events.create(
        db,
        source=source,
        type_=type_,
        confidence=confidence,
        camera_id=camera_id,
        robot_id=robot_id,
        zone_id=zone_id,
        node_id=node_id,
        x=x,
        y=y,
        bbox_json=bbox,
        detected_at=detected_at,
    )
    if image_uri:
        event.thumbnail_url = image_uri
        crud.events.add_media(db, event.event_id, uri=image_uri)

    return IngestResult(
        event=event,
        merged=False,
        response={
            "event_id": event.event_id,
            "status": event.status,
            "merged_into": None,
            "severity": event.severity,
            "target_node_id": event.node_id,
        },
    )
