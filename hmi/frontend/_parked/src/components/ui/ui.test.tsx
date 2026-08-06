/** 공통 컴포넌트 렌더 테스트 (fe_core). */

import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Gauge,
  Modal,
  Skeleton,
  StatusDot,
  StepList,
  ToastHost,
} from "./index";
import { useUiStore } from "../../store/uiStore";

describe("Button", () => {
  it("자식과 variant 클래스를 렌더한다", () => {
    render(<Button variant="danger">긴급정지</Button>);
    const button = screen.getByRole("button", { name: "긴급정지" });
    expect(button).toBeInTheDocument();
    expect(button).toHaveClass("ui-button--danger");
  });

  it("클릭 핸들러를 호출한다", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>확인</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("loading 이면 자동으로 비활성화되고 클릭이 무시된다", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        전송
      </Button>,
    );
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("disabled 면 클릭되지 않는다", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        불가
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("기본 type 은 submit 이 아니라 button 이다", () => {
    // 폼 안에서 의도치 않게 제출되는 사고를 막는다
    render(<Button>확인</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });
});

describe("Badge / StatusDot", () => {
  it("톤 클래스를 적용한다", () => {
    render(<Badge tone="danger">위험</Badge>);
    expect(screen.getByText("위험")).toHaveClass("ui-badge--danger");
  });

  it("blink 옵션으로 점멸 클래스를 붙인다", () => {
    render(<Badge tone="danger" blink>경보</Badge>);
    expect(screen.getByText("경보")).toHaveClass("ui-badge--blink");
  });

  it("StatusDot 은 label 이 있으면 접근 가능한 이미지가 된다", () => {
    render(<StatusDot tone="ok" label="연결됨" />);
    expect(screen.getByRole("img", { name: "연결됨" })).toBeInTheDocument();
  });
});

describe("Card", () => {
  it("제목·힌트·본문을 렌더한다", () => {
    render(
      <Card title="이벤트 큐" hint="3건">
        <p>내용</p>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: /이벤트 큐/ })).toBeInTheDocument();
    expect(screen.getByText("3건")).toBeInTheDocument();
    expect(screen.getByText("내용")).toBeInTheDocument();
  });

  it("title 이 없으면 헤더를 그리지 않는다", () => {
    render(<Card>본문만</Card>);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });
});

describe("Modal", () => {
  it("open=false 면 아무것도 렌더하지 않는다", () => {
    render(
      <Modal open={false} title="제목" onClose={() => undefined}>
        본문
      </Modal>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("dialog 역할과 제목을 노출한다", () => {
    render(
      <Modal open title="진압 확인" onClose={() => undefined}>
        본문
      </Modal>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("진압 확인");
  });

  it("Esc 로 닫힌다", async () => {
    const onClose = vi.fn();
    render(
      <Modal open title="제목" onClose={onClose}>
        본문
      </Modal>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("닫기 버튼으로 닫힌다", async () => {
    const onClose = vi.fn();
    render(
      <Modal open title="제목" onClose={onClose}>
        본문
      </Modal>,
    );
    await userEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("critical 이면 화면 테두리 경보를 함께 그린다", () => {
    const { container } = render(
      <Modal open critical title="화재 감지" onClose={() => undefined}>
        본문
      </Modal>,
    );
    expect(container.querySelector(".ui-critical-frame")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveClass("ui-modal--critical");
  });
});

describe("Toast", () => {
  it("store 에 쌓인 토스트를 렌더하고 닫을 수 있다", async () => {
    render(<ToastHost />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    act(() => {
      useUiStore.getState().pushToast({ tone: "warn", title: "연기 감지", description: "Z02" });
    });
    expect(await screen.findByText("연기 감지")).toBeInTheDocument();
    expect(screen.getByText("Z02")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "알림 닫기" }));
    await waitFor(() => expect(screen.queryByText("연기 감지")).not.toBeInTheDocument());
  });

  it("danger 토스트는 alert 역할로 즉시 읽힌다", async () => {
    render(<ToastHost />);
    act(() => {
      useUiStore.getState().pushToast({ tone: "danger", title: "화재" });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("화재");
  });

  it("duration=0 이면 자동으로 사라지지 않는다", async () => {
    vi.useFakeTimers();
    render(<ToastHost />);
    act(() => {
      useUiStore.getState().pushToast({ tone: "danger", title: "인터락 차단", duration: 0 });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    vi.useRealTimers();
    expect(screen.getByText("인터락 차단")).toBeInTheDocument();
  });
});

describe("상태 컴포넌트", () => {
  it("Skeleton 은 count 만큼 그린다", () => {
    const { container } = render(<Skeleton count={4} />);
    expect(container.querySelectorAll(".ui-skeleton")).toHaveLength(4);
  });

  it("EmptyState 는 기본 문구를 보여준다", () => {
    render(<EmptyState />);
    expect(screen.getByText("표시할 항목이 없습니다")).toBeInTheDocument();
  });

  it("ErrorState 는 alert 역할이고 재시도를 호출한다", async () => {
    const onRetry = vi.fn();
    render(<ErrorState description="네트워크 오류" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("Gauge", () => {
  it("meter 역할로 현재값을 노출한다", () => {
    render(<Gauge value={76} label="배터리" />);
    const meter = screen.getByRole("meter", { name: "배터리" });
    expect(meter).toHaveAttribute("aria-valuenow", "76");
    expect(meter).toHaveAttribute("aria-valuemax", "100");
  });

  it("값에 따라 톤이 바뀐다", () => {
    const { container: ok } = render(<Gauge value={80} />);
    expect(ok.querySelector(".ui-gauge__fill--ok")).toBeInTheDocument();

    const { container: danger } = render(<Gauge value={10} />);
    expect(danger.querySelector(".ui-gauge__fill--danger")).toBeInTheDocument();
  });

  it("max=0 이어도 NaN 을 만들지 않는다", () => {
    render(<Gauge value={5} max={0} label="빈 게이지" />);
    expect(screen.getByRole("meter", { name: "빈 게이지" })).toBeInTheDocument();
  });
});

describe("StepList", () => {
  const steps = [
    { label: "순찰 시작" },
    { label: "경로 주행" },
    { label: "존 차단기 점검" },
    { label: "경보 발령", alert: true },
    { label: "순찰 완료" },
  ];

  it("모든 스텝을 렌더하고 현재 위치를 표시한다", () => {
    render(<StepList steps={steps} current={3} />);
    steps.forEach((step) => expect(screen.getByText(step.label)).toBeInTheDocument());
    expect(screen.getByText("존 차단기 점검").closest("li")).toHaveAttribute("aria-current", "true");
  });

  it("지난 스텝은 done 으로 표시된다", () => {
    render(<StepList steps={steps} current={3} />);
    expect(screen.getByText("순찰 시작").closest("li")).toHaveClass("ui-steps__item--done");
  });

  it("current=0 이면 진행 중인 스텝이 없다", () => {
    const { container } = render(<StepList steps={steps} current={0} />);
    expect(container.querySelectorAll("[aria-current]")).toHaveLength(0);
  });

  it("경보 스텝은 alert 스타일을 쓴다", () => {
    render(<StepList steps={steps} current={4} />);
    expect(screen.getByText("경보 발령").closest("li")).toHaveClass("ui-steps__item--alert");
  });
});
