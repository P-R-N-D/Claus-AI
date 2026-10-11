# Claus Frontend

The frontend uses Next.js 16 App Router, React 19.3, TypeScript 6.0, Tailwind CSS 4, axios, SweetAlert2, Node `@playwright/test`, and Vitest with jsdom and React Testing Library for unit tests. Node 24.21.0 is pinned in the root `.nvmrc`; `packageManager` records npm 11.21.0. ESLint 9 is a temporary EOL compatibility exception, and development-tool advisories remain; see [the dependency strategy](../docs/DEPENDENCY-STRATEGY.md#compatibility-and-security-exceptions).

- `/` is the user surface, implemented by the `(user)` route group (which adds no URL segment).
- `/console` is the separate Claus product-operations surface, reserved as `/console/*`; only the `/console` index page exists today, and it is not Django Admin.
- `/core/*` and `/agent/*` are rewritten to the backend at `127.0.0.1:8000`, preserving trailing slashes. Django keeps its `APPEND_SLASH` redirects, whose relative `Location` stays on the frontend origin (`/core/health` to `/core/health/`). The agent FastAPI app returns 404 for a trailing-slash mismatch such as `/agent/health` or `/agent/docs/` instead of redirecting, because its redirect would be an absolute URL to the backend origin.
- UI trailing slashes retain their permanent (308) redirect, such as `/console/` to `/console`, with query parameters preserved by `src/proxy.ts`. Its matcher skips Next internals (`/_next/*`) and the exact `/core/` and `/agent/` prefixes, which go straight to the rewrites; similarly named UI paths such as `/core-ui/` follow the normal redirect. Leading slashes in the redirect target are collapsed, so the `Location` can never be scheme-relative (`//host`).
- `coreApi` and `agentApi` keep those URL contracts separate, and the home scaffold checks both health endpoints.
- The root layout hard-codes `<html lang="en">`. No i18n library is installed; the planned direction is in [docs/I18N.md](../docs/I18N.md).
- No WebMCP or agent-tool code exists; see [docs/INTERACTION-INTERFACES.md](../docs/INTERACTION-INTERFACES.md) and [docs/WEBMCP.md](../docs/WEBMCP.md) before proposing any. `next.config.ts` sets `experimental.mcpServer: false`, so `next dev` does not serve Next 16's unauthenticated dev-only MCP endpoint at `/_next/mcp` (`next start` never serves it), and `agentRules: false`, so `next dev` does not generate an agent-rules block in `frontend/AGENTS.md`; AI-facing instructions stay in [docs/CONTEXT.md](../docs/CONTEXT.md).

```bash
# From the repository root, using an nvm-managed Node installation, in a POSIX shell (Linux, macOS, or WSL2):
nvm install
nvm use
npm install --global npm@11.21.0
source .venv/bin/activate
cd frontend
npm ci
npx next typegen
npx playwright install chromium
npm run lint
npm run test:unit
npm run test:visual
npm run dev -- --port 3000
```

Create the backend environment first using [backend/README.md](../backend/README.md); Playwright starts the backend with `python`, so activate it in this terminal. In Windows PowerShell the activation is `.venv\Scripts\Activate.ps1`, but native Windows cannot produce a working backend environment from the Linux-resolved lock today, so run these steps in WSL2 (Ubuntu); see [Platform and accelerator lanes](../docs/DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes).

The `npm install --global npm@11.21.0` step is required: Node 24.21.0 bundles npm 11.19.0, and the `engines` pin only warns (`EBADENGINE`) instead of stopping `npm ci`. `frontend/next-env.d.ts` is generated and not tracked; `npx next typegen` (or `npm run dev` or `npm run build`) writes it; running it on a fresh clone is recommended before `tsc` or editor type checking. `npm run dev` binds to 127.0.0.1; use `npm run dev -- --hostname 0.0.0.0` only deliberately, for example to test from a phone on the local network.

`npm run test:unit` runs 13 Vitest tests in `tests/unit/` with jsdom and React Testing Library: the `coreApi` and `agentApi` URL and timeout contracts, and the `HealthCard` states, retry alerts, the in-flight retry guard, and the guard against superseded results. The `HealthCard` tests mock `@/lib/api` and `sweetalert2`; the API client tests use the real clients with a local axios adapter. No backend, network, or browser is needed. `vitest.config.mts` includes only `tests/unit/**/*.test.{ts,tsx}`, so the Playwright files are never collected.

`npm run test:visual` runs 12 tests: the `home.spec.ts` scenario and two browserless `proxy.spec.ts` unit tests, each in four projects (desktop/mobile × light/dark). The scenario checks the redirect and 404 contracts, the disabled `/_next/mcp` endpoint, and health retries (Retry `aria-disabled` with `aria-busy`, keeping keyboard focus, while a check is in flight); it rejects horizontal overflow, measured as `documentElement.scrollWidth - documentElement.clientWidth` with a self-check that proves the measurement can fail, and console/page/network errors; and it saves screenshots in `test-results/`. The proxy tests call `proxy()` directly, including with `//evil.example/`, and check the matcher. The default mode starts `next dev`, so no `npm run build` is needed first, and outside CI it reuses servers already listening on ports 3000 and 8000. `PLAYWRIGHT_NEXT_SERVER=production npm run test:visual` rebuilds, runs `next start`, and never reuses a running backend or Next.js server. For evidence runs, set `CI=1`, which disables reuse of both servers, and record the server mode. Review screenshots as described in [TESTING.md](../docs/TESTING.md).

If browser downloads are blocked, explicitly set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a system Chromium executable and report that browser's version. This does not verify the paired Playwright browser. On native Windows on Arm, Playwright ships only x64 browsers, which run under emulation, so such a run is not native Arm evidence; WSL2 (Ubuntu) on those machines uses Linux arm64 Chromium instead. Backend Python Playwright is a separate product-runtime dependency with its own browser lifecycle.
