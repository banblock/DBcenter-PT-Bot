from glob import glob
import os

from setuptools import find_packages, setup

package_name = "detect_cctv"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            ["resource/" + package_name],
        ),
        ("share/" + package_name, ["package.xml"]),
        (
            os.path.join("share", package_name, "models"),
            glob("models/*"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="user",
    maintainer_email="user@example.com",
    description="Dual webcam CCTV YOLO abnormal detection nodes",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "detect_cctv_node = detect_cctv.detect_cctv_node:main",
        ],
    },
)
