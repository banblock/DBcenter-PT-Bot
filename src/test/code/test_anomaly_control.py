"""fleet/anomaly_control.py(이상신호 급파 대상 판정) 순수 로직 검증. rclpy를
import하지 않으므로 ROS 2 환경 없이 그냥 pytest로 돈다 (test_dock_control.py
와 동일한 패턴).

이 파일 상단의 CANDIDATE_CASES / SELF_LOCATION_CASES는 pytest 전용이
아니라 build_anomaly_report.py가 그대로 import해서 표로 시각화하는 공유
데이터다 - 리포트에 나오는 결과가 실제 이 테스트가 검증한 값과 항상
일치하도록 강제하기 위함(리포트 따로, 테스트 따로 두면 둘이 어긋나도
아무도 못 알아챈다)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet import anomaly_control


# eligible_candidates() - CCTV 감지 급파 후보를 순찰 중(missions) + 이상신호
# 대응 중 아님(anomaly_busy) + 긴급정지 중 아님(emergency_stopped)으로
# 걸러내는 케이스들. build_anomaly_report.py가 이 목록을 그대로 불러다
# 표로 그린다.
CANDIDATE_CASES = [
    {
        'name': 'all_eligible',
        'title': '아무도 제외되지 않으면 순찰 중인 로봇 전체가 후보',
        'why': '가장 단순한 경우 - 둘 다 순찰 중이고, 이상신호 대응도 긴급정지도 아님.',
        'missions': ['robot3', 'robot8'],
        'anomaly_busy': set(),
        'emergency_stopped': set(),
        'expected': ['robot3', 'robot8'],
    },
    {
        'name': 'busy_excluded',
        'title': '이미 다른 이상신호 대응 중인 로봇은 후보에서 제외',
        'why': 'robot3가 이미 다른 이상신호를 처리 중이면 중복 급파하면 안 된다.',
        'missions': ['robot3', 'robot8'],
        'anomaly_busy': {'robot3'},
        'emergency_stopped': set(),
        'expected': ['robot8'],
    },
    {
        'name': 'emergency_stopped_excluded',
        'title': '긴급정지 중인 로봇은 후보에서 제외',
        'why': ('robot8이 긴급정지 중이면, 도킹 복귀(운영자의 명시적 override)와 '
                '달리 이상신호 급파는 자동 판단이라 안전하게 후보에서 뺀다.'),
        'missions': ['robot3', 'robot8'],
        'anomaly_busy': set(),
        'emergency_stopped': {'robot8'},
        'expected': ['robot3'],
    },
    {
        'name': 'both_excluded_combined',
        'title': 'busy와 emergency_stopped가 각각 다른 로봇을 제외해도 나머지는 후보',
        'why': 'robot3는 대응 중이라, robot8은 긴급정지라 각각 빠지고 robot9만 남는다.',
        'missions': ['robot3', 'robot8', 'robot9'],
        'anomaly_busy': {'robot3'},
        'emergency_stopped': {'robot8'},
        'expected': ['robot9'],
    },
    {
        'name': 'no_missions_gives_empty',
        'title': '순찰 중인 로봇이 하나도 없으면 후보도 없음',
        'why': '아직 구역 배정을 못 받아 missions가 비어 있는 초기 상태.',
        'missions': [],
        'anomaly_busy': set(),
        'emergency_stopped': set(),
        'expected': [],
    },
    {
        'name': 'everyone_excluded',
        'title': '전원이 대응 중이거나 긴급정지 중이면 후보가 빈 리스트',
        'why': ('fleet_node.py는 candidates가 비면 "no eligible robot" 경고만 찍고 '
                '급파를 포기한다.'),
        'missions': ['robot3', 'robot8'],
        'anomaly_busy': {'robot3'},
        'emergency_stopped': {'robot8'},
        'expected': [],
    },
]


@pytest.mark.parametrize('case', CANDIDATE_CASES, ids=[c['name'] for c in CANDIDATE_CASES])
def test_eligible_candidates(case):
    result = anomaly_control.eligible_candidates(
        case['missions'], case['anomaly_busy'], case['emergency_stopped'])
    assert result == case['expected']


# resolve_self_location() - AMR 자체 감지 위치 판정 케이스들.
DEFAULT_LOCATION = {'x': -2.33, 'y': 0.0313, 'yaw': 0.0}

SELF_LOCATION_CASES = [
    {
        'name': 'known_pose_used',
        'title': '최근 amcl_pose를 알고 있으면 그 좌표를 그대로 씀',
        'why': ('robot3가 amcl_pose를 받은 적 있으면(자체 감지 시점의 위치), 그 '
                'x/y를 그대로 이상 위치로 쓴다 - yaw는 amcl_pose에서 안 뽑으므로 '
                'default_location의 yaw를 그대로 따른다.'),
        'ns': 'robot3',
        'robot_pose': {'robot3': (-1.2, 0.5), 'robot8': (2.0, 2.0)},
        'expected': {'x': -1.2, 'y': 0.5, 'yaw': 0.0},
    },
    {
        'name': 'unknown_pose_falls_back_to_default',
        'title': 'amcl_pose를 아직 한 번도 못 받았으면 default_location 전체로 대체',
        'why': 'AMCL이 아직 초기화 전이라 위치를 모르는 극단 상황에 대한 안전망.',
        'ns': 'robot9',
        'robot_pose': {'robot3': (-1.2, 0.5)},
        'expected': dict(DEFAULT_LOCATION),
    },
    {
        'name': 'only_queries_requested_robot',
        'title': '요청된 로봇의 위치만 보고, 다른 로봇 위치는 무시',
        'why': 'robot_pose에 여러 로봇이 있어도 ns로 지정된 robot8 것만 써야 한다.',
        'ns': 'robot8',
        'robot_pose': {'robot3': (-1.2, 0.5), 'robot8': (3.3, 4.4)},
        'expected': {'x': 3.3, 'y': 4.4, 'yaw': 0.0},
    },
]


@pytest.mark.parametrize(
    'case', SELF_LOCATION_CASES, ids=[c['name'] for c in SELF_LOCATION_CASES])
def test_resolve_self_location(case):
    result = anomaly_control.resolve_self_location(
        case['ns'], case['robot_pose'], DEFAULT_LOCATION)
    assert result == case['expected']
