"""Gate camera alignment scaffolding for the AMR Control Node."""

import json
import time

from std_msgs.msg import String


class GateAlignmentFlowSupport:
    """Hooks for aligning the TurtleBot4 camera at gate waypoints."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        # TODO: Fleet Node/UI와 합의한 topic 이름으로 변경한다.
        self.alignment_done_pub = self.navigator.create_publisher(
            String, f'/fleet/{namespace}/gate_alignment_done', 10)
        # TODO: gate 정렬 완료 후 정지 시간을 운영 정책에 맞게 조정한다.
        self.pause_after_alignment_sec = 2.0

    def get_target_direction(self, waypoint):
        # TODO: waypoint의 gate 방향 필드 이름을 확정한다. 예: gate_yaw, camera_yaw.
        # TODO: 방향 필드가 없을 때 기본값을 waypoint yaw로 할지, 현재 자세로 할지 결정한다.
        return waypoint.get('gate_yaw', waypoint.get('yaw'))

    def align_camera_to_gate(self, waypoint):
        # TODO: TurtleBot4 카메라가 gate 방향을 보도록 정렬한다.
        # TODO: 카메라 pan/tilt가 없으면 로봇 base yaw를 회전시키는 방식으로 구현한다.
        # TODO: 정렬 허용 오차, timeout, 실패 시 retry 정책을 정의한다.
        target_direction = self.get_target_direction(waypoint)
        self.navigator.info(
            f'[{self.namespace}] aligning camera to gate direction {target_direction}')
        return True

    def publish_alignment_done(self, waypoint):
        # TODO: Fleet Node/UI와 합의한 메시지 schema로 변경한다.
        msg = String()
        msg.data = json.dumps({
            'robot': self.namespace,
            'point_id': waypoint.get('point_id'),
        })
        self.alignment_done_pub.publish(msg)

    def pause_after_alignment(self):
        # TODO: 정지 중에도 anomaly, emergency stop, cancel 신호를 감시한다.
        time.sleep(self.pause_after_alignment_sec)

    def prepare_for_gate_check(self, waypoint):
        # TODO: 정렬 실패 시 gate check를 진행할지, mission을 중단할지 정책을 정한다.
        if not self.align_camera_to_gate(waypoint):
            return False
        self.publish_alignment_done(waypoint)
        self.pause_after_alignment()
        return True
