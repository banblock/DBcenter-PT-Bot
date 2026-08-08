"""단독 테스트용 기본 구역 데이터. map/datacenter_map/datacenter_map_v4.pgm
기준으로 실제 좌표를 잡았다 (랙 A/B -> robot3, 랙 C/D -> robot8). 두
로봇의 경로가 모두 방 중앙의 F_BC_CD 통로를 지나가게 해뒀는데, Backend가
연결 안 된 상태에서도 교차 지점 판정 파이프라인이 실제로 뭔가를
중재하는 걸 볼 수 있게 하기 위함이다. 진짜 /backend/map_points 메시지가
오면 이 데이터는 통째로 교체된다.

fleet_node.py(rclpy를 import함)와 분리해둔 이유: 테스트/시각화 스크립트가
ROS 2 환경 없이도 이 데이터를 그대로 쓸 수 있게 하기 위해서다."""

DEFAULT_ZONES = [
    {
        'zone_id': 'zoneAB',
        'robot': 'robot3',
        'points': [
            {'x': -3.4, 'y': 1.75, 'yaw': 0.0, 'point_type': 'normal'},
            {'x': -1.85, 'y': -0.1, 'yaw': 90.0, 'point_type': 'normal'},
            {'x': -3.5, 'y': -0.116, 'yaw': 90.0, 'point_type': 'gate'},
        ],
    },
    {
        'zone_id': 'zoneCD',
        'robot': 'robot8',
        'points': [
            {'x': -1.3, 'y': 0.113, 'yaw': -100.0, 'point_type': 'normal'},
            {'x': -1.95, 'y': -0.1, 'yaw': -90.0, 'point_type': 'normal'},
            {'x': -1.37, 'y': 1.95, 'yaw': -90.0, 'point_type': 'gate'},
        ],
    },
]
