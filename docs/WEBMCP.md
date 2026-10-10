# Claus WebMCP Contract

This document is the technical contract Claus would follow if it exposes browser tools to AI agents through WebMCP. It records the specification facts the contract depends on, each with its source and verification date, and the rules Claus adds on top of them. It is not a description of existing code: the repository contains no WebMCP code, and the decision to adopt WebMCP has not been made.

The general adapter model (Human UI, WebMCP, Automation, Browser Computer Use) is defined in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md). This document covers WebMCP specifics only.

## Status

- Subject status: Experimental and Planned. WebMCP is an external technology whose specification and browser support are still changing. Every Claus rule below is a design contract, not current behavior.
- Implemented: nothing. At HEAD 8ec5623 (2026-10-08), and again at 14b9109 (2026-10-10; unchanged through 3fe4d1a), there is no WebMCP code, no registered tool, no `Permissions-Policy` header, and no WebMCP test. See "Current repository status".
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
- Unregistration does not cancel executions already running (CG draft). Each `execute` callback therefore captures the context identity and a registration generation at registration time and compares them with the current values when it starts, immediately before sending any state-changing request, and again immediately before returning any result, including a read result. A read with a mismatch at any check returns the machine-readable error result `context_changed` and no data, so data from the old context never reaches an agent working in the new one. A state-changing tool with a mismatch before sending sends nothing and returns `not_executed` (`outcome_unknown` for a resend under an `operation_id` already sent); after sending, what it returns depends on whether the server's answer arrived (see "State-changing tools: operation ids and outcomes").
- The client-side check is the only place a switch of the visible context is noticed; the server cannot see it. The server re-validates the acting user, the AI participant, and the scope for the context the request names, on every operation regardless of how the request originated (SEC-AUTH-001, SEC-SCOPE-003, SEC-AGENT-001), and denies with no partial side effect (SEC-FAIL-001).
- Execution-time cancellation: `execute` receives `options.signal` (CG draft). The CG draft aborts it only when the agent aborts its `executeTool()` call, which then rejects with the abort reason, or when the calling document is unloaded, which leaves no caller to observe a result. In both cases the browser discards whatever `execute` returns afterwards, so the agent receives neither an `outcome` nor an `operation_id` (CG draft, "cancel a pending tool execution"). `execute` sees an abort only through the signal, so an abort keeps the request from being sent only when it arrives before `execute` sends it, for example while `execute` awaits earlier work; code that sends without awaiting first cannot be stopped this way. Pass the signal to `fetch()` and other cancellable work so the tool stops waiting. Aborting stops the client only: once the request is sent, the server may already have applied the change. An agent that aborted a state-changing call therefore treats it as `outcome_unknown` and resolves it through the status tool's list of recent operations in the current context, never by calling the tool again as a new operation.
- Navigation: the CG draft's unloading cleanup steps complete pending executions of an unloaded target document with failure; Chrome docs say `executeTool()` "returns the result of the tool execution, or null when a navigation is triggered". A tool that triggers navigation must return before navigating or document that its result is lost. For a state-changing tool, a lost result is `outcome_unknown` to the agent, which recovers through the status tool, not by calling the tool again as a new operation.
- Logout invalidates the session the tools rely on. Abort the registration controller before clearing the session so no tool stays visible without credentials.

## State-changing tools: operation ids and outcomes

Planned and Experimental. Claus rules that apply the contract in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries" and "Outcomes, cancellation, and context changes" to tools. The CG draft defines none of this.

