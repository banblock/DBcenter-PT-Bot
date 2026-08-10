from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory


def declare_parameters_from_yaml(node, section_name):
    """config/params.yaml의 section_name(ros__parameters) 값들을 기본값으로 declare_parameter를 반복 호출.

    launch나 --ros-args --params-file로 넘어온 오버라이드는 그대로 우선 적용된다 -
    이 함수는 그게 없을 때(예: ros2 run 단독 실행) 쓰일 기본값을 yaml에서 가져올 뿐이다.
    """
    params_path = Path(get_package_share_directory('vision_detection')) / 'config' / 'params.yaml'
    all_params = yaml.safe_load(params_path.read_text())
    defaults = all_params.get(section_name, {}).get('ros__parameters', {})
    for name, value in defaults.items():
        node.declare_parameter(name, value)
