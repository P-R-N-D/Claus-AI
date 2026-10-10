# Claus Code Review Procedure

This document defines how a coding agent, or a person, independently reviews an actual code change in this repository and reports what it found. It covers establishing the change under review, what to check and in which order, what counts as verification evidence, when a change also needs a security review, and the report format.

It is not a security policy (see [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)), not the security review procedure (see [SECURITY-REVIEW.md](SECURITY-REVIEW.md)), not a catalog of tests (see [TESTING.md](TESTING.md)), and not an approval or merge policy. It creates no pull request template, GitHub configuration, or CI workflow. Read [CONTEXT.md](CONTEXT.md) first; it is the source of the rules, and this document names which of them a reviewer checks at each step without restating the rest.

## Status

- In effect for every code change, starting at HEAD 8ec5623 (committed 2026-09-21; the repository facts in this document were verified on 2026-10-08). The procedure depends on nothing that is Planned. (Updated 2026-10-10: the Frontend boundary checks were re-checked at 14b9109; unchanged through 3fe4d1a.)
- Tooling: `.github/workflows/ci.yml` is the only workflow in this repository's files. It runs the baseline backend, frontend, and Playwright checks on pull requests to `main` and pushes to `main`, on the checked lane only; [TESTING.md](TESTING.md) "Continuous integration" lists its jobs and what it does not cover. Its runs are evidence to read (see "Independence and authority" and step 8). GitHub-side integrations, such as review bots and required status checks, are configured outside the repository's files; whether one runs depends on the connected service and its settings, and its output is evidence to read. `.github/` also holds `copilot-instructions.md`, a bridge file that points to [CONTEXT.md](CONTEXT.md), and the advisory issue forms and pull request template described in [CONTEXT.md](CONTEXT.md) "GitHub issues and pull requests"; they are writing aids, not review tooling or evidence. Adding another workflow, or extending `ci.yml` beyond the checked lane, is a separate, explicitly requested change. (Updated 2026-10-10.)
- Verification baseline: the automated tests that exist are listed in [TESTING.md](TESTING.md) under "Tests that exist today". They were not executed in this documentation pass. "Exists" and "executed" stay distinct throughout this document.
- The checks under "Boundary-specific checks" for Realtime, Task, and Runtime cover Planned areas. They apply to a change that introduces such code; they never imply the feature exists.

## Independence and authority

- A review by the agent or session that wrote the change is a self-assessment. It is useful and must be labelled "self-assessment". It does not satisfy the review rule in [CONTEXT.md](CONTEXT.md). An independent review is done by a different agent, session, or person who did not author the diff and who starts from the diff and the repository, not from the author's summary.
- The author's description is input to the scope comparison. It is never a substitute for reading the diff.
- A review is evidence, not approval. "No blocking findings" means exactly that. It does not merge anything, and it does not grant the explicit approvals that [CONTEXT.md](CONTEXT.md) requires for migrations, model edits, package installs, lockfile changes, or infrastructure. Those come from the user and are cited in the change.
- CI runs, including those of `.github/workflows/ci.yml`, and review-bot comments are evidence to read, not verdicts. A green run shows that what ran passed, not that the right checks ran. A bot finding is a lead to verify, and a bot "approval" is nothing.
- Findings come first in the report. A review that lists style remarks on a change with a defect has failed.

## Procedure

Work through the steps in order. Record what was done at each step; the report cites it.

### 1. Establish the change

Identify the base (the commit the change targets, for example `origin/main`), the head (the tip of the change, or the working tree), and everything that differs between them, including staged, unstaged, and untracked files. Untracked files do not appear in `git diff`.

```bash
# Where the change sits
git rev-parse --abbrev-ref HEAD
git status --short --branch                 # staged, unstaged, and untracked files
git ls-files --others --exclude-standard    # untracked files, invisible to git diff
git stash list                              # work parked outside the tree, if any

# Committed change: <base> is the target commit, <head> is the change tip
git log --oneline <base>..<head>            # commits under review
git diff --stat <base>...<head>             # files touched since the merge base
git diff <base>...<head>                    # the diff to read in full
git diff --check <base>...<head>            # whitespace errors and conflict markers

# Uncommitted change: the working tree is the head
git diff --stat                             # unstaged
git diff --cached --stat                    # staged
git diff && git diff --cached               # the diff to read in full
git diff --check; git diff --cached --check  # run both; each exits nonzero on problems
# Each untracked file: read it whole, then whitespace-check it with the same rules.
# --no-index implies --exit-code, so a clean file exits 1 with no output.
# Judge this command by its output: no output is clean, any line is a finding.
git diff --no-index --check /dev/null <untracked-file>
```

