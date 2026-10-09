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
| `frontend/tests/visual/home.spec.ts` | One scenario in four Chromium projects (desktop/mobile × light/dark): health/OpenAPI proxy URLs return 200 without redirects; UI trailing-slash redirects preserve query parameters and do not exempt similar non-backend prefixes; FastAPI docs retain their own 307 redirect; `/` and `/console` render, including navigation through `/console/`; both health cards show their expected JSON and retry successfully; no horizontal page overflow, console/page errors, failed requests, or HTTP errors. Saves a full-page screenshot of each surface. |

No test covers authentication, authorization, scope separation, approval, Tasks, retrieval, Browser sessions, realtime, i18n, or WebMCP, because none of those features exist.

## Current scaffold checks

The checked backend baseline is Django 6.1 on CPython 3.12.15, Linux x86-64. Use uv 0.12.24 and the matching hash lock; [backend/locks/README.md](../backend/locks/README.md) describes regeneration, artifact evidence, and other-platform limits.

```bash
uv venv --python 3.12.15
source .venv/bin/activate
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
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
npm ci
npx playwright install chromium
npm run lint
npm run build
npm run test:visual
```

Use Node 24.21.0 and npm 11.21.0, and activate the backend environment in the terminal that starts Playwright: its web-server command invokes `python`. Playwright starts the local servers unless they are already running outside CI. The four projects check desktop/mobile widths, light/dark rendering, retries, horizontal overflow, and network/page/console failures. Inspect the saved screenshots for clipping and visual regressions; accessibility and pixel-diff baselines remain unimplemented.

The default browser is the binary paired with `@playwright/test`. If its download is unavailable, `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/absolute/path/to/chromium` explicitly selects a separately installed browser. Record its version and the failed download; that run is supplemental UI evidence, not qualification of Playwright's paired browser. Environment-limited failures must never be reported as passing. The dated results and remaining checks are in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md#applied-baseline-and-verification).

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

The architecture already requires free-threading compatibility. [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md) defines Python 3.15 Limited API/`abi3t` artifact selection, the dated wheel gaps, and performance admission budgets. These qualification checks and benchmarks are planned; the current test suite does not establish Python 3.15/3.15t support.

When a suitable environment and dependency set are available:

- Use separate environments for the qualified baseline, regular `cp315`, and free-threaded `cp315t`, with each lane's resolved lock, platform artifacts, and installer versions recorded.
- Check actual supported wheel tags and binary-only installation, including `abi3t` and dual `abi3.abi3t` wheels. A cross-version resolver probe or `py3-none-any` wheel is not runtime/thread-safety evidence.
- Verify `Py_GIL_DISABLED` and actual GIL state before and after imports, lazy initialization, and workloads; detect automatic GIL re-enablement instead of masking it with a forced-off flag.
- Run the existing backend checks and both ASGI server entrypoints on each candidate. Exercise concurrency, cancellation, GC/shutdown, storage collisions, and context propagation; database and real object-storage/browser integrations need separate environments and evidence.
- Compare Limited API versus version-specific artifacts on the same runtime separately from regular-versus-free-threaded runtime performance. Apply the strategy's workload, repetition, latency, throughput, and memory criteria.
- Preserve the known-good GIL-enabled fallback and record failed or unavailable lanes. No synthetic tag check, wheel inventory, or benchmark of only health endpoints qualifies the full stack.

## Security-negative testing

Every feature that introduces authentication, authorization, scope checks, approvals, or agent-originated actions must ship with tests that prove denial, not only success. Minimum cases, mapped to the requirement IDs in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md):

- Unauthenticated and unauthorized requests are rejected with the intended status and no side effect (`SEC-AUTH-001`, `SEC-AUTH-003`, `SEC-FAIL-001`).
- A user or AI participant in one scope cannot read, retrieve, or act on another scope's Files, Knowledge, Tasks, or Artifacts (`SEC-SCOPE-001` to `SEC-SCOPE-003`).
- Personal material is invisible to a team context until an explicit share, and the share is recorded (`SEC-SHARE-001`, `SEC-SHARE-002`).
- Upload does not index; indexing is explicit and scoped (`SEC-RAG-001`, `SEC-RAG-003`).
- Instructions embedded in retrieved or external content do not change what the system does (`SEC-INJ-001`, `SEC-INJ-002`).
- A member's direct request to a shared AI in a team context runs with that member as the actor and is not refused as untrusted content; the same request from someone without access to the context is denied; a request to read any member's personal context, the requester's own included, returns nothing; a message from an AI participant is not treated as a direct request; the reply of the member the AI asked, accepting its proposal ("yes"), is a direct request for exactly the operation and targets the AI showed, nothing more, and still passes server authorization, classification, and approval; another member's reply, or a reply that declines or asks back, requests nothing (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-SCOPE-002`, `SEC-SCOPE-003`).
- An operation, target, recipient, or scope that appears only in quoted, attached, retrieved, or web content inside a direct request, and that the request neither names nor designates, causes no state change; values the request designates ("invite the addresses in this file") are used as data and still pass authorization, classification, and approval; no message overrides authorization, classification, or approval requirements (`SEC-INJ-001`, `SEC-INJ-002`).
- A run under a valid standing authorization, such as a scheduled Task, executes the operations its stored definition covers, within its targets and scope, with no new message from a person; a run with no authorization, or with one that was revoked or is no longer valid, executes nothing; an operation, target, recipient, or scope outside the definition is refused; a forged trigger, or an event payload that claims a role, a permission, or an approval, widens nothing; retrieved, attached, or tool-output text read during the run changes neither the definition nor what it targets; permissions or scope narrowed before a run narrow or stop it; a consequential operation the run reaches waits for its own approval, and setting up or scheduling the automation is not accepted as one (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-AUTH-001`, `SEC-SCOPE-003`, `SEC-APPROVE-001`).
- Agent-originated requests receive the same authorization result as the Human UI for the same actor and operation, and the recorded origin changes nothing (`SEC-AGENT-001`).
- Requests under one browser session from the Human UI and from a WebMCP tool get the same authorization result for the same actor, scope, and operation; a forged `origin` is recorded at most as client-reported and grants nothing; an AI participant name or `ai_participant_ref` sent by a client or an external browser agent is not accepted as an AI participant and grants no participant's permissions, while an AI participant the server runs keeps its own effective permissions; a call from the browser session with no verifiable delegation runs as the user alone, with no AI participant; the audit record marks a client-reported origin as such (`SEC-AGENT-001`, `SEC-AGENT-003`).
- A presigned URL is only issued to an authorized requester and expires (`SEC-FILE-005`).

