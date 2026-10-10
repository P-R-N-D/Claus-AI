# Claus Security Architecture

This document defines the trust boundaries of Claus and the `SEC-*` security requirements that features, reviews, and tests map to. It is the only place where `SEC-*` identifiers are defined. It also records the security-relevant facts of the code that exists today, with file and line evidence.

It is not a vulnerability reporting policy, not a review procedure (see [SECURITY-REVIEW.md](SECURITY-REVIEW.md) and [CODE-REVIEW.md](CODE-REVIEW.md)), and not a claim that the requirements are met. Read [CONTEXT.md](CONTEXT.md) first; its rules are not repeated here.

## Status

- The repository implements health endpoints, the ASGI composition, `DATABASE_URL` parsing with SQLite fallback, and the S3-compatible storage backend. [ARCHITECTURE.md](ARCHITECTURE.md) keeps the implemented versus planned breakdown.
- No authentication flow, authorization check, scope model, sharing action, approval workflow, or audit trail exists for product features. There are no collaboration domain models. Requirements in those areas are Planned and describe target behavior only.
- Requirements marked Implemented are backed by repository code and by tests that exist in the repository; the tests were not executed in this documentation pass.
- WebMCP-related requirements are Planned and Experimental: WebMCP is a draft Community Group report, and no status-page entry reported default-on support in a stable browser release as of 2026-10-08 (see [WEBMCP.md](WEBMCP.md)).

Status words: Implemented, Partial, Planned, Experimental. Partial means supporting code exists but the requirement is not enforced. Fact/Partial marks a requirement that records a fact about current code with no product behavior behind it.

## Trust boundaries

Planned. Except where a bullet says "Today", the actors and boundaries below are the target model; see Status.

### Actors