Record in the report: the base and head identifiers (full SHA for commits; "working tree at <SHA> plus N untracked files" otherwise), the commands used, and the file list. For the untracked-file check, record its output rather than its exit status: Git documents `--check` as not compatible with `--exit-code`, which `--no-index` implies, so the status is nonzero even for a clean file. If the tree changes while the review runs, the report names the state it reviewed; anything later is unreviewed.

### 2. Preserve the author's work

- A review reads; it does not rewrite. Do not reformat, "clean up", revert, or restyle code outside the change, and do not touch the author's working tree, index, or stashes beyond the ignored build and test outputs that running the checks in step 8 writes. Do not run state-changing git commands (`checkout`, `reset`, `stash`, `commit`, `rebase`) on the author's tree.
- Pre-existing problems outside the diff are reported as findings whose `observed problem` opens with "pre-existing", normally at low or info severity (the severity and confidence terms are defined under "Severity" and "Confidence" below). They block the change only when the change makes them worse or depends on them.
- When fixes are needed, the author applies them. A reviewer who applies fixes becomes an author, and the next review must be independent again.

### 3. Compare requested and implemented scope

Take the requested scope from the user's request as recorded where the change was asked for (the conversation, issue, or pull request description), not from the author's summary. When no request is recorded, say so, compare against the author's stated scope, and mark the comparison unverified. Classify every difference:

- Missing: requested and not implemented, or implemented without the verification the request implied.
- Extra: implemented without being requested. Includes refactors, formatting churn, new files, configuration changes, new dependencies, and documentation rewrites unrelated to the request.
- Silently narrowed: a subset or weaker form delivered without saying so. Typical forms: a stub or `TODO` where behavior was requested, a relaxed validation, a skipped, deleted, or loosened test, a feature left disabled, an error swallowed.

Extra scope that touches a guardrail in [CONTEXT.md](CONTEXT.md) "Repository guardrails" (models, migrations, packages, lockfiles, `.env` files) or the backend rules under its "Architecture direction" (another backend framework, FastAPI outside `agent`, infrastructure manifests) is a blocker unless the change cites the user's explicit approval. A change that alters behavior or structure without updating [ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md), or the READMEs has missing scope; a change that describes a Planned feature as implemented is a finding.

### 4. Correctness and regression risk

Correctness before everything else.

- Read the whole diff before running anything. Then read the code the diff depends on: callers, callees, the tests that cover the touched paths, and the configuration that feeds them. Hunks alone are not enough.
- For each changed behavior ask: which input or state reaches it; what happens on invalid, empty, oversized, or duplicate input; what happens on failure midway, on retry, and under concurrent calls; does it fail closed; are return values and exceptions consistent with every caller.
- Regression: list the contracts the change touches, such as routes, JSON shapes, status codes, environment variable semantics, storage semantics, and test expectations. The routing contract in `backend/agent/tests.py` and the behaviors listed in [ARCHITECTURE.md](ARCHITECTURE.md) "Implemented foundations" are the current baseline.
- Any test change in the diff is reviewed as code: a weakened assertion, a removed case, a new skip, or a widened exception catch is a finding until justified.

### 5. Product invariants

