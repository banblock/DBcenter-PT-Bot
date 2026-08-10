"""고정 통로 그래프: 노드 = 교차로, 엣지 = 한 번에 로봇 한 대만 지날 수
있는 통로(외길).

맵 전용 YAML 파일에서 한 번 로드한다 (config/route_graph.yaml 참고).
UI가 준 순찰 지점은 이 그래프 위로 "스냅"된다 - 기존 교차로 노드가
가까우면 그 노드를 재사용하고, 아니면 가장 가까운 엣지를 쪼개서 그
투영된 위치에 새 노드를 끼워 넣는다 (통로 중간에 서는 차단기 점검
지점이 이렇게 처리됨). 두 순찰 지점 사이의 실제 경로는 이 그래프
위에서 단순 다익스트라(Dijkstra)로 구한다 - 그래서 로봇의 미션은 Nav2에
긴 직선 목표 하나를 주는 게 아니라, 통로를 따라가는 짧은 홉들의 촘촘한
나열이 된다 (TurtleBot4 localization이 긴 직선 이동에서 불안정하기
때문).
"""

import heapq
import math

import yaml


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


class RouteGraph:
    def __init__(self, nodes, edges):
        """nodes: {node_id: (x, y)}
        edges: (edge_id, a, b, base_id) 튜플들의 목록. base_id는 이 엣지가
        원래 어떤 엣지에서 쪼개져 나왔는지를 나타낸다 (쪼개진 적 없으면
        자기 자신). 교차 지점 판정(zone_router.py)이 "물리적으로 같은
        통로인지"를 이 base_id로 비교하기 때문에, 엣지를 여러 번 쪼개도
        base_id는 항상 맨 처음 원본 통로를 가리키도록 유지해야 한다."""
        self.nodes = dict(nodes)
        self.edges = {}
        self.adj = {}
        for edge_id, a, b, base_id in edges:
            self._add_edge(edge_id, a, b, base_id)

    def _add_edge(self, edge_id, a, b, base_id):
        w = _dist(self.nodes[a], self.nodes[b])
        self.edges[edge_id] = {'a': a, 'b': b, 'w': w, 'base': base_id}
        self.adj.setdefault(a, []).append((b, edge_id))
        self.adj.setdefault(b, []).append((a, edge_id))

    def _remove_edge(self, edge_id):
        e = self.edges.pop(edge_id)
        self.adj[e['a']] = [t for t in self.adj[e['a']] if t[1] != edge_id]
        self.adj[e['b']] = [t for t in self.adj[e['b']] if t[1] != edge_id]

    @classmethod
    def from_yaml(cls, path):
        """route_graph.yaml을 읽어 고정 그래프를 만든다. 처음 로드할
        때는 아직 아무것도 쪼개지지 않았으니 base_id = 자기 자신의
        edge_id."""
        with open(path) as f:
            data = yaml.safe_load(f)
        nodes = {nid: (float(v['x']), float(v['y']))
                  for nid, v in data['nodes'].items()}
        edges = [(e['id'], e['a'], e['b'], e['id']) for e in data['edges']]
        return cls(nodes, edges)

    def copy(self):
        """구역(zone)마다 이 고정 그래프를 독립적으로 수정(insert_point로
        쪼개기)해야 하므로, zone_router가 라우팅을 시작하기 전에 항상
        원본 대신 복사본을 사용한다 - 한 구역의 순찰 지점이 다른
        구역에서 만든 임시 노드에 영향을 주면 안 되기 때문."""
        edges = [(eid, e['a'], e['b'], e['base']) for eid, e in self.edges.items()]
        return RouteGraph(self.nodes, edges)

    def is_junction(self, node_id, min_degree=3):
        """이 노드가 실제 물리적 교차로(외길 통로 3개 이상이 만나는
        지점)인지 판정한다. zone_router가 교차 지점을 엣지 단위가 아니라
        노드 단위로 잡아야 하는지 판단하는 데 쓴다 - 순찰/차단기 지점을
        끼워 넣느라 엣지 중간을 쪼개서 생긴 노드는 항상 degree 2라서
        (원래 한 엣지의 양쪽으로만 이어짐) 교차로로 잡히지 않는다. 인자로
        받은 node_id가 이 그래프에 없으면(다른 zone의 사본에서만 쓰인
        임시 노드 등) degree 0 취급으로 안전하게 False를 반환한다."""
        return len(self.adj.get(node_id, [])) >= min_degree

    def _nearest_node(self, pt):
        return min(self.nodes.items(), key=lambda kv: _dist(pt, kv[1]))

    def _nearest_edge_projection(self, pt):
        """모든 엣지에 대해 pt를 수선의 발로 투영해보고 가장 가까운
        (edge_id, t, 투영좌표, 거리)를 고른다. t(0~1, 엣지 위 위치
        비율)는 (0.02, 0.98) 범위로 clamp해서, 투영점이 엣지 끝
        노드와 거의 겹쳐버리는 경우(퇴화)를 피한다."""
        best = None
        px, py = pt
        for eid, e in self.edges.items():
            ax, ay = self.nodes[e['a']]
            bx, by = self.nodes[e['b']]
            dx, dy = bx - ax, by - ay
            seg_len2 = dx * dx + dy * dy
            if seg_len2 < 1e-9:
                continue
            t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
            t = max(0.02, min(0.98, t))
            proj = (ax + t * dx, ay + t * dy)
            d = _dist(pt, proj)
            if best is None or d < best[3]:
                best = (eid, t, proj, d)
        return best

    def nearest_point(self, pt, snap_threshold=0.15):
        """pt에서 가장 가까운, 로봇이 실제로 갈 수 있는 통로 위의 좌표를
        돌려준다. `insert_point()`와 같은 스냅 규칙(기존 노드 우선,
        아니면 가장 가까운 엣지에 투영)을 쓰지만 그래프를 변형하지
        않는 조회 전용 버전이다 - 이상신호 CCTV 좌표처럼 랙 안쪽 등
        로봇이 실제로 갈 수 없는 위치가 들어와도, 통로 그래프 위에서
        가장 가까운 지점만 알고 싶을 때 쓴다(그 지점까지 이동한 뒤
        원래 좌표 쪽으로 카메라만 돌리는 용도 - fleet_node.py
        `_on_anomaly_trigger` 참고)."""
        near_id, near_xy = self._nearest_node(pt)
        if _dist(pt, near_xy) <= snap_threshold:
            return near_xy
        hit = self._nearest_edge_projection(pt)
        if hit is None:
            return near_xy
        _, _, proj, _ = hit
        return proj

    def insert_point(self, node_id, pt, snap_threshold=0.15):
        """순찰 지점 pt를 그래프에 노드로 끼워 넣는다. 이름 붙은
        알고리즘이라기보다 "점을 그래프에 투영"하는 단순 기하 연산:

        1) 기존 교차로 노드가 snap_threshold(15cm) 이내로 가까우면 새
           노드를 만들지 않고 그 노드를 그대로 재사용한다 (좌표 오차로
           같은 교차로가 중복 생성되는 걸 방지).
        2) 아니면 가장 가까운 엣지를 찾아 그 위의 투영 위치에 새 노드를
           만들고, 원래 엣지 하나를 두 개의 절반 엣지로 쪼갠다. 두
           절반 모두 base id는 원본 엣지를 그대로 물려받는다 - 그래야
           나중에 이 절반 엣지를 지나가는 두 로봇이 "같은 통로를
           쓴다"고 정확히 판정된다.

        실제로 쓰인 노드 id를 반환한다 (1번 경우엔 인자로 받은 node_id가
        아니라 재사용된 기존 노드의 id)."""
        near_id, near_xy = self._nearest_node(pt)
        if _dist(pt, near_xy) <= snap_threshold:
            return near_id

        hit = self._nearest_edge_projection(pt)
        if hit is None:
            self.nodes[node_id] = pt
            return node_id

        eid, t, proj, d = hit
        e = self.edges[eid]
        a, b, base = e['a'], e['b'], e['base']
        self.nodes[node_id] = proj
        self._remove_edge(eid)
        self._add_edge(f'{eid}~{node_id}~a', a, node_id, base)
        self._add_edge(f'{eid}~{node_id}~b', node_id, b, base)
        return node_id

    def shortest_path(self, start, goal):
        """다익스트라 최단 경로. 그래프가 작고(맵 하나에 노드 10개
        수준) 엣지 가중치가 전부 양수(유클리드 거리)라서 A* 같은 휴리스틱
        탐색 없이 표준 다익스트라로 충분하다.

        (node_ids, base_edge_ids)를 반환한다. base_edge_ids는 홉 하나당
        하나씩(node_ids[i] -> node_ids[i+1] 구간이 지나간 원본 통로
        id) 들어있고, zone_router가 이 값으로 "이 홉이 어느 물리적
        통로를 쓰는지"를 추적해서 교차 지점을 판정한다."""
        if start == goal:
            return [start], []

        dist = {start: 0.0}
        prev = {}
        visited = set()
        heap = [(0.0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)
            if u == goal:
                break
            for v, eid in self.adj.get(u, []):
                nd = d + self.edges[eid]['w']
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = (u, eid)
                    heapq.heappush(heap, (nd, v))

        if goal not in prev and goal != start:
            raise ValueError(f'no path from {start!r} to {goal!r} in route graph')

        # goal에서 prev 포인터를 거슬러 올라가며 경로를 복원한 뒤 뒤집는다.
        nodes = [goal]
        base_edges = []
        cur = goal
        while cur != start:
            u, eid = prev[cur]
            base_edges.append(self.edges[eid]['base'])
            nodes.append(u)
            cur = u
        nodes.reverse()
        base_edges.reverse()
        return nodes, base_edges
