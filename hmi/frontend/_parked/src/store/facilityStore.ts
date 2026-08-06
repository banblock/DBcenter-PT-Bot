/**
 * 시설 정적 데이터 store — 맵 · 구역 · 노드 · 탐지 프레임 · 진압 · 우선순위.
 *
 * SNAPSHOT 으로 한 번에 받고 거의 바뀌지 않는 것들이라 한 store 에 모았다.
 * (자주 바뀌는 로봇/이벤트/미션과 섞으면 정적 데이터를 쓰는 컴포넌트까지
 * 5Hz 로 리렌더된다.)
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  DetectionPayload,
  MapSnapshot,
  NodeSnapshot,
  PriorityUpdatedItem,
  SuppressionStatusPayload,
  ZoneSnapshot,
} from "../services/ws/messages";

interface FacilityState {
  map: MapSnapshot | null;
  zones: ZoneSnapshot[];
  nodes: NodeSnapshot[];
  suppressionMode: "MANUAL" | "AUTO";

  /** 카메라별 최신 탐지 프레임 (F-34 bbox 오버레이). 최신 1프레임만 유지. */
  detections: Record<string, DetectionPayload & { at: number }>;
  /** 진행 중 진압 시퀀스 */
  suppressions: Record<string, SuppressionStatusPayload>;
  /** 노드별 우선순위 점수 — 지도 히트맵(F-25)·랭킹 표(F-72) */
  priorityByNode: Record<string, PriorityUpdatedItem>;

  setSnapshot: (payload: {
    map: MapSnapshot | null;
    zones: ZoneSnapshot[];
    nodes: NodeSnapshot[];
    suppression_mode?: "MANUAL" | "AUTO";
  }) => void;
  setDetection: (payload: DetectionPayload) => void;
  setSuppression: (payload: SuppressionStatusPayload) => void;
  setPriorities: (items: PriorityUpdatedItem[]) => void;
  setSuppressionMode: (mode: "MANUAL" | "AUTO") => void;
  reset: () => void;
}

/** 탐지 프레임의 키 — CCTV 는 camera_id, 로봇 캠은 robot_id */
function detectionKey(payload: DetectionPayload): string {
  return payload.camera_id ?? payload.robot_id ?? payload.source;
}

export const useFacilityStore = create<FacilityState>((set) => ({
  map: null,
  zones: [],
  nodes: [],
  suppressionMode: "MANUAL",
  detections: {},
  suppressions: {},
  priorityByNode: {},

  setSnapshot: ({ map, zones, nodes, suppression_mode }) =>
    set({
      map,
      zones: zones ?? [],
      nodes: nodes ?? [],
      suppressionMode: suppression_mode ?? "MANUAL",
    }),

  setDetection: (payload) =>
    set((state) => ({
      detections: { ...state.detections, [detectionKey(payload)]: { ...payload, at: Date.now() } },
    })),

  setSuppression: (payload) =>
    set((state) => ({
      suppressions: { ...state.suppressions, [payload.suppression_id]: payload },
    })),

  setPriorities: (items) =>
    set((state) => {
      const priorityByNode = { ...state.priorityByNode };
      for (const item of items) priorityByNode[item.node_id] = item;
      // 노드 목록의 점수도 함께 갱신해야 지도 히트맵이 즉시 반영된다
      const nodes = state.nodes.map((node) =>
        priorityByNode[node.node_id]
          ? { ...node, priority_score: priorityByNode[node.node_id].score }
          : node,
      );
      return { priorityByNode, nodes };
    }),

  setSuppressionMode: (mode) => set({ suppressionMode: mode }),

  reset: () =>
    set({
      map: null,
      zones: [],
      nodes: [],
      detections: {},
      suppressions: {},
      priorityByNode: {},
    }),
}));

// ── 선택자 ────────────────────────────────────────────────────────────────
export const useMap = (): MapSnapshot | null => useFacilityStore((s) => s.map);
export const useZones = (): ZoneSnapshot[] => useFacilityStore(useShallow((s) => s.zones));
export const useNodes = (): NodeSnapshot[] => useFacilityStore(useShallow((s) => s.nodes));
export const useZone = (zoneId: string | null | undefined): ZoneSnapshot | undefined =>
  useFacilityStore((s) => (zoneId ? s.zones.find((z) => z.zone_id === zoneId) : undefined));
export const useDetection = (key: string) => useFacilityStore((s) => s.detections[key]);
export const useActiveSuppressions = (): SuppressionStatusPayload[] =>
  useFacilityStore(useShallow((s) => Object.values(s.suppressions)));

/**
 * 맵 좌표(m) → 픽셀 변환 (B-13 / F-19).
 * 백엔드 `crud/maps.py` 의 공식과 반드시 같아야 한다.
 *   px = (x - origin_x) / resolution
 *   py = height - (y - origin_y) / resolution
 */
export function mapToPixel(map: MapSnapshot, x: number, y: number): { px: number; py: number } {
  const [originX, originY] = map.origin;
  return {
    px: (x - originX) / map.resolution,
    py: map.height - (y - originY) / map.resolution,
  };
}

export function pixelToMap(map: MapSnapshot, px: number, py: number): { x: number; y: number } {
  const [originX, originY] = map.origin;
  return {
    x: px * map.resolution + originX,
    y: (map.height - py) * map.resolution + originY,
  };
}
