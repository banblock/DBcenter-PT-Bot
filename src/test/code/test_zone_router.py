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
    missions, _, _ = zone_router.build_missions(graph, ZONES)
    assert set(missions.keys()) == {'robot3', 'robot8'}
    # dense hop-by-hop route, not a direct 2-point jump
    assert len(missions['robot3']) > 2
    assert len(missions['robot8']) > 2


def test_shared_aisle_edge_is_flagged_as_crossing(graph):
    missions, crossing_log, _ = zone_router.build_missions(graph, ZONES)
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
    missions, _, _ = zone_router.build_missions(graph, ZONES)
    assert any(wp['has_gate'] for wp in missions['robot3'])
    assert any(wp['has_gate'] for wp in missions['robot8'])
    # pass-through junction waypoints must not accidentally be gate stops
    assert sum(wp['has_gate'] for wp in missions['robot3']) == 1
    assert sum(wp['has_gate'] for wp in missions['robot8']) == 1


def test_origin_marks_patrol_points_vs_inserted_transit_hops(graph):
    missions, _, _ = zone_router.build_missions(graph, ZONES)
    # every input point (2 per zone here) must land as 'patrol'; anything
    # else the router had to insert to connect them is 'transit'
    assert sum(wp['origin'] == 'patrol' for wp in missions['robot3']) == 2
    assert sum(wp['origin'] == 'patrol' for wp in missions['robot8']) == 2
    assert all(wp['origin'] in ('patrol', 'transit') for wps in missions.values() for wp in wps)


def test_junction_shared_via_different_edges_is_flagged_as_crossing(graph):
    # 하드웨어 테스트 중 실제로 발견된 케이스: 두 로봇이 같은 엣지를
    # 하나도 공유하지 않아도, 서로 다른 엣지로 같은 교차로(F_BC, 외길
    # 통로 3개(F_AB_BC/F_BC_CD/V_BC)가 만나는 실제 교차로 노드)를 지나가면
    # 그 자체로 교차 지점이어야 한다. robot3는 F_AB -> F_BC -> F_CD로
    # 전면 통로를 그대로 통과하고(F_AB_BC, F_BC_CD 사용), robot8은
    # R_BC -> F_BC로 세로 통로만 타고 올라와 F_BC에서 순찰을 끝낸다
    # (V_BC만 사용) - 둘이 공유하는 엣지는 하나도 없다.
    zones = [
        {
            'zone_id': 'zoneFront',
            'robot': 'robot3',
            'points': [
                {'x': -1.4, 'y': 1.94, 'yaw': 0.0, 'point_type': 'normal'},   # F_AB
                {'x': -3.55, 'y': 1.84, 'yaw': 0.0, 'point_type': 'normal'},  # F_CD
            ],
        },
        {
            'zone_id': 'zoneVertical',
            'robot': 'robot8',
            'points': [
                {'x': -2.38, 'y': 0.017, 'yaw': 0.0, 'point_type': 'normal'},  # R_BC
                {'x': -2.43, 'y': 1.9, 'yaw': 0.0, 'point_type': 'normal'},    # F_BC
            ],
        },
    ]
    missions, crossing_log, _ = zone_router.build_missions(graph, zones)
    assert len(crossing_log) == 1
    key, robots, point_id = crossing_log[0]
    assert key == 'F_BC'
    assert point_id == 'J_F_BC'
    assert robots == ['robot3', 'robot8']

    # 엣지 기반 point_id('X_...')는 하나도 없어야 한다 - 공유 엣지가
    # 없으므로 이 케이스는 순전히 노드(교차로) 판정으로만 잡혀야 함.
    all_point_ids = [
        wp['point_id']
        for wps in missions.values() for wp in wps if wp['point_id']
    ]
    assert all_point_ids == [point_id, point_id]


