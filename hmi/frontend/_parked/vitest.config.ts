import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // 각 테스트 파일이 독립된 모듈 그래프를 갖게 한다.
    // store 는 모듈 스코프 싱글턴이라, 공유하면 파일 간 상태가 새어 나간다.
    isolate: true,
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/test/**", "src/**/*.test.{ts,tsx}", "src/main.tsx"],
    },
  },
});
