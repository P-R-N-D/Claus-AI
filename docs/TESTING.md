# Claus Testing Strategy

This document separates checks for the current runnable scaffold from future feature-level verification. Do not claim planned behavior or platform support is tested when the corresponding implementation or runtime is absent.

## Tests that exist today

The repository contains these automated tests. Their existence is a fact; whether they pass must be shown by running them and citing the output.

| Location | What it covers |
|---|---|
| `backend/core/tests.py` | `GET /core/health/` returns 200 and the expected JSON. |
| `backend/core/test_database.py` | `DATABASE_URL` parsing: SQLite fallback for unset/blank values, PostgreSQL URL parsing with percent-decoding and query preservation, `postgres` alias and default port, nine invalid URLs rejected with `ImproperlyConfigured`; `SECRET_KEY` placeholder fallback. |
| `backend/core/test_storage.py` | `S3CompatibleStorage` contract against a fake S3 client: default backend wiring, save/open/exists/size/delete/url, binary-only reads, content type handling, collision retry (including `max_length` and concurrent saves), no implicit overwrite, missing-object errors, dangerous object names rejected, lazy and clear configuration errors, path-style SigV4 client, endpoint validation. Not an integration test against a real object store. |
| `backend/agent/tests.py` | ASGI routing through `config.asgi.application`: `/core/health/`, `/agent/health/`, `/agent/openapi.json` are 200; `/api/health/` is 404. |
| `frontend/tests/visual/home.spec.ts` | One Chromium test: `/` shows the heading, both health cards report Connected with the expected JSON fragments, a full-page screenshot is non-empty; `/console` shows its label text and heading; no console errors were logged. |

No test covers authentication, authorization, scope separation, approval, Tasks, retrieval, Browser sessions, realtime, i18n, or WebMCP, because none of those features exist.

## Current scaffold checks

The backend baseline is Django 6 on Python 3.12 or newer (Django 6.0 officially supports 3.12 through 3.14). Confirm resolved dependency versions when the baseline changes.

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

For each server, verify `/core/health/`, `/agent/health/`, `/agent/docs`, `/agent/openapi.json`, `/agent/redoc` (FastAPI's default, not set in code), and `/admin/`; `/api/health/` must remain absent.

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

Frontend Playwright coverage today is the single `home.spec.ts` test described above: `/` and `/console` render, both health cards are Connected, and no console errors occur, on one desktop Chromium viewport. It does not check failed network or resource requests, responsive viewports or overflow, light and dark rendering, or accessibility. Those remain required for UI changes that affect rendered behavior or appearance and must be added or run ad hoc, with the result reported. Environment-limited browser failures must be reported rather than treated as success.

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

## Security-negative testing

Every feature that introduces authentication, authorization, scope checks, approvals, or agent-originated actions must ship with tests that prove denial, not only success. Minimum cases, mapped to the requirement IDs in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md):

