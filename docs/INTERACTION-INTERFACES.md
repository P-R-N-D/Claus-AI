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
- **Actor**: the authenticated user whose session and permissions the request runs under. When an AI participant acts, it is recorded separately and its effective permissions apply (SEC-AUTH-001). When a shared AI participant acts on a member's direct request, that member is the actor ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md), "Direct requests and referenced content").

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
- Retries are expected, so every state-changing automated call carries the `operation_id` of its logical operation, created and stored before the first attempt and reused by every retry (SEC-IDEM-001). Each attempt is a distinct record (SEC-IDEM-002). See "Operation identity and retries".
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
- A classification, assigned by server policy (see "Classification and approval"): read, untrusted read, mutation, or consequential (the class [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) calls high-impact). The classification decides server-side whether an approval is required and is what [WEBMCP.md](WEBMCP.md) maps to tool annotations. Changing the classification of an operation is a security change.
- For a state-changing operation, an `operation_id` naming the logical operation, so retries do not duplicate side effects (SEC-IDEM-001; see "Operation identity and retries").
- The attached `InteractionContext` (see "Interaction origin as audit data").

Illustrative example, not implemented. The shape an operation call might carry, independent of interface:

```json
{
  "operation": "operation identifier",
  "operation_id": "logical operation identifier, reused by retries; null for a read",
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

## Classification and approval

Planned. Every state change is authenticated, authorized, scope-checked, and subject to server policy, whatever its class (SEC-AUTH-001, SEC-SCOPE-003). Only consequential operations also need an explicit human approval (SEC-APPROVE-001).

| Class | Meaning | Server requirement |
|---|---|---|
| read | Returns data and changes nothing. | Authorization and scope check. |
| untrusted read | A read whose result carries user, file, retrieval, or external content. | As read; the result is data, never instruction (SEC-INJ-001). |
| mutation | Any state change inside the actor's permitted scope that server policy does not classify as consequential, for example changing one's own settings, creating or editing a draft, or a low-risk edit the actor explicitly asked for in a context they may write to. | Authorization, scope, and policy checks, an `operation_id`, and an audit record. No approval step. |
| consequential | Server policy requires an explicit approval. At least: irreversible deletion; publishing personal material into a team or organization scope; posting or sending outside Claus; external work that uses sensitive credentials; high-risk Browser, Terminal, or Workspace execution. | Everything a mutation requires, plus an approval bound to this operation before execution (SEC-APPROVE-001 to SEC-APPROVE-003). Until the approval is recorded it is not executed: a call carried by a Task returns `pending` while the Task waits in `waiting_for_approval`, and a path that bypasses the approval is rejected (SEC-AGENT-004 for tools). |

- The server assigns the class per operation, and per input where the risk depends on it (moving an item to a recoverable trash versus deleting it permanently). A caller's claim, a tool annotation, a browser or agent confirmation prompt, and the interaction origin neither raise nor lower it (SEC-APPROVE-001, SEC-AGENT-001, SEC-AGENT-002).
- An approval is a recorded decision by a person with authority over the operation. A confirmation prompt shown by a browser or an agent is not one.
- When no Task carries the call (a person's own action in the Human UI) and server policy lets that person approve it, the approval is that person's explicit confirmation of the operation shown to them, recorded server-side and bound to the `operation_id` before the request is sent; the call then executes without a `pending` state.
- An approval step on an ordinary mutation is not a safety measure. When a mutation needs one, server policy reclassifies it as consequential, which is a security change.

## Operation identity and retries

Planned. Retries happen through every interface: a person resubmits a form, a network drops a response, an agent repeats a call, a Task runner restarts. Three identifiers keep them apart:

| Identifier | Identifies | Created by | Reuse |
|---|---|---|---|
| `operation_id` | One logical operation: one intent to run one operation with one input in one context. | The caller that owns the intent, before the first attempt. | Every retry, resubmission, and status lookup of that intent. It is the idempotency key. |
| `attempt_id` | One try to execute that operation. | Whoever makes the try: the client or the Task runner, for each send. | Never. Each attempt is its own record (SEC-IDEM-002). |
| `request_id` | One HTTP request or trace span. Optional. | The transport or tracing layer. | Never. Log correlation only; never used to detect duplicates or to authorize. |

Where the `operation_id` comes from:

- Human UI: created when a form or action is prepared for submission and kept across resubmits of the same input until a final outcome arrives: `succeeded`, `not_executed`, or a failure recorded with effects remaining (a `pending` answer keeps it). A changed input is a new intent: if the earlier outcome is `pending` or `outcome_unknown`, the UI first resolves the earlier id by status lookup and reports that outcome as the earlier input's, then sends the changed input under a new id.
- Background Task: created when the step that calls the operation is planned, and stored with the Task before the first attempt, so a restarted runner resumes with the same id.
- Automation: created per logical action and stored by the caller before sending.
- WebMCP: created in the tool callback unless the agent passes back an id whose earlier result was `pending` or an `outcome_unknown` other than `partially_applied` ([WEBMCP.md](WEBMCP.md)).

Retry or new intent:

- The server cannot reliably tell a retry from a new intent with the same input, and it never merges requests by comparing input, content, or timing. Duplicate protection holds only when the caller resends the same `operation_id`; a caller that creates a new id for every HTTP call gets no protection from it.
- A new intent gets a new `operation_id`, even when its input equals an earlier one. Sending the same message twice on purpose is two operations.
- A caller that lost its `operation_id` (reload, crash, navigation) does not guess. It looks up the actor's recent operations in the current context, which the server returns with the `operation_id`, the operation name, a safe input summary, and the outcome. If the listing shows the operation, the caller uses that outcome and id; if it shows none, the caller does not start a new operation on its own and asks the person (see `outcome_unknown`).

Server rules (SEC-IDEM-001 to SEC-IDEM-003):

- An `operation_id` is unique per actor: the server stores and resolves it together with the actor, so the same id sent by another actor is a different key and neither returns nor reveals the first actor's operation. It is not a credential.
- The first request with an `operation_id` binds it to the AI participant, the context and scope, the operation, and the input as received, in a canonical form, before validation. The `operation_id` itself is not part of the bound input.
- A later request with the same id and the same binding is never executed again. It receives the recorded outcome, or `pending` while the first execution runs.
- A later request with the same id and a different binding is rejected with `idempotency_conflict`. The rejection applies only to that request; the operation already bound to the id keeps its own outcome, which the caller reads by status lookup.
- The server resolves the binding before input validation and state-dependent checks. A request that claims a new id and is then rejected (validation, authorization, scope, approval denied) records `not_executed` as that id's outcome, so a late duplicate receives it instead of executing. A refusal of a request whose id is already bound (for example, because the actor no longer has access to the context) covers that request only: like `idempotency_conflict`, it carries an error code and no outcome for the id, and the caller treats it as `outcome_unknown`.
- Claiming the id and starting execution are one atomic step, so concurrent requests with the same id execute at most once.
- The binding and outcome are kept at least as long as callers may retry or look up the outcome; the period is set with the implementation.
- Attempts and the product Task have separate lifecycles. An attempt ends when its try ends; the Task continues, waits for approval, or is cancelled on its own terms.
- A confirmed `succeeded` or `not_executed` is final for its `operation_id`; a repeat receives it. Trying again after a confirmed `not_executed` (corrected input, restored permission, a transient failure confirmed to have left no effect) is a new intent with a new `operation_id`, not a retry. Only `pending` and an `outcome_unknown` other than `partially_applied` are resolved with the same id.
- An approval is bound to one `operation_id` and consumed by the execution it authorizes. A repeat with the same id and binding returns the recorded outcome and never executes again; an approval presented for another `operation_id` or binding is rejected, and a new `operation_id` waits for its own approval. Executing the same operation again after a recorded failure (a confirmed `not_executed`, or a failure recorded with effects remaining, below) uses a new `operation_id` and needs a new approval, unless an explicit re-execution policy for that operation lets the new id reference the earlier approval.

## Outcomes, cancellation, and context changes

Planned. A caller reports what it knows, not what it expects. A state-changing call ends in one of four outcomes:

- `succeeded`: the server confirmed that the change was applied.
- `not_executed`: it is confirmed that no change was applied. Either no request under its `operation_id` was ever sent (cancelled, or the context changed first), or the server rejected the request that claimed the id (validation, authorization, scope, approval denied) and recorded `not_executed` for it. A failure after execution began is `not_executed` only when the server confirms no effect remains, which SEC-FAIL-001 requires for authorization, scope, and approval failures; otherwise the server records what remains and the caller treats the call as `outcome_unknown` until a status lookup shows the state. A failure recorded with effects remaining is final for its `operation_id`: a repeat or status lookup returns that recorded state, and the caller reports it as `outcome_unknown` with `error.code: "partially_applied"` together with the state, never as success or as `not_executed`. The caller does not retry it or look it up again for a different answer; any further execution is a new operation, with a new `operation_id` and, when consequential, a new approval unless a re-execution policy allows otherwise (see "Operation identity and retries"), that the person decides on.
- `pending`: the server accepted the call and it has not finished, for example while its Task waits in `waiting_for_approval`.
- `outcome_unknown`: the call was sent and no confirmed answer arrived, because of a timeout, a network error, a lost response, or because the caller stopped waiting on cancellation, a context change, or navigation. It is never reported as success or as failure. The caller resolves it by a status lookup, or by retrying with the same `operation_id` and the same input, as the same actor, from the same context, which returns the recorded outcome instead of executing again (SEC-IDEM-005). When another member asks a shared AI to redo an operation whose outcome is unknown, the AI does not resend it under that member's key; it reports that the earlier outcome is unknown. A lookup that finds no record does not prove the call was not executed, because the original request may still arrive; the caller then retries with the same id or asks the person, and never starts a new operation on that basis. A refusal of the request alone, such as `idempotency_conflict`, is also `outcome_unknown` for the caller: it shows only that this request did nothing. An `outcome_unknown` with `partially_applied` is the one confirmed, final case (see `not_executed` above) and is not resolved further.

Rules:

- Before sending, a caller whose request is no longer wanted, or no longer belongs to the current context, does not send it. The outcome is `not_executed` when no request under that `operation_id` was sent before; for a retry under an id already sent, the operation stays unresolved and the caller reports `outcome_unknown`.
- After sending, cancelling on the client only stops waiting. Aborting a request, unregistering a tool, or navigating away neither stops nor undoes work the server has started. An operation that offers server-side cancellation does so as a separate operation that names the target `operation_id` and reports whether the cancellation took effect.
- Every call names its context. The server re-validates actor, AI participant, and scope for that context on every call (SEC-AUTH-001, SEC-SCOPE-003), but it does not know which Topic/Thread a client currently shows, so noticing that the person has moved to another context before sending is the caller's job.
- A read whose context changed while it was in flight returns no data; the caller discards the response and reports `context_changed`.
- A server answer that arrives after the context changed is reported with a safe summary only: the `operation_id`, the operation name, the outcome, and the error code when there is one. Its details, recorded state included, are read again through a status lookup called from the owning context, so personal and team data never cross through a stale response.
- A status lookup by `operation_id` is a read operation. It names its context like every call, is limited to the actor's own operations, and returns details only for an operation owned by the context it names. For an operation owned by another context that the AI participant, if any, may also read, it returns only the `operation_id`, the operation name, and the outcome; otherwise it returns no record.

## Server-side authority

Planned. For every operation, regardless of interface:

- **Authorization** is checked against the actor and, for AI, the AI participant's effective permissions, on the server, on every call (SEC-AUTH-001, SEC-SCOPE-003). New API views are default-deny (SEC-AUTH-003).
- **Approval** for consequential (high-impact) operations is an explicit, recorded human decision bound to the specific operation and its `operation_id`; a Task that carries the call waits on it (SEC-APPROVE-001 to SEC-APPROVE-003, SEC-IDEM-003). An ordinary mutation needs authorization and policy checks, not an approval (see "Classification and approval").
- **Consequential operations invoked without the approval path are rejected server-side**, whatever annotation or UI hint the caller claims (SEC-AGENT-004).
- **Failure is closed**: an authorization, scope, or approval failure leaves no partial side effect (SEC-FAIL-001).
- **The `/agent/*` surface is not covered by Django middleware today** (CSRF, session, auth, CORS, clickjacking; a current fact, see SEC-AUTH-004). Any `/agent` endpoint beyond health needs its own authentication and authorization tied to control-plane identity before it can host an operation.

Client-side state, including which tools are registered, which controls are visible, and what a browser agent believes it may do, is never consulted for these decisions.

## Interaction origin as audit data

Planned. Every operation call carries an `InteractionContext`, described conceptually in [STATE-SCHEMA.md](STATE-SCHEMA.md). Its fields: `origin`, `actor_ref`, `ai_participant_ref`, `context_ref`, `task_ref`, `operation`, `operation_id`, `attempt_id`, `approval_ref`, `request_id` (trace only), and `recorded_at`. It is not persisted today.

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
- When the user's context changes between request and execution (Topic/Thread switch, logout, navigation), the adapter that issued the call stops it if it has not been sent, and the server re-validates actor, AI participant, and scope for the context the call names. What the caller reports in each case is in "Outcomes, cancellation, and context changes"; the WebMCP-specific handling of in-flight executions is in [WEBMCP.md](WEBMCP.md).

## Boundaries and anti-patterns

Planned contracts, stated as what not to do:

- Do not implement a business rule, permission check, or approval only in the Human UI or only in a tool callback.
- Do not register a WebMCP tool for every control, and do not make any Human UI behavior wait on tool registration.
- Do not give Automation or a Task runner a path around approval or scope checks because "no user is present".
- Do not add an approval step to an ordinary mutation in place of classifying it, and do not decide whether approval is needed from an annotation, a prompt, or the origin.
- Do not detect duplicates by comparing input or timing, and do not create a new `operation_id` to retry a call whose outcome is `pending` or an `outcome_unknown` other than `partially_applied`.
- Do not report a call that was sent and never answered as failed or as not executed.
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
- An approval-required operation executes only after a bound approval through every interface; a call carried by a Task stops in `waiting_for_approval` until then. An ordinary mutation completes without an approval step through every interface.
- Two requests with the same `operation_id` and binding, sent one after the other or concurrently, through any interface, produce one side effect and the same recorded outcome; the same id with a different input is rejected with `idempotency_conflict`, and the same id sent by another actor neither returns nor reveals the first actor's operation; a new id with identical input executes as a new operation.
- A response lost after the server applied a change leads the caller to `outcome_unknown`, and a status lookup or a retry with the same `operation_id` returns the recorded outcome without executing again.
- `origin` is recorded correctly for each interface, and a client-supplied origin is ignored.
- The Human UI functions with WebMCP absent or disabled.
- A context switch before sending stops the call in the adapter with no request sent; after sending, the caller reports `outcome_unknown` or, on any server answer, a safe summary without the old context's data. The server re-validates actor and scope for the context the call names.
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
