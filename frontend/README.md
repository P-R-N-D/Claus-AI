# Claus Frontend

The frontend preserves Next.js App Router, React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node `@playwright/test`.

- `/` is the user surface, implemented by the `(user)` route group (which adds no URL segment).
- `/console` is the separate Claus product-operations surface, reserved as `/console/*`; only the `/console` index page exists today, and it is not Django Admin.
- `/core/*` and `/agent/*` are rewritten to the backend at `127.0.0.1:8000` in local development.
- `coreApi` and `agentApi` keep those URL contracts separate, and the home scaffold checks both health endpoints.
- The root layout hard-codes `<html lang="en">`. No i18n library is installed; the planned direction is in [docs/I18N.md](../docs/I18N.md).
- No WebMCP or agent-tool code exists; see [docs/INTERACTION-INTERFACES.md](../docs/INTERACTION-INTERFACES.md) and [docs/WEBMCP.md](../docs/WEBMCP.md) before proposing any.

```bash
cd frontend
npm install
npm run lint
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Node Playwright remains UI/E2E/visual/integration test infrastructure. The only test today, `tests/visual/home.spec.ts`, checks that `/` and `/console` render with both backend health cards Connected and no console errors on one desktop Chromium viewport; responsive, theme, and failed-request checks are not yet automated (see [docs/TESTING.md](../docs/TESTING.md)). Backend Python Playwright is a separate product-runtime dependency for future Agent Browser Computer Use.
