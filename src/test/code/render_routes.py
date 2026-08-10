#!/usr/bin/env python3
"""Render the fixed corridor graph + DEFAULT_ZONES routes over the actual
map, for visual verification of zone_router's route generation and
crossing-point detection. No ROS 2 environment needed.

    python3 render_routes.py

Writes src/test/result/routes_check.png.
"""

import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fleet'))
from fleet.route_graph import RouteGraph
from fleet import zone_router
from fleet.default_zones import DEFAULT_ZONES

HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.join(HERE, '..', '..', '..')
MAP_PGM = os.path.join(REPO_ROOT, 'map', 'datacenter_map', 'datacenter_map_v4.pgm')
GRAPH_YAML = os.path.join(HERE, '..', '..', 'fleet', 'config', 'route_graph.yaml')
OUT_PNG = os.path.join(HERE, '..', 'result', 'routes_check.png')

RESOLUTION = 0.05
ORIGIN_X, ORIGIN_Y = -5.15, -0.659

im = Image.open(MAP_PGM).convert('L')
arr = np.array(im)
h, w = arr.shape
extent = [ORIGIN_X, ORIGIN_X + w * RESOLUTION, ORIGIN_Y, ORIGIN_Y + h * RESOLUTION]

graph = RouteGraph.from_yaml(GRAPH_YAML)
missions, crossing_log, _ = zone_router.build_missions(graph, DEFAULT_ZONES)
print('crossings:', crossing_log)

fig, ax = plt.subplots(figsize=(10, 7))
ax.imshow(arr, cmap='gray', extent=extent, origin='upper', vmin=0, vmax=255)

for eid, e in graph.edges.items():
    xa, ya = graph.nodes[e['a']]
    xb, yb = graph.nodes[e['b']]
    ax.plot([xa, xb], [ya, yb], color='#888888', linewidth=1, linestyle='--', zorder=1)

for nid, (nx, ny) in graph.nodes.items():
    ax.plot(nx, ny, 'o', color='#888888', markersize=4, zorder=2)
    ax.annotate(nid, (nx, ny), fontsize=6, color='#555555', xytext=(2, 2), textcoords='offset points')

colors = {'robot3': '#1f77b4', 'robot8': '#d62728'}
for robot, wps in missions.items():
    xs = [wp['x'] for wp in wps]
    ys = [wp['y'] for wp in wps]
    ax.plot(xs, ys, '-', color=colors.get(robot, 'green'), linewidth=2, zorder=3, label=robot)
    for wp in wps:
        if wp['has_gate']:
            ax.plot(wp['x'], wp['y'], '*', color=colors.get(robot, 'green'), markersize=14, zorder=4)
        elif wp['origin'] == 'patrol':
            ax.plot(wp['x'], wp['y'], 'o', color=colors.get(robot, 'green'), markersize=8, zorder=4)
        else:
            ax.plot(wp['x'], wp['y'], 'o', color=colors.get(robot, 'green'), markersize=4, zorder=4)
        if wp['point_id']:
            ax.add_patch(plt.Circle((wp['x'], wp['y']), 0.12, fill=False, color='orange', linewidth=2, zorder=5))

ax.set_xlabel('x (m)')
ax.set_ylabel('y (m)')
ax.legend()
ax.set_title('Fleet routes over route_graph.yaml (orange ring = crossing point)')
plt.tight_layout()
os.makedirs(os.path.dirname(OUT_PNG), exist_ok=True)
plt.savefig(OUT_PNG, dpi=150)
print('saved', OUT_PNG)
