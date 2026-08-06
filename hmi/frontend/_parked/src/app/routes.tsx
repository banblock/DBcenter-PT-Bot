/**
 * 라우팅 설계.
 *
 * ## 구조
 * 관제 대시보드는 **단일 화면**이라는 요구(정보구조도)를 지킨다. 즉 `/` 한 곳에
 * 로봇 패널·지도·비전·작업큐·로그가 모두 있다. 나머지는 대시보드를 대체하는
 * 서브페이지이며, 관제 중 상시 보는 화면이 아니라 필요할 때 들어가는 화면이다.
 *
 *   /            관제 대시보드 (좌: 로봇 / 중: 지도·카메라 / 우: 통계·큐·로그)
 *   /equipment   설비 점검(align) 결과      — 체크리스트 F-63~F-68
 *   /stats       통계 · 순찰 우선순위        — F-69~F-74
 *   /history     이상 이벤트 이력 조회        — F-47~F-49
 *   /suppression 화재진압 제어 패널          — F-57~F-62
 *   /settings    시스템 설정
 *
 * ## 코드 분할
 * 대시보드는 첫 화면이라 즉시 번들에 포함하고, 서브페이지는 `lazy` 로 나눈다.
 * 관제실 PC 는 한 번 켜두고 쓰므로 초기 로딩이 화면 반응성보다 중요하다.
 *
 * ## 권한
 * 각 라우트의 요구 권한은 여기 한 곳에 적는다. 네비게이션 노출과 라우트 가드가
 * 같은 표를 보므로, 탭은 보이는데 못 들어가는 불일치가 생기지 않는다.
 */

import { lazy, Suspense, type ReactElement } from "react";
import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import {
  Activity,
  BarChart3,
  ClipboardList,
  Flame,
  LayoutDashboard,
  Settings,
} from "lucide-react";
import { AppLayout } from "./AppLayout";
import { RequirePermission } from "./RequirePermission";
import { DashboardPage } from "../pages/DashboardPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { PageFallback } from "../pages/PageFallback";
import type { Permission } from "../auth/permissions";

const EquipmentPage = lazy(() =>
  import("../pages/EquipmentPage").then((m) => ({ default: m.EquipmentPage })),
);
const StatsPage = lazy(() => import("../pages/StatsPage").then((m) => ({ default: m.StatsPage })));
const HistoryPage = lazy(() =>
  import("../pages/HistoryPage").then((m) => ({ default: m.HistoryPage })),
);
const SuppressionPage = lazy(() =>
  import("../pages/SuppressionPage").then((m) => ({ default: m.SuppressionPage })),
);
const SettingsPage = lazy(() =>
  import("../pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

export interface NavItem {
  path: string;
  label: string;
  icon: ReactElement;
  permission: Permission;
}

/** 네비게이션 탭 — 순서가 곧 화면 순서다. */
export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "관제", icon: <LayoutDashboard size={15} />, permission: "VIEW" },
  { path: "/equipment", label: "설비 점검", icon: <ClipboardList size={15} />, permission: "VIEW" },
  { path: "/stats", label: "통계·우선순위", icon: <BarChart3 size={15} />, permission: "VIEW" },
  { path: "/history", label: "이력", icon: <Activity size={15} />, permission: "VIEW" },
  {
    path: "/suppression",
    label: "화재진압",
    icon: <Flame size={15} />,
    permission: "SUPPRESSION_REQUEST",
  },
  { path: "/settings", label: "설정", icon: <Settings size={15} />, permission: "CONFIG_EDIT" },
];

function lazyRoute(element: ReactElement, permission: Permission): ReactElement {
  return (
    <RequirePermission permission={permission}>
      <Suspense fallback={<PageFallback />}>{element}</Suspense>
    </RequirePermission>
  );
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    errorElement: <NotFoundPage />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "equipment", element: lazyRoute(<EquipmentPage />, "VIEW") },
      { path: "stats", element: lazyRoute(<StatsPage />, "VIEW") },
      { path: "history", element: lazyRoute(<HistoryPage />, "VIEW") },
      { path: "suppression", element: lazyRoute(<SuppressionPage />, "SUPPRESSION_REQUEST") },
      { path: "settings", element: lazyRoute(<SettingsPage />, "CONFIG_EDIT") },
      // 알 수 없는 경로는 대시보드로 — 관제 화면은 항상 되돌아갈 곳이 있어야 한다
      { path: "404", element: <NotFoundPage /> },
      { path: "*", element: <Navigate to="/404" replace /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
