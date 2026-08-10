"""구역/순찰지점 데이터를 고정 통로 그래프(route_graph.py) 위에서
라우팅해서 로봇별 미션으로 바꾼다.

시스템 설계도의 AMR FLEET NODE 경로 생성 체인을 구현:
각 구역의 순찰 루트 생성 (한 번의 긴 Nav2 목표가 아니라, 통로 그래프
위에서 홉 단위로 구역별 경로를 만든다 - 맵이 좁고 TurtleBot4
localization이 긴 직선 이동에서 잘 버티지 못하기 때문)
-> 교차 가능 지점 계산 및 저장 (두 로봇 이상의 경로가 겹치면 그게 곧
공유 교차 지점 - 아래 "교차 지점 판정 단위" 참고)
-> 각 순찰 포인트에 차단기가 있는가? (차단기 점검 지점 태깅).

## 교차 지점 판정 단위 - 엣지와 노드(교차로)를 독립적으로 같이 본다

처음엔 "두 로봇이 같은 엣지(외길 통로)를 쓰는가"만으로 교차 지점을
잡았는데, 이 그래프가 사다리형 구조라 F_AB/F_BC/F_CD/R_AB/R_BC/R_CD처럼
외길 통로 3개가 만나는 실제 교차로 노드가 있다는 걸 놓치고 있었다.
로봇 A가 전면 통로(F_AB_BC)를 타고 F_BC를 그냥 통과하고, 로봇 B가
세로 통로(V_BC)를 타고 올라와 F_BC에서 꺾는 경우 - 둘은 서로 다른
엣지를 쓰기 때문에 엣지 전용 판정으로는 전혀 감지되지 않았지만, 실제로는
같은 교차로 지점에 동시에 들어올 수 있다(하드웨어 테스트 중 실제로
관찰된 문제).

그렇다고 "노드면 노드로만, 아니면 엣지로만" 식으로 배타적으로 나누면
안 된다 - 처음 그렇게 짰다가 V_AB/V_BC/V_CD 같은 세로 통로(양 끝이
전부 교차로)에서 기존에 정상 동작하던 엣지 공유 감지가 깨지는 걸
발견했다: 로봇 A가 세로 통로를 타고 내려가 그 남쪽 끝(R_BC)에
도착하고, 로봇 B가 같은 세로 통로를 타고 올라가 그 북쪽 끝(F_BC)에
도착하면, 둘은 같은 엣지(V_BC)를 정반대 방향으로 동시에 쓰는데도
"도착 노드"는 서로 달라서(R_BC vs F_BC) 노드 판정에도 안 걸리고 엣지
판정도 건너뛰면 둘 다 못 잡는다. 좁은 외길을 마주보고 달리는 딱 그
상황을 막으려고 애초에 엣지 판정을 만든 거라 이게 더 치명적이다.

그래서 지금은 두 판정을 **독립적으로 계산**한다:
- 엣지 판정(`X_<base_edge_id>`): 같은 엣지를 쓰는 로봇이 2대 이상이면
  (도착 노드가 어디든 상관없이) 그 엣지 전체가 교차 지점.
- 노드 판정(`J_<node_id>`): 목적지가 실제 교차로(`RouteGraph.is_junction()`,
  degree>=3)이고, 그 교차로를 지나가는 로봇이(어느 엣지로 왔든) 2대
  이상이면 그 교차로가 교차 지점.

한 웨이포인트가 둘 다에 걸릴 수도 있다(예: 공유 엣지를 타고 공유
교차로에 도착하는 경우). occupancy 프로토콜이 웨이포인트당 락 하나만
쓰는 구조라, 그럴 땐 union-find로 두 point_id를 하나로 합쳐서 하나의
락으로 두 요구를 동시에 만족시킨다(필요한 것보다 살짝 넓게 묶는 안전한
쪽 근사 - `_UnionFind`/`build_missions()` 참고).

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


def _emit_hop(g, from_node, to_node, dest_point, waypoints, incoming_edge,
              waypoint_node_ids):
    """`from_node`에서 `to_node`까지 다익스트라(RouteGraph.shortest_path)로
    실제 경로를 구해, 중간에 어쩔 수 없이 지나가는 경유 교차로(transit)와
    최종 목적지(`dest_point`, UI가 준 원본 좌표/yaw 사용) 웨이포인트를
    `waypoints`/`incoming_edge`/`waypoint_node_ids`에 이어붙인다. 순찰
    지점 사이 홉과, 로봇의 실제 시작 위치에서 첫 순찰 지점까지의 홉에
    공통으로 쓴다(`_route_zone()` 참고) - 둘 다 "그래프 위 한 노드에서
    다른 한 노드로 이동"이라는 점은 같기 때문."""
    path_nodes, base_edges = g.shortest_path(from_node, to_node)
    # path_nodes[0]은 from_node로, 이전 홉에서 이미 웨이포인트로 나갔거나
    # (또는 이번이 첫 홉이라 애초에 웨이포인트가 아니거나) 하니 그 다음부터
    # 이 홉의 나머지를 순서대로 펼친다.
    for j in range(1, len(path_nodes)):
        nid = path_nodes[j]
        edge = base_edges[j - 1]
        xy = g.nodes[nid]
        is_final = (j == len(path_nodes) - 1)
        if is_final and nid == to_node:
            # 이 홉의 목적지 = 원래 UI가 지정한 순찰 지점이므로,
            # 그래프에 스냅된 좌표가 아니라 UI가 준 원본 좌표/yaw를
            # 그대로 쓴다.
            waypoints.append({
                'x': float(dest_point['x']), 'y': float(dest_point['y']),
                'yaw': float(dest_point.get('yaw', 0.0)),
                'has_gate': _is_gate(dest_point.get('point_type')),
                'point_id': None,
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
        waypoint_node_ids.append(nid)


def _route_zone(graph, zone, start_pos=None):
    """이 구역의 순찰 지점들을 `graph`의 전용 복사본 위에 스냅해서 끼워
    넣고, 연속된 두 지점 사이마다 `_emit_hop()`으로 실제 경로를 구한 뒤
    전부 이어 붙여서 하나의 웨이포인트 리스트로 펼친다.

    `start_pos`(로봇의 현재 위치, (x, y))가 주어지면 첫 순찰 지점도
    "현재 위치 -> 0번 지점" 홉으로 취급해서 다른 홉들과 동일하게
    그래프 위에서 경로를 잡고 점유 판정 대상이 되게 한다(그래야 0번
    지점이 하필 공유 통로 위에 찍혀 있어도 그 통로를 다른 로봇과
    중재 없이 그냥 뚫고 들어가는 일이 없다 - 하드웨어 테스트 중
    실제로 관찰된 문제). Fleet이 아직 그 로봇의 amcl_pose를 한 번도
    못 받았으면(막 켜진 직후 등) `start_pos`가 None으로 넘어오고, 이
    경우 예전처럼 0번 지점을 점유 조정 대상이 아닌 첫 웨이포인트로
    그냥 박아 넣는다(로봇이 실제로 어디서 출발할지 몰라 경로를 계산할
    방법이 없으므로 - 이 폴백은 이전 동작과 동일).

    반환값은 (waypoints, incoming_edge, waypoint_node_ids) 세 개:
    - waypoints: 최종 미션에 실릴 웨이포인트 목록
    - incoming_edge: 웨이포인트별로 "그 웨이포인트 직전 홉이 지나온
      원본 통로 id(base_edge_id)"를 기록한 목록. start_pos가 없어서
      0번 지점을 직접 박아 넣은 경우에만 그 앞에 홉이 없어 None.
    - waypoint_node_ids: 웨이포인트별로 실제 도착한 그래프 노드 id(교차로에
      스냅됐으면 그 교차로 id, 아니면 새로 쪼갠 임시 노드 id).
    build_missions()가 이 두 목록으로 "어느 로봇이 어느 통로/교차로를
    쓰는지"를 모아서 교차 지점을 판정한다."""
    g = graph.copy()
    points = zone['points']
    # 순찰 지점마다 고유 id를 붙여서 그래프에 삽입 (기존 교차로에
    # 스냅되면 그 교차로 id가, 아니면 새로 쪼갠 노드의 id가 돌아온다).
    node_ids = [
        g.insert_point(f"{zone['zone_id']}_{i}", (float(p['x']), float(p['y'])))
        for i, p in enumerate(points)
    ]

    waypoints = []          # {x, y, yaw, has_gate, point_id, origin}
    incoming_edge = []      # 웨이포인트별 base_edge_id (또는 None)
    waypoint_node_ids = []  # 웨이포인트별 도착 노드 id

    def _append_first_direct():
        first = points[0]
        waypoints.append({
            'x': float(first['x']), 'y': float(first['y']),
            'yaw': float(first.get('yaw', 0.0)),
            'has_gate': _is_gate(first.get('point_type')), 'point_id': None,
            'origin': 'patrol',
        })
        incoming_edge.append(None)
        waypoint_node_ids.append(node_ids[0])

    if start_pos is None:
        _append_first_direct()
    else:
        start_node = g.insert_point(
            f"{zone['zone_id']}_start", (float(start_pos[0]), float(start_pos[1])))
        if start_node == node_ids[0]:
            # 로봇이 이미 0번 지점 바로 근처(snap_threshold 이내)에 있음 -
            # 별도 홉을 만들 이유가 없으니 그냥 직접 박아 넣는다
            # (shortest_path(start, start)는 웨이포인트를 하나도 안 만들어서
            # 0번 지점 자체가 미션에서 통째로 빠지는 걸 방지).
            _append_first_direct()
        else:
            _emit_hop(g, start_node, node_ids[0], points[0],
                      waypoints, incoming_edge, waypoint_node_ids)

    for i in range(len(points) - 1):
        _emit_hop(g, node_ids[i], node_ids[i + 1], points[i + 1],
                  waypoints, incoming_edge, waypoint_node_ids)

    return waypoints, incoming_edge, waypoint_node_ids


class _UnionFind:
    """엣지 자원(`X_<eid>`)과 교차로 노드 자원(`J_<nid>`)을 하나의
    point_id로 합칠 때 쓰는 단순 union-find(경로 압축만, union by rank는
    자원 개수가 워낙 적어서 불필요). build_missions()에서 한 웨이포인트가
    "공유 엣지"와 "공유 교차로"를 동시에 필요로 할 때, 둘을 같은
    그룹으로 묶어서 occupancy 프로토콜이 웨이포인트당 락 하나만 쓰는
    구조를 그대로 유지하게 해준다."""

    def __init__(self):
        self._parent = {}

    def find(self, key):
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def build_missions(graph, zones, robot_positions=None):
    """graph: RouteGraph (route_graph.py 참고). zones: 구역 dict 목록
    (모듈 docstring의 입력 형태 참고). robot_positions: {robot: (x, y)} -
    Fleet이 amcl_pose로 알고 있는 로봇의 현재 위치(옵션). 주어진 로봇은
    0번 순찰 지점까지도 그래프 경로로 계산돼 점유 조정 대상이 되고
    (`_route_zone()`의 start_pos 참고), 위치를 모르는 로봇(딕셔너리에
    없거나 robot_positions 자체가 None)은 예전처럼 0번 지점으로 직행하는
    폴백을 그대로 쓴다. (missions, crossing_log)를 반환한다."""
    robot_positions = robot_positions or {}
    per_robot_waypoints = {}
    per_robot_edges = {}
    per_robot_nodes = {}
    for zone in zones:
        start_pos = robot_positions.get(zone['robot'])
        waypoints, incoming_edge, waypoint_node_ids = _route_zone(
            graph, zone, start_pos=start_pos)
        per_robot_waypoints[zone['robot']] = waypoints
        per_robot_edges[zone['robot']] = incoming_edge
        per_robot_nodes[zone['robot']] = waypoint_node_ids

    # 교차 지점 판정은 사실 알고리즘이라기보다 집합 연산이다: 각 로봇이
    # 실제로 지나가는 엣지/도착하는 교차로 노드를 모아서, 두 로봇
    # "이상"이 같은 엣지나 같은 교차로를 쓰면 그게 곧 공유 교차 지점이
    # 된다. 구역 폴리곤이 겹치는지 같은 기하 계산은 필요 없다 - 통로
    # 그래프가 이미 고정 인프라라서 "같은 자원을 쓰는가"만 보면 충분하다.
    #
    # 엣지 판정과 노드 판정을 독립적으로 같이 계산한다(모듈 docstring
    # "교차 지점 판정 단위" 참고 - 배타적으로 나누면 세로 통로처럼 양
    # 끝이 다 교차로인 엣지에서 마주보고 오는 두 로봇을 놓친다).
    # incoming_edge가 None인 웨이포인트(로봇 시작 위치를 몰라 0번 지점을
    # 직행 폴백으로 박아 넣은 경우뿐 - _route_zone() 참고)는 그 앞에
    # 지나온 홉이 없어 점유 조정 대상이 아니므로 건너뛴다.
    edge_users = {}
    node_users = {}
    for robot, edges in per_robot_edges.items():
        nodes = per_robot_nodes[robot]
        for i, eid in enumerate(edges):
            if eid is None:
                continue
            edge_users.setdefault(eid, []).append((robot, i))
            nid = nodes[i]
            if graph.is_junction(nid):
                node_users.setdefault(nid, []).append((robot, i))

    def _is_shared(users):
        return len({robot for robot, _ in users}) >= 2

    active_edges = {eid for eid, users in edge_users.items() if _is_shared(users)}
    active_nodes = {nid for nid, users in node_users.items() if _is_shared(users)}

    # 한 웨이포인트가 활성 엣지 자원과 활성 노드 자원을 동시에 필요로
    # 하면(그 엣지도 다른 로봇과 공유되고, 도착하는 교차로도 다른
    # 로봇과 공유됨) 두 자원을 하나의 point_id로 합친다 - 정확히
    # 필요한 만큼만 나누는 대신 살짝 넓게 하나로 묶는 안전한 쪽 근사다.
    uf = _UnionFind()
    for robot, edges in per_robot_edges.items():
        nodes = per_robot_nodes[robot]
        for i, eid in enumerate(edges):
            if eid in active_edges and nodes[i] in active_nodes:
                uf.union(f'X_{eid}', f'J_{nodes[i]}')

    # point_id는 fleet_node의 기존 occupancy_request/grant/release
    # 프로토콜이 그대로 쓰는 키다 - 자원(들) 전체를 하나의 점유 대상으로
    # 취급해서, 거기 진입하는 모든 웨이포인트(양쪽 로봇, 왕복 포함)에
    # 같은 point_id를 붙인다.
    crossing_robots = {}  # point_id -> {robot, ...} (crossing_log 조립용)
    for robot, edges in per_robot_edges.items():
        nodes = per_robot_nodes[robot]
        for i, eid in enumerate(edges):
            needs_edge = eid in active_edges
            needs_node = nodes[i] in active_nodes
            if not needs_edge and not needs_node:
                continue
            key = f'X_{eid}' if needs_edge else f'J_{nodes[i]}'
            point_id = uf.find(key)
            per_robot_waypoints[robot][i]['point_id'] = point_id
            crossing_robots.setdefault(point_id, set()).add(robot)

    crossing_log = [
        (point_id[2:], sorted(robots), point_id)
        for point_id, robots in crossing_robots.items()
    ]
    return per_robot_waypoints, crossing_log
