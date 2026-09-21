# Claus Testing Strategy

This document separates checks for the current runnable scaffold from future feature-level verification. Do not claim planned behavior or platform support is tested when the corresponding implementation or runtime is absent.

## Current scaffold checks

The backend baseline is Django 6 on Python 3.12–3.14. Confirm resolved dependency versions when the baseline changes.

```bash
python -m pip install -r backend/requirements.txt
python backend/manage.py check

cd backend
python manage.py test core agent
python manage.py makemigrations --check --dry-run
```

The ASGI integration tests must exercise `config.asgi.application`, including `/core/health/`, `/agent/health/`, `/agent/openapi.json`, and the removed `/api/health/` route.

Verify both server entrypoints independently:

```bash
cd backend
python manage.py runserver 127.0.0.1:8000 --noreload
uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

For each server, verify `/core/health/`, `/agent/health/`, `/agent/docs`, `/agent/openapi.json`, and `/admin/`; `/api/health/` must remain absent.

Python Playwright package installation and the Chromium binary lifecycle are separate:

```bash
playwright install chromium
```

Frontend checks remain:

```bash
cd frontend
npm run lint
npm run build
npm run test:visual
```

Frontend Playwright should verify `/` and `/console`, independent Core/Agent health status, console errors, failed network/resource requests, responsive overflow, and light/dark rendering. Environment-limited browser failures must be reported rather than treated as success.

## Topic/Thread and context testing

When collaboration features are implemented, verify:

- Personal AI context is not exposed automatically to a team context.
- Team AI receives only the permitted Topic/Thread history, Files, and Knowledge.
- Navigation preserves the intended context.
- Topics do not leak Messages, Files, Task state, or retrieval results into each other.

## File, Artifact, and RAG testing

When these features are implemented, verify:

- Upload, download, and preview permissions.
- Personal versus team visibility.
- File upload does not automatically create team/organization Knowledge.
- Explicit indexing preserves source and scope metadata.
- Artifact provenance remains traceable to the source Task/context.
- Runtime-local temporary Files do not become persistent automatically.

## Background Task testing

When background execution is implemented, verify:

- Long-running work does not block the requesting conversation.
- Product Task state transitions are observable and consistent.
- Each agent execution attempt remains distinct from its product Task.
- Cancellation and failure do not corrupt conversation or persistent Files.
- Persistent output returns explicitly as a Message, File, Artifact, or Task result.
- Approval-required actions stop for the required human decision.

## Browser Computer Use testing

The current repository contains only the async Python Playwright package boundary, not Browser Computer Use product behavior. When implemented, verify:

- Session start and stop cleanup.
- Task/context association.
- Prevention of cross-context session access.
- Actions operate on the intended page and browser state.
- User/AI control handoff is exclusive and visible.
- Screenshots are generated and reviewed.
- Browser console errors and failed network/resource requests are checked.

## Shared result and view testing

When shared presentation is implemented, verify:

- The correct Artifact or live session is displayed.
- Intended presentation state remains consistent.
- Closing a shared view does not delete the underlying Artifact.
- Responsive layouts avoid unintended overflow and clipping.
- Supported light and dark modes remain usable.

## API testing

For future API changes, cover:

- Normal requests
- Missing required input and invalid formats
- Authentication and authorization failures
- Missing resources and boundary values
- Expected status codes and response schemas
- Stable error formats
- Intended state changes and regression risk

Do not issue state-changing requests against production without explicit approval or place real secrets in test assets.

## Free-threading compatibility testing

When a suitable environment and dependency set are available:

- Run relevant tests with the GIL disabled.
- Run a GIL-enabled compatibility baseline.
- Exercise concurrent access rather than assuming serialization.
- Verify native and third-party dependency compatibility.
- Record what was actually tested; do not infer support from static review.

## Change-scope checks

For documentation or scaffold work, also verify changed links and paths, stale naming, `git diff --check`, absence of secrets/generated artifacts, and that planned features remain clearly distinguished from implemented behavior. Do not introduce domain models, custom migrations, infrastructure, or unrelated frameworks as incidental test work.
