import { describe, expect, it } from "vitest";
import { dockPixelPosition } from "../../constants/dashboard";
import type { MapGrid } from "../../types";
import { heldWaypoint, includesUnknownCell } from "./mapRules";

const grid: MapGrid = {
  available: true,
  width: 5,
  height: 5,
  freeMin: 250,
  data: new Uint8Array([
    205, 205, 205, 205, 205,
    205,   0,   0,   0, 205,
    205,   0, 205,   0, 205,
    205,   0, 254,   0, 205,
    205, 205, 205, 205, 205,
  ]),
};

describe("zone boundary", () => {
  it("accepts a zone inside the black boundary, including an enclosed gray cell", () => {
    expect(includesUnknownCell(grid, { x: 1, y: 1, w: 3, h: 3 })).toBe(false);
  });

  it("rejects a zone when even one gray unknown cell is included", () => {
    expect(includesUnknownCell(grid, { x: 0, y: 1, w: 2, h: 2 })).toBe(true);
  });
});

describe("AMR waypoint marker", () => {
  const waypoints = [{ x: 10, y: 10 }, { x: 30, y: 10 }];

  it("stays hidden before the first waypoint is reached", () => {
    expect(heldWaypoint({ x: 1, y: 1 }, waypoints, null, 2)).toBeNull();
  });

  it("holds the reached waypoint while the AMR travels between waypoints", () => {
    const first = heldWaypoint({ x: 10.5, y: 10 }, waypoints, null, 2);
    expect(first).toEqual({ x: 10, y: 10 });
    expect(heldWaypoint({ x: 20, y: 10 }, waypoints, first, 2)).toEqual({ x: 10, y: 10 });
  });

  it("jumps only after the next waypoint is reached", () => {
    expect(heldWaypoint({ x: 29.5, y: 10 }, waypoints, { x: 10, y: 10 }, 2)).toEqual({ x: 30, y: 10 });
  });
});

describe("docking station placement", () => {
  it("places AMR-01 at upper-left and AMR-02 at lower-right of the map", () => {
    expect(dockPixelPosition("AMR-01", 100, 100)).toEqual({ x: 15, y: 22 });
    expect(dockPixelPosition("AMR-02", 100, 100)).toEqual({ x: 90, y: 82 });
  });
});
