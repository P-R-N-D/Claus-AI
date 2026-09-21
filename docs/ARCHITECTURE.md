# Claus Architecture

Claus is an AI collaboration project centered on personal AI conversations and team Topic/Thread collaboration. Conversation provides context, while files, knowledge, background tasks, artifacts, and execution runtimes remain separate resources.

This document distinguishes the current runnable scaffold from longer-term architecture contracts. Planned components are not implemented unless the repository contains working code and verification for them.

## Current runnable scaffold

- Frontend: Next.js user surface at `/` and a separate product-operations Console at `/console/*`, using React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright.
- Backend baseline: Django 6 on Python 3.12–3.14, Django REST Framework, FastAPI, Daphne, and Uvicorn.
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

The current implementation provides health APIs and package boundaries only. It does not implement the collaboration domain models, RAG pipeline, LLM provider, agent orchestration, background execution, Browser sessions, Terminal, Workspace, realtime transport, or authorization features described below.

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

## Authorization and safety

When the corresponding features are implemented, every context or tool operation must check:

- User and AI participant authorization
- Personal versus team visibility
- File and Knowledge scope
- Allowed tools and targets
- Read-only versus state-changing behavior
- Required human approval
- Artifact and secret-handling requirements

These are architecture contracts, not claims that the current health-only scaffold already implements them.