- A state-changing tool's `inputSchema` has an optional `operation_id` property. The agent passes it only to resolve an operation whose earlier result was `pending` or `outcome_unknown` other than `partially_applied`; after `succeeded`, `not_executed`, or `partially_applied` it leaves it out, and trying again is a new operation. Leaving it out declares a new intent. `execute` removes it from the input before sending, because it is not part of the operation input that the server binds.
- When it is absent, `execute` creates one with `crypto.randomUUID()` before sending anything, reuses it for any retry inside the same execution, and returns it in every result, success or not.
- A supplied `operation_id` is untrusted input, and the server decides whether it may be used ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries"). `execute` rejects a value that is not in the format Claus issues. It keeps the ids it issues with the user and context identity of the registration, and uses that record only as an early check:
  - Issued by this page for the current identity: sent as an ordinary retry.
  - Issued by this page for another user or context: nothing is sent; the result is `outcome_unknown` with `error.code: "operation_id_mismatch"`, and the agent resolves the id from the context it was issued in, or tells the person.
  - Not issued by this page, for example recovered through the status tool after a reload: not rejected for that reason. It is sent as a request that only resolves an existing operation, which the server accepts only for the acting user's own operation with the same binding, while that user may still act in the context, and never binds to a new operation.

  The server resolves an id only among the acting user's own operations (SEC-AGENT-003, SEC-IDEM-001). The id grants nothing.
- `execute` never decides that a call repeats an earlier one by comparing input. Without an `operation_id` from the agent, it is a new operation.
- A read-only status tool takes an `operation_id`, or lists the actor's recent operations in the current context with their `operation_id`s, and returns their outcomes through the same server authorization as any read (SEC-SCOPE-003). It returns details only for operations owned by the current context; for another context's operation that the AI participant, if any, may also read, it returns only the `operation_id`, the operation name, and the outcome, and otherwise no record. It is how an agent resolves `outcome_unknown`, including after an abort or a navigation lost a result, and how it recovers an id after a reload. An entry is the operation the agent lost only when the agent kept its `operation_id` or the person confirms it, never because the input looks the same; with several possible entries, the agent asks the person. A lookup that finds no record does not prove the call was not executed: the agent retries with the same `operation_id`, or tells the person, and neither creates a new `operation_id` for it nor starts a new operation on that basis. When it tells the person, it says that the earlier call may already have taken effect or may still take effect, unless that has been ruled out ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries").

Results of a state-changing tool. `ok` is true only when `outcome` is `succeeded`; every result carries `outcome` and `operation_id` (null only when the agent supplied a malformed one).

| What happened | Result |
|---|---|
| Context mismatch at the start or immediately before sending | `outcome: "not_executed"`, `error.code: "context_changed"`. Nothing was sent. |
| Execution signal aborted before sending | `outcome: "not_executed"`, `error.code: "cancelled"`. Reached only when the abort arrives before `execute` sends. The browser discards this result: an agent abort makes `executeTool()` reject with the abort reason, and an unloaded caller observes nothing (CG draft). |
| Invalid input, including a malformed `operation_id` | `outcome: "not_executed"`, `error.code: "invalid_input"`. Nothing was sent. When the agent supplied an `operation_id`, malformed included, `outcome_unknown` instead (see below). |
| Server rejected the request that claimed the id (validation, authorization, scope, approval denied) | `outcome: "not_executed"`, the server's `error.code`. The server recorded it as the id's outcome. |
| Server refused this request alone, with no outcome for the id (`idempotency_conflict`; a repeat refused because, for example, the actor lost access; or `operation_not_found` for a resolving request whose id the server has no record of for this user) | `outcome: "outcome_unknown"`, the server's `error.code`. This request did nothing and bound nothing; the operation already bound to the id, if any, keeps its own outcome, read through the status tool. |
| Server reported a failure recorded with effects remaining | `outcome: "outcome_unknown"`, `error.code: "partially_applied"`, and the server's `error` describing the recorded state. Final for the id: never success, never `not_executed`, not retried under the same id; executing again is a new operation the person decides on. |
| Server accepted the call and it has not finished | `outcome: "pending"`, with the Task's `status` as `task_status` when a Task carries it (for example `waiting_for_approval`). |
| Request sent and no confirmed answer arrived (timeout, network error, lost response) | `outcome: "outcome_unknown"`, `error.code: "timeout"` or `"network_error"`. Never success, never failure. |
| Request sent, then the execution signal aborted | `outcome: "outcome_unknown"`, `error.code: "cancelled"`. Discarded by the browser as above; the agent treats its aborted call as `outcome_unknown`. |
| Server confirmed success, context unchanged | `outcome: "succeeded"` and the data. |
| Any server answer, context changed before returning | The outcome as in the rows above, the operation name, `error.code` when there is one, and `data_withheld: "context_changed"`. No data, error details, or Task details from the old context; they come from the status tool called from the owning context. |
| The session ended (logout) before returning | Logout is a context switch, so the row above applies: `data_withheld: "context_changed"` and no data. Its details need a new session in the owning context. |

