/**
 * 앱 셸 — 헤더 · 네비게이션 · 전역 오버레이(토스트/CRITICAL 모달).
 *
 * WS 연결은 여기서 **단 한 번** 시작한다. 페이지를 옮겨 다녀도 소켓은 유지된다
 * (라우트마다 연결하면 이동할 때마다 SNAPSHOT 이 다시 오고 로그가 리셋된다).
 */

import { NavLink, Outlet } from "react-router-dom";
import { AlertBanner } from "../components/AlertBanner/AlertBanner";
import { TopBar } from "../components/TopBar/TopBar";
import { CriticalAlertModal } from "../components/CriticalAlertModal/CriticalAlertModal";
import { ToastHost } from "../components/ui";
import { useMonitorSocket } from "../services/ws/useMonitorSocket";
import { useUnacknowledgedCount } from "../store/eventStore";
import { usePermission } from "../components/ui";
import { NAV_ITEMS } from "./routes";
import "./app-layout.css";

export function AppLayout() {
  useMonitorSocket();
  const unacknowledged = useUnacknowledgedCount();

  return (
    <div className="app-shell">
      <TopBar />
      <AppNav unacknowledged={unacknowledged} />
      <AlertBanner />

      <main className="app-main">
        <Outlet />
      </main>

      <ToastHost />
      <CriticalAlertModal />
    </div>
  );
}

function AppNav({ unacknowledged }: { unacknowledged: number }) {
  return (
    <nav className="app-nav" aria-label="주요 화면">
      {NAV_ITEMS.map((item) => (
        <NavItem key={item.path} item={item} badge={item.path === "/history" ? unacknowledged : 0} />
      ))}
    </nav>
  );
}

function NavItem({
  item,
  badge,
}: {
  item: (typeof NAV_ITEMS)[number];
  badge: number;
}) {
  // 권한이 없는 화면은 탭 자체를 숨긴다 — 눌러서 거부당하는 경험을 만들지 않는다.
  const allowed = usePermission(item.permission);
  if (!allowed) return null;

  return (
    <NavLink
      to={item.path}
      end={item.path === "/"}
      className={({ isActive }) => `app-nav__item${isActive ? " app-nav__item--active" : ""}`}
    >
      {item.icon}
      {item.label}
      {badge > 0 && <span className="app-nav__badge">{badge}</span>}
    </NavLink>
  );
}
