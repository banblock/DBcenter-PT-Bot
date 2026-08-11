from setuptools import find_packages, setup

package_name = 'fleet'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/route_graph.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jung',
    maintainer_email='ts4955ts@gmail.com',
    description='AMR Fleet Node: central coordinator for patrol routes, crossing-point occupancy arbitration, and anomaly dispatch.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fleet_node = fleet.fleet_node:main',
            'backend_adapter = fleet.backend_adapter:main',
            'gate_check_bridge = fleet.gate_check_bridge:main',
        ],
    },
)
