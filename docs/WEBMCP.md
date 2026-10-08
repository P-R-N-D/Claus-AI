# Claus WebMCP Contract

This document is the technical contract Claus would follow if it exposes browser tools to AI agents through WebMCP. It records the specification facts the contract depends on, each with its source and verification date, and the rules Claus adds on top of them. It is not a description of existing code: the repository contains no WebMCP code, and the decision to adopt WebMCP has not been made.

The general adapter model (Human UI, WebMCP, Automation, Browser Computer Use) is defined in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md). This document covers WebMCP specifics only.

## Status

- Subject status: Experimental and Planned. WebMCP is an external technology whose specification and browser support are still changing. Every Claus rule below is a design contract, not current behavior.
- Implemented: nothing. At HEAD 8ec5623 (2026-10-08) there is no WebMCP code, no registered tool, no `Permissions-Policy` header, and no WebMCP test. See "Current repository status".
- Adoption: not decided. This document exists so a future proposal can be reviewed against a stable contract instead of against memory of the spec.
- External facts were verified on 2026-10-08 against the sources below. Re-verify before any implementation work.

## Sources

Every external statement in this document carries one of these labels:

- CG draft: "WebMCP, Draft Community Group Report, 2 October 2026", Web Machine Learning Community Group, https://webmachinelearning.github.io/webmcp/ . Its status section reads: "This specification was published by the Web Machine Learning Community Group. It is not a W3C Standard nor is it on the W3C Standards Track." Editors are from Microsoft and Google. Test suite: wpt.fyi/results/webmcp.
- Explainer: the non-normative explainer in the WebMCP repository.
- Chrome docs: developer.chrome.com/docs/ai/webmcp (updated 2026-10-07), its Imperative API page (updated 2026-09-21), and its tool security page (updated 2026-09-01). These describe one implementation, not the specification.
- Status page: `implementation-status.md` in the WebMCP repository (fetched 2026-10-08).

Chrome version numbers, flags, and recommendations are never treated as spec requirements.

## Platform status (2026-10-08)

