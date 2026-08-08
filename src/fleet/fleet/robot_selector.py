"""이상신호 대응 로봇 선정 - 순수 로직 (ROS 의존성 없음, pytest로 검증
가능하도록 route_graph.py / zone_router.py와 같은 패턴으로 fleet_node.py
에서 분리했다).

설계도의 `localization amcl -> 임무 수행 로봇 선택`을 구현하는 부분:
가장 가까운 로봇을 직선 거리가 아니라 통로 그래프 최단 경로 거리로
고른다. zone_router.py가 교차 지점을 폴리곤 겹침이 아니라 통로
그래프(edge_id 공유)로 판정하는 것과 같은 이유 - 이 맵은 랙/벽으로
나뉜 외길 통로 구조라서 직선 거리가 실제 이동 거리를 대표하지 못한다
(벽 너머로는 직선상 가까워도 실제로는 멀리 돌아가야 함).
"""

import math


def _path_length(graph, nodes):
    return sum(
        math.hypot(graph.nodes[a][0] - graph.nodes[b][0],
                   graph.nodes[a][1] - graph.nodes[b][1])
        for a, b in zip(nodes, nodes[1:])
    )


def path_distances(graph, loc, robot_poses, candidates):
    """candidates 중 위치를 아는 로봇 각각에 대해, 이상신호 좌표까지
    통로 그래프 최단 경로의 (노드 시퀀스, 총 거리)를 계산해서
    {ns: (nodes, distance)}로 돌려준다.

    select_nearest_robot과 시각화 리포트(build_selector_report.py)가
    이 계산을 공유한다 - 둘이 각자 거리를 다시 구하면 리포트가 실제
    선정 로직과 미묘하게 어긋날 수 있기 때문.

    그래프는 로봇/이상신호 좌표를 임시 노드로 끼워 넣으려고
    insert_point로 변형되므로, 호출부에서 이미 복사본(graph.copy())을
    넘겨야 한다 - 원본 고정 그래프를 훼손하면 안 되기 때문.
    """
    known = [ns for ns in candidates if ns in robot_poses]
    if not known:
        return {}

    anomaly_node = graph.insert_point('_anomaly_loc', (loc['x'], loc['y']))

    result = {}
    for ns in known:
        robot_node = graph.insert_point(f'_robot_{ns}', robot_poses[ns])
        nodes, _ = graph.shortest_path(robot_node, anomaly_node)
        result[ns] = (nodes, _path_length(graph, nodes))
    return result


def select_nearest_robot(graph, loc, robot_poses, candidates):
    """candidates(로봇 네임스페이스 목록) 중 loc({'x','y'})에 통로
    그래프 최단 경로로 가장 가까운 로봇을 고른다.

    robot_poses: {ns: (x, y)} - 로봇별 최근 amcl_pose. candidates 중
    아직 amcl_pose를 한 번도 못 받은 로봇은 비교 대상에서 제외한다
    (위치를 모르는 로봇과 아는 로봇의 거리를 비교할 수 없음). 아무도
    pose가 없으면(AMCL이 아직 안 떴거나 수동 테스트 환경) candidates의
    첫 번째로 대체한다 - 이 함수가 생기기 전 "트리거가 로봇을 안
    정해주면 미션 목록의 첫 로봇" 안전망과 동일하다.
    """
    dists = path_distances(graph, loc, robot_poses, candidates)
    if not dists:
        return candidates[0] if candidates else None
    return min(dists, key=lambda ns: dists[ns][1])
