# Claus Interaction Interfaces

This document defines the responsibilities and boundaries of the four ways a request can enter Claus: the Human UI, the WebMCP adapter, Automation, and Browser Computer Use. It also defines the shared application operation layer that all four call, where authority over state changes lives, and how interaction origin is recorded.

This is not an API specification, a tool catalog, or a security policy. The WebMCP technical contract lives in [WEBMCP.md](WEBMCP.md). Security requirements (`SEC-*`) are defined only in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) and are cited here by ID. Conceptual state shapes live in [STATE-SCHEMA.md](STATE-SCHEMA.md). Structural context is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Status

Planned. Everything in this document is target architecture. None of the interfaces, the operation layer, the origin record, or the verification requirements described here is implemented.

- The repository contains two pages and a health card component that calls both health endpoints (see "Current state"). There is no application operation, no authentication or authorization for product features, no WebMCP code, no automation entry point, and no Browser session management.
- WebMCP is also Experimental: the specification is a Community Group draft report, not a W3C standard, and on 2026-10-08 the implementation-status page reported only origin trials and experimental support, with no entry reporting default-on support in a stable browser release. Anything that depends on it is Planned and Experimental. Details and verification dates are in [WEBMCP.md](WEBMCP.md).

Do not describe any contract below as current behavior.

## Terms

Planned. The terms name target-architecture concepts; none of them exists in code today.

- **Interaction interface**: the path through which a request enters Claus. There are four: Human UI, WebMCP adapter, Automation, Browser Computer Use.
- **Application operation**: a named, server-side action on Claus state with a defined actor, context, scope, input, and classification. Operations are defined in terms of the collaboration model (Topic/Thread, Message, File, Knowledge, Task, Artifact, approval), never in terms of UI widgets, DOM elements, or agent tools.
- **Interaction origin**: which interface a request came through. It is recorded, never trusted as a permission input.
- **Actor**: the authenticated user whose session and permissions the request runs under. When an AI participant acts, it is recorded separately and its effective permissions apply (SEC-AUTH-001).

## Core contracts

All Planned.

1. **The Human UI does not depend on WebMCP.** Every product function is reachable through the Human UI with no `document.modelContext`, no tool registration, and no browser agent present. A browser that lacks WebMCP, or a page served with `Permissions-Policy: tools=()`, loses nothing except agent exposure.
2. **Human UI, WebMCP, and Automation use the same application operations.** No interface gets a private code path to state. A tool callback, a button handler, and a scheduled job all resolve to the same operation with the same validation.
3. **WebMCP is a semantic agent adapter, not a mandatory layer wrapping ordinary DOM clicks.** It exposes selected operations to a browser agent by meaning ("share this File into this Topic"), not by UI mechanics ("click the Share button"). Not every control becomes a tool, and no Human UI code waits on a tool.
4. **Visual and spatial manipulation is handled by the Human UI or by Browser Computer Use.** Drag, placement, drawing, region selection, and other actions whose meaning is the visual result are not forced into semantic operations. A person does them in the Human UI; an agent does them by actuating a browser.
5. **Application operations are separated from the browser execution environment.** An operation is valid without a browser, a DOM, a tab, or any client-side state. A browser is a client of operations, or, for Computer Use, a task-scoped runtime that is itself a client. Runtime-local state never becomes product state by itself (SEC-RUNTIME-002, SEC-FILE-006).
6. **Authorization and approval for state changes are verified server-side in the end.** Client-side checks, tool annotations, browser exposure lists, and runtime isolation improve UX or reduce attack surface; none of them is the decision (SEC-AUTH-001, SEC-SCOPE-003, SEC-APPROVE-001).
7. **Interaction origin is audit and diagnostic data, not a basis for permission.** Knowing that a request came from WebMCP, Automation, or a Browser session never grants, widens, or narrows what the actor may do (SEC-AGENT-001).

## Interfaces

### Human UI

The Next.js frontend used by people. Planned responsibilities:

