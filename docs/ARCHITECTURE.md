# Claus Architecture

Claus is an AI collaboration project centered on personal AI conversations and team Topic/Thread collaboration. Conversation provides context, while files, knowledge, background tasks, artifacts, and execution runtimes remain separate resources.

This document distinguishes the current runnable scaffold from longer-term architecture contracts. Planned components are not implemented unless the repository contains working code and verification for them.

## Current runnable scaffold

- Frontend: Next.js user surface at `/` and a separate product-operations Console reserved under `/console/*` (only the `/console` index page exists today), using React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright.
- Backend baseline: Django 6.1, Django REST Framework 3.18, django-cors-headers, FastAPI, Daphne, Uvicorn, psycopg (PostgreSQL driver), boto3 (S3-compatible storage client), and Python Playwright. The checked lane is CPython 3.12.15 on Linux x86-64 (glibc) with a hashed lock. Other lanes have static evidence only or are unsupported: Linux arm64 with glibc (for example NVIDIA DGX Spark) and macOS 15 or later on Apple Silicon are statically resolvable from the same lock, Windows through WSL2 uses the matching Linux lane, and native Windows, including Windows on Arm, cannot produce a working environment from the lock today; see [Platform and accelerator lanes](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes). No dependency is CUDA- or accelerator-specific today; a future accelerator package stays optional, keeps a CPU path, and gets its own lane, with CUDA on Arm (DGX Spark, RTX Spark) kept distinct from Arm without CUDA (for example Qualcomm Snapdragon). Django's wider 3.12–3.14 support does not qualify every Claus runtime combination.
- Django project: `config`.
- Django apps: `core` for the persistent product/control plane and `agent` for AI/RAG/agent execution.
- Routing: `/core/*` uses Django/DRF, `/agent/*` uses FastAPI, and `/admin/*` remains Django Admin.

`config.asgi.application` is the single composition root:

```text
config.asgi.application
├─ /agent → Agent FastAPI
└─ /      → Django ASGI
             ├─ /core/*
             └─ /admin/*
```

It sets Django settings and calls `get_asgi_application()` before importing Agent FastAPI. Daphne is first in `INSTALLED_APPS`, so `manage.py runserver` uses this ASGI application; Uvicorn imports the same object directly. WSGI is a Django-only fallback.

The Next.js 16 rewrites forward `/core/*` and `/agent/*` to the backend with their paths unchanged, including trailing slashes, and Next's own trailing-slash redirect is off (`skipTrailingSlashRedirect`). Both health URLs retain their final slash; `/agent/openapi.json` retains its unsuffixed form. Django keeps `APPEND_SLASH`, whose redirect has a relative `Location` (`/core/health` answers 301 to `/core/health/`) and therefore stays on the frontend origin. The agent FastAPI app does not redirect slash mismatches (`redirect_slashes=False`): its slash redirects were absolute URLs built from the upstream `Host` header, which behind the rewrite is the backend origin, so a wrong-slash path such as `/agent/health` or `/agent/docs/` returns 404. `frontend/src/proxy.ts` restores the UI's permanent (308) trailing-slash redirects, such as `/console/` to `/console`, preserving query parameters and collapsing leading slashes so a `Location` cannot become scheme-relative (`//host`). Its matcher skips Next internals under `/_next/` (static assets and image optimization) and the `/core/` and `/agent/` prefixes, and an in-code backend guard remains; it has no authentication, cookie, header, or locale logic. `frontend/next.config.ts` disables Next 16's dev-only MCP endpoint (`experimental.mcpServer: false`) and its generated agent rules (`agentRules: false`), and `npm run dev` binds to 127.0.0.1. The frontend uses Tailwind 4's PostCSS integration and native Next ESLint flat configuration. Runtime pins, locks, and remaining tooling exceptions are recorded in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md).

### Implemented foundations

The scaffold implements the following. The first three have tests in the repository (`backend/core/tests.py`, `backend/core/test_database.py`, `backend/core/test_storage.py`, `backend/agent/tests.py`); the runtime boundary has none:

- Health APIs: `GET /core/health/` (DRF) and `GET /agent/health/` (FastAPI), plus the ASGI routing contract above (`/agent/openapi.json` served, legacy `/api/health/` absent, wrong-slash `/agent/*` paths such as `/agent/health` answered with 404 and no redirect).
- Database configuration: `config/database.py` builds `DATABASES["default"]` from `DATABASE_URL`. Only `postgres`/`postgresql` URLs are accepted, parsing is strict (hostname and database name required, no fragment, no duplicate or malformed query parameters), query parameters become `OPTIONS`, and invalid URLs raise `ImproperlyConfigured` instead of falling back. An unset or blank `DATABASE_URL` selects SQLite at `backend/db.sqlite3`.
- File storage: `core/storage/s3.py` (`S3CompatibleStorage`) is the default Django storage backend. It reads `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_REGION`, and `OBJECT_STORAGE_BUCKET` lazily, validates the endpoint, rejects unsafe object names, saves with a conditional create so existing objects are never overwritten (a collision allocates a new name and retries), supports binary reads only, and returns presigned download URLs valid for one hour. The tests use a fake S3 client; no integration test against a real object store exists.
- Runtime boundary: `agent/runtime/browser/playwright.py` returns the async Playwright context manager without launching a browser. The `agent/llm`, `agent/rag`, `agent/orchestration`, and `agent/tools` packages are docstring-only boundaries.

### Not implemented

