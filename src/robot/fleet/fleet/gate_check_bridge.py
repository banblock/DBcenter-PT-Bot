#!/usr/bin/env python3
"""로봇 gate_check_request ↔ 백엔드 /api/align/check-gate 브릿지.

control_amr(InspectionFlowSupport)는 차단기 웨이포인트(has_gate)에 도착하면
  · ``/fleet/<ns>/gate_check_request``  {"robot": ns}            를 발행하고
  · ``/fleet/<ns>/gate_check_response`` {"state": "ok"|"fail"}   를 기다린다.
(inspect_gate() 는 응답이 올 때까지 0.5초마다 request 를 재발행한다.)

이 노드는 그 request 를 받아 백엔드 REST(``POST /api/align/check-gate``)를
호출한다. 백엔드는 다시 vision_bridge 로 비전(detect_main_node)에 차단기 실측
대조를 요청하고, DB 기준(평상시 ON=빨강)과 불일치면 이벤트를 자동 생성해 웹
대시보드에 '차단기 불일치' 카운터+알림을 띄운다. 그 판정 결과(error_state)를
gate_check_response 로 로봇에 돌려준다.

즉 전체 폐루프:
  robot(ROS) → 이 브릿지(ROS sub) → HTTP → backend
             → vision_bridge(ROS) → vision(ROS) → gate_state_equal/error_state
  robot(ROS) ← gate_check_response(ROS) ← 이 브릿지 ← HTTP resp ←

로봇이 넘기는 정보는 {"robot": ns} 뿐이라 "어느 차단기(equipment_id)인지"는
알 수 없다. 데모 배치는 로봇 1대당 구역에 차단기 1개(robot3/zoneAB→EQ-BRK-02,
robot8/zoneCD→EQ-BRK-01)이므로 ns→equipment 를 파라미터 고정 매핑으로 푼다.
로봇 한 대가 여러 차단기를 도는 배치로 확장하려면 gate_check_request 에
equipment_id 를 실어(웨이포인트까지 plumbing) 이 매핑을 대체하면 된다.

파라미터
--------
  · backend_base_url   기본 http://127.0.0.1:8000  (백엔드 FastAPI)
  · api_prefix         기본 /api
  · robot_namespaces   기본 [robot3, robot8]        (구독할 로봇 ns)
  · backend_robot_ids  기본 [AMR-01, AMR-02]        (check-gate 의 robot_id, ns 와 병렬)
  · equipment_ids      기본 [EQ-BRK-02, EQ-BRK-01]  (검사 대상 차단기, ns 와 병렬)
  · gate_id            기본 0                        (비전 쪽 차단기 식별자)
  · request_timeout_sec 기본 8.0                     (check-gate 는 비전 왕복까지 ~6s)
  · abort_on_mismatch  기본 False                    (True 면 불일치 시 로봇 미션 중단)
"""

