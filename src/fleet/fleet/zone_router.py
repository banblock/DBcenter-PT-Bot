"""구역/순찰지점 데이터를 고정 통로 그래프(route_graph.py) 위에서
라우팅해서 로봇별 미션으로 바꾼다.

시스템 설계도의 AMR FLEET NODE 경로 생성 체인을 구현:
각 구역의 순찰 루트 생성 (한 번의 긴 Nav2 목표가 아니라, 통로 그래프
위에서 홉 단위로 구역별 경로를 만든다 - 맵이 좁고 TurtleBot4
localization이 긴 직선 이동에서 잘 버티지 못하기 때문)
-> 교차 가능 지점 계산 및 저장 (두 로봇 이상의 경로가 같은 통로 엣지를
쓰면 그게 곧 공유 교차 지점)
-> 각 순찰 포인트에 차단기가 있는가? (차단기 점검 지점 태깅).

입력 형태 (구역 하나) - UI가 그린 구역 모서리(corners)는 라우팅에 안
쓴다 (통로 그래프는 고정된 공용 인프라라서, 로봇이 다른 구역의 교차로를
지나가도 상관없음). 실제로 쓰는 건 순서가 있는 순찰 지점 목록뿐:

    {
        'zone_id': str,
        'robot': str,                 # 이 구역이 배정된 로봇 네임스페이스
        'points': [{'x','y','yaw','point_type'}, ...],  # 순찰 순서대로;
                                        # point_type이 'gate'면 차단기
                                        # 점검 지점(보통 통로 중간)
    }

출력은 fleet_node.py가 원래부터 /fleet/<robot>/mission에 발행하던
형태와 동일: {robot: [{x, y, yaw, has_gate, point_id}, ...]}. 여기에
'origin' 필드('patrol' = UI가 지정한 실제 순찰 지점, 'transit' = 거기까지
가기 위해 라우터가 끼워 넣은 경유 교차로)가 추가로 붙는데, Control
Node는 이 필드를 그냥 무시하고 로깅/시각화용으로만 쓴다.
"""

import math


def _is_gate(point_type):
    return bool(point_type) and 'gate' in str(point_type).lower()