- CG draft: a Community Group draft report, not a W3C standard, dated 2 October 2026.
- Status page: Chrome origin trial live in Chrome 149; Edge origin trial live in Edge 150; Brave has experimental support in Leo; ChatGPT Desktop supports WebMCP; Firefox lists a standards-position issue and a Bugzilla entry, Safari a standards-position issue, and neither entry links an implementation; Meta Ray-Ban Display support is "coming soon" and off by default. No entry reports default-on support in a stable browser release.
- Chrome docs: WebMCP is a "proposed web standard"; local testing uses `chrome://flags/#enable-webmcp-testing`; the API "is primarily designed for local browser workflows with a human in the loop" and headless browsing is listed as a limitation; Chrome Extensions can query and execute WebMCP tools with content scripts.
- CG draft: the declarative (form-based) API section "is entirely a TODO". This document covers the imperative API only.
- Not in the CG draft: `navigator.modelContext`, `provideContext()`, `unregisterTool()`, `requestUserInteraction()`, `outputSchema`. The explainer defines none of them either; it lists `outputSchema` only as an open question (issue #9). The Chrome tool security page (2026-09-01) still says "the spec draft includes `requestUserInteraction()`"; treat this as a Chrome-doc versus spec discrepancy and follow the spec.
- CG draft: the browser's own agent retrieves tools through "a different internal mechanism" than `getTools()`. The wire format between browser and agent is not specified.

## Role in Claus

Planned and Experimental. WebMCP would be a semantic agent adapter over the same application operations the Human UI uses. Contract:

- The adapter contracts are those of [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md): progressive enhancement, tools call application operations rather than the UI, authorization and approval happen server-side (SEC-AGENT-001, SEC-AUTH-001, SEC-SCOPE-003), and browser-side mechanisms are not authorization (SEC-AGENT-002). This document adds only the WebMCP specifics.
- Tools are registered for the active personal or Topic/Thread context and unregistered when that context ends.

## API surface (CG draft, 2026-10-08)

IDL copied from the CG draft, sections 4.1 and 4.2; spec comments omitted.

```text
partial interface Document {
  [SecureContext, SameObject] readonly attribute ModelContext modelContext;
};

[Exposed=Window, SecureContext]
interface ModelContext : EventTarget {
  Promise<undefined> registerTool(ModelContextTool tool, optional ModelContextRegisterToolOptions options = {});
  Promise<sequence<RegisteredTool>> getTools(optional ModelContextGetToolOptions options = {});
  Promise<DOMString> executeTool(RegisteredTool tool, optional object inputObject, optional ModelContextExecuteToolOptions options = {});

  attribute EventHandler ontoolchange;
  attribute EventHandler ontoolactivated;
  attribute EventHandler ontoolcancel;
};

dictionary ModelContextTool {
  required DOMString name;
  USVString title;
  required DOMString description;
  object inputSchema;
  required ToolExecuteCallback execute;
  ToolAnnotations annotations;
};

dictionary ToolAnnotations {
  boolean readOnlyHint = false;
  boolean untrustedContentHint = false;
  boolean consequentialHint = false;
  boolean debugging = false;
};

dictionary ToolExecuteCallbackOptions {
  required AbortSignal signal;
};

callback ToolExecuteCallback = Promise<any> (object inputObject, ToolExecuteCallbackOptions options);

dictionary ModelContextRegisterToolOptions {
  sequence<USVString> exposedTo;
  AbortSignal signal;
};

dictionary ModelContextGetToolOptions {
  sequence<USVString> fromOrigins;
};

dictionary ModelContextExecuteToolOptions {
  AbortSignal signal;
};

dictionary RegisteredTool {
  required DOMString name;
  DOMString title;
  required DOMString description;
  object inputSchema;
  required Window window;
  required USVString origin;
  ToolAnnotations annotations;
};
```

Notes (CG draft):

- `[SecureContext]`: `document.modelContext` is absent on insecure origins. The draft carries only the `[SecureContext]` attribute; which origins count as secure (including the `localhost` exemption) comes from the Secure Contexts specification it references.
- `registerTool()` resolves with `undefined`. `executeTool()` resolves with the JSON-stringified return value of `execute`; if the argument object is omitted the tool receives an empty object.
- `getTools()` returns tools sorted by `name` in code unit order. A missing `title` is exposed as the empty string (open issue #224).
- Events: `toolchange` (tool list changed), `toolactivated` (`ToolActivatedEvent.toolName`, execution began), `toolcancel` (`ToolCancelEvent.toolName`, execution cancelled). Timing of `toolchange` relative to other queued tasks "cannot be relied upon".
- `getTools()` is designed for in-page agents (scripts, iframes); the browser's own agent uses an internal path. The explainer pairs `executeTool()` with it for the same in-page use.
- There is no `unregisterTool()`. Unregistration happens only by aborting the registration-time `signal`.

## Feature detection

Planned and Experimental.

- Detect with `"modelContext" in document`. The check is false on insecure origins (CG draft, `[SecureContext]`) and in browsers without the API (on 2026-10-08 the status page reported no stable default-on implementation).
- When the check is false, register nothing, log nothing at error level, and render the Human UI unchanged.
- When the check is true, registration may still fail per tool (next section). No UI state, route, or operation may depend on a registration having succeeded.
- Never probe for `navigator.modelContext` or other names that are not in the CG draft.

## Registration lifecycle

Planned and Experimental. Facts from the CG draft, `registerTool()` method steps:

- Registration is a Promise per tool. It rejects with `InvalidStateError` if the document is not fully active, if a tool with the same `name` is already registered, if `name` is empty, longer than 128, or contains characters other than ASCII alphanumerics, `_`, `-`, `.`, or if `description` is empty. It rejects with `NotAllowedError` if the document is not allowed to use the `tools` policy-controlled feature, with `SecurityError` if an `exposedTo` entry is not a potentially trustworthy origin, with the serialization exception if `inputSchema` cannot be JSON-serialized, and with the abort reason if `options.signal` is already aborted or is aborted before the promise settles.
- `inputSchema` is serialized at registration; `getTools()` returns a deep copy. Later mutation of the original object has no effect.
- Aborting the registration-time `signal` unregisters the tool. The spec note, paraphrased: unregistering a tool does not cancel an execution that has already invoked its ToolExecuteCallback; the registration-time signal only controls tool availability, while the execution-time signal controls the lifetime of a specific invocation. (Chrome docs: this behavior is available from Chrome 153.)
- The spec warns that unregistering and quickly re-registering the same `name` with a different `inputSchema` is not protected: input prepared for the old tool may reach the new one (open issue #92). Execution of a tool that no longer exists completes with failure; the caller currently sees `UnknownError` (granular errors such as `NotFoundError` are open issues).

Claus rules:

- One `AbortController` per registration set (one context). Register each tool with its own `registerTool()` call and handle rejection per tool; use `Promise.allSettled`, never a pattern where one rejection discards the others. A rejected registration is logged with the tool `name` and `DOMException.name` and is otherwise ignored by the UI.
- Re-registering to change `name`, `description`, `inputSchema`, `title`, or `annotations` means abort, then register again (same `name` cannot be registered twice while registered). Changing only the `execute` implementation needs no re-registration (explainer best practice). Keep metadata changes rare and tie them to context changes.
- Never register from a cross-origin iframe and never pass `exposedTo` without a security review (see "exposedTo versus Permissions Policy").

## Context switches and in-flight executions

Planned and Experimental. A context switch is a Topic/Thread change, a switch between personal and team context, logout, or navigation away from the page.

- On every context switch: abort the current registration controller, then register the tool set for the new context. Each registration and unregistration fires `toolchange` (CG draft), which lets agents refresh their tool list.
- Unregistration does not cancel executions already running (CG draft). Each `execute` callback therefore captures the context identity and a registration generation at registration time and compares them with the current values when it starts, before any state-changing call, and again immediately before returning any result, including a read result. On a mismatch at the start or before a state-changing call, it returns the machine-readable error result `context_changed` and performs no side effect. On a mismatch before returning, it discards the result, so data from the old context never reaches an agent working in the new one, and returns `context_changed`; when a state-changing call had already completed, the error result says the change was applied, so it is neither reported as lost nor retried.
- The client-side check reduces noise only. The server re-validates the acting user, the AI participant, and the scope on every operation regardless of how the request originated (SEC-AUTH-001, SEC-SCOPE-003, SEC-AGENT-001), and denies with no partial side effect (SEC-FAIL-001).
- Execution-time cancellation: `execute` receives `options.signal` (CG draft). Pass it to `fetch()` and other cancellable work. A cancelled invocation must not be reported as success and must not be retried silently.
- Navigation: the CG draft's unloading cleanup steps complete pending executions of an unloaded target document with failure; Chrome docs say `executeTool()` "returns the result of the tool execution, or null when a navigation is triggered". A tool that triggers navigation must return before navigating or document that its result is lost.
- Logout invalidates the session the tools rely on. Abort the registration controller before clearing the session so no tool stays visible without credentials.

## Tool naming, description, and schemas

Planned and Experimental.

Naming:

- CG draft: `name` length 1 to 128, ASCII alphanumerics, `_`, `-`, `.` only; it is the agent-facing identifier.
- Claus: stable, locale-independent, lowercase `snake_case` segments joined with `.` by area (for example `area.verb_object`). A name never changes meaning; new behavior gets a new name. Chrome docs recommend at most 30 characters per tool name and parameter name ("subject to change"); treat that as a budget, not a spec rule.

Description:

- CG draft: required, non-empty, natural language for agents.
- Claus: authored and reviewed by Claus, never derived from user content, kept short (Chrome docs recommend 500 characters per tool description and 150 per parameter description). Tool metadata is an injection surface (CG draft 6.3.1.1, "Tool Poisoning"; SEC-INJ-003).

Input schema:

- CG draft: `inputSchema` is a JSON Schema object. Chrome docs: JSON-stringified input arguments are deprecated from Chrome 155; pass objects.
- Claus: keep schemas small and self-describing with `description` on every property; use enum values that are stable machine strings. Validate strictly inside `execute` (explainer: "validate strictly in code, loosely in schema"); schema constraints are hints to the agent, not security controls. Inputs are untrusted (SEC-INJ-001, SEC-INJ-002). A tool never accepts credentials, tokens, or user identifiers to act as someone else (SEC-AGENT-003).

Output:

- CG draft: the result is the JSON-stringified return value of `execute`; there is no `outputSchema`. A rejected `execute` promise reaches the caller only as `UnknownError`.
- Claus: return a plain JSON object with a stable shape rather than throwing for expected failures: an `ok` boolean, a machine-readable `error.code` on failure (stable, locale-independent, never a translated message), and the data payload on success. Chrome docs recommend about 1.5K characters per tool output. Outputs never contain presigned URLs, tokens, or other secrets (SEC-SECRET-003, SEC-FILE-005). Outputs that contain user-generated or external content are marked with `untrustedContentHint` (next section).

## Tool classification and annotations

Planned and Experimental. CG draft: `ToolAnnotations` are hints to agents and browsers, not enforcement.

| Class | Annotations | Claus requirement |
|---|---|---|
| read | `readOnlyHint: true` | No state change; same server-side scope check as the UI (SEC-SCOPE-003). |
| untrusted read | `readOnlyHint: true`, `untrustedContentHint: true` | Output includes user, file, retrieval, or external content; the agent must treat it as data (SEC-INJ-001, SEC-RAG-002). |
| mutation | `readOnlyHint: false` | Server-side authorization, idempotency key per invocation, audit (SEC-AUTH-001, SEC-IDEM-001, SEC-SECRET-004). |
| consequential | `consequentialHint: true` | Irreversible, cross-scope, external side effect, or credential use; requires the same server-side approval path as the UI and is rejected server-side without it (SEC-AGENT-004, SEC-APPROVE-001, SEC-APPROVE-003). |

`debugging: true` is an annotation flag, not a fifth class: developer tooling only (Chrome docs: available from Chrome 156), never registered in production builds.

Definitions (CG draft): `readOnlyHint` = the tool does not modify state; `untrustedContentHint` = output contains data untrusted from the author's perspective; `consequentialHint` = execution results in significant, real-world, or non-reversible actions (spec example: booking a flight, transferring money); `debugging` = intended for debugging and developer tooling. The CG draft lists `untrustedContentHint` and `consequentialHint` as mitigations (6.4.4, 6.4.5): agents "can selectively enforce mandatory user confirmation prompts before executing high-stakes tools".

`destructiveHint`, `idempotentHint`, and `openWorldHint` are backend MCP terms. They do not exist in WebMCP and must not appear in Claus tool definitions. A browser confirmation prompt triggered by `consequentialHint` is a browser or agent UX feature; it is not Claus authorization (SEC-AGENT-002) and never replaces the Claus approval path (SEC-APPROVE-001, SEC-AGENT-004).

## exposedTo versus Permissions Policy

Planned and Experimental. The first two bullets are CG draft facts; the Claus rules after them are target behavior.

Two different mechanisms (CG draft):

- Permissions Policy `tools`: gates the whole API for a document. Default allowlist is `'self'` (section 4.5). `Permissions-Policy: tools=()` makes the feature unavailable to the document and all descendant frames, same-origin and cross-origin, before script runs (section 6.4.1). Registration and discovery in a disallowed document reject with `NotAllowedError`. Chrome docs: a cross-origin iframe needs `allow="tools"` to register tools.
- `exposedTo`: a per-tool list of potentially trustworthy origins whose documents in the same frame tree may discover and execute that tool. Same-origin documents always can. Entries that are not potentially trustworthy reject registration with `SecurityError`. A cross-origin consumer must also pass the owner origin in `getTools({ fromOrigins })`.

Claus rules:

- Neither mechanism authorizes Claus operations (SEC-AGENT-002). They decide which browser-side code can see a tool, nothing more.
- Pages that do not expose tools send `Permissions-Policy: tools=()` once WebMCP is adopted anywhere in the product (SEC-AGENT-005). Today no page sets this header and no registration code exists.
- `exposedTo` stays empty. Claus does not plan cross-origin tool exposure; any proposal to add an entry is a security review trigger ([SECURITY-REVIEW.md](SECURITY-REVIEW.md)).
- Chrome docs note that extensions with host permissions can run arbitrary page script with or without WebMCP. The server contract below, not the browser, is the control.

## UI state reflection

Planned and Experimental.

- A tool execution changes application state through the same operations as the UI, and the visible UI re-renders from that state. The explainer's best practice is "Synchronize visual UI state"; Chrome docs: "Tools execute on your webpage visibly, so users gain trust that tasks are completed as expected."
- The `toolactivated` and `toolcancel` events (CG draft) may drive a visible indicator that an agent is acting in the current context. Human UI must stay usable while a tool runs.
- Difference from Browser Computer Use ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md)): Browser Computer Use actuates the rendered UI (clicks, typing, observation); a WebMCP tool calls an application operation and lets the UI reflect the result. A WebMCP tool must never simulate clicks or submit forms on the user's behalf as a substitute for calling the operation.

## Server contract

Planned and Experimental. The server does not know or care that a request came from a tool, except for audit:

- Authorization: every operation is authorized server-side against the acting user and, for AI, the AI participant's effective permissions (SEC-AUTH-001, SEC-SCOPE-003). Client-side checks in `execute` are UX only.
- Identity: tool executions carry the user's existing session and auth context (SEC-AGENT-003). The `/agent/*` surface bypasses Django middleware and has no authentication today (SEC-AUTH-004); tools must not target it until that is resolved.
- Approval: consequential operations go through the Claus approval path before execution, and it is the Task, not the tool invocation, that stops in `waiting_for_approval` (SEC-APPROVE-001 to SEC-APPROVE-003, SEC-IDEM-003).
- Duplicate execution: each state-changing invocation generates one idempotency key in `execute` and attaches it to the operation; internal retries of that invocation reuse the key (SEC-IDEM-001). A new invocation by the agent is a new attempt and a new record, never a silent re-execution (SEC-IDEM-002).
- Audit: the interaction origin `webmcp` is recorded with the invocation's actor, AI participant, context, operation, approval, and request id (`InteractionContext` in [STATE-SCHEMA.md](STATE-SCHEMA.md)); the outcome lives on the ToolRun or Task record (SEC-SECRET-004). Origin is diagnostic data; it never widens permission (SEC-AGENT-001).
- Logs and tool results contain safe summaries only (SEC-SECRET-003).

Requirement IDs are defined in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md).