With an `operation_id` the agent supplied (malformed included), or one already sent in this execution, every row above that sends nothing reports `outcome_unknown` instead of `not_executed`: only this attempt was not sent, and the operation keeps the outcome of its earlier attempts.

A context switch alone does not stop waiting: a server answer that arrives after it follows the context-changed row, and only the absence of an answer makes the outcome `outcome_unknown`.

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
- Claus: keep schemas small and self-describing with `description` on every property; use enum values that are stable machine strings. Validate strictly inside `execute` (explainer: "validate strictly in code, loosely in schema"); schema constraints are hints to the agent, not security controls. Inputs are untrusted (SEC-INJ-001, SEC-INJ-002). A tool never accepts credentials, tokens, user identifiers, or AI participant identifiers to act as someone else (SEC-AGENT-003).

Output:

- CG draft: the result is the JSON-stringified return value of `execute`; there is no `outputSchema`. A rejected `execute` promise reaches the caller only as `UnknownError`.
- Claus: return a plain JSON object with a stable shape rather than throwing for expected failures: an `ok` boolean, a machine-readable `error.code` on failure (stable, locale-independent, never a translated message), and the data payload on success. State-changing tools also return `operation_id` and `outcome` (see "State-changing tools: operation ids and outcomes"). Chrome docs recommend about 1.5K characters per tool output. Outputs never contain presigned URLs, tokens, or other secrets (SEC-SECRET-003, SEC-FILE-005). Outputs that contain user-generated or external content are marked with `untrustedContentHint` (next section).

## Tool classification and annotations

Planned and Experimental. CG draft: `ToolAnnotations` are hints to agents and browsers, not enforcement.

| Class | Annotations | Claus requirement |
|---|---|---|
| read | `readOnlyHint: true` | No state change; same server-side scope check as the UI (SEC-SCOPE-003). |
| untrusted read | `readOnlyHint: true`, `untrustedContentHint: true` | Output includes user, file, retrieval, or external content; the agent must treat it as data (SEC-INJ-001, SEC-RAG-002). |
| mutation | `readOnlyHint: false` | Server-side authorization, scope, and policy checks; an `operation_id` per logical operation; audit. No approval step (SEC-AUTH-001, SEC-IDEM-001, SEC-SECRET-004). |
| consequential | `readOnlyHint: false`, `consequentialHint: true` | Server policy classifies the operation as consequential, at least for irreversible deletion, publishing personal material into a team or organization scope, posting or sending outside Claus, external work with sensitive credentials, or high-risk Browser, Terminal, or Workspace execution. It runs only after the same server-side approval path as the UI and is rejected without it (SEC-AGENT-004, SEC-APPROVE-001, SEC-APPROVE-003). |

The annotation mirrors the server's classification ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Classification and approval") so agents can warn people; it never decides it. Setting `consequentialHint` does not make an approval required, and leaving it out does not remove one. A tool whose operation is consequential for any of its inputs carries `consequentialHint: true`, or is split into one tool per class.

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

Planned and Experimental. The server cannot reliably tell that a request came from a tool rather than from the Human UI in the same session, and its decisions do not depend on it:

