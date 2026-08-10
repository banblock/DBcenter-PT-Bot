"""이상신호 급파 대상 판정 - ROS 의존성 없는 순수 로직 (dock_control.py /
robot_status.py와 같은 패턴으로 fleet_node.py에서 분리했다).

설계도의 이상신호는 두 개의 서로 다른 경로로 들어온다 (HMI가 보내주는
페이로드 모양으로 구분):
- AMR 자체 감지: HMI가 감지된 로봇 id만 알려준다({"robot": ns}, 좌표
  없음) - 그 로봇의 최근 위치(amcl_pose)를 그대로 이상 위치로 써서 그
  자리에 세운다(추가 이동 없음. fleet_node.py가 이 위치를
  /fleet/<ns>/anomaly로 그대로 내려보내면, Control의 startToPose()가
  사실상 제자리 정지로 동작한다).
- CCTV 감지: HMI가 이상 발생 좌표만 알려준다({"x","y"}, robot 없음) -
  순찰 중이고, 이미 다른 이상신호 대응 중이 아니며, 긴급정지 중이 아닌
  로봇 중 robot_selector.select_nearest_robot()로 가장 가까운 로봇을
  골라 급파한다.

두 경로 모두 긴급정지 중인 로봇은 대상에서 제외한다 - 이미 멈춰서 대기
중인 로봇에 새로 이동 명령을 얹으면 안 되기 때문이다. 이건 도킹
복귀(dock_control.py)와 정반대 정책인데, 도킹 복귀는 운영자가 명시적으로
누른 override 버튼이라 긴급정지를 풀고 보내는 게 맞지만, 이상신호
급파는 운영자 개입 없는 자동 판단이라 안전하게 제외하는 쪽을 기본값으로
한다."""

import math


def eligible_candidates(missions, anomaly_busy, emergency_stopped):
    """CCTV 감지 급파 후보 목록 - 순찰 중(missions에 있음) + 이상신호
    대응 중이 아님(anomaly_busy 아님) + 긴급정지 중이 아님
    (emergency_stopped 아님)인 로봇만 남긴다."""
    return [
        ns for ns in missions
        if ns not in anomaly_busy and ns not in emergency_stopped
    ]


def resolve_self_location(ns, robot_pose, default_location):
    """AMR 자체 감지 위치 판정 - 그 로봇의 최근 amcl_pose(x, y)를 그대로
    이상 위치로 쓴다(yaw는 amcl_pose를 안 쓰므로 default_location의
    yaw를 그대로 따른다). pose를 아직 한 번도 못 받았으면(AMCL 초기화
    전 등) default_location 전체로 대체한다."""
    if ns in robot_pose:
        x, y = robot_pose[ns]
        return {'x': x, 'y': y, 'yaw': default_location['yaw']}
    return dict(default_location)


def snap_cctv_location(graph, loc):
    """CCTV가 알려주는 이상 좌표는 카메라가 찍은 실제 위치라, 랙 안쪽
    같은 로봇이 물리적으로 갈 수 없는 지점일 수 있다(통로 그래프
    밖). 그 좌표로 로봇을 직행시키는 대신, 통로 그래프 위에서 가장
    가까운 지점(`RouteGraph.nearest_point()`)까지만 이동시키고, 카메라
    (로봇 정면)가 원래 이상 좌표 쪽을 보도록 yaw를 그 방향으로 계산해서
    돌려준다.

    AMR 자체 감지 위치(resolve_self_location())는 이미 로봇이 서 있는
    자리 그대로라 항상 그래프 근방이므로 이 함수를 거치지 않는다 - CCTV
    경로에서만 쓴다."""
    snapped = graph.nearest_point((loc['x'], loc['y']))
    yaw = math.degrees(math.atan2(loc['y'] - snapped[1], loc['x'] - snapped[0]))
    return {'x': snapped[0], 'y': snapped[1], 'yaw': yaw}