- **User**: an authenticated person acting through the Human UI, or later through automation on their behalf. Intent reaches Claus from users in two ways: a direct request, or a standing authorization that a user, or an administrator with authority over the context, set up in advance for automation (see "Basis for state changes"). Approval is a separate decision that only a person with authority over the operation makes (SEC-APPROVE-001); neither a direct request nor a standing authorization is one.
- **Automation identity**: a non-interactive caller that the control plane authenticates and authorizes with its own identity; a name or identifier a client sends is not one. It acts on a standing authorization, only within the permissions granted to that identity and the authorization's scope, and is never a source of approval.
- **AI participant**: a personal or shared AI acting inside one `CollaborationContext` ([STATE-SCHEMA.md](STATE-SCHEMA.md)) with its own effective permissions, never wider than the context allows. Claus identifies and runs it; the server applies its permissions only when the server itself established that this participant is acting, never because a request, tool input, or message names it.
- **External browser agent or extension**: an agent outside Claus that calls the WebMCP tools a page exposes or otherwise acts in the user's browser. It acts inside the user's authenticated session, so the server sees its requests as that user's and cannot establish which agent sent them; no delegation or agent-identification mechanism exists. It is not an AI participant and is untrusted by the server. Experimental.
- **External content**: files, web pages, retrieved chunks, tool outputs, the payload of an external event or webhook, messages from AI participants, and every message or quoted text other than the direct request being handled (conversation history, other members' earlier requests, quotes, forwarded or attached text). Untrusted input, never instructions.
- **Direct request**: the message an AI participant is handling, in which an authenticated user asks it to do something in a context where both are present. The server identifies it from the authenticated sender and the message being handled, never from the model's reading of the text. It carries that user's intent, within that user's own permissions and the AI participant's effective permissions; its quoted, attached, forwarded, or referenced parts are not part of it and remain external content. A reply from that user that accepts a proposal, or answers a question, the AI put to that user in the same exchange ("yes", "editor") is a direct request for exactly the operation and targets the AI showed, with the values it showed or the reply supplies; a reply that declines, asks back, or is ambiguous requests nothing. A message from an AI participant is never a direct request. See "Direct requests and referenced content".
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
- **Model <-> content**: everything a model reads from files, retrieval, web pages, tools, or other messages, and anything quoted, attached, or referenced inside a direct request, crosses a trust boundary; only the direct request's own text carries its sender's intent, and in an automated run only the stored definition of its standing authorization carries the intent of whoever authorized it. Content informs the model and must never command the system.

Planned: interaction origin (Human UI, WebMCP, automation, Browser Computer Use, background Task) will be recorded for audit and diagnostics and never widens permission. A value the server cannot verify, such as `human_ui` or `webmcp` for a request under a user's browser session, is recorded only as client-reported. Nothing records it today. See [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md).

### Direct requests and referenced content

Planned. A team context has several people who may legitimately ask a shared AI for work, and much content that must not steer it. Five questions are answered separately, and the answer to one never settles another:

1. Sender identity: who sent the message, as established by authentication. Text that claims a name, a role, or an approval is not identity.
2. Context access: whether that sender, and the AI participant, may act in this context and scope (SEC-AUTH-001, SEC-SCOPE-002, SEC-SCOPE-003).
3. Directness: whether the text is the sender's own request to the AI, or material the request points at, such as quotes, attachments, Files, retrieval results, web pages, tool outputs, and earlier messages.
4. Content trust: referenced material is untrusted data, whoever wrote or shared it; instructions inside it carry no authority (SEC-INJ-001).
5. Server policy: authorization, scope, classification, and whether an approval is required are decided by the server, and an approval itself comes only from a person through the approval path (SEC-APPROVE-001); neither a direct request nor any content overrides them (SEC-INJ-002).

Consequences:

- A direct request from a member who may act in the context is processed, with that member as the actor and within the member's and the AI participant's permissions. It is not refused as untrusted content merely because it comes from another member.
- A direct request may ask the AI to use referenced material, for example to summarize it or to apply its data. An operation the member's request does not ask for, or a target, recipient, or scope the request neither names nor designates, is not carried out on the material's authority; the AI may propose it, and the member's reply accepting the proposal is then a direct request for exactly the operation and targets shown, with the values shown or the reply supplies. When the request names the operation and designates the material as the source of its values ("invite the addresses in this file"), the values are used as data, and the resulting operations still go through server authorization, classification, and approval.
- A direct request never widens scope: a member cannot direct a shared AI to read any member's personal context, the member's own included. A direct request is not a share action; personal material reaches a team scope only through an explicit share (SEC-SCOPE-002, SEC-SHARE-001).
- Trust here concerns authority, not validation: every input, direct requests included, is validated as untrusted input.

### Basis for state changes

Planned. A state change needs a basis the server can verify. There are two, and neither replaces authentication, authorization, scope, classification, or a required approval (SEC-INJ-002):

1. A direct request: an authenticated user explicitly asks for the work in a context they may act in, either by calling the operation under their own session or in a message an AI participant handles (see "Direct requests and referenced content"). The server checks the user, the scope, the effective permissions of the AI participant if one acts, the operation's class, and any approval it needs. That a request is direct widens nothing.
2. A standing authorization: a Task or an automation policy that an authenticated user, or an administrator with authority over the context, set up and authorized in advance. A run needs no new message from a person, but does only what the server can verify from the stored definition: its operations, targets, scope, and conditions. It runs as an actor the control plane authenticates, the user who set it up or an automation identity, never as a name a client supplies. It covers nothing that the user or administrator who authorized it may not do in that context, so running it as an automation identity widens nothing (SEC-AUTH-001).

Rules for a standing authorization:

- A schedule reaching its time, or an external event arriving, may start a run that the authorization covers. A trigger is neither an authorization nor an approval and adds nothing to the definition.
- An event's payload, and retrieved, file, or tool content read during a run, are untrusted (SEC-INJ-001). They may supply values that the definition designates as input, but they never add an operation, target, recipient, or scope, change the definition, or establish a role, permission, or approval they claim.
- Each run is checked against the authorization, permissions, and scope in effect when it executes. A revoked or otherwise invalid authorization executes nothing, and narrowed permissions or scope narrow or stop the run (SEC-AUTH-001, SEC-SCOPE-003).
- A standing authorization is not an approval. Setting up, authorizing, or scheduling it approves none of the consequential operations a run may reach; each waits for its own approval, bound to its `operation_id` (SEC-APPROVE-001, SEC-APPROVE-002).

Under either basis, an AI participant may plan the work and split it into steps, but it adds no state change that the direct request or the standing authorization does not cover. A step outside them is not executed; the AI may propose it to a person, whose direct request then covers it. A consequential step that a basis covers, including one a person's direct request covers after such a proposal, still waits for its approval; an approval is not a basis and never covers a step that neither basis covers. The AI's own judgment is neither a direct request nor an authorization.

How a standing authorization is stored, who may grant one, and how an automation identity authenticates are not designed.

## Current implementation security facts

Verified on 2026-10-08. (Updated 2026-10-10: the `/core` tests, ASGI composition, Agent surface, Secrets hygiene, and Frontend facts were re-checked at 14b9109 and are unchanged through 3fe4d1a, except the Agent surface test description, updated for 3fe4d1a.) Line numbers refer to the file named in each heading unless another path is given.

### Django control plane (`backend/config/settings.py`)

- `SECRET_KEY` falls back to a dev placeholder whenever `DJANGO_SECRET_KEY` is unset or empty (lines 10-12). `DEBUG` defaults to true (line 14). `ALLOWED_HOSTS` is `127.0.0.1` and `localhost` (line 16).
- `django.contrib.auth` and `django.contrib.sessions` are installed (lines 21, 23), with `rest_framework` and `corsheaders` (lines 26-27). No login flow or permission check exists.
- Middleware (lines 32-41): CORS, Security, Session, Common, CSRF, Authentication, Message, X-Frame-Options. It runs only for requests routed to the Django ASGI app, not for `/agent/*`.
- `DATABASES["default"]` is built by `database_config(os.environ.get("DATABASE_URL"), ...)` (lines 63-65). `STORAGES["default"]` is `core.storage.s3.S3CompatibleStorage` (line 76).
- `CORS_ALLOWED_ORIGINS` lists `http://127.0.0.1:3000` and `http://localhost:3000` (lines 82-85).
- `REST_FRAMEWORK` sets only renderer classes, including `BrowsableAPIRenderer` (lines 87-92). `DEFAULT_PERMISSION_CLASSES` and `DEFAULT_AUTHENTICATION_CLASSES` are unset, so DRF defaults apply: `AllowAny` permission with `SessionAuthentication` and `BasicAuthentication`. No `SECURE_*`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_*`, or `SECURE_CSP` setting is configured.
- Routes: `admin/` and `core/` (`backend/config/urls.py:8-9`); `GET /core/health/` (`backend/core/urls.py:5`) returns four static strings (`backend/core/views.py:7-17`). Tests in `backend/core/tests.py`: `test_health`, and `CorsPreflightTests` (2 methods): a preflight from the allowed origin `http://127.0.0.1:3000` receives `Access-Control-Allow-Origin`, `GET` in `Access-Control-Allow-Methods`, and no credentials header; one from a disallowed origin still gets 200, because django-cors-headers answers every preflight, but no CORS headers. (Updated 2026-10-10.)

### ASGI composition (`backend/config/asgi.py`)

- Sets `DJANGO_SETTINGS_MODULE` (line 7), builds the Django ASGI app (line 10), then imports the agent app (line 12).
- The outer `FastAPI` disables its own docs, redoc, and OpenAPI (line 14), mounts the agent app at `/agent` (line 15) and Django at `/` (line 16).
- Consequence: CSRF, session, authentication, CORS, and clickjacking middleware never see `/agent/*` requests.
- FastAPI native telemetry: FastAPI 0.142.0 and later depends on `opentelemetry-api` (1.45.1, "via fastapi", `backend/locks/cp312-linux-x86_64.txt:540-543`) and imports it whenever FastAPI is imported. Its tracing, metrics, and log defaults activate only when a global OpenTelemetry provider (an SDK) is configured; the lock contains no SDK, so telemetry is inactive today. If an SDK or an auto-instrumentation agent is added, the outer `FastAPI` (line 14) traces every request, including Django's `/core/*` and `/admin/*` because Django is mounted inside it (line 16), and records `url.path` and `url.query` with only a few signature parameters redacted. The default logs signal also records unhandled exceptions that reach FastAPI (today the agent app) as `http.server.request.exception` log records with exception messages and stack traces, and request validation failures as `fastapi.validation.failed` records; any FastAPI extra that installs `opentelemetry-sdk` (`opentelemetry`, `standard`, `standard-no-fastapi-cloud-cli`, `all`) adds the SDK and the OTLP HTTP exporter; these signals go live only once a global provider is configured (in code, by an auto-instrumentation agent, through `OTEL_PYTHON_{TRACER,METER,LOGGER}_PROVIDER`, or by `FASTAPI_OTEL_AUTO_CONFIGURE=true` with an OTLP endpoint, which needs one of those extras), and `telemetry={"logs": False}` or the `exclude` option are the controls. `FASTAPI_OTEL_AUTO_CONFIGURE=true` adds OTLP exporters configured from the environment. An `OTEL_PROPAGATORS` value naming a propagator that is not installed makes `import fastapi`, and therefore this module, fail at import (author-run, environment-limited check: `OTEL_PROPAGATORS=b3` raised `ValueError: Propagator b3 not found`). Decision: documented, not disabled in code; enabling observability requires a review against `SEC-SECRET-003`. (Updated 2026-10-10.)

### Agent surface (`backend/agent/fastapi/`)

- `app.py:5-12` creates `FastAPI(title="Claus Agent", docs_url="/docs", openapi_url="/openapi.json", redirect_slashes=False)`, so `/agent/docs` and `/agent/openapi.json` are served; `redoc_url` is not set, so FastAPI's default `/agent/redoc` is served as well. `test_integrated_routing_contract` in `backend/agent/tests.py` asserts `/agent/openapi.json` returns 200.
- `redirect_slashes=False` (line 11, explained in lines 9-10): a path that differs from a route only by its trailing slash, such as `/agent/health`, `/agent/docs/`, `/agent/redoc/`, or `/agent/openapi.json/`, returns FastAPI's 404 JSON with no `Location` header. The slash redirect it replaces was an absolute URL built from the upstream `Host` header, which behind the Next.js rewrite is `127.0.0.1:8000`, so it sent the browser from the frontend origin to the backend origin (author-run reproduction at 6b7ea90 through `next start`: `/agent/docs/?q=1` answered 307 with `Location: http://127.0.0.1:8000/agent/docs?q=1`). `test_agent_slash_mismatch_is_404_without_redirect` in `backend/agent/tests.py` asserts the 404 without `Location` for those four paths and 200 for `/agent/docs`, `/agent/redoc`, `/agent/openapi.json`, and `/agent/docs/oauth2-redirect` (Swagger UI's OAuth2 redirect page, served while `docs_url` and `openapi_url` are set; extended in 3fe4d1a). Django's `APPEND_SLASH` redirect under `/core/*` is unchanged and uses a relative `Location` (`/core/health` answers 301 to `/core/health/`). (Updated 2026-10-10.)
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
- `.env.example` (lines 2-13) holds empty placeholders for `DJANGO_SECRET_KEY`, `DATABASE_URL`, and the five storage variables, plus `DJANGO_DEBUG=true`. Lines 15-20 hold two empty, non-secret, test-only Playwright variables, `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` (line 18) and `PLAYWRIGHT_NEXT_SERVER` (line 20), with comments stating that Playwright does not load the file. (Updated 2026-10-10.)
- The ignore rules only keep untracked files out of a commit. They do not apply to a file that is already tracked or that is added with `git add -f`, and they do not catch a secret written into source code, documentation, tests, or configuration, or one already in the history.
- No Git hook or secret scanner is configured in the repository's files. The one workflow, `.github/workflows/ci.yml`, runs build and test checks and scans nothing for secrets (see "CI workflow" below); `.github/` also holds `copilot-instructions.md` and advisory issue and pull request templates, none of which runs a check. (Updated 2026-10-10.) [SECURITY-REVIEW.md](SECURITY-REVIEW.md) step 7 defines a keyword check of a change's added lines, which a reviewer runs; nothing runs or enforces it automatically.