def _heading_deg(a, b):
    """경유 노드(transit)의 yaw는 UI가 준 값이 없으니, 이전 지점 -> 이
    지점 방향을 향하도록 자동으로 계산한다 (도 단위, 기존 코드와 동일
    컨벤션)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _route_zone(graph, zone):
    """이 구역의 순찰 지점들을 `graph`의 전용 복사본 위에 스냅해서 끼워
    넣고, 연속된 두 지점 사이마다 다익스트라(RouteGraph.shortest_path)로
    실제 경로를 구한 뒤 전부 이어 붙여서 하나의 웨이포인트 리스트로
    펼친다.

    반환값은 (waypoints, incoming_edge) 두 개:
    - waypoints: 최종 미션에 실릴 웨이포인트 목록
    - incoming_edge: 웨이포인트별로 "그 웨이포인트 직전 홉이 지나온
      원본 통로 id(base_edge_id)"를 기록한 목록. 맨 첫 웨이포인트는
      그 앞에 아무 홉도 없으니 None. build_missions()가 이 목록으로
      "어느 로봇이 어느 통로를 쓰는지"를 모아서 교차 지점을 판정한다."""
    g = graph.copy()
    points = zone['points']
    # 순찰 지점마다 고유 id를 붙여서 그래프에 삽입 (기존 교차로에
    # 스냅되면 그 교차로 id가, 아니면 새로 쪼갠 노드의 id가 돌아온다).
    node_ids = [
        g.insert_point(f"{zone['zone_id']}_{i}", (float(p['x']), float(p['y'])))
        for i, p in enumerate(points)
    ]

    waypoints = []       # {x, y, yaw, has_gate, point_id, origin}
    incoming_edge = []   # 웨이포인트별 base_edge_id (또는 None)

    # 첫 순찰 지점: 이 앞에는 지나온 홉이 없으니 점유 조정 대상이 아니다.
    first = points[0]
    waypoints.append({
        'x': float(first['x']), 'y': float(first['y']),
        'yaw': float(first.get('yaw', 0.0)),
        'has_gate': _is_gate(first.get('point_type')), 'point_id': None,
        'origin': 'patrol',
    })
    incoming_edge.append(None)

    for i in range(len(points) - 1):
        path_nodes, base_edges = g.shortest_path(node_ids[i], node_ids[i + 1])
        # path_nodes[0]은 node_ids[i]로, 이미 이전 반복에서 웨이포인트로
        # 나갔으니 그 다음부터 이 홉의 나머지를 순서대로 펼친다.
        for j in range(1, len(path_nodes)):
            nid = path_nodes[j]
            edge = base_edges[j - 1]
            xy = g.nodes[nid]
            is_final = (j == len(path_nodes) - 1)
            if is_final and nid == node_ids[i + 1]:
                # 이 홉의 목적지 = 원래 UI가 지정한 순찰 지점이므로,
                # 그래프에 스냅된 좌표가 아니라 UI가 준 원본 좌표/yaw를
                # 그대로 쓴다.
                p = points[i + 1]
                waypoints.append({
                    'x': float(p['x']), 'y': float(p['y']),
                    'yaw': float(p.get('yaw', 0.0)),
                    'has_gate': _is_gate(p.get('point_type')), 'point_id': None,
                    'origin': 'patrol',
                })
            else:
                # 목적지로 가는 길에 어쩔 수 없이 지나가야 하는 중간
                # 교차로 - UI가 지정한 지점이 아니므로 has_gate=False,
                # yaw는 자동 계산.
                prev_xy = g.nodes[path_nodes[j - 1]]
                waypoints.append({
                    'x': xy[0], 'y': xy[1],
                    'yaw': _heading_deg(prev_xy, xy),
                    'has_gate': False, 'point_id': None,
                    'origin': 'transit',
                })
            incoming_edge.append(edge)

    return waypoints, incoming_edge


def build_missions(graph, zones):
    """graph: RouteGraph (route_graph.py 참고). zones: 구역 dict 목록
    (모듈 docstring의 입력 형태 참고). (missions, crossing_log)를
    반환한다."""
    per_robot_waypoints = {}
    per_robot_edges = {}
    for zone in zones:
        waypoints, incoming_edge = _route_zone(graph, zone)
        per_robot_waypoints[zone['robot']] = waypoints
        per_robot_edges[zone['robot']] = incoming_edge

    # 교차 지점 판정은 사실 알고리즘이라기보다 집합 연산이다: 각
    # base_edge_id(=물리적 통로 하나)를 누가(어느 로봇이, 몇 번째
    # 웨이포인트에서) 지나가는지 모아서, 두 로봇 "이상"이 같은 통로를
    # 쓰면 그 통로가 곧 공유 교차 지점이 된다. 구역 폴리곤이 겹치는지
    # 같은 기하 계산은 필요 없다 - 통로 그래프가 이미 고정 인프라라서
    # "같은 엣지를 쓰는가"만 보면 충분하다.
    edge_users = {}
    for robot, edges in per_robot_edges.items():
        for i, eid in enumerate(edges):
            if eid is None:
                continue
            edge_users.setdefault(eid, []).append((robot, i))

    crossing_log = []
    for eid, users in edge_users.items():
        robots_involved = {robot for robot, _ in users}
        if len(robots_involved) < 2:
            continue
        # point_id는 fleet_node의 기존 occupancy_request/grant/release
        # 프로토콜이 그대로 쓰는 키다 - 통로 하나 전체를 하나의 점유
        # 대상으로 취급해서, 그 통로에 진입하는 모든 웨이포인트(양쪽
        # 로봇, 왕복 포함)에 같은 point_id를 붙인다.
        point_id = f'X_{eid}'
        for robot, i in users:
            per_robot_waypoints[robot][i]['point_id'] = point_id
        crossing_log.append((eid, sorted(robots_involved), point_id))

    return per_robot_waypoints, crossing_log
