"""구역 · 순찰 노드 · 경로 CRUD + 경로 유효성 검증 (B-17~B-20)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.crud import ids
from app.errors import ApiError, E, not_found
from app.schemas import RouteValidationError, RouteValidationOut


# ══════════════════════════════════════════════════════════════════════════
# 구역 (zone)
# ══════════════════════════════════════════════════════════════════════════
def create_zone(
    db: Session,
    *,
    name: str,
    zone_id: str | None = None,
    polygon: list | None = None,
    risk_base: int = 1,
    camera_ids: list | None = None,
) -> models.Zone:
    zid = zone_id or ids.next_zone_id(db)
    if db.get(models.Zone, zid) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 구역 ID 입니다: {zid}")
    obj = models.Zone(
        zone_id=zid,
        name=name,
        polygon_json=polygon or [],
        risk_base=risk_base,
        camera_ids=camera_ids or [],
    )
    db.add(obj)
    db.flush()
    return obj


def get_zone(db: Session, zone_id: str) -> models.Zone:
    obj = db.get(models.Zone, zone_id)
    if obj is None:
        raise not_found(E.ZONE_NOT_FOUND, zone_id)
    return obj


def list_zones(db: Session) -> list[models.Zone]:
    return list(db.execute(select(models.Zone).order_by(models.Zone.zone_id)).scalars())


def update_zone(db: Session, zone_id: str, **fields) -> models.Zone:
    obj = get_zone(db, zone_id)
    mapping = {"polygon": "polygon_json"}
    for key, value in fields.items():
        if value is None:
            continue
        setattr(obj, mapping.get(key, key), value)
    db.flush()
    return obj


def delete_zone(db: Session, zone_id: str) -> None:
    db.delete(get_zone(db, zone_id))
    db.flush()


# ══════════════════════════════════════════════════════════════════════════
# 순찰 노드
# ══════════════════════════════════════════════════════════════════════════
def create_node(db: Session, *, map_id: str, name: str, x: float, y: float, **kwargs) -> models.Node:
    if db.get(models.Map, map_id) is None:
        raise not_found(E.MAP_NOT_FOUND, map_id)
    zone_id = kwargs.pop("zone_id", None)
    if zone_id is not None:
        get_zone(db, zone_id)  # FK 위반을 500 대신 404 로 돌려주기 위한 선검사

    nid = kwargs.pop("node_id", None) or ids.next_node_id(db)
    if db.get(models.Node, nid) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 노드 ID 입니다: {nid}")

    obj = models.Node(node_id=nid, map_id=map_id, zone_id=zone_id, name=name, x=x, y=y, **kwargs)
    db.add(obj)
    db.flush()
    return obj


def get_node(db: Session, node_id: str) -> models.Node:
    obj = db.get(models.Node, node_id)
    if obj is None:
        raise not_found(E.NODE_NOT_FOUND, node_id)
    return obj


def list_nodes(
    db: Session, *, map_id: str | None = None, zone_id: str | None = None
) -> list[models.Node]:
    stmt = select(models.Node)
    if map_id:
        stmt = stmt.where(models.Node.map_id == map_id)
    if zone_id:
        stmt = stmt.where(models.Node.zone_id == zone_id)
    return list(db.execute(stmt.order_by(models.Node.node_id)).scalars())


def update_node(db: Session, node_id: str, **fields) -> models.Node:
    obj = get_node(db, node_id)
    if fields.get("zone_id") is not None:
        get_zone(db, fields["zone_id"])
    for key, value in fields.items():
        if value is not None:
            setattr(obj, key, value)
    db.flush()
    return obj


def delete_node(db: Session, node_id: str) -> None:
    """노드를 지우면 그 노드를 쓰던 경로에서도 빼준다 (경로에 유령 ID 가 남지 않게)."""
    obj = get_node(db, node_id)
    for route in db.execute(select(models.Route)).scalars():
        if node_id in (route.node_order_json or []):
            route.node_order_json = [n for n in route.node_order_json if n != node_id]
    db.delete(obj)
    db.flush()


# ══════════════════════════════════════════════════════════════════════════
# 순찰 경로
# ══════════════════════════════════════════════════════════════════════════
def create_route(
    db: Session,
    *,
    name: str,
    node_order: list[str],
    route_id: str | None = None,
    map_id: str | None = None,
    loop: bool = True,
) -> models.Route:
    rid = route_id or ids.next_route_id(db)
    if db.get(models.Route, rid) is not None:
        raise ApiError(E.DUPLICATE_ID, f"이미 존재하는 경로 ID 입니다: {rid}")
    obj = models.Route(
        route_id=rid, name=name, map_id=map_id, node_order_json=node_order, loop=loop
    )
    db.add(obj)
    db.flush()
    return obj


def get_route(db: Session, route_id: str) -> models.Route:
    obj = db.get(models.Route, route_id)
    if obj is None:
        raise not_found(E.ROUTE_NOT_FOUND, route_id)
    return obj


def list_routes(db: Session) -> list[models.Route]:
    return list(db.execute(select(models.Route).order_by(models.Route.route_id)).scalars())


def update_route(db: Session, route_id: str, **fields) -> models.Route:
    obj = get_route(db, route_id)
    mapping = {"node_order": "node_order_json"}
    for key, value in fields.items():
        if value is not None:
            setattr(obj, mapping.get(key, key), value)
    db.flush()
    return obj


def delete_route(db: Session, route_id: str) -> None:
    db.delete(get_route(db, route_id))
    db.flush()


def validate_route(db: Session, route_id: str) -> RouteValidationOut:
    """경로 유효성 검증 (B-20).

    지금 잡아내는 것: 빈 경로, 존재하지 않는 노드, 중복 노드, 맵 불일치.
    ``UNREACHABLE`` (실제 내비게이션 도달 가능성)은 Nav2 코스트맵이 있어야 판정할 수
    있어 여기서는 검사하지 않는다 — 판정 로직이 붙기 전까지 거짓 통과를 만들지
    않도록, 이 검증기는 '정적 무결성만 본다'는 점을 호출부가 알고 있어야 한다.
    """
    route = get_route(db, route_id)
    order: list[str] = route.node_order_json or []
    errors: list[RouteValidationError] = []

    if not order:
        raise ApiError(E.ROUTE_EMPTY, f"경로에 노드가 없습니다: {route_id}")

    seen: set[str] = set()
    for node_id in order:
        node = db.get(models.Node, node_id)
        if node is None:
            errors.append(RouteValidationError(node_id=node_id, reason="NOT_FOUND"))
            continue
        if node_id in seen:
            errors.append(RouteValidationError(node_id=node_id, reason="DUPLICATED"))
        seen.add(node_id)
        if route.map_id and node.map_id != route.map_id:
            errors.append(RouteValidationError(node_id=node_id, reason="MAP_MISMATCH"))
        if node.zone_id is None:
            errors.append(RouteValidationError(node_id=node_id, reason="NO_ZONE"))

    return RouteValidationOut(valid=not errors, errors=errors)
