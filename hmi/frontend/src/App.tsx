import { AlertBanner } from "./components/AlertBanner/AlertBanner";
import { CameraGrid } from "./components/CameraGrid/CameraGrid";
import { LogPanel } from "./components/LogPanel/LogPanel";
import { MapPanel } from "./components/MapPanel/MapPanel";
import { Modal } from "./components/Modal/Modal";
import { QueuePanel } from "./components/QueuePanel/QueuePanel";
import { RobotStatusCard } from "./components/RobotStatusCard/RobotStatusCard";
import { StatsPanel } from "./components/StatsPanel/StatsPanel";
import { TopBar } from "./components/TopBar/TopBar";
import { DashboardProvider } from "./context/DashboardContext";
import { useDashboard } from "./hooks/useDashboard";

function AmrColumn() {
  const { robots } = useDashboard();
  return (
    <aside className="dashboard-grid__left">
      {robots.map((robot) => (
        <RobotStatusCard key={robot.id} robot={robot} />
      ))}
    </aside>
  );
}

export default function App() {
  return (
    <DashboardProvider>
      <div className="app-shell">
        <TopBar />
        <AlertBanner />
        <main className="dashboard-grid">
          <AmrColumn />

          <section className="dashboard-grid__center">
            <MapPanel />
            <CameraGrid />
          </section>

          <aside className="dashboard-grid__right">
            <StatsPanel />
            <QueuePanel />
            <LogPanel />
          </aside>
        </main>
        <Modal />
      </div>
    </DashboardProvider>
  );
}
