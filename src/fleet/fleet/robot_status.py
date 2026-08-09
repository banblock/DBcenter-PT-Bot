"""로봇 상태 변화 감지 + 퍼블리시 판정 - ROS 의존성 없는 순수 로직 (route_graph.py /
zone_router.py / robot_selector.py와 같은 패턴으로 fleet_node.py에서 분리했다).

설계도의 `로봇 상태 변화 감지? -> 로봇 상태 퍼블리시`를 구현한다. UI팀이
2026-08-06에 스크린샷으로 준 robot_state 15개 상태표(OFFLINE ~ DOCKING)가
공식 어휘인데, 그중 Fleet Node가 지금 실제로 아는 정보(미션 배정 여부,
이상신호 대응 여부, 긴급정지 여부)만으로 판정 가능한 상태는 EMERGENCY_STOP /
IDLE / PATROLLING / DISPATCHING 4개뿐이다.

나머지 11개는 아래처럼 전부 Fleet이 구독하지 않는 입력이 있어야 판정
가능해서 로직을 만들지 않았다 (사용자 확인 후 결정 - "Fleet이 직접 아는
것만" 스코프):
- INSPECTING / REPORTING / RESUMING: 이상신호 대응의 세부 진행 상태.
  "존 뷰포인트 도착", "검증 완료", "중단 지점 도달" 같은 이탈 조건은
  Control Node의 nav 피드백이 있어야 아는데, Fleet은 dispatch(트리거)와
  done(완료) 두 끝점만 보고 그 사이는 못 본다.
- CHARGING / DOCKING / UNDOCKING / MAPPING / ERROR / OFFLINE / ALERTING:
  배터리, 도킹 상태, heartbeat, SLAM 진행 등 Fleet에 아예 들어오는 토픽이
  없다.
- PATROL_PAUSED: 남은 작업 4번(UI 명령 연동)에서 일시정지/재개 명령이
  생기면 그때 판정 로직이 생긴다.

/control/<ns>_State 토픽은 Fleet과 Control Node가 같이 쓴다 - 각자 자기가
실제로 아는 상태만 퍼블리시하는 순차적 소유권 이양 방식(Fleet이
DISPATCHING을 퍼블리시하면, 그 이후 INSPECTING/REPORTING/RESUMING은
Control Node가 이어서 퍼블리시). 그래서 이 파일에 15개 상태 문자열
상수를 전부 정의해뒀다 - Fleet이 판정하지 않는 상태라도, 나중에 Fleet
쪽에서 그 상태를 다뤄야 할 일이 생기면(EMERGENCY_STOP, PATROL_PAUSED 등)
같은 문자열을 그대로 재사용하도록 하기 위함.
"""

# UI팀 robot_state 어휘 전체 (2026-08-06 스크린샷 기준, robot_state.msg
# 상태값과 1:1). Fleet이 실제로 판정하는 건 FLEET_KNOWN_STATES뿐이고
# 나머지는 문자열 상수만 남겨둔 것 - 값 자체를 UI 표기가 아니라 상태값
# 컬럼(OFFLINE, IDLE 등) 그대로 쓴다.
OFFLINE = 'OFFLINE'
MAPPING = 'MAPPING'
IDLE = 'IDLE'
PATROLLING = 'PATROLLING'
PATROL_PAUSED = 'PATROL_PAUSED'
DISPATCHING = 'DISPATCHING'
INSPECTING = 'INSPECTING'
REPORTING = 'REPORTING'
RESUMING = 'RESUMING'
CHARGING = 'CHARGING'
EMERGENCY_STOP = 'EMERGENCY_STOP'
ERROR = 'ERROR'
ALERTING = 'ALERTING'
UNDOCKING = 'UNDOCKING'
DOCKING = 'DOCKING'

# Fleet Node가 지금 실제로 판정하는 상태만 (compute_status()의 우선순위
# 순서와 동일하게 나열 - 긴급정지가 최우선이고, 그다음 이상신호 대응 중이면
# 미션이 있어도 DISPATCHING이 이긴다).
FLEET_KNOWN_STATES = (EMERGENCY_STOP, DISPATCHING, PATROLLING, IDLE)


def compute_status(ns, missions, anomaly_busy, emergency_stopped):
    """로봇 하나의 현재 상태를 판정한다 (Fleet이 아는 4개 상태 한정).

    우선순위: 긴급정지(emergency_stopped) > 이상신호 대응 중(anomaly_busy)
    > 미션 보유(patrolling) > 둘 다 아님(idle). 긴급정지는 다른 어떤
    활동보다도 우선해야 하는 안전 이벤트라 anomaly_busy/missions 상태와
    무관하게 최우선으로 판정한다. 해제는 fleet_node.py의
    _release_all_robots()가 emergency_stopped에서 로봇을 빼는 것으로
    처리하므로, 이 함수 입장에서는 그냥 세트에 없으면 다음 순위(이상신호
    대응/미션 보유/idle)로 자연스럽게 떨어진다 - 해제 후 어느 상태로
    돌아갈지를 여기서 따로 계산하지 않는다.
    """
    if ns in emergency_stopped:
        return EMERGENCY_STOP
    if ns in anomaly_busy:
        return DISPATCHING
    if ns in missions:
        return PATROLLING
    return IDLE


def detect_changes(robots, missions, anomaly_busy, emergency_stopped, previous):
    """전체 로봇에 대해 상태를 새로 계산하고, previous와 달라진 것만
    골라낸다.

    previous는 이전 상태 딕셔너리(fleet_node.py의 self._robot_status를
    그대로 넘겨받아 in-place로 갱신)로, 설계도의 '로봇 상태 변화 감지?'
    분기가 '아니오'인 동안 같은 딕셔너리를 계속 재사용하며 자기 자신으로
    루프백하는 구조와 대응한다 - fleet_node.py가 이 함수를 1Hz 타이머로
    반복 호출해서 폴링 방식으로 변화를 감지한다.

    returns: [(ns, new_status), ...] 변경된 로봇만 (변화 없으면 빈 리스트).
    """
    changes = []
    for ns in robots:
        status = compute_status(ns, missions, anomaly_busy, emergency_stopped)
        if previous.get(ns) != status:
            previous[ns] = status
            changes.append((ns, status))
    return changes
