"""fleet/robot_status.py(로봇 상태 변화 감지) 순수 로직 검증. rclpy를
import하지 않으므로 ROS 2 환경 없이 그냥 pytest로 돈다 (test_robot_selector.py
와 동일한 패턴).

이 파일 상단의 SCENARIOS는 pytest 전용이 아니라 build_status_report.py가
그대로 import해서 상태 전이 타임라인으로 시각화하는 공유 데이터다 - 리포트에
나오는 상태값이 실제 이 테스트가 검증한 값과 항상 일치하도록 강제하기
위함(리포트 따로, 테스트 따로 두면 둘이 어긋나도 아무도 못 알아챈다)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet import robot_status


# 로봇 한 대가 시간 순서대로 거치는 상태 전이 시나리오. build_status_report.py
# 가 이 목록을 그대로 불러다 타임라인으로 그린다 - detect_changes()는
# previous 딕셔너리를 in-place로 갱신하는 상태 유지형 함수라, 각 step을
# 정의된 순서 그대로 재생해야 실제 fleet_node.py의 1Hz 폴링 호출과 같은
# 결과가 나온다.
SCENARIOS = [
    {
        'name': 'idle_to_patrol_to_dispatch_to_patrol',
        'label': '신규 로봇 등장 → 순찰 시작 → 이상신호 대응 → 순찰 복귀',
        'why': ('robot3가 아직 구역을 못 받았을 때(IDLE)부터 시작해서, Backend가 '
                '구역을 배정하면 PATROLLING으로, 이상신호 트리거로 dispatch되면 '
                'DISPATCHING으로, _on_anomaly_done을 받으면 다시 PATROLLING으로 '
                '바뀐다. 같은 상태가 유지되는 폴링 tick(3번째 step)에서는 '
                '퍼블리시가 일어나지 않아야 한다 - 1Hz 타이머가 매초 무조건 '
                '쏘면 안 되고 변화가 있을 때만 쏴야 하기 때문.'),
        'robot': 'robot3',
        'steps': [
            {'when': '초기 상태 (아직 구역 배정 전)',
             'missions': set(), 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.IDLE, 'expected_change': True},
            {'when': 'Backend가 구역 배정 -> 미션 생김',
             'missions': {'robot3'}, 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.PATROLLING, 'expected_change': True},
            {'when': '다음 폴링 tick, 아무것도 안 바뀜',
             'missions': {'robot3'}, 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.PATROLLING, 'expected_change': False},
            {'when': '이상신호 트리거로 dispatch',
             'missions': {'robot3'}, 'anomaly_busy': {'robot3'}, 'emergency_stopped': set(),
             'expected_status': robot_status.DISPATCHING, 'expected_change': True},
            {'when': '_on_anomaly_done 수신 -> 순찰 복귀',
             'missions': {'robot3'}, 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.PATROLLING, 'expected_change': True},
        ],
    },
    {
        'name': 'mission_removed_falls_back_to_idle',
        'label': '구역 재배정으로 미션이 사라지면 IDLE로 돌아감',
        'why': ('robot8이 순찰 중(PATROLLING)이던 도중 Backend가 구역 구성을 '
                '바꿔서 robot8에게 배정된 구역이 없어지면(예: 2대 구성에서 1대로 '
                '축소), 다음 폴링 tick에 missions에서 robot8이 빠지고 IDLE로 '
                '떨어져야 한다.'),
        'robot': 'robot8',
        'steps': [
            {'when': '초기 순찰 중',
             'missions': {'robot8'}, 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.PATROLLING, 'expected_change': True},
            {'when': 'Backend 구역 재배정 -> robot8 미션 사라짐',
             'missions': set(), 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.IDLE, 'expected_change': True},
        ],
    },
    {
        'name': 'emergency_stop_overrides_and_persists',
        'label': '순찰 중 긴급정지 → 이후 이상신호가 겹쳐도 EMERGENCY_STOP 유지',
        'why': ('robot8이 순찰 중에 /backend/emergency_stop_all이 오면 즉시 '
                'EMERGENCY_STOP으로 바뀐다. 해제(재개) 로직이 아직 없어서 그 '
                '뒤로는 계속 EMERGENCY_STOP이어야 하는데, fleet_node.py가 긴급정지 '
                '중인 로봇을 anomaly 후보에서 걸러내지 않는 극단 상황(다른 트리거가 '
                '겹쳐 들어와 anomaly_busy에도 잡힌 경우)까지 가정해도 우선순위상 '
                'EMERGENCY_STOP이 이겨야 한다는 계약을 고정.'),
        'robot': 'robot8',
        'steps': [
            {'when': '순찰 중',
             'missions': {'robot8'}, 'anomaly_busy': set(), 'emergency_stopped': set(),
             'expected_status': robot_status.PATROLLING, 'expected_change': True},
            {'when': '긴급정지 트리거 수신',
             'missions': {'robot8'}, 'anomaly_busy': set(), 'emergency_stopped': {'robot8'},
             'expected_status': robot_status.EMERGENCY_STOP, 'expected_change': True},
            {'when': '다음 폴링 tick, 아무것도 안 바뀜',
             'missions': {'robot8'}, 'anomaly_busy': set(), 'emergency_stopped': {'robot8'},
             'expected_status': robot_status.EMERGENCY_STOP, 'expected_change': False},
            {'when': '이상신호 트리거가 겹쳐 들어옴 (극단 케이스)',
             'missions': {'robot8'}, 'anomaly_busy': {'robot8'}, 'emergency_stopped': {'robot8'},
             'expected_status': robot_status.EMERGENCY_STOP, 'expected_change': False},
        ],
    },
]


@pytest.mark.parametrize('case', SCENARIOS, ids=[c['name'] for c in SCENARIOS])
def test_scenarios_follow_expected_transitions(case):
    ns = case['robot']
    previous = {}
    for step in case['steps']:
        changes = robot_status.detect_changes(
            [ns], step['missions'], step['anomaly_busy'], step['emergency_stopped'], previous)
        assert previous[ns] == step['expected_status']
        changed = any(c_ns == ns for c_ns, _ in changes)
        assert changed == step['expected_change']


# 아래는 시간 순서 전이가 아니라, compute_status()의 우선순위 규칙과
# detect_changes()가 여러 로봇을 동시에 다룰 때의 계약을 고정해두는
# 케이스. SCENARIOS와 마찬가지로 build_status_report.py가 표로 보여준다.
EDGE_CASES = [
    {
        'name': 'anomaly_busy_wins_over_mission',
        'title': '이상신호 대응이 미션 보유보다 우선',
        # 이상신호 대응 중에도 재발행 타이머 때문에 missions에는 여전히
        # 그 로봇의 순찰 웨이포인트가 남아있다(fleet_node.py는 미션을
        # 지우지 않고 그대로 재발행함) - 그래도 DISPATCHING이 나와야
        # 한다는 우선순위 규칙을 명시적으로 고정.
        'detail': 'robot3가 missions에도 있고 anomaly_busy에도 있을 때 -> DISPATCHING.',
        'missions': {'robot3'}, 'anomaly_busy': {'robot3'}, 'emergency_stopped': set(),
        'ns': 'robot3', 'expected': robot_status.DISPATCHING,
    },
    {
        'name': 'anomaly_busy_without_mission',
        'title': '미션 없이 anomaly_busy만 있어도 DISPATCHING',
        # 정상 흐름에서는 잘 안 생기지만(보통 미션 있는 로봇만 대응
        # 후보가 됨), 방어적으로 우선순위 규칙이 missions 존재 여부와
        # 무관하다는 걸 확인.
        'detail': 'robot9가 missions에는 없고 anomaly_busy에만 있을 때 -> DISPATCHING.',
        'missions': set(), 'anomaly_busy': {'robot9'}, 'emergency_stopped': set(),
        'ns': 'robot9', 'expected': robot_status.DISPATCHING,
    },
    {
        'name': 'unknown_robot_is_idle',
        'title': '미션도 이상신호 대응도 없으면 IDLE',
        'detail': 'robot8이 missions에도 anomaly_busy에도 없을 때 -> IDLE.',
        'missions': set(), 'anomaly_busy': set(), 'emergency_stopped': set(),
        'ns': 'robot8', 'expected': robot_status.IDLE,
    },
    {
        'name': 'emergency_stop_wins_over_anomaly_and_mission',
        'title': '긴급정지가 이상신호 대응·미션 보유보다 최우선',
        # 긴급정지는 다른 어떤 활동 중이었든 무조건 이겨야 하는 안전
        # 이벤트라는 걸 세 조건이 모두 겹친 최악의 경우로 고정.
        'detail': 'robot3가 missions/anomaly_busy/emergency_stopped 세 곳 모두에 있을 때 -> EMERGENCY_STOP.',
        'missions': {'robot3'}, 'anomaly_busy': {'robot3'}, 'emergency_stopped': {'robot3'},
        'ns': 'robot3', 'expected': robot_status.EMERGENCY_STOP,
    },
    {
        'name': 'emergency_stop_without_mission_or_anomaly',
        'title': 'idle 로봇도 긴급정지되면 EMERGENCY_STOP',
        'detail': 'robot9가 missions/anomaly_busy 어디에도 없고 emergency_stopped에만 있을 때 -> EMERGENCY_STOP.',
        'missions': set(), 'anomaly_busy': set(), 'emergency_stopped': {'robot9'},
        'ns': 'robot9', 'expected': robot_status.EMERGENCY_STOP,
    },
]


@pytest.mark.parametrize('case', EDGE_CASES, ids=[c['name'] for c in EDGE_CASES])
def test_edge_cases(case):
    status = robot_status.compute_status(
        case['ns'], case['missions'], case['anomaly_busy'], case['emergency_stopped'])
    assert status == case['expected']


def test_detect_changes_only_reports_changed_robots():
    # 여러 로봇을 한 번에 폴링할 때, 바뀐 로봇만 changes에 담기고 안
    # 바뀐 로봇은 조용히 넘어가야 한다 - fleet_node.py가 이 리스트
    # 길이만큼 퍼블리시를 하므로, 여기서 새는 로봇이 있으면 불필요한
    # 퍼블리시가 매초 쌓인다.
    previous = {}
    first = robot_status.detect_changes(
        ['robot3', 'robot8'], {'robot3', 'robot8'}, set(), set(), previous)
    assert {ns for ns, _ in first} == {'robot3', 'robot8'}

    second = robot_status.detect_changes(
        ['robot3', 'robot8'], {'robot3', 'robot8'}, {'robot3'}, set(), previous)
    assert second == [('robot3', robot_status.DISPATCHING)]
    assert previous == {'robot3': robot_status.DISPATCHING, 'robot8': robot_status.PATROLLING}
