from setuptools import find_packages, setup

package_name = 'control_amr'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='hungeunlee',
    maintainer_email='dlgnsrms00@naver.com',
    description='Mission control flows for a namespaced TurtleBot4 AMR',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'control_node = control_amr.control_node:main',
        ],
    },
)
