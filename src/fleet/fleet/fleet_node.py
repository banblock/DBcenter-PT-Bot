#!/usr/bin/env python3
"""Fleet Node - 시스템 설계도의 AMR FLEET NODE 쪽을 구현한 테스트 노드.

설계도와 매칭되는 책임:
- Backend가 준 구역/지점 데이터로 각 로봇의 순찰 경로를 만드는데, 한
  번의 긴 Nav2 목표가 아니라 고정 통로 그래프(route_graph.py /
  zone_router.py) 위에서 홉 단위로 만든다 - 맵이 좁고 TurtleBot4
  localization이 긴 직선 이동에서 잘 버티지 못하기 때문
  (구역이 겹치는가? -> 각 구역의 순찰 루트 생성)
- 어느 웨이포인트에 차단기 점검이 필요한지(has_gate), 어느 웨이포인트가
  공유 교차 지점인지(point_id) 태깅한다 - 여기 통로는 전부 외길이라,
  두 로봇 이상의 경로가 같은 통로를 쓰면 그게 곧 교차 지점이다
  (교차 가능 지점 계산 및 저장 -> 각 순찰 포인트에 차단기가 있는가?)
- 교차 지점 점유를 중재해서 두 로봇이 동시에 같은 지점을 점유하지
  못하게 한다 (교차지점인가? -> 점유돼있는가?)

ROS 2 도메인 어디서든 한 번만 실행하면 된다 (특정 로봇과 같은 위치에
있을 필요 없음). 각 로봇의 Control Node와는 정식 .srv/.msg 타입이
아니라 그냥 std_msgs/String + JSON으로 통신하므로, colcon 빌드 없이도
바로 시험해볼 수 있다.
"""

import json
import os
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import (QoSProfile, QoSDurabilityPolicy,
                        QoSReliabilityPolicy, QoSHistoryPolicy)
from std_msgs.msg import String

from fleet import zone_router
from fleet.route_graph import RouteGraph
from fleet.default_zones import DEFAULT_ZONES

MISSION_QOS = QoSProfile(
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    reliability=QoSReliabilityPolicy.RELIABLE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)

# /fleet/anomaly_trigger 메시지에 좌표가 안 실려 왔을 때 쓰는 기본값 -
# 실제 비전 노드가 감지한 좌표 대신 쓰는 자리표시자.
DEFAULT_ANOMALY = {'x': -2.33, 'y': 0.0313, 'yaw': 0.0}