- Present conversation, Files, Tasks, Artifacts, and the shared viewing surface as described in [ARCHITECTURE.md](ARCHITECTURE.md).
- Call application operations through the Django control-plane API (`/core/*`, DRF) and, where an execution concern requires it, the Agent surface (`/agent/*`).
- Own visual and spatial manipulation (contract 4).
- Reflect state produced by every other interface. When an agent shares a File through a WebMCP tool or a background Task posts a result, the Human UI shows it through the realtime channel described in ARCHITECTURE once that channel exists. A person must always be able to see what an agent did.
- Keep every client-side permission check cosmetic: hiding a control is not authorization (contract 6).

The Human UI is the reference interface. If an operation is reachable by an agent but not by a person, that is a design defect.

### WebMCP adapter

Planned and Experimental. The WebMCP adapter is browser-side code in the Human UI that exposes a small set of application operations to a browser-resident agent acting in the signed-in user's session, so the agent can call them by meaning instead of by clicking. A tool callback calls the same operation the UI would call, under the user's existing session (SEC-AGENT-001, SEC-AGENT-003); browser-side exposure controls are not authorization for Claus operations (SEC-AGENT-002). The UI reflects what a tool did, which is the role difference from Browser Computer Use: WebMCP calls operations, Computer Use actuates the UI. No registration code, tool list, or adoption decision exists today. The full contract, including registration lifecycle, naming, annotations, exposure controls, context switches, and verification requirements, is in [WEBMCP.md](WEBMCP.md).

### Automation

Planned. Automation is any non-interactive caller of application operations: scheduled or triggered flows, integrations and scripted clients, and in-product AI invoking operations outside a live UI. Responsibilities and limits:

- Runs as an authenticated actor known to the control plane. Anonymous automation does not exist. The Agent FastAPI surface is not an identity authority for it (SEC-AUTH-002).
- Calls the same operations with the same validation, scope checks, and approval path as the Human UI (contract 2). Automation does not get a bypass for approval-required operations (SEC-APPROVE-001).
- Retries are expected, so every state-changing automated call carries an idempotency key or targets an idempotent operation (SEC-IDEM-001). Each attempt is a distinct record (SEC-IDEM-002).
- Long-running work runs as a background Task and never blocks a conversation. Entry through Automation is recorded as `automation`; execution inside a Task runner is recorded as `background_task`. A product Task and an execution attempt remain distinct, as ARCHITECTURE requires.
- Automation that reaches external systems holds credentials server-side only and logs each call with a safe summary (SEC-TOOL-001, SEC-TOOL-002).

### Browser Computer Use

Planned. Browser Computer Use is an AI participant driving a browser inside a task-scoped runtime; the execution model itself is set in [CONTEXT.md](CONTEXT.md) and [ARCHITECTURE.md](ARCHITECTURE.md). It is the interface for work whose meaning is the visual result (contract 4) and for pages that offer no operation layer, including third-party sites.

- The runtime is attached to one Task, isolated from other runtimes and from the control plane, and holds no long-lived credentials (SEC-RUNTIME-001).
- It produces nothing durable on its own. Screenshots, downloads, extracted data, and generated files re-enter Claus only as Messages, Files, Artifacts, or Task state through authorized writes; runtime-local state is discarded when the Task ends (SEC-RUNTIME-002, SEC-FAIL-003).
- Page content it observes is untrusted input to the model. Text on a page carries no authority over Claus operations (SEC-INJ-001, SEC-INJ-002).
- Viewing a live session and taking control are authorized per participant; control handoff is exclusive and visible (SEC-RUNTIME-003).
- When a Computer Use session actuates Claus's own Human UI, the resulting requests are ordinary operation calls. The server attributes them to the `browser_computer_use` origin from the session it already knows about, never from a client-asserted value. How such a session would authenticate to Claus, if it is ever allowed to, is undecided; it must not be given user tokens as input (SEC-AGENT-003).

The only runtime code today is a lazy `async_playwright()` factory with no launch and no session management (SEC-RUNTIME-004).

## Responsibility matrix

Planned.

| Concern | Human UI | WebMCP adapter | Automation | Browser Computer Use |
|---|---|---|---|---|
| Who acts | A person | A browser agent in the person's session | A non-interactive caller or AI participant | An AI participant driving a browser in a Task |
| How state is reached | Calls operations | Tool callback calls operations | Calls operations | Actuates a page; the page calls operations |
| Semantic operations | Yes | Yes | Yes | Indirect, through the UI |
| Visual/spatial manipulation | Yes | No | No | Yes |
| Works without the others | Yes | Needs the Human UI page | Needs no UI | Needs a runtime |
| Recorded origin | `human_ui` | `webmcp` | `automation` or `background_task` | `browser_computer_use` |
| Authorization decided by | Server | Server | Server | Server |
| Approval path | Same | Same | Same | Same |