- Authorization: every operation is authorized server-side against the acting user (SEC-AUTH-001, SEC-SCOPE-003). The calling agent is an external browser agent ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Callers and identity"): the server cannot establish which agent called, and the agent adds no AI participant's effective permissions. Client-side checks in `execute` are UX only.
- Identity: tool executions carry the user's existing session and auth context (SEC-AGENT-003). A tool never accepts an AI participant name or reference as input, the adapter sends none, and the server records no AI participant for a tool call. The `/agent/*` surface bypasses Django middleware and has no authentication today (SEC-AUTH-004); tools must not target it until that is resolved.
- Approval: operations that server policy classifies as consequential go through the Claus approval path before execution; it is the Task, not the tool invocation, that stops in `waiting_for_approval`, and the tool returns `pending` (SEC-APPROVE-001 to SEC-APPROVE-003, SEC-IDEM-003). The approval comes from a person with authority over the operation, through a step that the calling agent, acting in the same browser session, cannot complete by itself; no tool input, tool result, or agent reply supplies it or stands for it ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Classification and approval", "Callers and identity"). An ordinary mutation needs authorization and policy checks, not an approval.
- Duplicate execution: every state-changing request carries the `operation_id` described above. The server binds it at first use; the same id with the same binding returns the recorded outcome instead of executing again, a different binding is rejected with `idempotency_conflict`, and concurrent duplicates execute at most once (SEC-IDEM-001). Each send is a separate attempt record (SEC-IDEM-002). A call without an `operation_id` from the agent is a new operation, never a guessed retry, and a request that only resolves an existing operation never binds a new one.
- Audit: the origin `webmcp` that the adapter reports is recorded as client-reported (`origin_basis` in `InteractionContext`, [STATE-SCHEMA.md](STATE-SCHEMA.md)), because the server cannot verify it, with the invocation's actor, context, operation, `operation_id`, `attempt_id`, approval, and request id, and with no AI participant; the outcome is the one recorded with the `operation_id` binding ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries"), and a Task or ToolRun record may reference it (SEC-SECRET-004). Origin is diagnostic data; it never widens permission (SEC-AGENT-001).
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

Illustrative example, not implemented. The `execute` body of a state-changing tool, registered as above with `readOnlyHint: false`, showing where each outcome in "State-changing tools: operation ids and outcomes" comes from. `captured` is the user and context identity recorded at registration; `contextIsCurrent`, `isOperationId`, `issuedIdentity`, `sameIdentity`, `issueOperationId`, and `callOperation` are placeholders.

```js
// Illustrative example, not implemented.
async function execute(input, { signal }) {
  const { operation_id: supplied, ...args } = input; // the id is not operation input
  if (supplied !== undefined && !isOperationId(supplied)) {
    // The agent was resolving an earlier operation: this attempt says nothing about it.
    return { ok: false, operation_id: null, outcome: "outcome_unknown", error: { code: "invalid_input" } };
  }
  // This page's record of the ids it issued is an early check; the server decides.
  const issuedTo = supplied === undefined ? null : issuedIdentity(supplied);
  if (issuedTo && !sameIdentity(issuedTo, captured)) {
    // Issued here for another user or context: resolve it there.
    return { ok: false, operation_id: supplied, outcome: "outcome_unknown", error: { code: "operation_id_mismatch" } };
  }
  // An id this page has no record of (recovered after a reload, for example) only
  // resolves an existing operation: the server never binds it to a new one.
  const resolveOnly = supplied !== undefined && !issuedTo;
  // crypto.randomUUID(), recorded with the identity in `captured` for issuedIdentity().
  const operation_id = supplied ?? issueOperationId(captured);
  // A stop before sending: an id sent before keeps its earlier, unresolved outcome.
  const notSent = (code) => ({ ok: false, operation_id,
    outcome: supplied === undefined ? "not_executed" : "outcome_unknown", error: { code } });
  if (!contextIsCurrent(captured)) return notSent("context_changed");
  if (signal.aborted) return notSent("cancelled"); // only if an await came before this

  let answer;
  try {
    // Resolves with the server's answer: succeeded, not_executed, pending,
    // outcome_unknown with partially_applied, or a refusal of this request alone
    // (an error code and no outcome, such as idempotency_conflict, or
    // operation_not_found when a resolveOnly request names an id the server has
    // no record of for this user). Rejects when no answer arrived, including a
    // server error that does not say whether the change was applied.
    answer = await callOperation("example.update_item", args, { operation_id, resolveOnly, signal });
  } catch (error) {
    // The request may have been applied: never success, never failure. After
    // an abort the browser discards this result (CG draft).
    const code = signal.aborted ? "cancelled"
      : error.name === "TimeoutError" ? "timeout" : "network_error";
    return { ok: false, operation_id, outcome: "outcome_unknown", error: { code } };
  }
  // A refusal of this request alone: the operation bound to the id may have run.
  const outcome = answer.outcome ?? "outcome_unknown";
  if (!contextIsCurrent(captured)) {
    // The agent now works in another context: outcome and codes only, no details.
    return { ok: outcome === "succeeded", operation_id, outcome, operation: "example.update_item",
      error: answer.error && { code: answer.error.code }, data_withheld: "context_changed" };
  }
  if (outcome !== "succeeded") {
    return { ok: false, operation_id, outcome, error: answer.error, task_status: answer.task_status };
  }
  return { ok: true, operation_id, outcome, data: answer.data };
}
```

