/**
 * 전체 페이지 라우팅 통합 점검 (fe_core).
 *
 * 실제 라우트 테이블(`routes`)을 그대로 메모리 라우터에 얹어, 각 경로가
 * 렌더되는지 · 권한에 따라 막히는지 · 없는 경로가 404 로 가는지 확인한다.
 * WS 는 실제로 붙지 않도록 가짜 소켓을 주입한다.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { routes } from "./routes";
import { useUiStore } from "../store/uiStore";
import { __resetMonitorSocket } from "../services/ws/useMonitorSocket";
import type { Role } from "../auth/permissions";

/** 아무 것도 하지 않는 소켓 — 테스트에서 실제 네트워크를 열지 않기 위해. */
class SilentSocket {
  static readonly OPEN = 1;
  readyState = 0;
  onopen: unknown = null;
  onmessage: unknown = null;
  onclose: unknown = null;
  onerror: unknown = null;
  send() {}
  close() {}
}

function renderAt(path: string, role: Role = "ADMIN") {
  act(() => useUiStore.getState().setRole(role));
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  __resetMonitorSocket();
  vi.stubGlobal("WebSocket", SilentSocket);
  // 서브페이지가 마운트되며 REST 를 부른다. 빈 성공 응답으로 고정.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ result: "SUCCESS", data: [], count: 0, page: 1, size: 50 }), {
        headers: { "content-type": "application/json" },
      }),
    ),
  );
});

describe("라우트 렌더", () => {
  it("/ 는 관제 대시보드를 그린다", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { name: /설비 안전 AMR/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /시설 맵/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /이벤트 큐/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /활동 로그/ })).toBeInTheDocument();
  });

  it("/equipment 는 설비 점검 화면을 그린다", async () => {
    renderAt("/equipment");
    expect(await screen.findByRole("heading", { name: "설비 점검 결과" })).toBeInTheDocument();
  });

  it("/stats 는 통계 화면을 그린다", async () => {
    renderAt("/stats");
    expect(await screen.findByRole("heading", { name: "통계 · 순찰 우선순위" })).toBeInTheDocument();
  });

  it("/history 는 이력 화면을 그린다", async () => {
    renderAt("/history");
    expect(await screen.findByRole("heading", { name: "이상 이벤트 이력" })).toBeInTheDocument();
  });

  it("/suppression 은 진압 제어 화면을 그린다", async () => {
    renderAt("/suppression");
    expect(await screen.findByRole("heading", { name: "화재진압 제어" })).toBeInTheDocument();
  });

  it("/settings 는 설정 화면을 그린다", async () => {
    renderAt("/settings");
    expect(await screen.findByRole("heading", { name: "시스템 설정" })).toBeInTheDocument();
  });

  it("없는 경로는 404 화면으로 간다", async () => {
    renderAt("/이런경로는없다");
    expect(await screen.findByText("화면을 찾을 수 없습니다")).toBeInTheDocument();
  });

  it("모든 화면에서 헤더와 네비게이션이 유지된다", async () => {
    renderAt("/stats");
    expect(await screen.findByRole("navigation", { name: "주요 화면" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /긴급정지/ })).toBeInTheDocument();
  });
});

describe("권한별 라우트 접근", () => {
  it("운영자는 /settings 에서 사유를 보게 된다", async () => {
    renderAt("/settings", "OPERATOR");
    expect(await screen.findByTestId("permission-denied")).toHaveTextContent("CONFIG_EDIT");
    expect(screen.queryByRole("heading", { name: "시스템 설정" })).not.toBeInTheDocument();
  });

  it("뷰어는 /suppression 에 들어갈 수 없다", async () => {
    renderAt("/suppression", "VIEWER");
    expect(await screen.findByTestId("permission-denied")).toHaveTextContent("SUPPRESSION_REQUEST");
  });

  it("뷰어에게는 진압·설정 탭이 아예 보이지 않는다", async () => {
    renderAt("/", "VIEWER");
    const nav = await screen.findByRole("navigation", { name: "주요 화면" });
    expect(nav).toHaveTextContent("관제");
    expect(nav).toHaveTextContent("이력");
    expect(nav).not.toHaveTextContent("화재진압");
    expect(nav).not.toHaveTextContent("설정");
  });

  it("관리자에게는 모든 탭이 보인다", async () => {
    renderAt("/", "ADMIN");
    const nav = await screen.findByRole("navigation", { name: "주요 화면" });
    for (const label of ["관제", "설비 점검", "통계·우선순위", "이력", "화재진압", "설정"]) {
      expect(nav).toHaveTextContent(label);
    }
  });

  it("뷰어는 순찰 시작 버튼을 조작할 수 없다", async () => {
    renderAt("/", "VIEWER");
    await screen.findByRole("heading", { name: /설비 안전 AMR/ });
    // disable 모드 — 버튼은 보이되 pointer-events 가 막힌다
    expect(screen.getByTestId("permission-disabled")).toHaveAttribute("aria-disabled", "true");
  });
});

describe("페이지 이동", () => {
  it("탭을 눌러 다른 화면으로 이동한다", async () => {
    const router = createMemoryRouter(routes, { initialEntries: ["/"] });
    act(() => useUiStore.getState().setRole("ADMIN"));
    render(<RouterProvider router={router} />);

    await screen.findByRole("heading", { name: /설비 안전 AMR/ });
    await act(async () => {
      await router.navigate("/stats");
    });

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "통계 · 순찰 우선순위" })).toBeInTheDocument(),
    );
    // 헤더는 그대로 — 셸이 다시 마운트되지 않았다는 뜻
    expect(screen.getByRole("heading", { name: /설비 안전 AMR/ })).toBeInTheDocument();
  });
});