### Frontend

- `frontend/src/lib/api.ts` creates axios instances for `/core/` and `/agent/`. No authentication, cookie, Server Action, route handler, or WebMCP code exists, and no custom response header (security, cookie, or `Permissions-Policy`) is configured; the Proxy sets only the `Location` of its redirects. (Updated 2026-10-10.)
- `frontend/next.config.ts`: `skipTrailingSlashRedirect: true` (line 5); rewrites of `/core/:path(.*)` and `/agent/:path(.*)` to `http://127.0.0.1:8000`, which preserve trailing slashes (lines 12-23); `agentRules: false` (line 7), so `next dev` generates no agent-rules block in `frontend/AGENTS.md`; and `experimental.mcpServer: false` (lines 8-11), so `next dev` serves no `/_next/mcp` endpoint (without the option, Next 16's `next dev` serves an unauthenticated MCP endpoint at that path; `next start` never serves it). The `dev` script in `frontend/package.json` binds `next dev` to `127.0.0.1`. (Updated 2026-10-10.)
- `frontend/src/proxy.ts` is a Next.js 16 Proxy (the file convention that replaces `middleware.ts`). Its matcher (line 20) skips `/_next/*` and the `/core/` and `/agent/` prefixes, so UI paths and App Router metadata routes such as `/icon.svg` pass through it. A UI path that ends in `/`, other than `/`, gets a 308 to the same path without the slash, with the query preserved (lines 7-13); leading slashes in the target are collapsed so the `Location` can never be scheme-relative (`//host`) (line 11), and the in-code `/core/` and `/agent/` exemption (line 5) remains as a guard. Next's own router answers raw `//` and backslash paths with its own 308 before the Proxy runs, so that guard is defense in depth. The Proxy has no authentication, cookie, header, or locale logic. It is not an authorization layer; authorization belongs to the Django control plane (`SEC-AUTH-001`). (Updated 2026-10-10.)
- Tests: `frontend/tests/visual/home.spec.ts` (one scenario) checks that `/core/health/`, `/agent/health/`, and `/agent/openapi.json` return 200 without a redirect; that `/console/`, `/core-ui/`, and `/agent-ui/` get a 308 that keeps the query; that `//evil.example/` and `/%2F%2Fevil.example/` get a 308 with a same-origin `Location`; that `/agent/docs/` and `/agent/health` return 404 without `Location` and `/core/health` a same-origin 301; that `POST /_next/mcp` returns 404; and that the pages have no horizontal overflow, measured against `document.documentElement.clientWidth` with a self-check that proves the measurement can fail. `frontend/tests/visual/proxy.spec.ts` (two tests, no browser) calls `proxy()` directly, including with `//evil.example/`, and checks the matcher with Next's version-pinned `unstable_doesMiddlewareMatch`. Each test runs in the four desktop/mobile, light/dark projects. They cover same-origin redirects and the disabled MCP endpoint; none exercises authentication or authorization, which does not exist. (Updated 2026-10-10.)

### CI workflow (`.github/workflows/ci.yml`)

Added 2026-10-10. The workflow runs the baseline checks listed in [TESTING.md](TESTING.md) "Continuous integration".

- Triggers: `push` and `pull_request` for `main`, and `workflow_dispatch` (lines 8-13). There is no `pull_request_target` or `workflow_run` trigger, so pull request code, including a fork's, runs with the token GitHub gives that pull request, which for a fork is read-only and comes without the repository's secrets.
- The workflow token is limited to `contents: read` (lines 15-16). No job references a secret.
- Every `actions/checkout` step sets `persist-credentials: false`, so the token is not left in the runner's Git configuration for later steps (lines 35-37, 71-73, 106-108).
- Every action is pinned to a full commit SHA, with its version as a comment (lines 35, 38, 71, 74, 106, 109, 121, 138).
- Every job runs on a GitHub-hosted `ubuntu-24.04` runner (lines 32, 64, 95); no self-hosted runner is referenced. A job installs tools from the network at run time: uv through `astral-sh/setup-uv`, which checks it against the action's known SHA-256, CPython through `uv venv --managed-python`, the hash-locked Python packages, Node through `actions/setup-node`, npm 11.21.0 by version without a hash, the npm lock's packages, and Chromium with its system packages.
- The E2E jobs upload `frontend/test-results/` as a run artifact kept for 7 days (lines 138-143). Logs and artifacts of runs in this public repository can be read by people outside the project, so a workflow step must not print secrets or personal data.

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

- `SEC-AUTH-001` Planned. Every state-changing or data-returning product operation is authorized on the server against the actor (the acting user or automation identity) and, for AI, the AI participant's effective permissions. Client-side checks are UX only. Current: no product operations exist; DRF defaults to `AllowAny`; `/agent/*` has no auth.
- `SEC-AUTH-002` Partial. Authentication state is established by the Django control plane, whose auth and session framework is installed as apps and middleware. The Agent FastAPI surface must not become an independent identity authority. Current: no product login flow; the FastAPI surface is unauthenticated.
- `SEC-AUTH-003` Planned. New API views are default-deny: explicit permission classes per view, or a restrictive project-wide `DEFAULT_PERMISSION_CLASSES`, before any non-health endpoint ships. Current: not set.
- `SEC-AUTH-004` Fact/Partial. Requests under `/agent/*` bypass Django middleware (CSRF, session, auth, CORS, clickjacking). Any `/agent` endpoint beyond health requires its own authentication and authorization dependency tied to control-plane identity, and the exposure of `/agent/docs`, `/agent/openapi.json`, and `/agent/redoc` must be reviewed before deployment. The fact is documented; nothing is implemented.

### SCOPE: context scopes

The five scopes are personal, topic (Topic/Thread), team/project (written `team` in [STATE-SCHEMA.md](STATE-SCHEMA.md)), organization, and external. No other scope list exists.

- `SEC-SCOPE-001` Planned. The five scopes are distinct, and every File, Knowledge reference, Task, Artifact, and tool permission carries exactly one owning scope.
- `SEC-SCOPE-002` Planned. A shared AI participant operates only within its current Topic/Thread scope and the materials permitted there. It never reads members' personal context, including when a member's direct request asks it to; personal material enters the scope only through an explicit share (`SEC-SHARE-001`).
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
- `SEC-RAG-002` Planned. Retrieved content is untrusted input to the model, including when a user's direct request asked for the retrieval. It is never treated as policy, permission, approval, or instruction, and provenance (source, scope, owner, version or validity, indexing state) stays attached.
- `SEC-RAG-003` Planned. Indexing is an explicit, scoped, auditable operation separate from upload and share.

### INJ: prompt injection and external content

Text that reaches a model from outside the system has no authority.

- `SEC-INJ-001` Planned. All model-visible content from files, web pages, retrieval, and tool outputs, the payload of an external event that starts or feeds an automated run, every message from an AI participant, and every message or quoted text other than the direct request being handled is untrusted; instructions found in it carry no authority. A direct request from an authenticated user carries that user's intent only, within their permissions; material quoted, attached, or referenced in it stays untrusted (see "Direct requests and referenced content").
- `SEC-INJ-002` Planned. Decisions with side effects derive from server policy and from a basis the server can verify: the direct request of an authenticated user who may act in the context, or a standing authorization (a Task or automation policy that an authenticated user, or an administrator with authority over the context, set up and authorized in advance), within its stored operations, targets, scope, and conditions (see "Basis for state changes"). They never derive from text in untrusted content, an event payload included, or from the AI's own judgment. An operation the request or the authorization does not cover, or a target, recipient, or scope it neither names nor designates, is not carried out on the authority of referenced material; a schedule or an event only starts a run that the authorization covers. Neither basis, nor any content, overrides the server's authentication, authorization, scope, classification, or approval requirements, and a standing authorization is not an approval. Untrusted content is delimited and labelled when passed to a model.
- `SEC-INJ-003` Planned. Tool metadata (names, descriptions, schemas) and tool outputs exposed to agents are authored by Claus, kept short, and reviewed; user-generated content in outputs is marked untrusted. Applies to WebMCP through the AGENT area.

### AGENT: WebMCP and agent-originated actions

Agents reach the same operations as people, through the same server checks. The WebMCP contract is in [WEBMCP.md](WEBMCP.md); the adapter model is in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md).

