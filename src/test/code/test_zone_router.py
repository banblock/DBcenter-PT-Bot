import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet.route_graph import RouteGraph
from fleet import zone_router

GRAPH_YAML = os.path.join(
    os.path.dirname(__file__), '..', '..', 'fleet', 'config', 'route_graph.yaml')


@pytest.fixture
def graph():
    return RouteGraph.from_yaml(GRAPH_YAML)


ZONES = [
    {
        'zone_id': 'zoneAB',
        'robot': 'robot3',
        'points': [
            {'x': -4.55, 'y': -0.08, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -1.8, 'y': -0.1, 'yaw': 90.0, 'point_type': 'gate'},
        ],
    },
    {
        'zone_id': 'zoneCD',
        'robot': 'robot8',
        'points': [
            {'x': -0.05, 'y': -0.1, 'yaw': 180.0, 'point_type': 'normal'},
            {'x': -1.9, 'y': -0.1, 'yaw': -90.0, 'point_type': 'gate'},
        ],
    },
]


def test_missions_built_for_every_robot(graph):
    missions, _ = zone_router.build_missions(graph, ZONES)
    assert set(missions.keys()) == {'robot3', 'robot8'}
    # dense hop-by-hop route, not a direct 2-point jump
    assert len(missions['robot3']) > 2
    assert len(missions['robot8']) > 2


def test_shared_aisle_edge_is_flagged_as_crossing(graph):
    missions, crossing_log = zone_router.build_missions(graph, ZONES)
    assert len(crossing_log) == 1
    eid, robots, point_id = crossing_log[0]
    # route_graph.yaml이 rviz 실측값으로 바뀌면서 front/rear 배치가
    # 반대가 됐고(주석 참고), 이 테스트의 y=-0.1/-0.08/-0.1/-0.1 좌표들은
    # 이제 rear aisle(R_*) 쪽에 스냅된다.
    assert eid == 'R_AB_BC'
    assert robots == ['robot3', 'robot8']

    ids_a = [wp['point_id'] for wp in missions['robot3'] if wp['point_id']]
    ids_b = [wp['point_id'] for wp in missions['robot8'] if wp['point_id']]
    assert ids_a == [point_id]
    assert ids_b == [point_id]


def test_gate_points_tagged_has_gate(graph):
    missions, _ = zone_router.build_missions(graph, ZONES)
    assert any(wp['has_gate'] for wp in missions['robot3'])
    assert any(wp['has_gate'] for wp in missions['robot8'])
    # pass-through junction waypoints must not accidentally be gate stops
    assert sum(wp['has_gate'] for wp in missions['robot3']) == 1
    assert sum(wp['has_gate'] for wp in missions['robot8']) == 1


def test_origin_marks_patrol_points_vs_inserted_transit_hops(graph):
    missions, _ = zone_router.build_missions(graph, ZONES)
    # every input point (2 per zone here) must land as 'patrol'; anything
    # else the router had to insert to connect them is 'transit'
    assert sum(wp['origin'] == 'patrol' for wp in missions['robot3']) == 2
    assert sum(wp['origin'] == 'patrol' for wp in missions['robot8']) == 2
    assert all(wp['origin'] in ('patrol', 'transit') for wps in missions.values() for wp in wps)


def test_non_overlapping_routes_have_no_crossings(graph):
    zones = [
        {
            'zone_id': 'a',
            'robot': 'robot3',
            'points': [
                {'x': -4.6, 'y': -0.1, 'yaw': 0.0, 'point_type': 'normal'},
                {'x': -3.48, 'y': -0.1, 'yaw': 0.0, 'point_type': 'normal'},
            ],
        },
        {
            'zone_id': 'b',
            'robot': 'robot8',
            'points': [
                {'x': -0.04, 'y': -0.1, 'yaw': 0.0, 'point_type': 'normal'},
                {'x': -1.3, 'y': -0.1, 'yaw': 0.0, 'point_type': 'normal'},
            ],
        },
    ]
    missions, crossing_log = zone_router.build_missions(graph, zones)
    assert crossing_log == []
    assert all(wp['point_id'] is None for wps in missions.values() for wp in wps)
