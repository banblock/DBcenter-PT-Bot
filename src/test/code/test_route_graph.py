import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet.route_graph import RouteGraph

GRAPH_YAML = os.path.join(
    os.path.dirname(__file__), '..', '..', 'fleet', 'config', 'route_graph.yaml')


@pytest.fixture
def graph():
    return RouteGraph.from_yaml(GRAPH_YAML)


def test_loads_expected_topology(graph):
    assert len(graph.nodes) == 10
    assert len(graph.edges) == 13


def test_shortest_path_along_front_aisle(graph):
    nodes, edges = graph.shortest_path('F_left', 'F_right')
    assert nodes == ['F_left', 'F_AB', 'F_BC', 'F_CD', 'F_right']
    assert edges == ['F_left_AB', 'F_AB_BC', 'F_BC_CD', 'F_CD_right']


def test_shortest_path_does_not_cross_to_rear_unnecessarily(graph):
    nodes, _ = graph.shortest_path('F_AB', 'F_CD')
    assert 'R_left' not in nodes and 'R_right' not in nodes


def test_insert_point_snaps_to_nearby_existing_node(graph):
    before = len(graph.nodes)
    node_id = graph.insert_point('p1', (-1.38, 1.94))  # ~0.02m from F_AB
    assert node_id == 'F_AB'
    assert len(graph.nodes) == before


def test_insert_point_splits_nearest_edge(graph):
    before_nodes = len(graph.nodes)
    before_edges = len(graph.edges)
    node_id = graph.insert_point('gate1', (-2.99, 1.87))  # mid F_BC_CD
    assert node_id == 'gate1'
    assert len(graph.nodes) == before_nodes + 1
    assert len(graph.edges) == before_edges + 1  # one edge -> two
    assert 'F_BC_CD' not in graph.edges
    sub_edges = [e for e in graph.edges.values() if e['base'] == 'F_BC_CD']
    assert len(sub_edges) == 2


def test_path_through_inserted_point_keeps_base_edge_id(graph):
    node_id = graph.insert_point('gate1', (-2.99, 1.87))  # mid F_BC_CD
    nodes, base_edges = graph.shortest_path('F_BC', node_id)
    assert nodes == ['F_BC', 'gate1']
    assert base_edges == ['F_BC_CD']


def test_is_junction_true_for_three_way_nodes(graph):
    # F_AB/F_BC/F_CD/R_AB/R_BC/R_CD는 전면·후면 통로 + 세로 통로가
    # 만나는 실제 교차로(degree 3) - zone_router가 노드 단위 교차 판정에
    # 쓰는 기준.
    for nid in ('F_AB', 'F_BC', 'F_CD', 'R_AB', 'R_BC', 'R_CD'):
        assert graph.is_junction(nid), f'{nid} should be a junction'


def test_is_junction_false_for_dead_ends_and_inserted_points(graph):
    # 복도 끝(degree 1)은 교차로가 아니다.
    for nid in ('F_left', 'F_right', 'R_left', 'R_right'):
        assert not graph.is_junction(nid)
    # 엣지를 쪼개서 생긴 노드는 항상 degree 2라 교차로가 아니다.
    mid_id = graph.insert_point('gate1', (-2.99, 1.87))  # mid F_BC_CD
    assert not graph.is_junction(mid_id)
    # 이 그래프에 없는(다른 zone 사본에서만 쓰인) id는 degree 0 취급.
    assert not graph.is_junction('does_not_exist')