- `SEC-AGENT-001` Planned. Agent-originated actions (WebMCP tools, automation, in-product AI) call the same application operations as the Human UI with the same server-side authorization. Interaction origin is recorded for audit and diagnostics but never grants or widens permission. An origin the server cannot verify, such as `human_ui` or `webmcp` for a request under a user's browser session, is recorded only as client-reported, never as established fact, and never overrides a server-verified value.
- `SEC-AGENT-002` Planned/Experimental. Browser-side WebMCP mechanisms act in the browser only: `exposedTo` and Permissions Policy `tools` control exposure to agents, and annotations are behavioural hints. None of them is authorization for Claus operations.
- `SEC-AGENT-003` Planned/Experimental. Tool executions carry the user's existing session and auth context. A tool must never accept credentials, tokens, user identifiers, or AI participant identifiers as input to act as someone else. A caller in the user's browser session, including an external browser agent, acts as that user: it is not a Claus AI participant, gains no AI participant's permissions, and is not recorded as one unless the server itself established that participant.
- `SEC-AGENT-004` Planned/Experimental. Every state-changing tool is annotated `readOnlyHint: false` and is authorized server-side like the same operation from the UI. A tool for an operation that server policy classifies as consequential is also annotated `consequentialHint: true`, runs only through the same server-side approval path as the UI, and is rejected server-side if invoked without it. A tool whose operation is consequential for any of its inputs carries `consequentialHint: true`, or is split into one tool per class. Annotations mirror the server's classification; they neither require nor waive an approval, and a browser or agent confirmation prompt is not one.
- `SEC-AGENT-005` Planned/Experimental. Pages that do not intend to expose tools send `Permissions-Policy: tools=()` once WebMCP is adopted anywhere. Until adoption, no registration code exists.

