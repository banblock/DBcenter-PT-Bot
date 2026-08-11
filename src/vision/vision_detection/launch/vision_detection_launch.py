import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    share_dir = get_package_share_directory('vision_detection')
    params_file = os.path.join(share_dir, 'config', 'params.yaml')

    return LaunchDescription([
        Node(
            package='vision_detection',
            executable='detect_main_node',
            name='detect_main_node',
            parameters=[params_file],
        ),
        Node(
            package='vision_detection',
            executable='detect_ambient_node',
            name='detect_ambient_node',
            parameters=[params_file],
        ),
        Node(
            package='vision_detection',
            executable='detect_station_node',
            name='detect_station_node',
            parameters=[params_file],
        ),
        Node(
            package='vision_detection',
            executable='detect_cctv_node',
            name='detect_cctv_node',
            parameters=[params_file],
        ),
    ])
