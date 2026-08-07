from setuptools import find_packages, setup

package_name = 'vision_detection'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/vision_detection_launch.py']),
        ('share/' + package_name + '/config', ['config/params.yaml']),
        ('share/' + package_name + '/models', ['resource/best.pt', 'resource/cctv_best.pt']),
    ],
    install_requires=['setuptools', 'ultralytics'],
    zip_safe=True,
    maintainer='junh0012',
    maintainer_email='junh001224@gmail.com',
    description='Vision-based detection and post-processing/judgment package',
    license='TODO',
    entry_points={
        'console_scripts': [
            'detect_main_node = vision_detection.detect_main_node:main',
            'detect_ambient_node = vision_detection.detect_ambient_node:main',
            'detect_station_node = vision_detection.detect_station_node:main',
            'detect_cctv_node = vision_detection.detect_cctv_node:main',
        ],
    },
)