Check the change against the invariants in [CONTEXT.md](CONTEXT.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [STATE-SCHEMA.md](STATE-SCHEMA.md). Today only the scaffold exists, so most of these apply when a change introduces domain code; a change that introduces domain models without the explicit approval above is itself a blocker.

- Personal context stays separate from team context; nothing moves across without an explicit user share.
- File, Artifact, and Knowledge are distinct; upload implies neither sharing nor indexing.
- A Task is not a Message; a product Task is not an agent execution, and an agent execution is not an operation attempt. Retrying an operation whose outcome is `pending` or an `outcome_unknown` other than `partially_applied` is a new attempt with the same `operation_id`; trying again after a confirmed `not_executed` or a `partially_applied` failure is a new operation ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Operation identity and retries").
- Browser, Terminal, and Workspace runtimes are task-scoped and separate from persistent state; results return only as Messages, Files, Artifacts, or Task results.
- A shared viewing surface holds presentation state, not storage.
- The Agent FastAPI surface is an execution interface, not a source of truth for users, permissions, or contexts.
- IDs, enum values, error codes, permission names, and tool names are machine values and are never translated or locale-dependent (see [I18N.md](I18N.md)).

### 6. Boundary-specific checks

Apply the subset that matches the files in the diff.

API (DRF under `/core/*`, FastAPI under `/agent/*`):

- A new non-health DRF view without explicit permission classes is a blocker and a security review trigger; no project-wide default is configured, so DRF's own default (`AllowAny`) applies (see [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)).
- Requests under `/agent/*` do not pass through Django middleware. Any new route there is a security review trigger.
- FastAPI stays confined to the `agent` app; general product APIs stay in `core` with DRF ([CONTEXT.md](CONTEXT.md)).
- Status codes, response shapes, and error formats stay stable, and a change to them adds the tests that [TESTING.md](TESTING.md) "API testing" lists for future API changes; today the only API tests are the health and ASGI routing assertions.

Frontend (Next.js App Router under `frontend/src`):

- `/` and `/console` still render; `coreApi` and `agentApi` keep the `/core/` and `/agent/` URL contracts in `frontend/src/lib/api.ts` and the rewrites in `frontend/next.config.ts`; `frontend/src/proxy.ts` keeps a matcher that skips `/_next/*`, `/core/`, and `/agent/`, and issues only UI trailing-slash redirects (308, query preserved, same origin, never a scheme-relative `Location`). Backend paths keep their trailing slashes through the rewrites and are never redirected by the frontend. The Proxy is not an authorization layer ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "Frontend"); `frontend/tests/visual/proxy.spec.ts` and `home.spec.ts` are the regression baseline for these contracts.
- Rendered changes are checked at phone and desktop widths for overflow and clipping, in light and dark rendering, with no console errors and no failed network or resource requests. The existing Playwright scenario (`frontend/tests/visual/home.spec.ts`) runs in desktop/mobile and light/dark projects; it measures horizontal overflow against `document.documentElement.clientWidth`, with a self-check that proves the measurement can fail (a `window.innerWidth`-based check cannot fail under mobile emulation), asserts empty console, page, failed-request, and HTTP-error lists, and saves screenshots. It runs against `next dev` by default and against a fresh production build with `PLAYWRIGHT_NEXT_SERVER=production`; record which mode ran. Review the screenshots for clipping and report the browser actually used ([TESTING.md](TESTING.md)).
- No secrets or server-only values in client components. New user-visible strings are noted against the i18n direction in [I18N.md](I18N.md); `<html lang="en">` is hard-coded today.

Realtime (Planned; nothing exists): a change introducing WebSocket or SSE is checked for transport matching the resource (no large binaries over message transport; [ARCHITECTURE.md](ARCHITECTURE.md) "Realtime collaboration"), for per-context event separation, and for connection authentication (the AUTH and SCOPE areas of [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)).

Task (Planned; nothing exists): product Task state and agent attempts are distinct records; state transitions are observable; long-running work does not block the requesting conversation; approval-required work stops in `waiting_for_approval`; outputs return as Message, File, Artifact, or Task result ([STATE-SCHEMA.md](STATE-SCHEMA.md) `AgentTaskState`).

Runtime (only `backend/agent/runtime/browser/playwright.py` exists, a lazy factory with no launch): runtimes are task-scoped, clean up on failure and cancellation, hold no long-lived credentials, and never turn runtime-local files into persistent Files or Artifacts implicitly. Backend Python Playwright and frontend Node Playwright are separate dependencies with separate purposes.

Data (ORM, migrations, storage):

- A diff that edits `models.py` or adds files under a `migrations/` directory requires the user's explicit approval cited in the change; otherwise blocker. `python manage.py makemigrations --check --dry-run` must be clean.
- File access goes through the Django storage API. Object-name validation, no-overwrite saves, and presigned URL handling in `backend/core/storage/s3.py` are preserved; any change to them is a security review trigger.
- `DATABASE_URL` parsing stays strict; a new silent fallback is a finding.

### 7. Performance, concurrency, maintainability

