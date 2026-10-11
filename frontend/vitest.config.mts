import { defineConfig } from "vitest/config";

// Unit tests only. Playwright owns tests/visual, so the include list stays
// explicit and Vitest never collects those files.
export default defineConfig({
  resolve: {
    // Resolves the "@/*" path alias from tsconfig.json.
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