## Localization

Planned and Experimental. See [I18N.md](I18N.md) for the UI locale model.

- Locale-independent: `name`, `inputSchema` property names, enum values, `error.code` values, and any other machine-readable value. These never change with the UI locale.
- `title`: the CG draft says "It is recommended that this string be localized to the user's language." Claus recommendation: supply `title` from the UI locale resolved for the page, and never change `name` for that purpose.
- `description`: agent-facing. Keep it in one authoring language (English) until a reviewed design for agent-locale handling exists.
- Lifecycle consideration: `title` is fixed at registration. Changing it means abort and register again, because the same `name` cannot be registered twice while registered (CG draft). A UI locale change would therefore re-register the whole set. Either keep title changes rare and apply them at the next context switch, or defer title localization entirely. This is an open choice, not a decision.

## Illustrative example

Illustrative example, not implemented. Shape only: feature detection, one registration with annotations, registration-time and execution-time signals. The tool name, schema, and result are placeholders, not a planned tool.

```js
// Illustrative example, not implemented.
if ("modelContext" in document) {
  const registration = new AbortController();

  document.modelContext
    .registerTool(
      {
        name: "example.read_item",
        title: localizedTitle, // from the UI locale, see I18N.md
        description: "Illustrative read-only tool.",
        inputSchema: {
          type: "object",
          properties: {
            item_id: { type: "string", description: "Identifier of the item." },
          },
          required: ["item_id"],
        },
        annotations: { readOnlyHint: true, untrustedContentHint: true },
        async execute(input, { signal }) {
          // 1. Check the captured context identity against the current one.
          // 2. Call the shared application operation; the server authorizes.
          // 3. Check the context again; on mismatch discard the result and
          //    return the error result `context_changed`.
          // 4. Return a plain object; the browser JSON-serializes it.
          return { ok: false, error: { code: "not_implemented" } };
        },
      },
      { signal: registration.signal },
    )
    .catch((error) => {
      // Per-tool failure. The Human UI keeps working.
    });

  // On Topic/Thread change or logout: registration.abort();
}
```

