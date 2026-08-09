#!/usr/bin/env python3
"""Print a robot's current /fleet/<ns>/mission in full (one waypoint per
line), since `ros2 topic echo` truncates long strings and hides the JSON.

    python3 scripts/dump_mission.py robot3
"""

import json
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import (QoSProfile, QoSDurabilityPolicy,
                        QoSReliabilityPolicy, QoSHistoryPolicy)
from std_msgs.msg import String

MISSION_QOS = QoSProfile(
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    reliability=QoSReliabilityPolicy.RELIABLE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 dump_mission.py <robot namespace>  (e.g. robot3)')
        sys.exit(1)
    ns = sys.argv[1]

    rclpy.init()
    node = Node('dump_mission')
    received = {}

    def cb(msg):
        received['data'] = msg.data

    node.create_subscription(String, f'/fleet/{ns}/mission', cb, MISSION_QOS)

    print(f'waiting for /fleet/{ns}/mission ...')
    while 'data' not in received:
        rclpy.spin_once(node, timeout_sec=0.5)

    waypoints = json.loads(received['data'])
    print(f'{len(waypoints)} waypoints:')
    for i, wp in enumerate(waypoints):
        flags = []
        if wp.get('has_gate'):
            flags.append('GATE')
        if wp.get('point_id'):
            flags.append(f"CROSSING:{wp['point_id']}")
        flag_str = f"  [{' '.join(flags)}]" if flags else ''
        print(f"  {i}: x={wp['x']:.2f} y={wp['y']:.2f} yaw={wp['yaw']:.1f}{flag_str}")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