### APPROVE: approval and high-risk operations

High-impact work stops for a human decision bound to that exact operation.

- `SEC-APPROVE-001` Planned. High-impact operations (the class [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) calls consequential) require an explicit human approval, recorded with the Task or, when no Task carries the call, with the operation, before execution, regardless of interaction origin. Server policy assigns the class, never an annotation, a prompt, a client claim, or the origin. It covers at least irreversible deletion, publishing personal material into a team or organization scope, posting or sending outside Claus, external work that uses sensitive credentials, and high-risk Browser, Terminal, or Workspace execution. Ordinary mutations, such as changing one's own settings, creating a draft, or a low-risk change the user explicitly asked for, need authentication, authorization, scope, and policy checks, not an approval. The approval comes from a person with authority over the operation. The agent that requests or runs the operation never completes the approval step by itself, an agent acting in a person's browser session included, because it can send what the person could; a UI click, an agent's reply, or an approval flag sent by a client is not by itself a verified approval. How independent human approval is implemented is decided when the authentication and approval system is designed.
- `SEC-APPROVE-002` Planned. An approval is verified and recorded by the server and bound to a specific operation, its `operation_id`, actor, target, parameters, and scope. It cannot be reused for a different operation or replayed. The only exception is an explicit re-execution policy for the same operation, target, parameters, scope, and actor, after a recorded failure (SEC-IDEM-003).
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

