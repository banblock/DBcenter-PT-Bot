import type { MapGrid, ZoneRect } from "../../types";

export interface MapPoint {
  x: number;
  y: number;
}

/**
 * 존 사각형이 실제 맵 바깥의 회색(205) 셀을 하나라도 포함하는지 검사한다.
 * 맵 테두리에서 4방향으로 연결된 205만 외부로 본다. 검정 설비에 둘러싸인 내부의
 * 닫힌 회색 공간은 실제 맵 외부가 아니므로 존 사각형에 포함할 수 있다.
 */
export function includesUnknownCell(mapGrid: MapGrid | null, rect: ZoneRect): boolean {
  if (!mapGrid?.available || !mapGrid.data) return false;
  const { data, width, height } = mapGrid;
  const exterior = new Uint8Array(width * height);
  const queue: number[] = [];
  const enqueue = (x: number, y: number) => {
    const index = y * width + x;
    if (data[index] !== 205 || exterior[index]) return;
    exterior[index] = 1;
    queue.push(index);
  };
  for (let x = 0; x < width; x++) {
    enqueue(x, 0);
    enqueue(x, height - 1);
  }
  for (let y = 1; y < height - 1; y++) {
    enqueue(0, y);
    enqueue(width - 1, y);
  }
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const index = queue[cursor];
    const x = index % width;
    const y = Math.floor(index / width);
    if (x > 0) enqueue(x - 1, y);
    if (x + 1 < width) enqueue(x + 1, y);
    if (y > 0) enqueue(x, y - 1);
    if (y + 1 < height) enqueue(x, y + 1);
  }

  const x0 = Math.max(0, Math.floor(rect.x));
  const y0 = Math.max(0, Math.floor(rect.y));
  const x1 = Math.min(width, Math.ceil(rect.x + rect.w));
  const y1 = Math.min(height, Math.ceil(rect.y + rect.h));
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      if (exterior[y * width + x]) return true;
    }
  }
  return false;
}

/**
 * 실시간 좌표가 waypoint 도달 반경 안일 때만 그 waypoint를 반환한다.
 * 이동 중이면 직전 도달 waypoint를 그대로 유지하고, 첫 도달 전에는 null이다.
 */
export function heldWaypoint(
  live: MapPoint,
  waypoints: MapPoint[],
  previous: MapPoint | null,
  snapDistance: number,
): MapPoint | null {
  if (waypoints.length === 0) return null;
  let nearest: MapPoint | null = null;
  let nearestDistance = Infinity;
  for (const waypoint of waypoints) {
    const distance = Math.hypot(waypoint.x - live.x, waypoint.y - live.y);
    if (distance < nearestDistance) {
      nearestDistance = distance;
      nearest = waypoint;
    }
  }
  return nearest && nearestDistance <= snapDistance ? { ...nearest } : previous;
}