def _default_graph_path():
    """colcon 빌드로 설치된 share 경로를 우선 쓰고, 실패하면(빌드 전
    상태) 이 파일 옆의 소스 경로로 대체한다 - 이 패키지의 다른 부분과
    마찬가지로 `python3 fleet_node.py`를 빌드 없이 바로 실행해도
    동작하게 하기 위함."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return os.path.join(get_package_share_directory('fleet'), 'config', 'route_graph.yaml')
    except Exception:
        return os.path.join(os.path.dirname(__file__), '..', 'config', 'route_graph.yaml')


class FleetNode(Node):
    def __init__(self):
        super().__init__('fleet_node')
        self._lock = threading.Lock()
        self._occupied = {}  # point_id -> 현재 그 지점을 점유 중인 로봇 네임스페이스

        self.graph = RouteGraph.from_yaml(_default_graph_path())

        # 로봇별 퍼블리셔는 미리 만들어두지 않고, 실제로 그 로봇의
        # 미션이 생기는 시점에 _ensure_robot_pubs()에서 지연 생성한다
        # (구역 구성이 바뀌면 로봇 목록도 바뀔 수 있어서 고정 딕셔너리로
        # 못 박아둘 수 없음).
        self._mission_pubs = {}
        self._anomaly_pubs = {}
        self.grant_pub = self.create_publisher(String, '/fleet/occupancy_grant', 10)
        self.create_subscription(String, '/fleet/occupancy_request', self._on_request, 10)
        self.create_subscription(String, '/fleet/occupancy_release', self._on_release, 10)

        # 실제 비전 노드(이상신호 감지 비전노드)가 연결되기 전까지 쓰는
        # 수동 대역. {"robot":"robot3","x":-0.3,"y":2,"yaw":0} 같은
        # JSON을 /fleet/anomaly_trigger에 퍼블리시하면 감지 상황을
        # 흉내낼 수 있다.
        self._anomaly_busy = set()  # 현재 이상 상황 대응 중이라 순찰을 이탈한 로봇들
        self.create_subscription(
            String, '/fleet/anomaly_trigger', self._on_anomaly_trigger, 10)
        self.create_subscription(
            String, '/fleet/anomaly_done', self._on_anomaly_done, 10)

        # 설계도의 실제 Backend 입력 (구역, 지점좌표 -> /backend/map_points),
        # {"zones": [{zone_id, robot, points: [{x,y,yaw,point_type}, ...]}]}
        # 형태. corners(구역 모서리)는 여기서 안 쓴다 - 통로 그래프는
        # 고정된 공용 인프라라서 구역마다 다시 계산할 대상이 아니다.
        self.create_subscription(String, '/backend/map_points', self._on_map_points, 10)

        self.missions = {}
        self._apply_zones(DEFAULT_ZONES)

        # TRANSIENT_LOCAL QoS에 더해서, 이 Fleet Node보다 늦게 뜬
        # Control Node도 자기 미션을 받을 수 있도록 주기적으로
        # 재발행한다.
        self.create_timer(1.0, self._publish_missions)

    def _ensure_robot_pubs(self, ns):
        if ns not in self._mission_pubs:
            self._mission_pubs[ns] = self.create_publisher(
                String, f'/fleet/{ns}/mission', MISSION_QOS)
        if ns not in self._anomaly_pubs:
            self._anomaly_pubs[ns] = self.create_publisher(String, f'/fleet/{ns}/anomaly', 10)

    def _apply_zones(self, zones):
        """zone_router로 구역/지점 데이터를 실제 로봇별 미션으로 라우팅하고,
        어떤 교차 지점이 발견됐는지 로그로 남긴 뒤 필요한 퍼블리셔를
        준비한다."""
        missions, crossing_log = zone_router.build_missions(self.graph, zones)
        for eid, robots, point_id in crossing_log:
            self.get_logger().info(
                f'crossing point {point_id}: aisle {eid} shared by {robots}')
        self.missions = missions
        for ns in missions:
            self._ensure_robot_pubs(ns)

    def _on_map_points(self, msg):
        try:
            payload = json.loads(msg.data)
            zones = payload['zones']
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.get_logger().warn(f'bad /backend/map_points payload, ignoring: {exc}')
            return
        self._apply_zones(zones)
        self._publish_missions()
        self.get_logger().info(
            f"zones updated from /backend/map_points: {[z['zone_id'] for z in zones]}")

    def _publish_missions(self):
        for ns, waypoints in self.missions.items():
            msg = String()
            msg.data = json.dumps(waypoints)
            self._mission_pubs[ns].publish(msg)

    def _on_request(self, msg):
        req = json.loads(msg.data)
        robot, point = req['robot'], req['point']
        with self._lock:
            holder = self._occupied.get(point)
            if holder is None or holder == robot:
                self._occupied[point] = robot
                granted = True
            else:
                granted = False
        status = 'granted' if granted else f'denied (held by {holder})'
        self.get_logger().info(f'occupancy request: {robot} wants {point} -> {status}')
        self._send_grant(robot, point, granted)

    def _send_grant(self, robot, point, granted):
        msg = String()
        msg.data = json.dumps({'robot': robot, 'point': point, 'granted': granted})
        self.grant_pub.publish(msg)

    def _on_anomaly_trigger(self, msg):
        try:
            payload = json.loads(msg.data) if msg.data else {}
        except json.JSONDecodeError:
            payload = {}

        # 임무 수행 로봇 선택: 아직 실시간 위치 추적 기반 선정 로직이
        # 없어서, 트리거 메시지에 지정된 로봇을 그대로 쓰고 없으면
        # 미션 목록의 첫 번째 로봇으로 대체한다.
        robot = payload.get('robot') or next(iter(self.missions), None)
        loc = {
            'x': payload.get('x', DEFAULT_ANOMALY['x']),
            'y': payload.get('y', DEFAULT_ANOMALY['y']),
            'yaw': payload.get('yaw', DEFAULT_ANOMALY['yaw']),
        }
        if robot not in self._anomaly_pubs:
            self.get_logger().warn(f'anomaly trigger: unknown robot {robot!r}, ignoring')
            return
        if robot in self._anomaly_busy:
            self.get_logger().info(
                f'anomaly trigger: {robot} already handling an anomaly, ignoring')
            return

        self._anomaly_busy.add(robot)
        self.get_logger().info(f'anomaly at {loc} -> dispatching {robot}')
        out = String()
        out.data = json.dumps(loc)
        self._anomaly_pubs[robot].publish(out)

    def _on_anomaly_done(self, msg):
        payload = json.loads(msg.data)
        robot = payload.get('robot')
        self._anomaly_busy.discard(robot)
        self.get_logger().info(f'{robot} finished handling anomaly, ready for new triggers')

    def _on_release(self, msg):
        rel = json.loads(msg.data)
        robot, point = rel['robot'], rel['point']
        with self._lock:
            if self._occupied.get(point) == robot:
                del self._occupied[point]
                self.get_logger().info(f'{robot} released {point}')


def main():
    rclpy.init()
    node = FleetNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
