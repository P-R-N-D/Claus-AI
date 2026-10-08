# Claus Security Architecture

This document defines the trust boundaries of Claus and the `SEC-*` security requirements that features, reviews, and tests map to. It is the only place where `SEC-*` identifiers are defined. It also records the security-relevant facts of the code that exists today, with file and line evidence.

It is not a vulnerability reporting policy, not a review procedure (see [SECURITY-REVIEW.md](SECURITY-REVIEW.md) and [CODE-REVIEW.md](CODE-REVIEW.md)), and not a claim that the requirements are met. Read [CONTEXT.md](CONTEXT.md) first; its rules are not repeated here.

## Status

- The repository implements health endpoints, the ASGI composition, `DATABASE_URL` parsing with SQLite fallback, and the S3-compatible storage backend. [ARCHITECTURE.md](ARCHITECTURE.md) keeps the implemented versus planned breakdown.
- No authentication flow, authorization check, scope model, sharing action, approval workflow, or audit trail exists for product features. There are no collaboration domain models. Requirements in those areas are Planned and describe target behavior only.
- Requirements marked Implemented are backed by repository code and, except `SEC-SECRET-001` (file evidence only), by tests that exist in the repository; the tests were not executed in this documentation pass.
- WebMCP-related requirements are Planned and Experimental: WebMCP is a draft Community Group report, and no status-page entry reported default-on support in a stable browser release as of 2026-10-08 (see [WEBMCP.md](WEBMCP.md)).

Status words: Implemented, Partial, Planned, Experimental. Partial means supporting code exists but the requirement is not enforced. Fact/Partial marks a requirement that records a fact about current code with no product behavior behind it.

## Trust boundaries

Planned. Except where a bullet says "Today", the actors and boundaries below are the target model; see Status.

### Actors

- **User**: an authenticated person acting through the Human UI, or later through automation on their behalf. The only source of intent and approval.
- **AI participant**: a personal or shared AI acting inside one `CollaborationContext` ([STATE-SCHEMA.md](STATE-SCHEMA.md)) with its own effective permissions, never wider than the context allows.
- **Browser agent or extension**: a WebMCP-capable agent running in the user's browser. It sees only what the page exposes and is untrusted by the server. Experimental.
- **External content**: files, web pages, retrieved chunks, tool outputs, and messages from other users. Untrusted input, never instructions.
- **External tools**: third-party APIs called with server-held credentials. Their outputs are external content.
- **Runtimes**: Browser, Terminal, and Workspace execution environments. Task-scoped, isolated, without persistent authority.

### Boundaries

```text
Browser: Human UI, browser agent/extension           untrusted client
        | HTTP; CORS allowlist; session and auth (Planned)
Control plane: Django /core/*, /admin/*              source of truth
        | in-process mount; Django middleware does not apply
Agent surface: FastAPI /agent/*                      execution interface
        | environment-configured clients
Stores: PostgreSQL or SQLite, S3-compatible objects  trusted stores
        | task-scoped isolation (Planned)
Runtimes: Browser, Terminal, Workspace               untrusted output
```

- **Browser <-> control plane**: every request is untrusted until the server authenticates the actor and authorizes the operation. Client-side checks are UX only. Today this boundary carries a CORS allowlist and the middleware stack listed in `settings.py:32-41`; no login or product operation exists.
- **Control plane <-> agent surface**: Django `core` owns users, permissions, contexts, Files, Knowledge scope, and Task state. The FastAPI app under `/agent` is mounted beside Django in the same ASGI process and receives none of Django's middleware. Identity and authorization must flow from the control plane; the agent surface must never become an independent authority.
- **Control plane <-> storage and database**: connections are configured from the environment and validated at startup (database URL) or at first use (object storage). A presigned object URL is a bearer capability; issuing one is an authorization decision.
- **Runtimes <-> everything**: Browser, Terminal, and Workspace runtimes must hold no long-lived credentials, must not reach other contexts, and must return results only through authorized writes of Messages, Files, Artifacts, or Task state.
- **Model <-> content**: everything a model reads from files, retrieval, web pages, tools, or other users crosses a trust boundary. It informs the model and must never command the system.