No secret enters the repository or its history, and runtime records hold safe summaries only. The controls in place today lower the risk; none of them shows that the repository holds no secret.

- `SEC-SECRET-001` Partial. No secret or real credential enters the repository, whether in a tracked file of any kind or in the history. In place: `.gitignore` excludes `.env` files other than examples, private key and certificate files, and credential-shaped JSON; `.env.example` holds empty placeholders instead of credentials; and [SECURITY-REVIEW.md](SECURITY-REVIEW.md) step 7 defines a check of a change's added lines. Not in place: enforcement of these controls, a scan of tracked files or history, and any protection for a file that is already tracked or force-added. These are repository and review controls, not proof that no secret is present; a secret that reached a commit is treated as exposed even after it is removed. Evidence: `.gitignore:30-53`, `.env.example:2-13`.
- `SEC-SECRET-002` Partial. `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=false` must be set in any non-local environment. The code falls back to a dev placeholder key and `DEBUG=true` by default; the `SECRET_KEY` fallback is documented and has a test in the repository (`test_secret_key_uses_placeholder_for_missing_or_empty_environment`), the `DEBUG` default is documented but untested, and nothing enforces the override at deployment.
- `SEC-SECRET-003` Planned. Logs, Task records, and ToolRun summaries contain safe summaries only: never credentials, presigned URLs, raw tokens, or full prompts containing personal data.
- `SEC-SECRET-004` Planned. The audit trail records who (the actor, a user or automation identity, and the AI participant, if any), what operation, on which scope and resource, via which interaction origin, when, with what approval, and with what outcome.

