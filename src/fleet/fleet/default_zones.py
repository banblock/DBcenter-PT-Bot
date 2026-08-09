"""단독 테스트용 기본 구역 데이터. map/datacenter_map/datacenter_map_v4.pgm
기준으로 HMI에서 직접 실측/지정한 좌표다 (랙 A/B -> robot3, 랙 C/D ->
robot8). 두 로봇의 경로가 모두 V_BC 통로(F_BC-R_BC 세로 연결)를
지나가게 돼서, Backend가 연결 안 된 상태에서도 교차 지점 판정
파이프라인이 실제로 뭔가를 중재하는 걸 볼 수 있다. 진짜
/backend/map_points 메시지가 오면 이 데이터는 통째로 교체된다.
gate(차단기 점검) 지점은 아직 없음 - 전부 point_type='normal'.

fleet_node.py(rclpy를 import함)와 분리해둔 이유: 테스트/시각화 스크립트가
ROS 2 환경 없이도 이 데이터를 그대로 쓸 수 있게 하기 위해서다."""

DEFAULT_ZONES = [
    {
        'zone_id': 'zoneAB',
        'robot': 'robot3',
        'points': [
            {'x': -2.4, 'y': 0.924, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -3.4, 'y': -0.0411, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -4.53, 'y': 0.8, 'yaw': 0.0, 'point_type': 'normal'},
        ],
    },
    {
        'zone_id': 'zoneCD',
        'robot': 'robot8',
        'points': [
            {'x': -2.32, 'y': 0.05, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -2.46, 'y': 2, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -1.3, 'y': 1.05, 'yaw': 0.0, 'point_type': 'normal'},
        ],
    },
]
