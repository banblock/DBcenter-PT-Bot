"""여러 노드가 공유하는 유틸.

yaml 기반 파라미터 선언 헬퍼(declare_parameters_from_yaml)와,
CamState.msg 규격에 고정된 프로토콜 상수(STATE_BY_CLASS, CAMERA_ID_BY_NAME)를 담는다.
"""

from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory
from rcl_interfaces.msg import ParameterDescriptor


def declare_parameters_from_yaml(node, section_name):
    """config/params.yaml의 section_name(ros__parameters) 값들을 기본값으로 declare_parameter를 반복 호출.

    launch나 --ros-args --params-file로 넘어온 오버라이드는 그대로 우선 적용된다 -
    이 함수는 그게 없을 때(예: ros2 run 단독 실행) 쓰일 기본값을 yaml에서 가져올 뿐이다.
    """
    params_path = Path(get_package_share_directory('vision_detection')) / 'config' / 'params.yaml'
    all_params = yaml.safe_load(params_path.read_text())
    defaults = all_params.get(section_name, {}).get('ros__parameters', {})
    for name, value in defaults.items():
        if isinstance(value, list) and not value:
            # 빈 리스트는 원소가 없어 타입 추론이 불가 → NOT_SET으로 선언되어 get 시
            # ParameterUninitializedException이 발생한다. 동적 타이핑을 허용해 회피.
            node.declare_parameter(name, value, ParameterDescriptor(dynamic_typing=True))
        else:
            node.declare_parameter(name, value)


# CamState.msg 프로토콜 상수 - detect_cctv_node/detect_ambient_node가 공유한다.
# CamState.msg 필드 주석(state, camera_id)과 반드시 일치해야 하고 백엔드도 이
# 매핑을 그대로 전제하므로, yaml 파라미터가 아니라 코드 상수로 고정해서 노드별로
# 따로 정의하지 않고 여기서만 관리한다.

# CamState.msg의 state 값
STATE_BY_CLASS = {'fire': 0, 'smoke': 1, 'coolant': 2}

# CamState.msg의 camera_id 값
CAMERA_ID_BY_NAME = {
    'cctv1': 0,
    'cctv2': 1,
    'robot3': 2,
    'robot8': 3,
}