- No unprotected process-global mutable state. Shared in-process state uses explicit synchronization; `backend/core/storage/s3.py` uses a `threading.Lock` for its lazy client and is the existing reference. Nothing may rely on the GIL, and a native or third-party dependency the change adds is checked for free-threaded compatibility ([CONTEXT.md](CONTEXT.md) "Concurrency direction").
- Blocking I/O inside async handlers, unbounded queries or payloads, missing timeouts, and work that could block a request path are findings with their expected impact stated.
- Maintainability: names match the vocabulary in [STATE-SCHEMA.md](STATE-SCHEMA.md), no dead code, docstring-only boundary packages stay boundaries until a decision is recorded, and documentation that the change makes stale is updated in the same change.

### 8. Verification evidence

Confirm that the checks matching the change-scope were actually run, by the reviewer where the environment already has what they need, and read their output. The reviewer does not install packages or change the environment to run a check; a check the environment cannot run is reported as not run. Which checks apply is defined in [TESTING.md](TESTING.md): "Current scaffold checks" for backend and frontend commands, "Change-scope checks" for documentation and scaffold work, and the feature sections for new areas. Do not re-list test cases in the report; cite the commands and results.

| Statement | Allowed when |
|---|---|
| "a test exists for X" | the test is in the repository at head and asserts X |
| "X is tested" | the test exists and was executed in this review, with output cited |
| "tests pass" | never without the command, working directory, and the summary line of its output |

- For each command, record: the command, the working directory, the exit status, the summary line (for example the `Ran N tests` line, or the lint and build result), and any environment limit (Django not installed, `node_modules` absent, Chromium missing).
- An inspection with no command (links and paths, naming, absence of secrets or generated artifacts, Planned versus implemented wording, from [TESTING.md](TESTING.md) "Change-scope checks") is recorded as the inspection performed, what it covered, and its result.
- Each check is reported as run (with its output), not applicable (with why the change cannot affect it), or not run (with the environment reason). Findings that depend on a check that did not run are suspected at most.
- The author's statement that tests were run is a claim. Pasted output counts as evidence only when it includes the command and the summary and is consistent with the diff.
- A CI run counts as a run check only for the commit it ran on and the commands its job ran ([TESTING.md](TESTING.md) "Continuous integration"). Cite the run, the commit SHA, the job, and the summary line from its log. A run on an earlier commit of the change, or a check the workflow does not run, still has to be run or reported as not run.
- Environment-limited failures are reported as failures to verify, never as success.

### 9. Security review triggers

A change that touches any of the following also goes through [SECURITY-REVIEW.md](SECURITY-REVIEW.md) after this procedure, as does a change that cites, implements, or claims to change the status of any `SEC-*` ID, and any change for which the user asks for one. The requirement IDs it cites are defined only in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md); this document names triggers and links.

- Authentication, permission, or scope code, including any new DRF view's permission classes and any new route under `/agent/*`.
- File and storage code: `core/storage/s3.py`, object naming, overwrite semantics, presigned URLs, upload and download paths.
- Retrieval and indexing: RAG, Knowledge references, anything that decides what a model can read.
- Tool or runtime execution: Browser, Terminal, Workspace, Playwright, external tool calls.
- Approval flows and anything that changes when a human decision is required.
- Secrets and configuration: `backend/config/settings.py`, `backend/config/database.py`, environment variable handling, `.env.example`, `.gitignore`, `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, CORS.
- WebMCP and agent-originated actions ([INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [WEBMCP.md](WEBMCP.md)).
- External content handling: web pages, uploaded files, retrieved text, or tool output that reaches a model or a user.
- Logging and audit: what is written to logs, Task records, or tool run summaries.
- i18n locale input handling: cookies, `Accept-Language`, preference values, `<html lang>` ([I18N.md](I18N.md)).
- CI workflows: any file under `.github/workflows/`, including its trigger events, `permissions`, secrets, `runs-on` labels, and action references.

The code review report states which triggers matched and whether the security review was done or is pending. Security findings use the finding format below plus the `SEC-*` IDs they map to, as [SECURITY-REVIEW.md](SECURITY-REVIEW.md) specifies.

## Report format

Findings first, most severe first. An empty findings list is a valid result and is stated as such.

```text
Review of <base>..<head>  (or: working tree at <sha> plus N untracked files)
Reviewer: <agent or person>; independent of the author: yes | no (self-assessment)

