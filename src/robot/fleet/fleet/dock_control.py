"""도킹 복귀 대상 로봇 판정 - ROS 의존성 없는 순수 로직 (robot_status.py /
robot_selector.py와 같은 패턴으로 fleet_node.py에서 분리했다).

설계도의 '명령 분기 -> 도킹 복귀' 처리: /backend/dock 요청에서 실제로
도킹 명령을 내려도 되는 로봇 목록을 걸러낸다. Control Node가 그 지점까지
goToPose로 이동한 뒤 실제 irobot_create_msgs/action/Dock을 호출하는 건
Control 몫이라, Fleet은 "어느 로봇에게 어느 좌표를 보낼지"만 판정하고
로봇 현재 위치는 따로 계산하지 않는다 (goToPose 자체가 amcl_pose 기반으로
현재 위치에서부터 경로를 잡으므로 - 목표 지점은 로봇 현재 위치와 무관한
고정값이다)."""

# 로봇별 도킹 스테이션 앞 대기 지점. 정밀한 도킹 자세가 아니라 "Dock
# 액션(도킹 스테이션 IR 비콘 기반 자동 접근)이 알아서 붙을 수 있는
# 근방"이면 된다. x/y는 실측값, yaw는 아직 실측 전이라 0.0으로 임시로
# 둔다 - 실측되는 대로 교체할 것.
# TODO(사용자 실측 예정): yaw를 실제 도킹 스테이션을 향하는 각도로 교체.
DOCK_STATIONS = {
    'robot3': {'x': -4.3, 'y': 1.8, 'yaw': 0.0},
    'robot8': {'x': -0.5, 'y': 0.0818, 'yaw': 0.0},
}


def resolve_dock_targets(requested, registered_robots, emergency_stopped):
    """/backend/dock payload의 robots 목록을 실제로 도킹 명령을 내릴 로봇만
    걸러낸다.

    requested: payload.get('robots') 그대로 - None/빈 리스트면 등록된 로봇
        전체가 대상이다 (긴급정지-전체와 동일 패턴).
    registered_robots: 현재 Fleet에 등록된 로봇 네임스페이스 목록
        (fleet_node.py의 self._status_pubs.keys()).
    emergency_stopped: 현재 긴급정지 중인 로봇 집합 - 도킹 복귀는 운영자가
        명시적으로 내리는 복귀 명령이라 긴급정지 중이어도 대상에서 빼지
        않는다. 대신 긴급정지 상태였던 로봇은 released_from_emergency로
        따로 표시해서, 호출부(fleet_node.py)가 _emergency_stopped에서
        제거하고 해제 신호도 같이 내려보내게 한다 (그렇지 않으면 Control
        입장에서 "정지하라"는 신호를 받은 적만 있고 "움직여도 된다"는
        신호는 못 받은 채로 dock 명령만 오는 모순이 생긴다).

    반환: (dispatched, unknown, released_from_emergency, skipped_no_station)
    - dispatched: 실제로 도킹 명령을 내릴 로봇 리스트.
    - unknown: requested에는 있지만 등록되지 않은 로봇.
    - released_from_emergency: dispatched 중 긴급정지 상태였던 로봇 -
      도킹 복귀와 함께 긴급정지도 암묵적으로 해제된다.
    - skipped_no_station: 등록은 됐지만 DOCK_STATIONS에 좌표가 없는 로봇.
    """
    if requested:
        candidates = [ns for ns in requested if ns in registered_robots]
        unknown = [ns for ns in requested if ns not in registered_robots]
    else:
        candidates = list(registered_robots)
        unknown = []

    dispatched = []
    released_from_emergency = []
    skipped_no_station = []
    for ns in candidates:
        if ns not in DOCK_STATIONS:
            skipped_no_station.append(ns)
            continue
        if ns in emergency_stopped:
            released_from_emergency.append(ns)
        dispatched.append(ns)

    return dispatched, unknown, released_from_emergency, skipped_no_station