Choosing an interface:

- A person doing ordinary work: Human UI.
- An agent in the user's browser needs a well-defined operation: WebMCP tool, once adopted; the Human UI must still work without it.
- An agent needs a visual result, or a page without an operation layer: Browser Computer Use.
- No person present, or an external trigger: Automation, usually through a background Task.
- Work that outlives a request: a background Task, regardless of how it was requested.

## Shared application operations

Planned. The operation layer belongs to the Django control plane (`core`), which remains the source of truth for users, permissions, context, Files, Knowledge scope, Tasks, and approvals. The Agent FastAPI surface is an execution interface and never a second operations authority (SEC-AUTH-002).

Each operation defines:

- An actor and, when AI is involved, an AI participant.
- The context (personal, or Topic/Thread) and exactly one owning scope: personal, topic, team/project, organization, or external (SEC-SCOPE-001).
- Validated input. Input is data; instructions embedded in it carry no authority (SEC-INJ-001).
- A classification: read, untrusted read, mutation, or consequential (the class [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) calls high-impact). The classification drives approval requirements server-side and is what [WEBMCP.md](WEBMCP.md) maps to tool annotations. Changing the classification of an operation is a security change.
- A `request_id` usable as an idempotency key, so retries of mutations and consequential operations invoked by agents or Tasks do not duplicate side effects (SEC-IDEM-001).
- The attached `InteractionContext` (next section).

Illustrative example, not implemented. The shape an operation call might carry, independent of interface:

```json
{
  "operation": "operation identifier",
  "actor_ref": "user identifier",
  "ai_participant_ref": "AI participant identifier or null",
  "context_ref": "personal or Topic/Thread context",
  "scope": "personal | topic | team | organization | external",
  "classification": "read | untrusted_read | mutation | consequential",
  "input": "validated parameters",
  "interaction": "InteractionContext"
}
```

Operation identifiers, enum values, error codes, and other machine values are locale-independent and are never translated; labels are mapped at the UI edge (see [I18N.md](I18N.md)). A tool `name` in WebMCP is such a machine value.

## Server-side authority

Planned. For every operation, regardless of interface:

- **Authorization** is checked against the actor and, for AI, the AI participant's effective permissions, on the server, on every call (SEC-AUTH-001, SEC-SCOPE-003). New API views are default-deny (SEC-AUTH-003).
- **Approval** for high-impact operations is an explicit, recorded human decision bound to the specific operation, and the Task waits on it (SEC-APPROVE-001 to SEC-APPROVE-003, SEC-IDEM-003).
- **Consequential operations invoked without the approval path are rejected server-side**, whatever annotation or UI hint the caller claims (SEC-AGENT-004).
- **Failure is closed**: an authorization, scope, or approval failure leaves no partial side effect (SEC-FAIL-001).
- **The `/agent/*` surface is not covered by Django middleware today** (CSRF, session, auth, CORS, clickjacking; a current fact, see SEC-AUTH-004). Any `/agent` endpoint beyond health needs its own authentication and authorization tied to control-plane identity before it can host an operation.

Client-side state, including which tools are registered, which controls are visible, and what a browser agent believes it may do, is never consulted for these decisions.

## Interaction origin as audit data

Planned. Every operation call carries an `InteractionContext`, described conceptually in [STATE-SCHEMA.md](STATE-SCHEMA.md). Its fields: `origin`, `actor_ref`, `ai_participant_ref`, `context_ref`, `task_ref`, `operation`, `approval_ref`, `request_id` (usable as an idempotency key), and `recorded_at`. It is not persisted today.

`origin` takes one of:

- `human_ui`: a person acting in the frontend.
- `webmcp`: a tool callback registered by the WebMCP adapter.
- `automation`: a non-interactive caller.
- `browser_computer_use`: a Computer Use session actuating a page.
- `background_task`: a Task runner executing on behalf of a Task.