The repository does not implement the collaboration domain models (no Django models beyond Django's built-ins), authentication or authorization for product features (DRF default permissions apply, and the `/agent/*` surface runs outside Django middleware), approval workflows, the RAG pipeline, an LLM provider, agent orchestration, background execution, Browser sessions, Terminal, Workspace, realtime transport, WebMCP, or frontend i18n. The frontend consists of two pages and a health card component that calls both health endpoints. Sections below describe direction, not behavior.

### Related direction documents

- Interaction architecture (planned): Human UI, WebMCP, Automation, and Browser Computer Use are separate entry points that should invoke the same application operations, with authorization and approval decided server-side and the interaction origin recorded only for audit. See [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md); the WebMCP-specific contract, an experimental external technology, is in [WEBMCP.md](WEBMCP.md).
- Internationalization (planned): English canonical with Korean, `next-intl` without locale-prefixed routes, preference resolved from account setting, cookie, environment, then English. Nothing is installed yet. See [I18N.md](I18N.md).
- Security: trust boundaries, `SEC-*` requirements, and the security facts of the current code are in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md).

## Collaboration model

```text
Personal context
├─ Personal AI chat
├─ Personal topics
└─ Personal files/work

Team context
└─ Topic / Post
   ├─ Thread / messages
   ├─ People
   ├─ Shared AI participants
   ├─ Files
   ├─ Background tasks
   └─ Artifacts / shared views
```

A Topic/Thread is a durable collaboration context rather than merely a chat transcript. Messages, Files, Tasks, Artifacts, and Knowledge references should remain traceable to the context that created or shared them. Personal AI context remains private until a user explicitly shares it into a team context.

## Frontend surfaces

The product direction separates these concerns:

- **Conversation**: personal chat and team Topic/Thread discussion.
- **Files and artifacts**: uploaded Files and generated results with distinct provenance.
- **Tasks**: long-running work whose state is independent from the chat timeline.
- **Shared viewing surface**: documents, media, charts, tables, notebook/HTML output, Artifacts, and live Browser sessions.
- **Browser work**: observation and, when implemented, explicit user/AI control handoff.

A shared viewing surface is presentation state; it does not replace persistent File or Artifact storage. A live Browser session and a persistent Artifact have different lifecycles.

## Backend control plane

Django and `core` remain the default source of truth for persistent product state:

- Authentication and users
- Personal/team authorization boundaries
- Topic/Thread context and Messages
- File metadata and access
- Knowledge/RAG scope
- Artifact metadata
- Product Task and approval state
- Internal Django Admin workflows

Agent FastAPI is an execution interface, not an independent source of truth for users, teams, Topics, Files, or permissions. A future `core.Task` represents the user-visible product request; an agent execution represents one attempt to perform it, so retries must remain distinct.

## Realtime collaboration

Transport should match the resource being transferred:

- Chat events, Task state, presence, approvals, and presentation state may use WebSocket or SSE when implemented.
- Large binary Files belong in file/object storage rather than message transport.
- Live Browser viewing/control requires a runtime-appropriate channel and is not ordinary chat-message transport.

The exact transport is deferred until the corresponding feature is implemented.

## Agent and background execution

The conceptual flow is:

```text
Personal or Topic/Thread context
        ↓
Resolve permitted Files and RAG scope
        ↓
Choose direct response or background Task
        ↓
Model / Retrieval / Browser / Terminal / Workspace
        ↓
Independent Task state and intermediate events
        ↓
Message / Artifact / File / Task Result
        ↓
Clean up runtime-local state
```

An AI Task is not a conversation Message, and a product Task is not an agent execution attempt. LangGraph, DeepAgents, or another agent framework may be an implementation detail, but product contracts must not depend on one framework.

## Browser, Terminal, and Workspace lifecycles

- **Browser**: primary interactive Computer Use target; deterministic Playwright/CDP operations are preferred where appropriate.
- **Terminal**: task-scoped command execution when explicitly implemented and permitted.
- **Workspace**: isolated task working directory/runtime for temporary inputs and generated output.

These runtimes remain separate from persistent collaboration state. Persistent results return explicitly as Messages, Files, Artifacts, or Task results. Full desktop/OS streaming and control are outside the current scope.

## Files, Artifacts, and RAG

These concepts are not interchangeable:

```text
File upload
≠ automatic RAG indexing
≠ Artifact
≠ runtime-local temporary file

File upload
├─ personal/shared File
├─ optional current-task input
└─ optional explicit Knowledge/RAG indexing
```

Retrieval must respect personal, Topic/Thread, team/project, organization, and external-source scopes. Source, ownership, scope, version/validity, and indexing state should remain traceable when implemented. An Artifact is a generated or transformed result and must retain provenance to its Task and context; it is not synonymous with an uploaded File.

## Python concurrency

Claus is designed so correctness does not depend on the GIL:

- Avoid unprotected process-global mutable state.
- Use explicit synchronization for unavoidable shared mutable state.
- Prefer suitable external stores for distributed/shared state when those systems are introduced.
- Treat async I/O and parallel execution as complementary but distinct mechanisms.
- Verify free-threading safety of native and third-party dependencies before enabling it.
- Keep a GIL-enabled runtime as a compatibility fallback.

Free-threading does not remove the need for process isolation, task workers, or horizontal scaling where operationally appropriate.

[DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md) applies this existing direction to Python 3.15 Limited API and `abi3t` wheels. It defines compatible version selection, native artifact qualification, performance budgets, and the GIL-enabled fallback when a target combination is blocked. Neither a published wheel nor a resolver result means Claus currently supports Python 3.15/3.15t.

## Authorization and safety

When the corresponding features are implemented, every context or tool operation must check:

- User and AI participant authorization
- Personal versus team visibility
- File and Knowledge scope
- Allowed tools and targets
- Read-only versus state-changing behavior
- Required human approval
- Artifact and secret-handling requirements

These are architecture contracts, not claims that the current scaffold already implements them. The requirement-level version of these contracts, with stable `SEC-*` identifiers and the current implementation status of each, lives in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md).