def test_shared_edge_endpoints_at_different_junctions_still_flagged(graph):
    # 처음 노드/엣지 판정을 배타적으로("교차로면 노드로만, 아니면
    # 엣지로만") 나눴다가 여기서 회귀가 생겼었다: 세로 통로(V_BC, 양 끝
    # F_BC/R_BC가 둘 다 교차로)를 마주보고 지나가는 두 로봇은 "도착
    # 노드"가 서로 달라서(하나는 F_BC, 하나는 R_BC) 노드 판정에 안
    # 걸리고, 배타적 분기 때문에 엣지 판정도 건너뛰어서 아예 교차
    # 지점으로 안 잡혔다. robot3는 R_BC 방향으로, robot8은 F_BC 방향으로
    # 같은 V_BC를 반대로 지나가야 한다 - DEFAULT_ZONES(HMI 좌표)가 실제로
    # 이 패턴이라 커밋 5d7c8c2 이후 계속 의존해온 케이스.
    zones = [
        {
            'zone_id': 'zoneSouth',
            'robot': 'robot3',
            'points': [
                {'x': -2.40, 'y': 0.924, 'yaw': 0.0, 'point_type': 'normal'},  # V_BC 중간
                {'x': -3.40, 'y': -0.0411, 'yaw': 0.0, 'point_type': 'normal'},  # R_CD
            ],
        },
        {
            'zone_id': 'zoneNorth',
            'robot': 'robot8',
            'points': [
                {'x': -2.32, 'y': 0.05, 'yaw': 0.0, 'point_type': 'normal'},  # R_BC 근처
                {'x': -2.46, 'y': 2.0, 'yaw': 0.0, 'point_type': 'normal'},   # F_BC 근처
            ],
        },
    ]
    missions, crossing_log, _ = zone_router.build_missions(graph, zones)
    assert len(crossing_log) == 1
    key, robots, point_id = crossing_log[0]
    assert key == 'V_BC'
    assert point_id == 'X_V_BC'
    assert robots == ['robot3', 'robot8']


def test_edge_and_junction_resources_merge_into_one_point_id(graph):
    # 한 웨이포인트가 "공유 엣지"와 "공유 교차로"를 동시에 필요로 하는
    # 경우(robotA/robotC는 F_AB_BC 엣지를 같이 쓰면서 F_BC에 도착하고,
    # robotB는 다른 엣지(V_BC)로 같은 F_BC에 도착) - union-find가 셋을
    # 전부 하나의 point_id로 합쳐야 occupancy 프로토콜(웨이포인트당 락
    # 하나)이 세 로봇 모두를 올바르게 조정할 수 있다.
    zones = [
        {
            'zone_id': 'zA',
            'robot': 'robotA',
            'points': [
                {'x': -1.4, 'y': 1.94, 'yaw': 0.0, 'point_type': 'normal'},   # F_AB
                {'x': -3.55, 'y': 1.84, 'yaw': 0.0, 'point_type': 'normal'},  # F_CD
            ],
        },
        {
            'zone_id': 'zC',
            'robot': 'robotC',
            'points': [
                {'x': -0.25, 'y': 2, 'yaw': 0.0, 'point_type': 'normal'},     # F_left
                {'x': -2.43, 'y': 1.9, 'yaw': 0.0, 'point_type': 'normal'},   # F_BC
            ],
        },
        {
            'zone_id': 'zB',
            'robot': 'robotB',
            'points': [
                {'x': -2.38, 'y': 0.017, 'yaw': 0.0, 'point_type': 'normal'},  # R_BC
                {'x': -2.43, 'y': 1.9, 'yaw': 0.0, 'point_type': 'normal'},    # F_BC
            ],
        },
    ]
    missions, crossing_log, _ = zone_router.build_missions(graph, zones)
    assert len(crossing_log) == 1
    key, robots, point_id = crossing_log[0]
    assert robots == ['robotA', 'robotB', 'robotC']

    all_point_ids = {
        wp['point_id']
        for wps in missions.values() for wp in wps if wp['point_id']
    }
    assert all_point_ids == {point_id}, '세 로봇이 같은 point_id 하나로 합쳐져야 함'


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
    missions, crossing_log, _ = zone_router.build_missions(graph, zones)
    assert crossing_log == []
    assert all(wp['point_id'] is None for wps in missions.values() for wp in wps)