Rules:

- Origin is set by the server from what it can verify (the authenticated session, the known Task or Browser session, the entry endpoint). A client-supplied origin value is ignored or rejected, never trusted.
- Origin is used for the audit trail (who, what, on which scope, via which origin, when, with what approval, with what outcome: SEC-SECRET-004), for diagnostics, for abuse analysis, and for presentation ("done by AI through a tool"). It is never an input to authorization or approval (contract 7).
- Origin records contain safe summaries only: no credentials, presigned URLs, raw tokens, or full prompts with personal data (SEC-SECRET-003).
- When the user's context changes between request and execution (Topic/Thread switch, logout, navigation), the adapter that issued the call checks the context at execution time, and the server re-validates actor, context, and scope regardless. The WebMCP-specific handling of in-flight executions is in [WEBMCP.md](WEBMCP.md).

## Boundaries and anti-patterns

Planned contracts, stated as what not to do:

- Do not implement a business rule, permission check, or approval only in the Human UI or only in a tool callback.
- Do not register a WebMCP tool for every control, and do not make any Human UI behavior wait on tool registration.
- Do not give Automation or a Task runner a path around approval or scope checks because "no user is present".
- Do not let a Browser session write product state directly; it returns results through authorized writes.
- Do not derive permission, trust, or a shortcut from `origin`.
- Do not accept credentials, tokens, or user identifiers as operation or tool input to act as someone else.
- Do not let text observed by a Computer Use session or returned by a tool act as an instruction.
- Do not log origin records with secrets, presigned URLs, or prompt contents.

## Current state

Implemented today (tests exist in the repository for the first four items and were not executed in this documentation pass):

- Two pages: `/` rendering `WorkspaceShell` (`frontend/src/app/(user)/page.tsx`) and `/console`, a static placeholder (`frontend/src/app/console/page.tsx`).
- `HealthCard` (`frontend/src/components/HealthCard.tsx`) calls `GET /core/health/` and `GET /agent/health/` through the axios instances in `frontend/src/lib/api.ts` and shows connection badges and JSON.
- One frontend Playwright test (`frontend/tests/visual/home.spec.ts`) covering both pages.
- Backend health endpoints, ASGI composition, `DATABASE_URL` parsing, and the S3-compatible storage backend, as described in [ARCHITECTURE.md](ARCHITECTURE.md).
- A lazy Playwright factory, `playwright_runtime()` in `backend/agent/runtime/browser/playwright.py`, that does not launch a browser. No test exercises this factory.

Not implemented: application operations, authentication or authorization for product features, approval, `InteractionContext` recording, the WebMCP adapter, any Automation entry point, Browser session management, realtime transport. The health endpoints are the only product endpoints any interface can call, and their payloads are static values; Django Admin (`/admin/*`), `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` also respond but expose no application operation.

## Verification requirements

Planned. When the operation layer and any second interface exist, tests must show, in addition to the checks in [TESTING.md](TESTING.md):

- The same operation with the same actor yields the same authorization decision through every interface.
- A denied operation has no side effect, through every interface.
- An approval-required operation stops in `waiting_for_approval` through every interface, and executes only after a bound approval.
- `origin` is recorded correctly for each interface, and a client-supplied origin is ignored.
- The Human UI functions with WebMCP absent or disabled.
- A context switch between request and execution is detected by the adapter and rejected by the server.
- A Browser session's runtime-local state does not survive Task completion or failure as product state.

Changes to any of these areas are security-sensitive and go through [SECURITY-REVIEW.md](SECURITY-REVIEW.md) after the ordinary [CODE-REVIEW.md](CODE-REVIEW.md) pass.

## Related documents

- [CONTEXT.md](CONTEXT.md): canonical rules.
- [ARCHITECTURE.md](ARCHITECTURE.md): structure, control plane, runtimes, realtime.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): `InteractionContext`, `AgentTaskState`, `ToolRun`, `BrowserSession`.
- [WEBMCP.md](WEBMCP.md): the WebMCP technical contract.
- [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md): `SEC-*` requirement registry.
- [I18N.md](I18N.md): locale handling and machine-value separation.
- [TESTING.md](TESTING.md): existing checks and future test requirements.