## Current repository status

Facts, HEAD 8ec5623, 2026-10-08:

- `grep -rni 'modelContext\|webmcp' frontend/src` returns nothing. No tool is registered anywhere.
- No `Permissions-Policy` header is configured. `frontend/next.config.ts` contains only rewrites for `/core/:path*` and `/agent/:path*`; there is no `middleware.ts`, `proxy.ts`, or route handler.
- The frontend has two pages (`/` and `/console`) and one client component that calls the two health endpoints. No application operations layer exists for tools to call.
- Backend: the only product API routes are the two health endpoints (`/admin/`, `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` also respond); the S3-compatible storage backend and `DATABASE_URL` parsing are implemented; there is no authentication, authorization, approval, idempotency, or audit for product features ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)).
- Tests: `frontend/tests/visual/home.spec.ts` is one Playwright test run by the `chromium` project (the `Desktop Chrome` device preset); it does not touch WebMCP. No backend test covers agent-originated actions.
- Adoption decision: none. Nothing in this document is scheduled.

## Verification requirements

Planned and Experimental. Required before any WebMCP code is merged; recorded here so [TESTING.md](TESTING.md) and reviewers can cite it.

Environment:

- Chrome docs: a browser at Chrome 149 or newer with an origin trial token, or the `chrome://flags/#enable-webmcp-testing` flag. Which Chromium build the frontend's `@playwright/test` dependency (lockfile 1.61.0) bundles, and whether that flag can be enabled for a Playwright-launched browser, is unverified.
- Environment-limited failures (no WebMCP support in the test browser) must be reported as such, never as passing coverage.