- Unauthenticated and unauthorized requests are rejected with the intended status and no side effect (`SEC-AUTH-001`, `SEC-AUTH-003`, `SEC-FAIL-001`).
- A user or AI participant in one scope cannot read, retrieve, or act on another scope's Files, Knowledge, Tasks, or Artifacts (`SEC-SCOPE-001` to `SEC-SCOPE-003`).
- Personal material is invisible to a team context until an explicit share, and the share is recorded (`SEC-SHARE-001`, `SEC-SHARE-002`).
- Upload does not index; indexing is explicit and scoped (`SEC-RAG-001`, `SEC-RAG-003`).
- Instructions embedded in retrieved or external content do not change what the system does (`SEC-INJ-001`, `SEC-INJ-002`).
- A member's direct request to a shared AI in a team context runs with that member as the actor and is not refused as untrusted content; the same request from someone without access to the context is denied; a request to read any member's personal context, the requester's own included, returns nothing; a message from an AI participant is not treated as a direct request; the reply of the member the AI asked, accepting its proposal ("yes"), is a direct request for exactly the operation and targets the AI showed, nothing more, and still passes server authorization, classification, and approval; another member's reply, or a reply that declines or asks back, requests nothing (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-SCOPE-002`, `SEC-SCOPE-003`).
- An operation, target, recipient, or scope that appears only in quoted, attached, retrieved, or web content inside a direct request, and that the request neither names nor designates, causes no state change; values the request designates ("invite the addresses in this file") are used as data and still pass authorization, classification, and approval; no message overrides authorization, classification, or approval requirements (`SEC-INJ-001`, `SEC-INJ-002`).
- Agent-originated requests receive the same authorization result as the Human UI for the same actor and operation, and the recorded origin changes nothing (`SEC-AGENT-001`).
- A presigned URL is only issued to an authorized requester and expires (`SEC-FILE-005`).

## Personal and team boundary testing

Extend the Topic/Thread cases above with explicit negative tests: a shared AI participant asked about a member's personal conversation returns nothing from it; cross-Topic retrieval returns nothing; navigation between personal and team surfaces does not carry drafts, files, or retrieval context across.

## Approval and retry testing

When approval, background execution, or any state-changing operation that takes an `operation_id` exists, verify:

- A high-impact (consequential) operation performs no side effect before a bound approval: a call carried by a Task stops in `waiting_for_approval` until the decision, and a person's own Human UI action without a Task executes only after that person's confirmation is recorded for its `operation_id`; denial and timeout leave no side effect (`SEC-APPROVE-001`, `SEC-APPROVE-003`).
- An ordinary mutation, such as a settings change or a draft, completes with server-side authorization and no approval step; an annotation, prompt, or origin neither adds nor removes an approval requirement (`SEC-APPROVE-001`, `SEC-AGENT-004`).
- An approval cannot be reused for a different operation, parameters, or actor, or for another `operation_id` except after a recorded failure under an explicit re-execution policy; a repeat of an approved request with the same `operation_id` returns the recorded outcome without executing again; executing an approved operation again after a recorded failure takes a new `operation_id` and a new approval unless a re-execution policy allows it (`SEC-APPROVE-002`, `SEC-IDEM-003`).
- Requests with the same `operation_id` and binding, sent one after the other and concurrently, execute once and return the recorded outcome, each as its own attempt record; the same id with a different input is rejected with `idempotency_conflict` and leaves the bound operation unchanged; the same id from another actor neither returns nor reveals the first actor's operation; after a confirmed `not_executed`, a new `operation_id` executes; a new `operation_id` with identical input executes again; a request that claims a new id and is rejected records `not_executed`, which a late duplicate receives (`SEC-IDEM-001`, `SEC-IDEM-002`).
- When the response is lost after the server applied a change, the caller reports `outcome_unknown` and recovers the recorded outcome by status lookup or by retrying with the same `operation_id`, without a second side effect; a lookup that finds no record is not treated as not executed; a retry under an id already sent that is stopped before sending, or refused for that request alone, is reported as `outcome_unknown`, never `not_executed`; a failure recorded with effects remaining returns `outcome_unknown` with `partially_applied` on every repeat and lookup and is not retried (`SEC-IDEM-005`).
- Storage collision and retry behavior stays correct under concurrency (existing `test_storage.py` cases are the baseline).

## Internationalization testing

Nothing is implemented; when [I18N.md](I18N.md) is implemented, verify:

- Locale resolution order (account setting, cookie, environment language, English fallback) with each source present and absent, and with invalid values rejected.
- Server and client render the same locale (no hydration mismatch) and `<html lang>` matches.
- Every Korean message has an English source and matching placeholders and the plural branches its locale requires; missing Korean falls back to English by the documented policy.
- Dates, times, and numbers use the configured time zone and locale.
- IDs, enum values, permission names, error codes, and WebMCP tool names are not translated.
- Playwright renders `/` and `/console` in both locales with `<html lang>` matching the resolved locale, no console errors, and no overflow from longer or shorter labels, in light and dark color schemes.

## WebMCP testing

Nothing is implemented, and the API is experimental; when a tool is proposed under [WEBMCP.md](WEBMCP.md), verify in a browser build that actually exposes `document.modelContext`:

- Feature detection: the Human UI works identically when the API is absent.
- Registration failure (duplicate `name`, invalid `name`, empty `description`, non-serializable `inputSchema`, `Permissions-Policy: tools=()`) is handled per tool without affecting other tools or the UI.
- Context switch and logout unregister tools, and in-flight executions are validated against the current context before any server call and again before returning a result: a read that completes after the switch returns no data from the old context, and any server answer that arrives after the switch returns only a safe summary, and the status tool returns its details only when called from the owning context.
- The server rejects a tool-originated operation that the same user could not perform from the UI, and records the origin.
- Aborting `executeTool()` while `execute` has not yet sent the request (it is awaiting earlier work) makes it send nothing; any abort rejects `executeTool()` with the abort reason; aborting after the request left leaves the change standing, and the status tool reports the actual outcome with one side effect.
- A response dropped after the server applied a change returns `outcome_unknown`; a retry with the returned `operation_id` returns the recorded outcome with one side effect; the same id sent concurrently executes once.
- Pages that expose no tools send `Permissions-Policy: tools=()`; the audit record carries origin `webmcp` and the `operation_id`. The full list is in [WEBMCP.md](WEBMCP.md) "Verification requirements".

## Change-scope checks

For documentation or scaffold work, also verify changed links and paths, stale naming, `git diff --check`, absence of secrets/generated artifacts, and that planned features remain clearly distinguished from implemented behavior. Do not introduce domain models, custom migrations, infrastructure, or unrelated frameworks as incidental test work. The review procedure that consumes these checks is [CODE-REVIEW.md](CODE-REVIEW.md); security-sensitive changes also go through [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
