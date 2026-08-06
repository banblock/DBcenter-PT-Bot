/** 권한별 화면 노출 테스트 (fe_core). */

import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RequirePermission } from "../app/RequirePermission";
import { Button, PermissionGate } from "../components/ui";
import { useUiStore } from "../store/uiStore";
import { ROLES, ROLE_PERMISSIONS, can, canAll, canAny, isRole } from "./permissions";

function setRole(role: (typeof ROLES)[number]) {
  act(() => useUiStore.getState().setRole(role));
}

describe("권한 매트릭스", () => {
  it("뷰어는 조회만 가능하다", () => {
    expect(can("VIEWER", "VIEW")).toBe(true);
    expect(can("VIEWER", "ROBOT_CONTROL")).toBe(false);
    expect(can("VIEWER", "MASTER_EDIT")).toBe(false);
    expect(can("VIEWER", "SUPPRESSION_APPROVE")).toBe(false);
  });

  it("뷰어도 긴급정지는 할 수 있다 — 안전 기능은 권한으로 막지 않는다", () => {
    expect(can("VIEWER", "EMERGENCY_STOP")).toBe(true);
  });

  it("운영자는 로봇 제어·이벤트 대응은 되지만 마스터 편집·설정 변경은 안 된다", () => {
    expect(canAll("OPERATOR", ["ROBOT_CONTROL", "EVENT_HANDLE", "SUPPRESSION_REQUEST"])).toBe(true);
    expect(can("OPERATOR", "MASTER_EDIT")).toBe(false);
    expect(can("OPERATOR", "CONFIG_EDIT")).toBe(false);
    expect(can("OPERATOR", "SUPPRESSION_APPROVE")).toBe(false);
  });

  it("관리자는 모든 권한을 가진다", () => {
    expect(ROLE_PERMISSIONS.ADMIN).toHaveLength(9);
    expect(canAll("ADMIN", ROLE_PERMISSIONS.ADMIN)).toBe(true);
  });

  it("권한은 역할이 올라갈수록 늘어난다 (뷰어 ⊂ 운영자 ⊂ 관리자)", () => {
    // EMERGENCY_STOP 을 뷰어에게 연 정책이 상위 역할에서 누락되지 않았는지 확인
    for (const permission of ROLE_PERMISSIONS.VIEWER) {
      expect(can("OPERATOR", permission)).toBe(true);
      expect(can("ADMIN", permission)).toBe(true);
    }
    for (const permission of ROLE_PERMISSIONS.OPERATOR) {
      expect(can("ADMIN", permission)).toBe(true);
    }
  });

  it("canAny 는 하나만 있어도 통과한다", () => {
    expect(canAny("VIEWER", ["MASTER_EDIT", "VIEW"])).toBe(true);
    expect(canAny("VIEWER", ["MASTER_EDIT", "CONFIG_EDIT"])).toBe(false);
  });

  it("isRole 은 알 수 없는 값을 거부한다", () => {
    expect(isRole("ADMIN")).toBe(true);
    expect(isRole("SUPERUSER")).toBe(false);
    expect(isRole(null)).toBe(false);
  });
});

describe("PermissionGate", () => {
  it("권한이 있으면 자식을 그대로 그린다", () => {
    setRole("ADMIN");
    render(
      <PermissionGate permission="MASTER_EDIT">
        <Button>설비 추가</Button>
      </PermissionGate>,
    );
    expect(screen.getByRole("button", { name: "설비 추가" })).toBeInTheDocument();
  });

  it("hide 모드에서 권한이 없으면 렌더하지 않는다", () => {
    setRole("VIEWER");
    render(
      <PermissionGate permission="MASTER_EDIT">
        <Button>설비 추가</Button>
      </PermissionGate>,
    );
    expect(screen.queryByRole("button", { name: "설비 추가" })).not.toBeInTheDocument();
  });

  it("hide 모드에서 fallback 을 대신 보여줄 수 있다", () => {
    setRole("VIEWER");
    render(
      <PermissionGate permission="MASTER_EDIT" fallback={<span>권한 없음</span>}>
        <Button>설비 추가</Button>
      </PermissionGate>,
    );
    expect(screen.getByText("권한 없음")).toBeInTheDocument();
  });

  it("disable 모드에서는 버튼을 남기되 조작을 막는다", () => {
    setRole("VIEWER");
    render(
      <PermissionGate permission="ROBOT_CONTROL" mode="disable">
        <Button>순찰 시작</Button>
      </PermissionGate>,
    );
    expect(screen.getByRole("button", { name: "순찰 시작" })).toBeInTheDocument();
    expect(screen.getByTestId("permission-disabled")).toHaveAttribute("aria-disabled", "true");
  });

  it("역할을 바꾸면 즉시 반영된다", () => {
    setRole("VIEWER");
    render(
      <PermissionGate permission="ROBOT_CONTROL">
        <Button>순찰 시작</Button>
      </PermissionGate>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    setRole("OPERATOR");
    expect(screen.getByRole("button", { name: "순찰 시작" })).toBeInTheDocument();
  });
});

describe("RequirePermission (라우트 가드)", () => {
  it("권한이 있으면 화면을 보여준다", () => {
    setRole("ADMIN");
    render(
      <MemoryRouter>
        <RequirePermission permission="CONFIG_EDIT">
          <div>설정 화면</div>
        </RequirePermission>
      </MemoryRouter>,
    );
    expect(screen.getByText("설정 화면")).toBeInTheDocument();
  });

  it("권한이 없으면 화면 대신 사유를 보여준다 (조용한 리다이렉트 금지)", () => {
    setRole("OPERATOR");
    render(
      <MemoryRouter>
        <RequirePermission permission="CONFIG_EDIT">
          <div>설정 화면</div>
        </RequirePermission>
      </MemoryRouter>,
    );
    expect(screen.queryByText("설정 화면")).not.toBeInTheDocument();
    const denied = screen.getByTestId("permission-denied");
    expect(denied).toHaveTextContent("이 화면을 볼 권한이 없습니다");
    expect(denied).toHaveTextContent("CONFIG_EDIT");
  });
});

describe("역할 영속성", () => {
  it("선택한 역할이 localStorage 에 저장된다", () => {
    setRole("ADMIN");
    expect(localStorage.getItem("amr.role")).toBe("ADMIN");
  });
});
