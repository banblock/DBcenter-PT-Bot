import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom 에 없는 것들 — 컴포넌트가 이걸 호출해 터지지 않게 최소 스텁을 둔다.
if (!("matchMedia" in window)) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

if (!("AudioContext" in window)) {
  // CriticalAlertModal 의 경고음 — 테스트에서는 조용히 무시된다.
  Object.defineProperty(window, "AudioContext", { writable: true, value: undefined });
}

if (!("scrollIntoView" in Element.prototype)) {
  Element.prototype.scrollIntoView = () => undefined;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
