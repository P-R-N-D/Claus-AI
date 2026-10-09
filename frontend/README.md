# Claus Frontend

The frontend uses Next.js 16 App Router, React 19.3, TypeScript 6.0, Tailwind CSS 4, axios, SweetAlert2, and Node `@playwright/test`. Node 24.21.0 is pinned in the root `.nvmrc`; `packageManager` records npm 11.21.0. ESLint 9 is a temporary EOL compatibility exception, and development-tool advisories remain; see [the dependency strategy](../docs/DEPENDENCY-STRATEGY.md#compatibility-and-security-exceptions).

- `/` is the user surface, implemented by the `(user)` route group (which adds no URL segment).
- `/console` is the separate Claus product-operations surface, reserved as `/console/*`; only the `/console` index page exists today, and it is not Django Admin.
- `/core/*` and `/agent/*` are rewritten to the backend at `127.0.0.1:8000`, preserving trailing slashes and letting Django/FastAPI handle their own canonical URLs.
- `coreApi` and `agentApi` keep those URL contracts separate, and the home scaffold checks both health endpoints.
- The root layout hard-codes `<html lang="en">`. No i18n library is installed; the planned direction is in [docs/I18N.md](../docs/I18N.md).
- No WebMCP or agent-tool code exists; see [docs/INTERACTION-INTERFACES.md](../docs/INTERACTION-INTERFACES.md) and [docs/WEBMCP.md](../docs/WEBMCP.md) before proposing any.

```bash
# From the repository root, using an nvm-managed Node installation:
nvm install
nvm use
npm install --global npm@11.21.0
source .venv/bin/activate
cd frontend
npm ci
npx playwright install chromium
npm run lint
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Create the backend environment first using [backend/README.md](../backend/README.md). The test scenario runs four projects (desktop/mobile × light/dark), checks proxy responses and health retries, rejects horizontal overflow and console/page/network errors, and saves screenshots in `test-results/`. Review screenshots as described in [TESTING.md](../docs/TESTING.md).

If browser downloads are blocked, explicitly set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a system Chromium executable and report that browser's version. This does not verify the paired Playwright browser. Backend Python Playwright is a separate product-runtime dependency with its own browser lifecycle.