1. Findings            most severe first, each in the field format below
2. Scope comparison    requested vs implemented: missing / extra / narrowed
3. Verification        commands run with output summaries, inspections performed, checks not applicable or not run and why
4. Security review     not triggered | triggered: <triggers>, done | pending
5. Residual risk       what could not be verified and what would verify it
6. Result              no blocking findings | N blocking findings. Not an approval.
```

### Finding fields

Every finding carries all eight fields. Short is fine; absent is not.

- `severity`: blocker, high, medium, low, or info.
- `confidence`: confirmed, likely, or suspected.
- `location`: file path and line range at head, plus the function, class, or component.
- `observed problem`: what the code does, stated in terms of the code, not the intent.
- `impact`: who or what is affected and the worst realistic outcome.
- `reproduction conditions`: the inputs, state, and steps that show the problem, or "not reproduced" with what would be needed.
- `required fix`: the smallest change that resolves the problem; alternatives when the fix is a design choice.
- `regression verification`: the test or check that proves the fix and prevents recurrence, with its command.

For a documentation finding, `location` is the file and line range, `reproduction conditions` is the reading or command that shows the problem (for example a link that does not resolve), and `regression verification` names the inspection or script that would catch it again, or `none` with the reason.

### Severity

- blocker: must be fixed before the change is usable or merged. Data loss, a security boundary, a broken contract, or a guardrail violation.
- high: incorrect behavior likely in normal use, or a regression of existing behavior.
- medium: incorrect behavior in edge cases, missing verification for changed behavior, or a maintainability problem that will produce defects.
- low: minor or cosmetic, no behavioral impact.
- info: an observation, question, or follow-up note; no action required.

### Confidence

- confirmed: reproduced, demonstrated by a test, or shown by a complete and unambiguous code path.
- likely: strong evidence from the code, not reproduced.
- suspected: plausible from partial evidence. The finding names the step that would confirm or dismiss it.

Never present speculation as a confirmed defect. A finding without a reproduction or a complete code path is suspected at most. When the reviewer could not confirm because the environment lacked a dependency or a runtime, the finding says so and stays suspected.

### Worked example

Illustrative example, not implemented. The change it describes does not exist at HEAD; it shows the format only. Assume a diff that relaxes `database_config()` in `backend/config/database.py` so that a `DATABASE_URL` with an unsupported scheme falls back to SQLite instead of raising, and that edits `backend/core/test_database.py` to expect the fallback.

- `severity`: blocker
- `confidence`: confirmed
- `location`: `backend/config/database.py`, `database_config()`, changed hunk; `backend/core/test_database.py`, the invalid-URL test, assertion changed from `ImproperlyConfigured` to the SQLite configuration.
- `observed problem`: an invalid `DATABASE_URL` (for example an unsupported scheme such as `mysql://`) now returns the SQLite configuration instead of raising `ImproperlyConfigured`. The test that proved rejection was changed to accept the fallback, so the suite stays green.
- `impact`: a misconfigured non-local deployment starts against a local SQLite file; data written there is lost on redeploy, and the misconfiguration is invisible at startup. This reverses the fail-closed behavior recorded in [ARCHITECTURE.md](ARCHITECTURE.md) "Implemented foundations". It is also a security review trigger (secrets and configuration).
- `reproduction conditions`: with the change applied, set `DATABASE_URL="mysql://app@db.internal/claus"` and run `python backend/manage.py check`; it completes and `DATABASES["default"]["ENGINE"]` is the SQLite backend. At HEAD the same command raises `ImproperlyConfigured` while loading settings.
- `required fix`: restore strict parsing so that any invalid URL raises `ImproperlyConfigured` and SQLite is selected only when `DATABASE_URL` is unset or blank; restore the original assertion in `test_database.py`. If an explicit fallback is wanted, it needs a recorded decision and documentation changes, not a silent default.
- `regression verification`: `cd backend && python manage.py test core` with the restored test; the invalid-URL cases fail on the changed code and pass after the fix. Cite the `Ran N tests` summary line in the report.

## Related documents

- [CONTEXT.md](CONTEXT.md): the rules this procedure enforces, including the review rule and the repository guardrails.
- [ARCHITECTURE.md](ARCHITECTURE.md) and [STATE-SCHEMA.md](STATE-SCHEMA.md): the structure and invariants checked in steps 4 to 6.
- [TESTING.md](TESTING.md): the checks that exist, the checks to run per change-scope, and the verification still required.
- [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md): the only definition of `SEC-*` requirements.
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md): the procedure that follows when a trigger in step 9 matches.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [WEBMCP.md](WEBMCP.md), [I18N.md](I18N.md): background for the agent-originated action and locale triggers.
