// 뇌에 접근하는 통로

import { useDashboardContext } from "../context/DashboardContext";

export function useDashboard() {
  return useDashboardContext();
}