import json
import threading
import urllib.error
import urllib.request

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class GateCheckBridge(Node):
    def __init__(self):
        super().__init__('gate_check_bridge')

        self.declare_parameter('backend_base_url', 'http://127.0.0.1:8000')
        self.declare_parameter('api_prefix', '/api')
        self.declare_parameter('robot_namespaces', ['robot3', 'robot8'])
        self.declare_parameter('backend_robot_ids', ['AMR-01', 'AMR-02'])
        # 웹 맵의 차단기 표시와 일치하도록 검사 대상 차단기를 교체(robot3→EQ-BRK-02, robot8→EQ-BRK-01).
        self.declare_parameter('equipment_ids', ['EQ-BRK-02', 'EQ-BRK-01'])
        self.declare_parameter('gate_id', 0)
        self.declare_parameter('request_timeout_sec', 8.0)
        self.declare_parameter('abort_on_mismatch', False)

        base_url = self.get_parameter('backend_base_url').value.rstrip('/')
        api_prefix = self.get_parameter('api_prefix').value
        self.check_gate_url = f"{base_url}{api_prefix}/align/check-gate"
        self.gate_id = int(self.get_parameter('gate_id').value)
        self.request_timeout_sec = float(self.get_parameter('request_timeout_sec').value)
        self.abort_on_mismatch = bool(self.get_parameter('abort_on_mismatch').value)

        namespaces = list(self.get_parameter('robot_namespaces').value)
        backend_robot_ids = list(self.get_parameter('backend_robot_ids').value)
        equipment_ids = list(self.get_parameter('equipment_ids').value)
        if not (len(namespaces) == len(backend_robot_ids) == len(equipment_ids)):
            raise ValueError(
                'robot_namespaces / backend_robot_ids / equipment_ids 길이가 달라 매핑할 수 없음: '
                f'{namespaces} / {backend_robot_ids} / {equipment_ids}')

        # ns → (backend_robot_id, equipment_id)
        self._target_by_ns = {
            ns: (rid, eq)
            for ns, rid, eq in zip(namespaces, backend_robot_ids, equipment_ids)
        }

        # 응답 발행자 + request 구독자 (로봇별)
        self._response_pubs = {}
        # inspect_gate 가 0.5초마다 재발행하므로, 같은 로봇의 검사가 진행 중이면
        # 뒤이어 오는 중복 request 는 무시한다.
        self._inflight = set()
        self._inflight_lock = threading.Lock()

        for ns in namespaces:
            self._response_pubs[ns] = self.create_publisher(
                String, f'/fleet/{ns}/gate_check_response', 10)
            self.create_subscription(
                String, f'/fleet/{ns}/gate_check_request',
                lambda msg, n=ns: self._on_gate_check_request(n, msg), 10)

        self.get_logger().info(
            'gate_check_bridge up — %s → %s (%s)'
            % (self.check_gate_url, self._target_by_ns,
               'abort_on_mismatch' if self.abort_on_mismatch else 'continue_on_mismatch'))

    def _on_gate_check_request(self, ns, msg):
        target = self._target_by_ns.get(ns)
        if target is None:
            self.get_logger().warn(f'매핑 없는 로봇 ns={ns} 의 gate_check_request 무시')
            return

        with self._inflight_lock:
            if ns in self._inflight:
                return  # 같은 로봇 검사 진행 중 — 재발행분 무시
            self._inflight.add(ns)

        # HTTP 왕복(비전 대조 ~6s)이 executor 스레드를 막지 않도록 워커 스레드에서 처리.
        robot_id, equipment_id = target
        threading.Thread(
            target=self._run_check, args=(ns, robot_id, equipment_id), daemon=True).start()

    def _run_check(self, ns, robot_id, equipment_id):
        try:
            state = self._call_backend(ns, robot_id, equipment_id)
        finally:
            with self._inflight_lock:
                self._inflight.discard(ns)
        self._response_pubs[ns].publish(String(data=json.dumps({'state': state})))
        self.get_logger().info(f'[{ns}] gate_check_response → {state}')

    def _call_backend(self, ns, robot_id, equipment_id):
        """백엔드 check-gate 호출 → 로봇에 돌려줄 'ok'/'fail' 로 변환.

        · error_state 0(일치) → ok
        · error_state 1(불일치) → 웹 알림은 백엔드가 이미 띄움. 로봇은 순찰을
          계속하도록 기본 ok, abort_on_mismatch=True 면 fail(미션 중단).
        · error_state 2/3(비전이 못 봄) 또는 HTTP 실패 → fail(검사 불가).
        """
        body = json.dumps({
            'equipment_id': equipment_id,
            'robot_id': robot_id,
            'gate_id': self.gate_id,
        }).encode('utf-8')
        req = urllib.request.Request(
            self.check_gate_url, data=body,
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout_sec) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            self.get_logger().error(f'[{ns}] check-gate 호출 실패({equipment_id}): {exc}')
            return 'fail'

        data = payload.get('data') or {}
        error_state = data.get('error_state')
        verdict = data.get('verdict')
        self.get_logger().info(
            f'[{ns}] check-gate({equipment_id}) → verdict={verdict} error_state={error_state}')

        if error_state == 0:
            return 'ok'
        if error_state == 1:  # 불일치 — 웹 알림은 이미 발행됨
            return 'fail' if self.abort_on_mismatch else 'ok'
        return 'fail'  # 2(못 찾음)/3(cam 없음)/None(비정상 응답)


def main(args=None):
    rclpy.init(args=args)
    node = GateCheckBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