### IDEM: retry, replay, and idempotency

Retries never duplicate side effects; an unconfirmed outcome is never reported as success or failure; approvals cannot be replayed. Identifiers are defined in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries", and outcomes in its "Outcomes, cancellation, and context changes".

- `SEC-IDEM-001` Planned. Every state-changing operation takes an `operation_id` that names one logical operation and is reused by every retry. The id is unique per actor; the server stores and resolves it together with the actor and binds it at first use to the AI participant, context and scope, operation, and the input as received, in a canonical form. The same id with the same binding returns the recorded outcome instead of executing again; a different binding is rejected with `idempotency_conflict`, which leaves the operation already bound to the id unchanged; concurrent requests with the same id execute at most once. The server resolves the binding before input validation; a request that claims a new id and is rejected records `not_executed` for it, and a refusal of a request whose id is already bound covers that request only. A confirmed `succeeded` or `not_executed` is final for its id, and trying again after a confirmed `not_executed` is a new operation with a new id. The server never merges requests by comparing input or timing, so protection holds only when the caller resends the same id. The server, not a client's record of the ids it issued, decides whether an id belongs to the actor and may be retried; a request under an id the caller has no issuance record for (recovered after a reload) only resolves an existing binding and never binds the id to a new operation.
- `SEC-IDEM-002` Planned. Each operation attempt (`attempt_id`) is a distinct record, separate from the product Task and from the Task's agent execution, with its own lifecycle. A retry of an operation whose outcome is `pending` or `outcome_unknown` (other than `partially_applied`) is a new attempt with the same `operation_id`, never a silent re-execution and never a new operation. A Task retry is a new agent execution; it reuses the `operation_id` of each step whose outcome is still unresolved in that sense, under the actor the step was first sent as.
- `SEC-IDEM-003` Planned. An approval is bound to one `operation_id` and consumed by the one execution it authorizes. A repeat of an approved request with the same `operation_id` and binding returns the recorded outcome and never executes again; an approval presented for another `operation_id` or binding is rejected, and a request under a new `operation_id` waits for its own approval. Executing the same operation again after a recorded failure (a confirmed `not_executed`, or a failure recorded with effects remaining) uses a new `operation_id` and needs a new approval, unless an explicit re-execution policy for that operation lets the new id reference the earlier approval.
- `SEC-IDEM-004` Implemented. Storage saves are retry-safe after a name collision: content is buffered once and re-uploaded under a new name on conflict, so an existing object is never overwritten. Evidence: `s3.py:151-180`; collision retry tests. This covers only the backend's own retry inside one save; it does not make a logical save idempotent, because a save repeated after a lost response stores a second object under a new name. Exactly-once saves rely on `operation_id` (`SEC-IDEM-001`).
- `SEC-IDEM-005` Planned. A caller that sent a state-changing request and received no confirmed answer (timeout, network error, lost response, or cancellation or navigation after sending), or received a refusal of that request alone such as `idempotency_conflict`, reports `outcome_unknown`, never success or failure; so does a retry under an id already sent that is stopped before sending. It resolves the outcome by an authorized status lookup or by retrying with the same `operation_id`; a lookup that finds no record does not prove the call was not executed. A failure the server recorded with effects remaining is reported as `outcome_unknown` with `partially_applied` and is final for its id. Cancelling on the client does not undo or stop server-side work. A caller that lost its id recovers it only through an authorized status lookup, never by guessing it or by matching input; when it cannot identify one operation, or the server has no record of the id it holds, it keeps `outcome_unknown`, issues no new `operation_id` for it, executes nothing again on its own, and asks the person whether to request it anew, saying, unless it has been ruled out, that the earlier operation may already have taken effect or may still take effect. A consequential operation requested anew in that state is not executed without the state check and reconciliation it needs to avoid a duplicate effect; that mechanism is not designed.

### FAIL: fail closed and cleanup

Failure means denial and cleanup, not a weaker default.