## Personal and team boundary testing

Extend the Topic/Thread cases above with explicit negative tests: a shared AI participant asked about a member's personal conversation returns nothing from it; cross-Topic retrieval returns nothing; navigation between personal and team surfaces does not carry drafts, files, or retrieval context across.

## Approval and retry testing

When approval, background execution, or any state-changing operation that takes an `operation_id` exists, verify:

- A high-impact (consequential) operation performs no side effect before a bound approval: a call carried by a Task stops in `waiting_for_approval` until the decision, and a person's own Human UI action without a Task executes only after that person's confirmation is recorded for its `operation_id`; neither the agent that requested or runs the operation nor an agent acting in the person's browser session completes the approval step by itself, and a UI click, an agent's reply, or an approval flag sent by a client alone is not accepted as a verified approval; denial and timeout leave no side effect (`SEC-APPROVE-001`, `SEC-APPROVE-003`).
- An ordinary mutation, such as a settings change or a draft, completes with server-side authorization and no approval step; an annotation, prompt, or origin neither adds nor removes an approval requirement (`SEC-APPROVE-001`, `SEC-AGENT-004`).
- An approval is recorded with the `operation_id`, actor, target, parameters, and scope it covers and cannot be reused for a different operation, target, parameters, scope, or actor, or for another `operation_id` except after a recorded failure under an explicit re-execution policy; a repeat of an approved request with the same `operation_id` returns the recorded outcome without executing again; executing an approved operation again after a recorded failure takes a new `operation_id` and a new approval unless a re-execution policy allows it (`SEC-APPROVE-002`, `SEC-IDEM-003`).
- Requests with the same `operation_id` and binding, sent one after the other and concurrently, execute once and return the recorded outcome, each as its own attempt record; the same id with a different input is rejected with `idempotency_conflict` and leaves the bound operation unchanged; the same id from another actor neither returns nor reveals the first actor's operation; after a confirmed `not_executed`, a new `operation_id` executes; a new `operation_id` with identical input executes again; a request that claims a new id and is rejected records `not_executed`, which a late duplicate receives (`SEC-IDEM-001`, `SEC-IDEM-002`).
- When the response is lost after the server applied a change, the caller reports `outcome_unknown` and recovers the recorded outcome by status lookup or by retrying with the same `operation_id`, without a second side effect; a lookup that finds no record is not treated as not executed; a retry under an id already sent that is stopped before sending, or refused for that request alone, is reported as `outcome_unknown`, never `not_executed`; a failure recorded with effects remaining returns `outcome_unknown` with `partially_applied` on every repeat and lookup and is not retried (`SEC-IDEM-005`).
- After a reload, a caller with no local record of the id recovers the `operation_id` through a status lookup; a retry under it with the same actor, AI participant, context, operation, and input returns the recorded outcome with no second side effect; the same id with another input is rejected with `idempotency_conflict`; another actor's id, an id in a context the actor can no longer access, and an id the server has no record of each reveal nothing, execute nothing, and bind nothing; when several entries could be the lost operation, the caller picks none and starts nothing new; while the operation stays unresolved, no new `operation_id` is issued and nothing executes until the person requests it anew, the message to the person says that the earlier operation may have taken effect or may still take effect, and a consequential operation requested anew does not execute without its state check and its own approval (`SEC-IDEM-001`, `SEC-IDEM-003`, `SEC-IDEM-005`, `SEC-SCOPE-003`).
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
- After a reload, an `operation_id` recovered through the status tool and unknown to the new page is resolved by the server: with the same input it returns the recorded outcome with one side effect in total, and an id the server has no record of binds nothing.
- Pages that expose no tools send `Permissions-Policy: tools=()`; the audit record carries the user as actor, origin `webmcp` marked client-reported, no AI participant, and the `operation_id`. The full list is in [WEBMCP.md](WEBMCP.md) "Verification requirements".

## Change-scope checks

For documentation or scaffold work, also verify changed links and paths, stale naming, `git diff --check`, absence of secrets/generated artifacts, and that planned features remain clearly distinguished from implemented behavior. Do not introduce domain models, custom migrations, infrastructure, or unrelated frameworks as incidental test work. The review procedure that consumes these checks is [CODE-REVIEW.md](CODE-REVIEW.md); security-sensitive changes also go through [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
