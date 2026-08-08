"""Mission-level scaffolding for the AMR Control Node."""


class MissionFlowSupport:
    """Hooks for mission validation, route updates, and completion handling."""

    def __init__(self, namespace, navigator):
        self.namespace = namespace
        self.navigator = navigator
        # TODO: 1시간 주기 순찰 정책이 확정되면 parameter로 받을 수 있게 한다.
        self.patrol_interval_sec = 60 * 60

    def validate_mission(self, mission):
        # TODO: mission JSON 형식과 waypoint 필수 필드(x, y, yaw)를 검증한다.
        # TODO: point_id, has_gate 같은 선택 필드의 타입과 허용 범위를 검증한다.
        # TODO: 검증 실패 시 Fleet Node에 mission reject/failure 상태를 보고한다.
        return True

    def request_route_update(self, reason, current_waypoint=None):
        # TODO: 장애물, 충돌 위험, navigation 실패 시 Fleet Node에 경로 갱신을 요청한다.
        # TODO: 현재 waypoint, 실패 원인, 로봇 현재 위치를 함께 전달한다.
        # TODO: 새 waypoint 목록을 받을 때까지 대기하거나 기존 경로 재시도 정책을 적용한다.
        return None

    def handle_mission_complete(self):
        # TODO: Fleet Node에 mission complete 상태를 보고한다.
        # TODO: 다음 동작을 결정한다. 예: 대기, 충전 복귀, 다음 미션 요청.
        self.navigator.info(f'[{self.namespace}] mission complete')

    def choose_post_mission_action(self):
        # TODO: 배터리 상태, 다음 미션 존재 여부, 운영 정책에 따라 후속 동작을 선택한다.
        # TODO: 반환값 예시: "standby", "charge", "request_next_mission".
        return 'standby'

    def get_patrol_interval_sec(self):
        # TODO: ROS parameter 또는 Fleet Node 설정값으로 순찰 주기를 덮어쓸 수 있게 한다.
        return self.patrol_interval_sec

    def wait_until_next_patrol_cycle(self):
        # TODO: 1시간 주기까지 남은 시간을 계산하고 다음 순찰 시작 시점까지 대기한다.
        # TODO: 단순 sleep 대신 ROS timer를 쓸지, Control Node main loop에서 처리할지 결정한다.
        # TODO: 대기 중 anomaly, emergency stop, 수동 mission override를 계속 감시한다.
        return True

    def should_start_next_patrol(self):
        # TODO: 마지막 순찰 완료 시각과 현재 시각을 비교해서 다음 순찰 시작 여부를 판단한다.
        # TODO: 배터리 부족, 충전 중, Fleet hold 상태면 False를 반환한다.
        return False

    def request_next_patrol_mission(self):
        # TODO: Fleet Node에 다음 1시간 주기 순찰 mission을 요청한다.
        # TODO: 기존 mission topic을 재사용할지, 별도 request topic/service를 둘지 결정한다.
        return None

    def mark_patrol_cycle_started(self):
        # TODO: 순찰 cycle 시작 시각과 cycle id를 기록한다.
        # TODO: Fleet Node/UI에 patrol cycle started 상태를 보고한다.
        return None

    def mark_patrol_cycle_finished(self):
        # TODO: 순찰 cycle 완료 시각, 성공/실패 여부, 다음 예정 시각을 기록한다.
        # TODO: Fleet Node/UI에 다음 순찰 예정 정보를 보고한다.
        return None