Tests to add, each with normal, denial, boundary, and retry cases:

- Feature detection: with `document.modelContext` absent, the Human UI renders and works unchanged; with it present, the expected tool set appears in `getTools()`.
- Registration failure: duplicate `name`, invalid `name`, empty `description`, non-serializable `inputSchema`, and `Permissions-Policy: tools=()` each reject only the affected registration; the page keeps working.
- Context switch: after a Topic/Thread change or logout, the old set is gone from `getTools()`, the new set is present, an execution started before the switch returns `context_changed` with no side effect, and a read already awaiting the server when the switch happens returns `context_changed` instead of the old context's data.
- Abort: aborting the execution-time signal cancels the underlying request and the invocation is not reported as success.
- Permissions Policy: pages that expose no tools send `Permissions-Policy: tools=()`.
- Server contract: an invocation without authorization or without a required approval is rejected server-side; a repeated invocation with the same idempotency key does not duplicate the side effect; the audit record carries origin `webmcp`.

Review: WebMCP and agent-originated actions are a security review trigger ([CODE-REVIEW.md](CODE-REVIEW.md), [SECURITY-REVIEW.md](SECURITY-REVIEW.md)).

## Open items to re-verify before adoption

All from the CG draft or explainer as of 2026-10-08:

- Granular errors: `executeTool()` failures surface as `UnknownError`; `NotFoundError` and `DataError` are open.
- `outputSchema` (issue #9), input and output validation by the browser (issue #92), in-place `updateTool()` (issues #167, #255), tool results across navigation (issue #135), user prompting and elicitation (issue #165), a `native-agent` exposure keyword, service worker integration.
- Chrome-doc versus spec discrepancies such as `requestUserInteraction()`.
- Declarative API: still a TODO in the spec; not covered here.

Related: [CONTEXT.md](CONTEXT.md), [ARCHITECTURE.md](ARCHITECTURE.md), [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md), [I18N.md](I18N.md), [STATE-SCHEMA.md](STATE-SCHEMA.md), [TESTING.md](TESTING.md).
