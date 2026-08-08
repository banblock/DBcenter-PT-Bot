"""fleet/robot_selector.py(이상신호 대응 로봇 선정) 순수 로직 검증.
route_graph.py를 쓰긴 하지만 rclpy는 import하지 않으므로 ROS 2 환경
없이 그냥 pytest로 돈다 (test_route_graph.py / test_zone_router.py와
동일한 패턴).

이 파일 상단의 SCENARIOS는 pytest 전용이 아니라
build_selector_report.py가 그대로 import해서 지도 위에 시각화하는
공유 데이터다 - 리포트에 나오는 숫자가 실제 이 테스트가 검증한 값과
항상 일치하도록 강제하기 위함(리포트 따로, 테스트 따로 두면 둘이
어긋나도 아무도 못 알아챈다)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))

from fleet.robot_selector import select_nearest_robot
from fleet.route_graph import RouteGraph

GRAPH_YAML = os.path.join(
    os.path.dirname(__file__), '..', '..', 'fleet', 'config', 'route_graph.yaml')


@pytest.fixture
def graph():
    # 테스트마다 새로 로드한다 - select_nearest_robot()이 내부적으로
    # insert_point()를 호출해서 넘겨받은 그래프 객체를 변형(노드/엣지
    # 추가)하기 때문에, 테스트 간에 그래프 인스턴스를 공유하면 먼저
    # 실행된 테스트가 끼워 넣은 임시 노드가 다음 테스트에 그대로
    # 남아있게 된다.
    return RouteGraph.from_yaml(GRAPH_YAML)


# 그래프 경로 거리 비교가 실제로 의미 있는 시나리오들. build_selector_report.py
# 가 이 목록을 그대로 불러다 지도 위에 그려서 시각적으로도 검증한다 -
# 숫자를 리포트에 손으로 옮겨적지 않기 위해 pytest와 리포트가 같은
# SCENARIOS를 공유한다.
SCENARIOS = [
    {
        'name': 'similar_straight_line_diverges_on_graph',
        'label': '직선 거리는 비슷하지만 그래프 경로에서 갈리는 경우',
        'why': ('robot3(뒷쪽 통로 R_AB)와 robot8(앞쪽 통로 F_BC)는 이상신호(F_left)'
                '까지 직선 거리가 비슷하다(2.29m vs 2.22m). 하지만 robot3는 세로 '
                '통로를 하나 더 거쳐야 해서 그래프 경로로는 3.12m로 벌어지고, '
                'robot8은 2.22m로 직선 거리와 거의 같다.'),
        'anomaly': {'x': -4.60, 'y': -0.10},  # F_left
        'poses': {'robot3': (-3.48, 1.90), 'robot8': (-2.38, -0.10)},  # R_AB, F_BC
        'candidates': ['robot3', 'robot8'],
        'expected': 'robot8',
    },
    {
        'name': 'direct_aisle_beats_detour',
        'label': '직결 통로 대 우회 통로',
        'why': ('robot3는 이상신호(R_CD)와 같은 세로 통로(V_CD) 위라 직선 거리와 '
                '그래프 경로가 둘 다 2.00m로 같다(우회 없음). robot8은 옆 통로라 '
                '직선 거리는 2.27m로 큰 차이가 없어 보이지만, 가로 통로를 거쳐 '
                '우회해야 해서 그래프 경로로는 3.08m까지 벌어진다.'),
        'anomaly': {'x': -1.30, 'y': 1.90},  # R_CD
        'poses': {'robot3': (-1.30, -0.10), 'robot8': (-2.38, -0.10)},  # F_CD, F_BC
        'candidates': ['robot3', 'robot8'],
        'expected': 'robot3',
    },
]

@pytest.mark.parametrize('case', SCENARIOS, ids=[c['name'] for c in SCENARIOS])
def test_scenarios_pick_expected_robot(graph, case):
    # 여기서는 "누가 이겼는가"만 확인한다. 실제 경로 거리 숫자(2.22m,
    # 3.12m 등, case['why']에 적어둔 값들)까지 assert하진 않는데, 그
    # 숫자는 손으로 검증하기보다 build_selector_report.py가 그린 지도
    # 위에서 눈으로 확인하는 쪽이 더 신뢰할 수 있기 때문 - 다익스트라
    # 경로가 이상하게 나오면(예: 엉뚱한 통로로 새는 버그) 숫자만 봐서는
    # 못 알아채도 지도로 보면 바로 티가 난다(이전 세션에 zone_router의
    # origin 필드 버그를 시각화 중에 발견한 것과 같은 이유).
    winner = select_nearest_robot(graph, case['anomaly'], case['poses'], case['candidates'])
    assert winner == case['expected']


# 아래는 기하(누가 더 가까운가)가 아니라 입력이 불완전할 때의 안전망을
# 검증하는 케이스라 SCENARIOS처럼 지도로 그릴 이유는 없다. 그래도
# build_selector_report.py가 "예외 케이스" 절에서 이 목록을 그대로
# 불러다 결과를 표로 보여준다 - SCENARIOS와 마찬가지로 리포트와 테스트가
# 서로 다른 값을 말하는 일을 막기 위함.
ANOMALY_AT_F_LEFT = {'x': -4.60, 'y': -0.10}  # 좌표 자체는 임의값, F_left 재사용

EDGE_CASES = [
    {
        'name': 'excludes_unknown_pose',
        'title': '위치 모르는 로봇은 후보에서 제외',
        # robot8, robot9는 amcl_pose를 한 번도 못 받았다고 가정(poses
        # 딕셔너리에 키 자체가 없음). 위치를 모르는 로봇과 아는 로봇의
        # 거리를 비교할 수 없으므로 robot3만 후보에 남아야 한다.
        'detail': 'robot3만 위치를 알고 robot8, robot9는 amcl_pose를 받은 적 없을 때.',
        'anomaly': ANOMALY_AT_F_LEFT,
        'poses': {'robot3': (-3.48, -0.10)},  # F_AB, 이상신호 바로 옆
        'candidates': ['robot3', 'robot8', 'robot9'],
        'expected': 'robot3',
    },
    {
        'name': 'falls_back_when_no_pose_known',
        'title': '아무도 위치를 모르면 첫 후보로 대체',
        # AMCL이 아직 안 떴거나(막 부팅) 수동 테스트 환경이라 로봇
        # 위치가 하나도 없는 경우 - 거리 비교 자체가 불가능하므로
        # candidates 목록 순서상 첫 번째로 대체한다(폴백). 일부러
        # ['robot8', 'robot3'] 순서로 둬서, 알파벳 순이 아니라 실제
        # candidates 리스트 순서를 따르는지까지 확인한다.
        'detail': 'AMCL이 아직 안 떴거나 수동 테스트 환경일 때 -> candidates 목록의 첫 로봇.',
        'anomaly': ANOMALY_AT_F_LEFT,
        'poses': {},
        'candidates': ['robot8', 'robot3'],
        'expected': 'robot8',
    },
    {
        'name': 'none_for_empty_candidates',
        'title': '후보가 없으면 None',
        # 이상신호 대응 중이 아닌(idle) 로봇이 하나도 없는 경우 - 호출부인
        # fleet_node._on_anomaly_trigger()는 이 None을 보고 트리거를
        # 그냥 무시한다. 예외를 던지지 않고 None을 돌려주는 게 계약이라는
        # 걸 여기서 명시적으로 고정해둔다.
        'detail': '이상신호 대응 중이 아닌 로봇이 하나도 없을 때(모두 busy) -> 호출부가 트리거를 무시.',
        'anomaly': ANOMALY_AT_F_LEFT,
        'poses': {},
        'candidates': [],
        'expected': None,
    },
]


@pytest.mark.parametrize('case', EDGE_CASES, ids=[c['name'] for c in EDGE_CASES])
def test_edge_cases(graph, case):
    winner = select_nearest_robot(graph, case['anomaly'], case['poses'], case['candidates'])
    assert winner == case['expected']
