"""초기 시드 데이터 — 설비 기준값 + 대조 룰 + 데모용 맵/구역/노드/경로.

멱등(idempotent)하게 만든다. 여러 번 돌려도 중복 생성되지 않으므로 개발 중
언제든 `python -m scripts.seed_db` 로 다시 부를 수 있다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings
from app.enums import AlignVerdict, EquipmentType, ObservedState, Severity
from app.logging_config import get_logger

log = get_logger("db")

DEMO_MAP_ID = "MAP-DEMO-1F"
DC_MAP_ID = "MAP-DC-V4"

#: 설비 기준값 — '정상은 무엇인가'의 원천. 실제 현장 값으로 교체해야 한다.
EQUIPMENT_SEED: list[dict] = [
    {
        "equipment_id": "EQ-BRK-01", "type_": EquipmentType.BREAKER.value,
        "name": "1F 주배전반 A", "node_id": "N-004", "zone_id": "Z01",
        "normal_state": ObservedState.ON.value, "last_observed_state": ObservedState.ON.value,
        "value_min": 22.0, "value_max": 26.0, "unit": "V",
    },
    {
        "equipment_id": "EQ-BRK-02", "type_": EquipmentType.BREAKER.value,
        "name": "1F 주배전반 B", "node_id": "N-005", "zone_id": "Z02",
        "normal_state": ObservedState.ON.value, "last_observed_state": ObservedState.ON.value,
        "value_min": 22.0, "value_max": 26.0, "unit": "V",
    },
    {
        "equipment_id": "EQ-LOCK-02", "type_": EquipmentType.LOCK.value,
        "name": "배터리실 출입 잠금장치", "node_id": "N-006", "zone_id": "Z02",
        "normal_state": ObservedState.LOCKED.value, "unit": None,
    },
    {
        "equipment_id": "EQ-PANEL-01", "type_": EquipmentType.BATTERY_PANEL.value,
        "name": "ESS 배터리 패널 #1", "node_id": "N-007", "zone_id": "Z03",
        "normal_state": ObservedState.NORMAL.value, "value_min": 45.0, "value_max": 58.0, "unit": "V",
    },
    {
        "equipment_id": "EQ-VALVE-01", "type_": EquipmentType.VALVE.value,
        "name": "소화 배관 밸브", "node_id": "N-008", "zone_id": "Z03",
        "normal_state": ObservedState.NORMAL.value, "unit": None,
    },
]

#: 체크리스트 B-53 이 요구한 '대표 mismatch 케이스 3종'
ALIGN_RULE_SEED: list[dict] = [
    {
        "rule_id": "RULE-001",
        "equipment_type": EquipmentType.BREAKER.value,
        "condition": {"work_order_status": "IN_PROGRESS", "observed_state": "ON", "expected_state": "OFF"},
        "verdict": AlignVerdict.MISMATCH.value,
        "severity": Severity.CRITICAL.value,
        "message": "작업 진행 중인데 차단기가 ON 상태 — 감전 위험",
        "priority": 100,
    },
    {
        "rule_id": "RULE-002",
        "equipment_type": EquipmentType.LOCK.value,
        "condition": {"work_order_status": "NONE", "observed_state": "UNLOCKED", "normal_state": "LOCKED"},
        "verdict": AlignVerdict.MISMATCH.value,
        "severity": Severity.WARN.value,
        "message": "작업 종료 상태인데 잠금장치가 해제됨",
        "priority": 80,
    },
    {
        "rule_id": "RULE-003",
        "equipment_type": EquipmentType.BATTERY_PANEL.value,
        "condition": {"observed_state": "ABNORMAL"},
        "verdict": AlignVerdict.MISMATCH.value,
        "severity": Severity.CRITICAL.value,
        "message": "배터리 패널 수치가 정상 범위를 벗어남",
        "priority": 90,
    },
]

ZONE_SEED = [
    {"zone_id": "Z01", "name": "배전반 구역", "polygon": [[0, 0], [8, 0], [8, 6], [0, 6]],
     "risk_base": 3, "camera_ids": ["CAM-01"]},
    {"zone_id": "Z02", "name": "배터리실", "polygon": [[9, 0], [16, 0], [16, 6], [9, 6]],
     "risk_base": 5, "camera_ids": ["CAM-02"]},
    {"zone_id": "Z03", "name": "ESS·소화설비 구역", "polygon": [[0, 7], [16, 7], [16, 13], [0, 13]],
     "risk_base": 4, "camera_ids": ["CAM-03"]},
]

NODE_SEED = [
    {"node_id": "N-001", "zone_id": "Z01", "name": "배전반 앞 A", "x": 2.0, "y": 2.0, "theta": 0.0},
    {"node_id": "N-002", "zone_id": "Z01", "name": "배전반 통로", "x": 5.0, "y": 2.0, "theta": 0.0},
    {"node_id": "N-003", "zone_id": "Z01", "name": "구역 경계 A", "x": 7.5, "y": 4.0, "theta": 1.57,
     "is_blindspot": True},
    {"node_id": "N-004", "zone_id": "Z01", "name": "배전반 앞 B", "x": 3.2, "y": 4.5, "theta": 1.57,
     "dwell_sec": 5, "inspect_targets": ["EQ-BRK-01"]},
    {"node_id": "N-005", "zone_id": "Z02", "name": "배터리실 배전반", "x": 11.0, "y": 2.0, "theta": 0.0,
     "dwell_sec": 5, "inspect_targets": ["EQ-BRK-02"]},
    {"node_id": "N-006", "zone_id": "Z02", "name": "배터리실 출입구", "x": 14.0, "y": 3.0, "theta": 3.14,
     "dwell_sec": 3, "inspect_targets": ["EQ-LOCK-02"], "is_blindspot": True},
    {"node_id": "N-007", "zone_id": "Z03", "name": "ESS 패널 앞", "x": 4.0, "y": 10.0, "theta": 1.57,
     "dwell_sec": 8, "inspect_targets": ["EQ-PANEL-01"]},
    {"node_id": "N-008", "zone_id": "Z03", "name": "소화 배관 점검점", "x": 12.0, "y": 10.0, "theta": 0.0,
     "dwell_sec": 4, "inspect_targets": ["EQ-VALVE-01"]},
]


def seed_all(db: Session, *, with_demo_map: bool = True) -> dict[str, int]:
    """전체 시드. 이미 있는 행은 건너뛴다."""
    counts = {"maps": 0, "zones": 0, "nodes": 0, "routes": 0, "equipment": 0, "rules": 0, "robots": 0}

    if with_demo_map and db.get(models.Map, DEMO_MAP_ID) is None:
        crud.maps.create(
            db,
            map_id=DEMO_MAP_ID,
            name="factory_1f",
            image_path="/media/maps/factory_1f.png",
            yaml_path="/media/maps/factory_1f.yaml",
            resolution=0.05,
            origin_x=-12.4,
            origin_y=-8.2,
            origin_theta=0.0,
            width=1024,
            height=768,
            is_active=True,
        )
        counts["maps"] += 1

    # 실측 SLAM 맵(datacenter_map_v4) — media/maps 에 pgm/png/yaml 이 준비돼 있어야 한다.
    # 등록되면 이 맵을 현재 활성 맵으로 올린다(활성 맵은 항상 1장 — set_active 가 나머지를 내린다).
    if db.get(models.Map, DC_MAP_ID) is None:
        crud.maps.create(
            db,
            map_id=DC_MAP_ID,
            name="datacenter_map_v4",
            image_path="/media/maps/datacenter_map_v4.png",
            yaml_path="/media/maps/datacenter_map_v4.yaml",
            resolution=0.05,
            origin_x=-5.15,
            origin_y=-0.659,
            origin_theta=0.0,
            width=113,
            height=66,
        )
        crud.maps.set_active(db, DC_MAP_ID)
        counts["maps"] += 1

    for zone in ZONE_SEED:
        if db.get(models.Zone, zone["zone_id"]) is None:
            crud.patrol.create_zone(db, **zone)
            counts["zones"] += 1

    if with_demo_map:
        for node in NODE_SEED:
            if db.get(models.Node, node["node_id"]) is None:
                crud.patrol.create_node(db, map_id=DEMO_MAP_ID, **node)
                counts["nodes"] += 1

        if db.get(models.Route, "R-01") is None:
            crud.patrol.create_route(
                db,
                route_id="R-01",
                name="1F 정규 순찰",
                map_id=DEMO_MAP_ID,
                node_order=[n["node_id"] for n in NODE_SEED],
                loop=True,
            )
            counts["routes"] += 1

    for equipment in EQUIPMENT_SEED:
        if db.get(models.Equipment, equipment["equipment_id"]) is None:
            payload = dict(equipment)
            if not with_demo_map:
                payload["node_id"] = None  # 노드 없이 시드할 땐 FK 를 비운다
            crud.equipment.create(db, **payload)
            counts["equipment"] += 1

    for rule in ALIGN_RULE_SEED:
        if db.get(models.AlignRule, rule["rule_id"]) is None:
            crud.equipment.create_rule(db, **rule)
            counts["rules"] += 1

    for index, robot_id in enumerate(settings.robot_ids, start=1):
        if db.get(models.Robot, robot_id) is None:
            robot = crud.robots.ensure(db, robot_id, name=f"로봇 {index} ({robot_id})")
            robot.dock_id = f"D{index}"
            counts["robots"] += 1

    db.commit()
    log.info("시드 완료 — %s", counts)
    return counts