## Current repository status

Facts at 14b9109 on branch `codex/dependency-updates-abi3t`, 2026-10-10, unchanged through 3fe4d1a; first recorded at HEAD 8ec5623 on 2026-10-08:

- `grep -rni 'modelContext\|webmcp' frontend/src` returns nothing. No tool is registered anywhere.
- No `Permissions-Policy` header is configured. `frontend/next.config.ts` sets `skipTrailingSlashRedirect: true`, rewrites `/core/:path(.*)` and `/agent/:path(.*)` to the backend, and sets `agentRules: false` and `experimental.mcpServer: false`. `frontend/src/proxy.ts` only issues the UI's trailing-slash 308 redirects; it sets no header other than their `Location` and registers no tool. There is no `middleware.ts` or route handler.
- By default, Next.js 16's `next dev` serves an MCP endpoint at `/_next/mcp`. It is a server-side endpoint for development tooling, unrelated to WebMCP, which registers tools in the browser page. `experimental.mcpServer: false` disables it, and `frontend/tests/visual/home.spec.ts` asserts that `POST /_next/mcp` returns 404.
- The frontend has two pages (`/` and `/console`) and one client component that calls the two health endpoints. No application operations layer exists for tools to call.
- Backend: the only product API routes are the two health endpoints (`/admin/`, `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` also respond); the S3-compatible storage backend and `DATABASE_URL` parsing are implemented; there is no authentication, authorization, approval, idempotency, or audit for product features ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)).
- Tests: `frontend/tests/visual/home.spec.ts` (one scenario) and `frontend/tests/visual/proxy.spec.ts` (two tests that need no browser) run in four Playwright projects, the `Desktop Chrome` and `Pixel 7` device presets each in light and dark; none touches WebMCP. No backend test covers agent-originated actions.
- Adoption decision: none. Nothing in this document is scheduled.

## Verification requirements

Planned and Experimental. Required before any WebMCP code is merged; recorded here so [TESTING.md](TESTING.md) and reviewers can cite it.

Environment:

- Chrome docs: a browser at Chrome 149 or newer with an origin trial token, or the `chrome://flags/#enable-webmcp-testing` flag. The frontend's `@playwright/test` dependency (lockfile 1.64.0) is paired with Chromium 156.0.8078.4 ([DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md)); whether that build supports the flag, and whether the flag can be enabled for a Playwright-launched browser, is unverified.
- Environment-limited failures (no WebMCP support in the test browser) must be reported as such, never as passing coverage.

Tests to add, each with normal, denial, boundary, and retry cases:

- Feature detection: with `document.modelContext` absent, the Human UI renders and works unchanged; with it present, the expected tool set appears in `getTools()`.
- Registration failure: duplicate `name`, invalid `name`, empty `description`, non-serializable `inputSchema`, and `Permissions-Policy: tools=()` each reject only the affected registration; the page keeps working.
- Context switch: after a Topic/Thread change or logout, the old set is gone from `getTools()` and the new set is present; a state-changing execution whose context changed before sending sends nothing and returns `not_executed` with `context_changed` (`outcome_unknown` when the agent supplied the `operation_id`); a read already awaiting the server when the switch happens returns `context_changed` and none of the old context's data.
- Cancellation before sending: an abort that arrives while `execute` awaits work before sending makes it send nothing, and `executeTool()` rejects with the abort reason; an abort issued before `execute` starts does not stop code that sends without awaiting first, so the test checks through the status tool that at most one side effect occurred.
- Supplied ids: an `operation_id` this page issued for another user or context returns `outcome_unknown` with `operation_id_mismatch` and sends nothing; a resend under a supplied id that is stopped before sending returns `outcome_unknown`, never `not_executed`.
- Recovery after a reload: in a new page with no local record of the id, the status tool lists the earlier operation and its `operation_id`; a retry with that id and the same input returns the recorded outcome with one side effect in total; with another input it is rejected with `idempotency_conflict`; an id of another user, an id in a context the user can no longer access, and an id the server has no record of each return `outcome_unknown`, reveal nothing, and bind nothing; with several possible entries the agent asks the person and starts nothing; while the operation stays unresolved, the agent creates no new `operation_id` and starts nothing, and its message to the person says that the earlier call may have taken effect or may still take effect.
- Cancellation after the server started: aborting after the request reached the server rejects `executeTool()` with the abort reason, the change stands, and the status tool's listing reports the actual outcome with one side effect.
- Lost response: when the server applies a change and the response is dropped, the tool returns `outcome_unknown`; a retry with the returned `operation_id` returns the recorded outcome, and the side effect happens once.
- Duplicates: the same `operation_id` sent twice, one after the other and concurrently, produces one side effect; the same id with a different input is rejected with `idempotency_conflict` and reported as `outcome_unknown`; a call without an `operation_id` is a new operation even when its input repeats an earlier call.
- Stale data: any server answer that arrives after a switch between contexts, including between personal and team, returns only the safe summary, and its details, recorded state included, are readable only through the status tool called from the owning context.
- Permissions Policy: pages that expose no tools send `Permissions-Policy: tools=()`.
- Server contract: an invocation without authorization is rejected server-side; an ordinary mutation completes without an approval step; a consequential operation never executes before a bound approval (the tool returns `pending` while its Task waits in `waiting_for_approval`, and an attempt to execute it outside the approval path is rejected); a tool call receives the same decision as the same user's Human UI request; the audit record carries the user as actor, origin `webmcp` marked client-reported, no AI participant, and the `operation_id`; a request with a forged origin or AI participant reference gains nothing.

Review: WebMCP and agent-originated actions are a security review trigger ([CODE-REVIEW.md](CODE-REVIEW.md), [SECURITY-REVIEW.md](SECURITY-REVIEW.md)).

## Open items to re-verify before adoption

All from the CG draft or explainer as of 2026-10-08:

- Granular errors: `executeTool()` failures surface as `UnknownError`; `NotFoundError` and `DataError` are open.
- `outputSchema` (issue #9), input and output validation by the browser (issue #92), in-place `updateTool()` (issues #167, #255), tool results across navigation (issue #135), user prompting and elicitation (issue #165), a `native-agent` exposure keyword, service worker integration.
- Chrome-doc versus spec discrepancies such as `requestUserInteraction()`.
- Declarative API: still a TODO in the spec; not covered here.

Related: [CONTEXT.md](CONTEXT.md), [ARCHITECTURE.md](ARCHITECTURE.md), [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md), [I18N.md](I18N.md), [STATE-SCHEMA.md](STATE-SCHEMA.md), [TESTING.md](TESTING.md).
