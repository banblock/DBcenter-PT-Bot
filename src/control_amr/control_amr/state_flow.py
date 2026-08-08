"""Robot state, battery, and reporting scaffolding for the AMR Control Node."""


class StateFlowSupport:
    """Hooks for robot state publishing and battery/charging behavior."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator

    def publish_state(self, state, detail=None):
        # TODO: Fleet Node/UI가 볼 수 있도록 로봇 상태 topic을 publish한다.
        # TODO: state 예시: waiting, moving, crossing_wait, inspecting, charging, error.
        # TODO: detail에 waypoint index, point_id, battery, error reason 등을 포함한다.
        return {'robot': self.namespace, 'state': state, 'detail': detail}

    def report_waypoint_reached(self, waypoint_index, waypoint):
        # TODO: waypoint 도착 결과를 Fleet Node에 보고한다.
        # TODO: 도착 시간, pose 오차, waypoint metadata를 함께 보낸다.
        return {'index': waypoint_index, 'waypoint': waypoint}

    def report_failure(self, reason, detail=None):
        # TODO: mission/navigation/inspection 실패를 Fleet Node에 보고한다.
        # TODO: 실패 후 대기, 재시도, 복귀 중 어떤 상태인지 명확히 알린다.
        self.navigator.info(f'[{self.namespace}] failure: {reason}')
        return {'reason': reason, 'detail': detail}

    def check_battery(self):
        # TODO: battery_state topic을 구독하거나 service로 현재 배터리 상태를 확인한다.
        # TODO: low/critical/charging/normal 상태를 구분한다.
        return 'unknown'

    def handle_low_battery(self):
        # TODO: 배터리 부족 시 Fleet Node에 보고하고 현재 mission 중단/보류 여부를 결정한다.
        # TODO: 충전 위치로 복귀하거나 dock action을 실행한다.
        return False

    def return_to_charger(self):
        # TODO: 충전 스테이션 pose로 이동하고 docking action을 호출한다.
        # TODO: docking 성공/실패/timeout을 구분해서 보고한다.
        return False
