# Claus Frontend

The frontend preserves Next.js App Router, React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node `@playwright/test`.

- `/` is the user surface, implemented by the `(user)` route group (which adds no URL segment).
- `/console/*` is the separate Claus product-operations surface; it is not Django Admin.
- `/core/*` and `/agent/*` are rewritten to the backend at `127.0.0.1:8000` in local development.
- `coreApi` and `agentApi` keep those URL contracts separate, and the home scaffold checks both health endpoints.

```bash
cd frontend
npm install
npm run lint
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Node Playwright remains UI/E2E/visual/integration test infrastructure. Backend Python Playwright is a separate product-runtime dependency for future Agent Browser Computer Use.
