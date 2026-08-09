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
    node_id = graph.insert_point('p1', (-3.46, -0.09))  # ~0.02m from F_AB
    assert node_id == 'F_AB'
    assert len(graph.nodes) == before


def test_insert_point_splits_nearest_edge(graph):
    before_nodes = len(graph.nodes)
    before_edges = len(graph.edges)
    node_id = graph.insert_point('gate1', (-1.8, -0.1))  # mid F_BC_CD
    assert node_id == 'gate1'
    assert len(graph.nodes) == before_nodes + 1
    assert len(graph.edges) == before_edges + 1  # one edge -> two
    assert 'F_BC_CD' not in graph.edges
    sub_edges = [e for e in graph.edges.values() if e['base'] == 'F_BC_CD']
    assert len(sub_edges) == 2


def test_path_through_inserted_point_keeps_base_edge_id(graph):
    node_id = graph.insert_point('gate1', (-1.8, -0.1))
    nodes, base_edges = graph.shortest_path('F_BC', node_id)
    assert nodes == ['F_BC', 'gate1']
    assert base_edges == ['F_BC_CD']