- `SEC-FAIL-001` Planned. Authorization, scope, or approval failure results in denial with no partial side effect.
- `SEC-FAIL-002` Implemented for database and storage. Configuration errors fail at startup or first use with clear messages rather than falling back to insecure defaults: an invalid `DATABASE_URL` and missing storage settings raise `ImproperlyConfigured`. Evidence: `config/database.py`, `s3.py`. The `SECRET_KEY` and `DEBUG` fallbacks (`SEC-SECRET-002`) are the documented exception.
- `SEC-FAIL-003` Planned. Failed or cancelled Tasks clean up runtime-local state and leave the conversation and persistent Files consistent.

### API: API surface hardening

Cross-cutting facts about the surfaces that exist today.

- `SEC-API-001` Implemented. Health endpoints return no sensitive data; the current payloads are static strings. Evidence: `core/views.py:7-17`, `agent/fastapi/routes/health.py:6-12`.
- `SEC-API-002` Partial. The browsable API renderer, `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` are development conveniences; their exposure must be decided before any non-local deployment. All four are enabled today, the ReDoc page by default rather than by configuration. `/agent/docs/oauth2-redirect`, Swagger UI's OAuth2 redirect page, is served with `/agent/docs` and disappears with it; `backend/agent/tests.py` asserts these documentation routes are reachable, so a decision to disable them must update that test. (Updated 2026-10-10.)
- `SEC-API-003` Partial. The CORS allowlist is limited to local dev origins and `ALLOWED_HOSTS` is local only; both must be environment-specific. The dev values are hard-coded today.

Internationalization has no area of its own. Locale and preference input is validated under `SEC-INJ-001`, translation strings never carry identifiers or permissions ([I18N.md](I18N.md)), and WebMCP tool names stay stable under `SEC-AGENT-002`.

## Status summary

Evidence names repository files and tests that exist; "none yet" means no code or test supports the item. Tests were not executed in this documentation pass.

| ID | Status | Evidence |
|---|---|---|
| SEC-AUTH-001 | Planned | none yet |
| SEC-AUTH-002 | Partial | `settings.py:21, 23, 35, 38` (auth and session apps and middleware installed) |
| SEC-AUTH-003 | Planned | none yet (`settings.py:87-92` sets no permission classes) |
| SEC-AUTH-004 | Fact/Partial | `asgi.py:14-16`, `agent/fastapi/app.py:5-12` (fact only) |
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
| SEC-SECRET-001 | Partial | `.gitignore:30-53`, `.env.example:2-13` (ignore rules and placeholders; nothing enforces them or scans tracked files and history) |
| SEC-SECRET-002 | Partial | `settings.py:10-14`; `test_secret_key_uses_placeholder_for_missing_or_empty_environment` |
| SEC-SECRET-003 | Planned | none yet |
| SEC-SECRET-004 | Planned | none yet |
| SEC-IDEM-001 | Planned | none yet |
| SEC-IDEM-002 | Planned | none yet |
| SEC-IDEM-003 | Planned | none yet |
| SEC-IDEM-004 | Implemented | `s3.py:151-180`; `test_collision_retry_rewinds_payload_and_preserves_content_type` |
| SEC-IDEM-005 | Planned | none yet |
| SEC-FAIL-001 | Planned | none yet |
| SEC-FAIL-002 | Implemented (database and storage) | `config/database.py:11-64`, `s3.py:43-70`; `test_invalid_urls_fail_instead_of_falling_back`, `test_missing_configuration_is_lazy_and_clear` |
| SEC-FAIL-003 | Planned | none yet |
| SEC-API-001 | Implemented | `core/views.py:7-17`, `agent/fastapi/routes/health.py:6-12`; `test_health`, `test_integrated_routing_contract` |
| SEC-API-002 | Partial | `settings.py:87-92`, `agent/fastapi/app.py:5-12` (enabled today) |
| SEC-API-003 | Partial | `settings.py:16, 82-85` (dev values hard-coded); `CorsPreflightTests` (allowed and disallowed preflight origins) |

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md): implemented versus planned components and runtime boundaries.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): conceptual shapes for `CollaborationContext`, `AgentTaskState`, `ToolRun`, and `InteractionContext`.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md): Human UI, WebMCP, Automation, and Browser Computer Use as entry points to shared operations.
- [WEBMCP.md](WEBMCP.md): the WebMCP technical contract and its experimental status.
- [I18N.md](I18N.md): locale handling and the separation of translated strings from machine values.
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md): the review procedure that cites these IDs.
- [CODE-REVIEW.md](CODE-REVIEW.md): the general review procedure and finding format.
- [TESTING.md](TESTING.md): existing tests and the security-negative, boundary, and approval tests still required.
- [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md): dependency/native-artifact remediation and maintenance planning, including `abi3t` wheel provenance and bundled-library updates; it changes no `SEC-*` definition or status.
