"""fleet/dock_control.py(도킹 복귀 대상 로봇 판정) 순수 로직 검증. rclpy를
import하지 않으므로 ROS 2 환경 없이 그냥 pytest로 돈다 (test_robot_status.py
와 동일한 패턴).

이 파일 상단의 CASES는 pytest 전용이 아니라 build_dock_report.py가
그대로 import해서 표로 시각화하는 공유 데이터다 - 리포트에 나오는
결과가 실제 이 테스트가 검증한 값과 항상 일치하도록 강제하기 위함
(리포트 따로, 테스트 따로 두면 둘이 어긋나도 아무도 못 알아챈다)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet import dock_control


# resolve_dock_targets()가 /backend/dock의 robots 목록 + 등록된 로봇 +
# 긴급정지 집합을 보고 실제 도킹 명령을 내릴 로봇을 걸러내는 케이스들.
#
# 설계 결정(HMI 팀 논의 결과): 도킹 복귀는 운영자가 명시적으로 내리는
# 복귀 명령이라, 긴급정지 중인 로봇도 대상에서 빼지 않는다. 대신 그런
# 로봇은 도킹 복귀와 함께 긴급정지도 암묵적으로 해제된다
# (released_from_emergency) - 그렇지 않으면 Control이 "정지하라"는
# 신호만 받은 채로 dock 명령을 받는 모순이 생기기 때문이다.
#
# build_dock_report.py가 이 목록을 그대로 불러다 표로 그린다.
CASES = [
    {
        'name': 'all_robots_targeted_by_default',
        'title': 'robots 필드가 없으면 등록된 로봇 전체가 대상',
        'why': ('긴급정지-전체(/backend/emergency_stop_all)와 동일한 패턴 - UI가 '
                '"전체 도킹 복귀" 버튼을 누른 경우를 가정한다. requested가 '
                'None이면 등록된 로봇을 전부 대상으로 삼아야 한다.'),
        'requested': None,
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': set(),
        'expected': {
            'dispatched': ['robot3', 'robot8'],
            'unknown': [],
            'released_from_emergency': [],
            'skipped_no_station': [],
        },
    },
    {
        'name': 'specific_robot_only',
        'title': 'robots에 특정 로봇만 지정하면 그 로봇만 대상',
        'why': 'UI에서 robot3 한 대만 골라 도킹 복귀시키는 시나리오.',
        'requested': ['robot3'],
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': set(),
        'expected': {
            'dispatched': ['robot3'],
            'unknown': [],
            'released_from_emergency': [],
            'skipped_no_station': [],
        },
    },
    {
        'name': 'unregistered_robot_filtered_out',
        'title': '등록되지 않은 로봇은 unknown으로 분리되고 대상에서 빠짐',
        'why': ('robot9는 아직 구역 배정을 못 받아 Fleet에 등록된 적이 없는 '
                '로봇이다(_status_pubs에 없음) - 오타나 존재하지 않는 로봇 '
                'id가 와도 조용히 무시되지 않고 unknown에 남아야 나중에 '
                'fleet_node.py가 경고 로그를 찍을 수 있다.'),
        'requested': ['robot9'],
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': set(),
        'expected': {
            'dispatched': [],
            'unknown': ['robot9'],
            'released_from_emergency': [],
            'skipped_no_station': [],
        },
    },
    {
        'name': 'emergency_stopped_robot_still_dispatched_and_released',
        'title': '긴급정지 중인 로봇도 도킹 대상에 포함되고 긴급정지가 함께 해제됨',
        'why': ('robot8이 긴급정지 중이어도 도킹 복귀는 운영자의 명시적 override라 '
                '대상에서 빼지 않는다. 대신 robot8은 released_from_emergency로 '
                '표시되어, fleet_node.py가 _emergency_stopped에서 빼고 '
                '/fleet/robot8/emergency_stop에 해제 신호도 같이 내려보낸다 - '
                '그래야 Control이 dock 명령을 받았을 때 "정지 상태인데 왜 '
                '움직이라는거지"라는 모순 없이 움직일 수 있다.'),
        'requested': None,
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': {'robot8'},
        'expected': {
            'dispatched': ['robot3', 'robot8'],
            'unknown': [],
            'released_from_emergency': ['robot8'],
            'skipped_no_station': [],
        },
    },
    {
        'name': 'no_dock_station_configured_is_skipped',
        'title': 'DOCK_STATIONS에 좌표가 없는 로봇은 skipped_no_station으로 제외',
        'why': ('robot9가 구역을 배정받아 등록은 됐지만(_status_pubs에 있음) '
                'DOCK_STATIONS에는 아직 도킹 스테이션 좌표가 없는 가상의 3번째 '
                '로봇 상황을 가정한다 - 좌표가 없는 로봇에게 명령을 못 내리는 게 '
                '당연하지만, 조용히 무시하지 않고 skipped_no_station으로 남겨 '
                '설정 누락을 알아챌 수 있게 한다.'),
        'requested': None,
        'registered_robots': {'robot3', 'robot8', 'robot9'},
        'emergency_stopped': set(),
        'expected': {
            'dispatched': ['robot3', 'robot8'],
            'unknown': [],
            'released_from_emergency': [],
            'skipped_no_station': ['robot9'],
        },
    },
    {
        'name': 'mixed_requested_list',
        'title': '요청 목록에 등록/미등록/긴급정지가 섞여도 각자 맞는 분류로 나뉨',
        'why': ('실제 UI 요청은 이 사유들이 동시에 섞여 들어올 수 있다 - 미등록 '
                '로봇(robot9)은 unknown으로 빠지고, 긴급정지 중인 robot3는 '
                'dispatched에 남으면서 released_from_emergency로도 표시되고, '
                '정상인 robot8은 그냥 dispatched된다.'),
        'requested': ['robot3', 'robot9', 'robot8'],
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': {'robot3'},
        'expected': {
            'dispatched': ['robot3', 'robot8'],
            'unknown': ['robot9'],
            'released_from_emergency': ['robot3'],
            'skipped_no_station': [],
        },
    },
    {
        'name': 'all_emergency_stopped_robots_are_dispatched_and_released',
        'title': '전체가 긴급정지 중이어도 전체가 도킹 대상 + 전체 해제',
        'why': ('두 로봇 모두 긴급정지 중일 때 "전체 도킹 복귀" 명령이 오면 둘 다 '
                '도킹 스테이션으로 보내고, 둘 다 긴급정지가 해제된다 - 긴급정지가 '
                '도킹 복귀를 막는 조건이 아니라는 걸 극단적인 케이스로도 고정.'),
        'requested': None,
        'registered_robots': {'robot3', 'robot8'},
        'emergency_stopped': {'robot3', 'robot8'},
        'expected': {
            'dispatched': ['robot3', 'robot8'],
            'unknown': [],
            'released_from_emergency': ['robot3', 'robot8'],
            'skipped_no_station': [],
        },
    },
]


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_resolve_dock_targets(case):
    dispatched, unknown, released_from_emergency, skipped_no_station = (
        dock_control.resolve_dock_targets(
            case['requested'], case['registered_robots'], case['emergency_stopped']))
    # dispatched/unknown/released_from_emergency/skipped_no_station은
    # 순서가 requested 또는 registered_robots 순회 순서를 따르므로 원래
    # list로 비교하는 게 정확하지만, registered_robots 자체가 set이라
    # requested가 None인 케이스(등록된 전체 대상)는 순회 순서가 흔들릴
    # 수 있어 그 경우만 set으로 비교한다.
    if case['requested'] is None:
        assert set(dispatched) == set(case['expected']['dispatched'])
        assert set(skipped_no_station) == set(case['expected']['skipped_no_station'])
        assert set(released_from_emergency) == set(case['expected']['released_from_emergency'])
    else:
        assert dispatched == case['expected']['dispatched']
        assert unknown == case['expected']['unknown']
        assert released_from_emergency == case['expected']['released_from_emergency']
        assert skipped_no_station == case['expected']['skipped_no_station']


def test_dock_stations_cover_known_robots():
    # robot3/robot8은 이 프로젝트가 실제로 쓰는 두 대의 TurtleBot4다 -
    # DOCK_STATIONS에서 둘 중 하나라도 빠지면 도킹 복귀 자체가 조용히
    # 안 먹히므로(skipped_no_station), 최소한 이 둘은 항상 있어야 한다.
    assert {'robot3', 'robot8'} <= set(dock_control.DOCK_STATIONS)
    for ns, station in dock_control.DOCK_STATIONS.items():
        assert set(station) == {'x', 'y', 'yaw'}
        assert all(isinstance(station[k], (int, float)) for k in ('x', 'y', 'yaw'))
