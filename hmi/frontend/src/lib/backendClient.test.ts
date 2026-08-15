import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backendCommand, pixelToWorld, resetCaches, translateFrame, worldToPixel } from "./backendClient";
import type { MapInfo } from "../types";

function eventFrame(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    type: "EVENT",
    payload: {
      event_id: "EVT-CCTV-001",
      source: "cctv",
      camera_id: "CAM-02",
      type: "SMOKE",
      severity: "CRITICAL",
      status: "QUEUED",
      zone_id: "Z-P2",
      confidence: 0.91,
      detected_at: "2026-08-09T06:00:00Z",
      ...overrides,
    },
  });
}

describe("CCTV automatic popup event", () => {
  beforeEach(() => resetCaches());
  afterEach(() => vi.unstubAllGlobals());

  it("marks a newly received CCTV event as an automatic-popup detection", () => {
    const message = translateFrame(eventFrame());
    expect(message?.detectedCctvEvent).toMatchObject({
      id: "EVT-CCTV-001",
      kind: "CCTV",
      cameraId: "CAM-02",
      severity: "DANGER",
    });
  });

  it("does not reopen the popup for a status update of the same event", () => {
    translateFrame(eventFrame());
    const update = translateFrame(eventFrame({ status: "ASSIGNED" }));
    expect(update?.detectedCctvEvent).toBeUndefined();
  });

  it("does not classify an AMR-origin event as a CCTV popup", () => {
    const message = translateFrame(eventFrame({ event_id: "EVT-AMR-001", source: "amr" }));
    expect(message?.detectedCctvEvent).toBeUndefined();
  });
});

describe("individual patrol restart", () => {
  beforeEach(() => resetCaches());
  afterEach(() => vi.unstubAllGlobals());

  it("restarts the robot's latest route instead of the global first route", async () => {
    translateFrame(JSON.stringify({
      type: "SNAPSHOT",
      payload: {
        robots: [],
        events: [],
        zones: [],
        missions: [
          { mission_id: "MSN-NEW", robot_id: "AMR-01", route_id: "R-20", status: "DONE" },
          { mission_id: "MSN-OLD", robot_id: "AMR-01", route_id: "R-01", status: "DONE" },
        ],
      },
    }));

    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      let data: unknown = {};
      if (url.endsWith("/api/map/active")) {
        data = {
          map_id: "MAP-DC-V4", name: "DC", image_url: null, resolution: 0.05,
          origin: [-5.15, -0.659, 0], width: 113, height: 66, is_active: true,
        };
      } else if (url.endsWith("/api/routes")) {
        data = [
          { route_id: "R-01", map_id: "MAP-DEMO-1F" },
          { route_id: "R-20", map_id: "MAP-DC-V4" },
        ];
      } else if (url.includes("/missions?")) {
        data = [];
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ result: "SUCCESS", data }),
      };
    }));

    await backendCommand("AMR-01", "start");
    const startCall = calls.find((call) => call.url.endsWith("/api/patrol/start"));
    expect(JSON.parse(String(startCall?.init?.body))).toMatchObject({
      route_id: "R-20",
      robot_ids: ["AMR-01"],
      apply_priority: false,
    });
  });
});

describe("datacenter_map_v4 coordinate transform", () => {
  const map: MapInfo = {
    map_id: "MAP-DC-V4",
    name: "datacenter_map_v4",
    image_url: null,
    resolution: 0.05,
    origin: [-5.15, -0.659, 0],
    width: 113,
    height: 66,
    is_active: true,
  };

  it("matches the ROS YAML origin and resolution in both directions", () => {
    const world = { x: -4.3025, y: 1.915 };
    const pixel = worldToPixel(map, world.x, world.y);
    expect(pixel.px).toBeCloseTo(16.95, 8);
    expect(pixel.py).toBeCloseTo(14.52, 8);
    expect(pixelToWorld(map, pixel.px, pixel.py)).toEqual(expect.objectContaining({
      x: expect.closeTo(world.x, 8),
      y: expect.closeTo(world.y, 8),
    }));
  });
});
