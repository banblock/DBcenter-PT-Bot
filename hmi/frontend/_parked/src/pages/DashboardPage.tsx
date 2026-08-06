/**
 * 관제 대시보드 (`/`) — 정보구조도의 단일 화면 구성.
 *
 *   좌: 로봇별 미션 패널 · 중: 시설 맵 + 카메라 피드 · 우: 통계 · 이벤트 큐 · 로그
 */

import { CameraGrid } from "../components/CameraGrid/CameraGrid";
import { LogPanel } from "../components/LogPanel/LogPanel";
import { MapPanel } from "../components/MapPanel/MapPanel";
import { QueuePanel } from "../components/QueuePanel/QueuePanel";
import { RobotStatusCard } from "../components/RobotStatusCard/RobotStatusCard";
import { StatsPanel } from "../components/StatsPanel/StatsPanel";
import { EmptyState, Skeleton } from "../components/ui";
import { useDashboard } from "../hooks/useDashboard";
import { useRobotIds } from "../store/robotStore";

function RobotColumn() {
  const robotIds = useRobotIds();
  const { now, pending, sendCommand, snapshotLoaded } = useDashboard();

  if (!snapshotLoaded && robotIds.length === 0) {
    // 로딩 스켈레톤 (체크리스트 §3-12)
    return (
      <aside className="dashboard-grid__left">
        <div className="panel" style={{ padding: 16 }}>
          <Skeleton height={20} />
          <div style={{ height: 12 }} />
          <Skeleton count={5} height={16} />
        </div>
      </aside>
    );
  }

  if (robotIds.length === 0) {
    return (
      <aside className="dashboard-grid__left">
        <div className="panel">
          <EmptyState
            title="등록된 로봇이 없습니다"
            description="백엔드 설정(AMR_ROBOT_IDS)에 로봇을 추가하세요"
          />
        </div>
      </aside>
    );
  }

  return (
    <aside className="dashboard-grid__left">
      {robotIds.map((robotId) => (
        <RobotStatusCard
          key={robotId}
          robotId={robotId}
          now={now}
          pending={pending[robotId]}
          onCommand={sendCommand}
        />
      ))}
    </aside>
  );
}

export function DashboardPage() {
  return (
    <div className="dashboard-grid">
      <RobotColumn />

      <section className="dashboard-grid__center">
        <MapPanel />
        <CameraGrid />
      </section>

      <aside className="dashboard-grid__right">
        <StatsPanel />
        <QueuePanel />
        <LogPanel />
      </aside>
    </div>
  );
}
