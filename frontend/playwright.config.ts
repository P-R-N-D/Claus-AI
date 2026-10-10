import { defineConfig, devices } from "@playwright/test";

const nextServerMode = process.env.PLAYWRIGHT_NEXT_SERVER || "dev";
if (nextServerMode !== "dev" && nextServerMode !== "production") {
  throw new Error(`PLAYWRIGHT_NEXT_SERVER must be "dev" or "production", got "${nextServerMode}"`);
}
// Production mode always rebuilds and never reuses running servers, so results describe this checkout.
const production = nextServerMode === "production";

export default defineConfig({
  testDir: "./tests/visual",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    },
  },
  projects: (["light", "dark"] as const).flatMap((colorScheme) => [
    {
      name: `desktop-${colorScheme}`,
      use: { ...devices["Desktop Chrome"], colorScheme },
    },
    {
      name: `mobile-${colorScheme}`,
      use: { ...devices["Pixel 7"], colorScheme },
    },
  ]),
  webServer: [
    {
      command: "python ../backend/manage.py runserver 127.0.0.1:8000 --noreload",
      url: "http://127.0.0.1:8000/core/health/",
      reuseExistingServer: !process.env.CI && !production,
      timeout: 60_000,
    },
    {
      command: production
        ? "npm run build && npm run start -- --hostname 127.0.0.1 --port 3000"
        : "npm run dev -- --port 3000",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: !process.env.CI && !production,
      timeout: production ? 300_000 : 60_000,
    },
  ],
});
