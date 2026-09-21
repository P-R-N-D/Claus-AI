# Claus Agent Guidelines

This file is the canonical source of truth for AI-facing context and instructions in this repository. Read it before following any bridge file, tool-specific rule, or generated suggestion.

## Project purpose

Claus is an AI collaboration project where people and AI share context, discuss work, execute tasks, and produce results together.

The primary collaboration direction is topic- and thread-based rather than chat-only:

- Personal AI conversations and personal topics remain user-scoped by default.
- Team topics and threads contain people and shared AI participants.
- AI may use files, RAG knowledge, background tasks, Browser, Terminal, and Workspace execution when the current task and permissions allow it.
- Documents, media, charts/tables, notebook/HTML results, artifacts, and live browser sessions may be presented in a shared viewing surface.
- Full desktop/OS streaming and control are outside the current project scope. Browser-based Computer Use is the primary interactive execution target.

## Current application scaffold

- Frontend: Next.js user UI at `/`, product Console at `/console/*`, React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright tests.
- Backend: Django 6 on Python 3.12, or newer, using one Django project (`config`) with Django apps `core` and `agent`.
- `/core/*`: Django REST Framework control-plane APIs; `/agent/*`: Agent FastAPI; `/admin/*`: Django Admin.
- `config.asgi.application` composes Django and FastAPI and is served by both Daphne-backed `manage.py runserver` and direct Uvicorn.
- Python Playwright under `agent/runtime/browser` is the async Browser Computer Use foundation, separate from frontend Playwright testing.

Only health APIs and architecture boundaries are currently implemented. Collaboration models, RAG, LLM orchestration, background execution, and browser sessions remain future work.

## Document roles

- `docs/CONTEXT.md` is the canonical AI-facing instruction and context file.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) describes the project architecture direction and current scaffold boundaries.
- [`docs/STATE-SCHEMA.md`](STATE-SCHEMA.md) describes conceptual collaboration and runtime state shapes. It is not a database schema.
- [`docs/TESTING.md`](TESTING.md) describes testing strategy and current scaffold checks.
- Human-facing explanation documents should be split by language when both Korean and English versions are maintained.

## Core collaboration rules

- Treat Topic/Thread context as a durable collaboration context, not merely a sequence of chat messages.
- Keep private Personal AI context separate from shared team context.
- Do not expose personal conversations, personal files, or personal working state to a team context unless the user explicitly shares them.
- Shared AI may use only the conversation, files, knowledge, and tools permitted for its current context.
- Keep file sharing and RAG/knowledge indexing separate. Uploading a file must not automatically make it team-wide or organization-wide knowledge.
- Keep conversation, background tasks, persistent files/artifacts, and execution runtimes as separate concerns.
- Long-running work must not block the conversation that requested it.
- Browser/Terminal/Workspace execution is task-scoped. Persistent results should return as files, artifacts, task state, or messages rather than relying on runtime-local state.

## Architecture direction

### Frontend

The primary frontend stack is Next.js + React + TypeScript + Tailwind CSS. The frontend API client uses axios. Frontend alert/modal UX uses SweetAlert2.

The frontend direction includes:

- Personal AI conversations
- Team Topic/Thread collaboration
- Rich AI responses and generated artifacts
- File and knowledge interactions
- Background task status
- Shared document, media, data, and artifact views
- Live Browser viewing and browser-control handoff when implemented

UI changes must remain responsive and must be validated with Playwright when they affect rendered behavior or appearance.

### Backend

The primary backend stack is Django + Django REST Framework + Django Admin. Django ORM is the primary ORM. Django remains the default control plane for users, permissions, collaboration context, files, knowledge scope, task state, and internal admin workflows.

Long-running AI work and Browser/Terminal/Workspace execution should be separated from normal HTTP request handling. A separate runtime service may be introduced later only when its responsibility and operational benefit are clear.

Agent orchestration is an implementation detail. LangGraph, DeepAgents, or another framework may be used when appropriate, but Claus domain contracts should not depend on one orchestration framework.

FastAPI is approved only for the current `agent` execution surface. General product and control-plane APIs remain in `core` with Django REST Framework. Do not expand FastAPI into `core`, create another FastAPI project/service, or introduce another backend framework without explicit approval. Do not add custom domain models, custom migrations, SQL, SQLAlchemy, Alembic, Docker, Nginx, K8s, Helm, or deployment manifests without explicit approval.

## RAG and knowledge direction

RAG is contextual knowledge retrieval, not an automatic consequence of file upload.

Keep at least these scopes conceptually distinct:

- Personal files/context
- Current Topic/Thread files
- Team/project knowledge
- Organization knowledge
- External sources

Retrieval must respect the current user's and AI participant's effective permissions. Source, ownership, scope, version/validity, and indexing state should remain traceable when those features are implemented.

## Tool and execution roles

- Playwright is the primary browser automation and browser validation tool.
- Browser-based Computer Use may combine deterministic Playwright/CDP actions with visual interaction only when needed.
- Terminal and Workspace execution should run in isolated task runtimes when implemented.
- Newman/Postman CLI is used for API verification when API collection testing is in scope.
- Local or online LLMs may support planning, retrieval, generation, summarization, and tool use.
- Existing specialized scanner/compliance skills remain task-level experiments and are not the top-level Claus product definition.

## Concurrency direction

Claus should be designed for free-threaded Python compatibility.

- Application correctness must not rely on the GIL as an implicit synchronization mechanism.
- Avoid unprotected process-global mutable application state.
- Use explicit synchronization when shared in-process state is unavoidable.
- Prefer PostgreSQL, Redis, queues, or other external stores for distributed shared state.
- Treat ASGI async I/O and free-threaded parallelism as complementary mechanisms.
- Verify thread safety and free-threaded compatibility of native and third-party dependencies before enabling GIL-free production execution.
- A GIL-enabled CPython runtime remains a compatibility and stability fallback.

## Development rules

- Do not claim a planned feature is implemented unless the current code and tests demonstrate it.
- Preserve the separation between personal and shared context.
- Preserve the separation between conversation, tasks, files/artifacts, knowledge, and execution runtimes.
- Check authorization before accessing shared files, knowledge, or tools.
- Require explicit approval for high-impact or state-changing actions when such workflows are implemented.
- UI/web design changes must include Playwright-based visual testing.
- Backend changes must at least run Django checks.
- API contract changes should include appropriate Django/DRF tests and Postman/Newman verification when that workflow is in scope.
- Do not rely on the GIL for thread safety.

## Repository guardrails

- Do not implement DB schema changes without explicit approval.
- Do not create migrations without explicit approval.
- Do not edit ORM models without explicit approval.
- Do not implement backend or frontend features unless explicitly requested.
- Do not install packages or change lockfiles unless explicitly requested.
- Do not use root, sudo, or administrator privileges.
- Do not run destructive commands.
- Do not write or commit secrets, API keys, passwords, tokens, or real server credentials.
- Do not create `.env` files. Use `.env.example` only when explicitly needed.
- Preserve existing LICENSE policy.