Planned: interaction origin (Human UI, WebMCP, automation, Browser Computer Use, background Task) will be recorded for audit and diagnostics and never widens permission. Nothing records it today. See [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md).

## Current implementation security facts

Verified on 2026-10-08. Line numbers refer to the file named in each heading unless another path is given.

### Django control plane (`backend/config/settings.py`)

- `SECRET_KEY` falls back to a dev placeholder whenever `DJANGO_SECRET_KEY` is unset or empty (lines 10-12). `DEBUG` defaults to true (line 14). `ALLOWED_HOSTS` is `127.0.0.1` and `localhost` (line 16).
- `django.contrib.auth` and `django.contrib.sessions` are installed (lines 21, 23), with `rest_framework` and `corsheaders` (lines 26-27). No login flow or permission check exists.
- Middleware (lines 32-41): CORS, Security, Session, Common, CSRF, Authentication, Message, X-Frame-Options. It runs only for requests routed to the Django ASGI app, not for `/agent/*`.
- `DATABASES["default"]` is built by `database_config(os.environ.get("DATABASE_URL"), ...)` (lines 63-65). `STORAGES["default"]` is `core.storage.s3.S3CompatibleStorage` (line 76).
- `CORS_ALLOWED_ORIGINS` lists `http://127.0.0.1:3000` and `http://localhost:3000` (lines 82-85).
- `REST_FRAMEWORK` sets only renderer classes, including `BrowsableAPIRenderer` (lines 87-92). `DEFAULT_PERMISSION_CLASSES` and `DEFAULT_AUTHENTICATION_CLASSES` are unset, so DRF defaults apply: `AllowAny` permission with `SessionAuthentication` and `BasicAuthentication`. No `SECURE_*`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_*`, or `SECURE_CSP` setting is configured.
- Routes: `admin/` and `core/` (`backend/config/urls.py:8-9`); `GET /core/health/` (`backend/core/urls.py:5`) returns four static strings (`backend/core/views.py:7-17`). Test: `test_health` in `backend/core/tests.py`.

### ASGI composition (`backend/config/asgi.py`)

- Sets `DJANGO_SETTINGS_MODULE` (line 7), builds the Django ASGI app (line 10), then imports the agent app (line 12).
- The outer `FastAPI` disables its own docs, redoc, and OpenAPI (line 14), mounts the agent app at `/agent` (line 15) and Django at `/` (line 16).
- Consequence: CSRF, session, authentication, CORS, and clickjacking middleware never see `/agent/*` requests.

### Agent surface (`backend/agent/fastapi/`)

- `app.py:5-9` creates `FastAPI(title="Claus Agent", docs_url="/docs", openapi_url="/openapi.json")`, so `/agent/docs` and `/agent/openapi.json` are served; `redoc_url` is not set, so FastAPI's default `/agent/redoc` is served as well. `test_integrated_routing_contract` in `backend/agent/tests.py` asserts `/agent/openapi.json` returns 200.
- `routes/health.py:6-12`: `GET /health/` returns three static strings.
- `dependencies.py:1` is a docstring only. No authentication, authorization, CORS, CSRF, or session handling exists on this surface.
- `backend/agent/runtime/browser/playwright.py:6-8` returns `async_playwright()` without launching a browser. `agent/llm`, `agent/rag`, `agent/orchestration`, and `agent/tools` are docstring-only packages.

### Database configuration (`backend/config/database.py`)

- Unset or blank `DATABASE_URL` selects SQLite (lines 26-27) at the path settings pass in, `backend/db.sqlite3` (`backend/config/settings.py:64`).
- Otherwise parsing is strict and failures raise `ImproperlyConfigured` instead of falling back: malformed URLs and query strings (lines 29-41); schemes other than `postgres` or `postgresql`, fragments, missing hostname (lines 43-50); missing database name (lines 52-56); duplicate query keys (lines 58-64); invalid percent escapes or invalid UTF-8 in components (lines 11-21). Query parameters become `OPTIONS` (line 73).
- Tests: `backend/core/test_database.py` (5 methods, including nine invalid URLs and the `SECRET_KEY` placeholder).

### Object storage (`backend/core/storage/s3.py`)

- Required settings `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_REGION`, `OBJECT_STORAGE_BUCKET` (lines 17-23) are read lazily under a `threading.Lock` with double-checked locking (lines 33, 35-53). Missing values raise `ImproperlyConfigured` on first use (lines 43-48).
- The endpoint must be an http(s) URL with a hostname and no credentials, query, fragment, or path (lines 55-70).
- The boto3 client is created lazily with SigV4 and path-style addressing (lines 72-91).
- Object names: empty rejected with `ValueError`, backslashes rejected with `SuspiciousFileOperation`, then Django `validate_file_name(name, allow_relative_path=True)` rejects absolute paths and `..` (lines 97-105).
- `_open` supports mode `rb` only; missing objects raise `FileNotFoundError` (lines 122-134).
- `_save` buffers content once in a `SpooledTemporaryFile` and uploads with `IfNoneMatch="*"`; on HTTP 409 or 412, `PreconditionFailed`, or `ConditionalRequestConflict` it allocates a new name and retries (lines 113-120, 151-180). Existing objects are never overwritten.
- `url(name)` returns a presigned `get_object` URL valid for `PRESIGNED_URL_EXPIRATION = 3600` seconds (lines 16, 206-212). Anyone holding the URL can download the object until it expires; no permission check happens at download time.
- Tests: `S3CompatibleStorageContractTests` in `backend/core/test_storage.py` (13 methods, most against a `FakeS3Client`; not an integration test against a real object store).

### Secrets hygiene

- `.gitignore` ignores `.env` and `.env.*` except example files (lines 30-33), private key files (lines 36-43), credential-shaped JSON (lines 46-53), logs (lines 64-65), `backend/db.sqlite3` (line 26), and browser profile directories (lines 83-88).
- `.env.example` (lines 2-13) holds empty placeholders for `DJANGO_SECRET_KEY`, `DATABASE_URL`, and the five storage variables, plus `DJANGO_DEBUG=true`.

### Frontend

- `frontend/src/lib/api.ts` creates axios instances for `/core/` and `/agent/`; `frontend/next.config.ts` rewrites both prefixes to `http://127.0.0.1:8000`. No authentication, cookie, header, middleware, Server Action, route handler, or WebMCP code exists. The single visual test checks rendering and the two health responses; it exercises nothing security-related.

## Requirements

Each area opens with its invariant, written as target behavior. The invariant is Planned unless an item below it is marked Implemented or Partial.

### How IDs are maintained

- Format `SEC-<AREA>-<NNN>`. An ID is never renumbered or reused.
- A requirement that no longer applies is marked deprecated with a note and kept in place.
- A new requirement takes the next number in its area. A new area needs a short uppercase name and an intro here.
- Status changes only with repository evidence: a reviewer must cite the code and the test before moving an item from Planned to Partial or Implemented.
- Other documents cite IDs; only this file defines or changes them.

### AUTH: authentication and server-side authorization

Identity is established by the Django control plane and every operation is authorized there. Today DRF's `AllowAny` default applies and `/agent/*` has no authentication.

- `SEC-AUTH-001` Planned. Every state-changing or data-returning product operation is authorized on the server against the acting user and, for AI, the AI participant's effective permissions. Client-side checks are UX only. Current: no product operations exist; DRF defaults to `AllowAny`; `/agent/*` has no auth.
- `SEC-AUTH-002` Partial. Authentication state is established by the Django control plane, whose auth and session framework is installed as apps and middleware. The Agent FastAPI surface must not become an independent identity authority. Current: no product login flow; the FastAPI surface is unauthenticated.
- `SEC-AUTH-003` Planned. New API views are default-deny: explicit permission classes per view, or a restrictive project-wide `DEFAULT_PERMISSION_CLASSES`, before any non-health endpoint ships. Current: not set.
- `SEC-AUTH-004` Fact/Partial. Requests under `/agent/*` bypass Django middleware (CSRF, session, auth, CORS, clickjacking). Any `/agent` endpoint beyond health requires its own authentication and authorization dependency tied to control-plane identity, and the exposure of `/agent/docs`, `/agent/openapi.json`, and `/agent/redoc` must be reviewed before deployment. The fact is documented; nothing is implemented.

### SCOPE: context scopes

The five scopes are personal, topic (Topic/Thread), team/project (written `team` in [STATE-SCHEMA.md](STATE-SCHEMA.md)), organization, and external. No other scope list exists.

- `SEC-SCOPE-001` Planned. The five scopes are distinct, and every File, Knowledge reference, Task, Artifact, and tool permission carries exactly one owning scope.
- `SEC-SCOPE-002` Planned. A shared AI participant operates only within its current Topic/Thread scope and the materials permitted there. It never reads members' personal context.
- `SEC-SCOPE-003` Planned. Scope checks are enforced server-side on every read, retrieval, and tool call, independent of how the request originated.

### SHARE: explicit sharing

Nothing moves from personal to team scope by default.

- `SEC-SHARE-001` Planned. Personal conversations, files, and working state move into a team scope only by an explicit user share action that records who shared what, into which scope, and when.
- `SEC-SHARE-002` Planned. Sharing a File is distinct from indexing it as Knowledge; neither is implied by upload.

### FILE: File, Artifact, Knowledge, and storage

File, Artifact, and Knowledge are different records. The storage backend already enforces name validation, no-overwrite saves, and configuration checks.

- `SEC-FILE-001` Planned. File, Artifact, and Knowledge are separate records with their own provenance and scope. An Artifact keeps a link to its Task and context.
- `SEC-FILE-002` Implemented. Object names are validated before any storage call: no empty names, no backslashes, no absolute paths, no `..`. Evidence: `core/storage/s3.py:98-105`; `test_dangerous_names_are_rejected`.
- `SEC-FILE-003` Implemented. Stored objects are never overwritten implicitly; saves use conditional create and allocate a new name on collision. Evidence: `s3.py:151-180`; collision and concurrency tests.
- `SEC-FILE-004` Implemented. Storage configuration is validated (endpoint scheme and host, no embedded credentials or path) and missing settings fail clearly rather than silently. Evidence: `s3.py:35-70`; configuration and endpoint tests.
- `SEC-FILE-005` Partial. A presigned URL from `S3CompatibleStorage.url()` is a bearer capability valid for 3600 s, so issuing one is an authorization decision. URLs must be generated only after the server authorizes the requester, must not be logged or embedded in shared or indexed content, and lifetimes must be reconsidered per use case. URL generation is implemented; no authorization layer exists.
- `SEC-FILE-006` Planned. Runtime-local temporary files never become persistent Files or Artifacts without an explicit, scoped write.

### RAG: knowledge retrieval

Retrieval is permission-filtered and its results are untrusted model input.

- `SEC-RAG-001` Planned. Retrieval is filtered by the requester's and AI participant's effective scope permissions before ranking; results outside scope are never returned.
- `SEC-RAG-002` Planned. Retrieved content is untrusted input to the model. It is never treated as policy, permission, approval, or instruction, and provenance (source, scope, owner, version or validity, indexing state) stays attached.
- `SEC-RAG-003` Planned. Indexing is an explicit, scoped, auditable operation separate from upload and share.

### INJ: prompt injection and external content

Text that reaches a model from outside the system has no authority.

- `SEC-INJ-001` Planned. All model-visible content from files, web pages, retrieval, tool outputs, and other users is untrusted; instructions found in it carry no authority.
- `SEC-INJ-002` Planned. Decisions with side effects derive from user intent and server policy, not from text in untrusted content. Untrusted content is delimited and labelled when passed to a model.
- `SEC-INJ-003` Planned. Tool metadata (names, descriptions, schemas) and tool outputs exposed to agents are authored by Claus, kept short, and reviewed; user-generated content in outputs is marked untrusted. Applies to WebMCP through the AGENT area.

### AGENT: WebMCP and agent-originated actions

Agents reach the same operations as people, through the same server checks. The WebMCP contract is in [WEBMCP.md](WEBMCP.md); the adapter model is in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md).

- `SEC-AGENT-001` Planned. Agent-originated actions (WebMCP tools, automation, in-product AI) call the same application operations as the Human UI with the same server-side authorization. Interaction origin is recorded for audit and diagnostics but never grants or widens permission.
- `SEC-AGENT-002` Planned/Experimental. Browser-side WebMCP mechanisms act in the browser only: `exposedTo` and Permissions Policy `tools` control exposure to agents, and annotations are behavioural hints. None of them is authorization for Claus operations.
- `SEC-AGENT-003` Planned/Experimental. Tool executions carry the user's existing session and auth context. A tool must never accept credentials, tokens, or user identifiers as input to act as someone else.
- `SEC-AGENT-004` Planned/Experimental. Tools that change state or are consequential are annotated as such, require the same server-side approval path as the UI, and are rejected server-side if invoked without it.
- `SEC-AGENT-005` Planned/Experimental. Pages that do not intend to expose tools send `Permissions-Policy: tools=()` once WebMCP is adopted anywhere. Until adoption, no registration code exists.

### APPROVE: approval and high-risk operations

High-impact work stops for a human decision bound to that exact operation.

- `SEC-APPROVE-001` Planned. High-impact operations (irreversible, cross-scope, external side effects, credential use; the class [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) calls consequential) require an explicit human approval recorded with the Task before execution, regardless of interaction origin.
- `SEC-APPROVE-002` Planned. An approval is bound to a specific operation, parameters, scope, and actor. It cannot be reused for a different operation or replayed.
- `SEC-APPROVE-003` Planned. An approval request stops the Task in `waiting_for_approval`; denial or timeout results in no side effect.

### RUNTIME: Browser, Terminal, and Workspace

Runtimes are task-scoped and isolated; only authorized writes carry results out.

- `SEC-RUNTIME-001` Planned. Browser, Terminal, and Workspace runtimes are task-scoped and isolated from each other and from the control plane. They hold no long-lived credentials.
- `SEC-RUNTIME-002` Planned. Runtime outputs re-enter the system only as Messages, Files, Artifacts, or Task state through authorized writes. Runtime-local state is discarded on completion or failure.
- `SEC-RUNTIME-003` Planned. Cross-context access to a live Browser session (viewing or control) is authorized per participant. Control handoff is exclusive and visible.
- `SEC-RUNTIME-004` Fact/Partial. The only runtime code today is `agent/runtime/browser/playwright.py`, a lazy `async_playwright()` factory that launches nothing. No session management exists.

### TOOL: external tools and credentials

Credentials stay on the server; every call is accountable.

- `SEC-TOOL-001` Planned. External tool and API credentials are held server-side or in a secrets store, scoped to the Task or participant that may use them, and never exposed to the model, the browser, or tool inputs and outputs.
- `SEC-TOOL-002` Planned. Each external tool call is logged with tool, actor, scope, Task, a safe parameter summary, and result status.

### SECRET: secrets, logs, and audit

The repository holds no secrets; runtime records hold safe summaries only.

- `SEC-SECRET-001` Implemented. No secrets in the repository; `.env` files are ignored; `.env.example` holds placeholders only. Evidence: `.gitignore:30-33`, `.env.example`.
- `SEC-SECRET-002` Partial. `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=false` must be set in any non-local environment. The code falls back to a dev placeholder key and `DEBUG=true` by default; the `SECRET_KEY` fallback is documented and has a test in the repository (`test_secret_key_uses_placeholder_for_missing_or_empty_environment`), the `DEBUG` default is documented but untested, and nothing enforces the override at deployment.
- `SEC-SECRET-003` Planned. Logs, Task records, and ToolRun summaries contain safe summaries only: never credentials, presigned URLs, raw tokens, or full prompts containing personal data.
- `SEC-SECRET-004` Planned. The audit trail records who (user or AI participant), what operation, on which scope and resource, via which interaction origin, when, with what approval, and with what outcome.

### IDEM: retry, replay, and idempotency

Retries never duplicate side effects; approvals cannot be replayed.

- `SEC-IDEM-001` Planned. State-changing operations invoked by agents or background Tasks are idempotent or carry an idempotency key so retries do not duplicate side effects.
- `SEC-IDEM-002` Planned. Each agent execution attempt is a distinct record from the product Task. Retries are new attempts, never silent re-execution.
- `SEC-IDEM-003` Planned. Replay of a previously approved request is rejected through approval binding and a nonce or key.
- `SEC-IDEM-004` Implemented. Storage saves are retry-safe: content is buffered once and re-uploaded under a new name on conflict. Evidence: `s3.py:151-180`; collision retry tests.

### FAIL: fail closed and cleanup

Failure means denial and cleanup, not a weaker default.

- `SEC-FAIL-001` Planned. Authorization, scope, or approval failure results in denial with no partial side effect.
- `SEC-FAIL-002` Implemented for database and storage. Configuration errors fail at startup or first use with clear messages rather than falling back to insecure defaults: an invalid `DATABASE_URL` and missing storage settings raise `ImproperlyConfigured`. Evidence: `config/database.py`, `s3.py`. The `SECRET_KEY` and `DEBUG` fallbacks (`SEC-SECRET-002`) are the documented exception.
- `SEC-FAIL-003` Planned. Failed or cancelled Tasks clean up runtime-local state and leave the conversation and persistent Files consistent.

### API: API surface hardening

Cross-cutting facts about the surfaces that exist today.

- `SEC-API-001` Implemented. Health endpoints return no sensitive data; the current payloads are static strings. Evidence: `core/views.py:7-17`, `agent/fastapi/routes/health.py:6-12`.
- `SEC-API-002` Partial. The browsable API renderer, `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` are development conveniences; their exposure must be decided before any non-local deployment. All four are enabled today, the ReDoc page by default rather than by configuration.
- `SEC-API-003` Partial. The CORS allowlist is limited to local dev origins and `ALLOWED_HOSTS` is local only; both must be environment-specific. The dev values are hard-coded today.

Internationalization has no area of its own. Locale and preference input is validated under `SEC-INJ-001`, translation strings never carry identifiers or permissions ([I18N.md](I18N.md)), and WebMCP tool names stay stable under `SEC-AGENT-002`.

## Status summary

Evidence names repository files and tests that exist; "none yet" means no code or test supports the item. Tests were not executed in this documentation pass.

| ID | Status | Evidence |
|---|---|---|
| SEC-AUTH-001 | Planned | none yet |
| SEC-AUTH-002 | Partial | `settings.py:21, 23, 35, 38` (auth and session apps and middleware installed) |
| SEC-AUTH-003 | Planned | none yet (`settings.py:87-92` sets no permission classes) |
| SEC-AUTH-004 | Fact/Partial | `asgi.py:14-16`, `agent/fastapi/app.py:5-9` (fact only) |
| SEC-SCOPE-001 | Planned | none yet |
| SEC-SCOPE-002 | Planned | none yet |
| SEC-SCOPE-003 | Planned | none yet |
| SEC-SHARE-001 | Planned | none yet |
| SEC-SHARE-002 | Planned | none yet |
| SEC-FILE-001 | Planned | none yet |
| SEC-FILE-002 | Implemented | `s3.py:98-105`; `test_dangerous_names_are_rejected` |
| SEC-FILE-003 | Implemented | `s3.py:151-180`; `test_default_save_contract_does_not_overwrite`, `test_concurrent_saves_atomically_allocate_distinct_names` |
| SEC-FILE-004 | Implemented | `s3.py:35-70`; `test_missing_configuration_is_lazy_and_clear`, `test_endpoint_path_is_rejected` |
| SEC-FILE-005 | Partial | `s3.py:16, 206-212` (URL generation only) |
| SEC-FILE-006 | Planned | none yet |
| SEC-RAG-001 | Planned | none yet |
| SEC-RAG-002 | Planned | none yet |
| SEC-RAG-003 | Planned | none yet |
| SEC-INJ-001 | Planned | none yet |
| SEC-INJ-002 | Planned | none yet |
| SEC-INJ-003 | Planned | none yet |
| SEC-AGENT-001 | Planned | none yet |
| SEC-AGENT-002 | Planned/Experimental | none yet |
| SEC-AGENT-003 | Planned/Experimental | none yet |
| SEC-AGENT-004 | Planned/Experimental | none yet |
| SEC-AGENT-005 | Planned/Experimental | none yet |
| SEC-APPROVE-001 | Planned | none yet |
| SEC-APPROVE-002 | Planned | none yet |
| SEC-APPROVE-003 | Planned | none yet |
| SEC-RUNTIME-001 | Planned | none yet |
| SEC-RUNTIME-002 | Planned | none yet |
| SEC-RUNTIME-003 | Planned | none yet |
| SEC-RUNTIME-004 | Fact/Partial | `agent/runtime/browser/playwright.py:6-8` |
| SEC-TOOL-001 | Planned | none yet |
| SEC-TOOL-002 | Planned | none yet |
| SEC-SECRET-001 | Implemented | `.gitignore:30-33`, `.env.example:2-13` |
| SEC-SECRET-002 | Partial | `settings.py:10-14`; `test_secret_key_uses_placeholder_for_missing_or_empty_environment` |
| SEC-SECRET-003 | Planned | none yet |
| SEC-SECRET-004 | Planned | none yet |
| SEC-IDEM-001 | Planned | none yet |
| SEC-IDEM-002 | Planned | none yet |
| SEC-IDEM-003 | Planned | none yet |
| SEC-IDEM-004 | Implemented | `s3.py:151-180`; `test_collision_retry_rewinds_payload_and_preserves_content_type` |
| SEC-FAIL-001 | Planned | none yet |
| SEC-FAIL-002 | Implemented (database and storage) | `config/database.py:11-64`, `s3.py:43-70`; `test_invalid_urls_fail_instead_of_falling_back`, `test_missing_configuration_is_lazy_and_clear` |
| SEC-FAIL-003 | Planned | none yet |
| SEC-API-001 | Implemented | `core/views.py:7-17`, `agent/fastapi/routes/health.py:6-12`; `test_health`, `test_integrated_routing_contract` |
| SEC-API-002 | Partial | `settings.py:87-92`, `agent/fastapi/app.py:5-9` (enabled today) |
| SEC-API-003 | Partial | `settings.py:16, 82-85` (dev values hard-coded) |

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md): implemented versus planned components and runtime boundaries.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): conceptual shapes for `CollaborationContext`, `AgentTaskState`, `ToolRun`, and `InteractionContext`.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md): Human UI, WebMCP, Automation, and Browser Computer Use as entry points to shared operations.
- [WEBMCP.md](WEBMCP.md): the WebMCP technical contract and its experimental status.
- [I18N.md](I18N.md): locale handling and the separation of translated strings from machine values.
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md): the review procedure that cites these IDs.
- [CODE-REVIEW.md](CODE-REVIEW.md): the general review procedure and finding format.
- [TESTING.md](TESTING.md): existing tests and the security-negative, boundary, and approval tests still required.
