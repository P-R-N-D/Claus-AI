# Claus Security Review Procedure

This document defines how a coding agent, or a person, reviews a changed piece of code for security in this repository: which changes need it, the nine steps to work through in order, what counts as verification, and how to report the result. It reuses the `SEC-*` requirement identifiers defined in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) and the finding format defined in [CODE-REVIEW.md](CODE-REVIEW.md).

It is not a security policy and defines no requirement: the meaning and status of every `SEC-*` ID live only in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md). It is not the general code review (see [CODE-REVIEW.md](CODE-REVIEW.md)), not a catalog of tests (see [TESTING.md](TESTING.md)), not a vulnerability reporting policy, and not an approval or merge decision. Read [CONTEXT.md](CONTEXT.md) first; its rules are not repeated here.

## Status

- In effect for every change that matches a trigger below or a run condition under "When this review runs", from HEAD 8ec5623 (committed 2026-09-21; the repository facts in this document were verified on 2026-10-08). The procedure itself depends on nothing that is Planned. (Updated 2026-10-10: the step 1 file table was re-checked at 14b9109 (unchanged through 3fe4d1a) and now lists `frontend/src/proxy.ts`, the lockfiles, and the runtime version files.)
- Implemented today, and therefore reviewable as behavior: the health endpoints, the ASGI composition, `DATABASE_URL` parsing with SQLite fallback, and the S3-compatible storage backend. Their security facts, with file and line evidence, are in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "Current implementation security facts".
- Not implemented: authentication, authorization, scope checks, sharing, approval, audit, RAG, model calls, Tasks, runtimes beyond a lazy Playwright factory, and WebMCP. Most `SEC-*` requirements are Planned; "How Planned requirements are reviewed" below says what that means for a review.
- WebMCP is Experimental: an external draft with no default-on browser support as of 2026-10-08 ([WEBMCP.md](WEBMCP.md)). In the AGENT area, `SEC-AGENT-001` is Planned and `SEC-AGENT-002` to `SEC-AGENT-005` are Planned/Experimental.
- The tests named in this document exist in the repository; they were not executed in this documentation pass.

## When this review runs

- After the general procedure in [CODE-REVIEW.md](CODE-REVIEW.md), when its step 9 lists a matching trigger. The code review's base, head, diff, and file list are reused, not re-established.
- When a change cites, implements, or claims to change the status of any `SEC-*` ID, whether or not a trigger matched. When this is the only reason the review runs, the examination is the ID and status consistency check (step 3's status rule and the registry's maintenance rules in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md)), not the behavioral steps.
- When the user asks for one.

The independence rule of [CODE-REVIEW.md](CODE-REVIEW.md) applies unchanged: a review by the author of the diff is a self-assessment and is labelled as such. The reviewer reads; it does not rewrite the change and does not run state-changing git commands on the author's tree.

### Trigger to primary SEC areas

Triggers are the ones listed in [CODE-REVIEW.md](CODE-REVIEW.md) step 9. Area names are those of [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md). Primary areas are always examined for that trigger; "also" areas are examined when a flow traced in step 2 reaches them.

| Trigger | Primary areas | Also |
|---|---|---|
| Authentication, permission, or scope code; a new DRF view; a new `/agent/*` route | AUTH, SCOPE, SHARE | FAIL, API, `SEC-AGENT-001`, `SEC-SECRET-004`, `SEC-IDEM-001`, `SEC-IDEM-005` |
| File and storage code (`core/storage/s3.py`, object naming, overwrite semantics, presigned URLs, upload and download paths) | FILE, `SEC-IDEM-004` | SCOPE, `SEC-FAIL-002`, `SEC-SECRET-003` |
| Retrieval and indexing | RAG, SHARE, SCOPE | INJ, `SEC-FILE-001` |
| Tool or runtime execution (Browser, Terminal, Workspace, Playwright, external tool calls) | RUNTIME, TOOL | IDEM, SECRET, `SEC-FILE-006`, `SEC-FAIL-003` |
| Approval flows | APPROVE, IDEM | `SEC-FAIL-001`, `SEC-SECRET-004` |
| Secrets and configuration (`settings.py`, `config/database.py`, environment variables, `.env.example`, `.gitignore`, `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, CORS) | SECRET, API, `SEC-FAIL-002` | `SEC-AUTH-004`, `SEC-FILE-004` |
| WebMCP and agent-originated actions | AGENT, APPROVE, IDEM | AUTH, `SEC-INJ-003`, `SEC-SECRET-004` |
| External content handling (web pages, uploaded files, retrieved text, tool output reaching a model or a user) | INJ, `SEC-RAG-002` | `SEC-SECRET-003`, `SEC-TOOL-002` |
| Logging and audit | `SEC-SECRET-003`, `SEC-SECRET-004`, `SEC-TOOL-002` | `SEC-FILE-005` |
| i18n locale input handling (cookies, `Accept-Language`, preference values, `<html lang>`) | `SEC-INJ-001`, `SEC-AGENT-002` | AUTH, when a preference is stored on the account |

### How Planned requirements are reviewed

Most IDs are Planned, so most reviews check that a change keeps the target reachable rather than that the target is met. The status of each ID is read from [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "Status summary", never assumed.

- A change that introduces the feature an ID describes makes that ID applicable in full: the behavior is implemented, tested with denial cases (step 8), and the ID's status is updated in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) in the same change, citing the code and the test.
- A change that adds a path the requirement would later have to cover (a new endpoint, a new place where content reaches a model, a new side effect) without the control is a finding. It is a blocker when the path is reachable on a running server, because nothing upstream denies it today (`SEC-AUTH-001`, `SEC-AUTH-003`).
- A change that describes a Planned requirement as satisfied, in code comments, documentation, or the change description, is a finding.
- A change that moves an ID's status, or defines one as Implemented or Partial, without citing the code and the test (or, for the Partial status of a repository-hygiene requirement such as `SEC-SECRET-001`, the file) is a finding. A status moves only with repository evidence.

## Procedure

Work through the steps in order; each later step uses the record of the earlier ones. Record what was examined, not only what was found. Steps 3 to 7 state what the reviewer checks against each ID; the requirement text and status stay in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md), and a check here describes target behavior unless the ID is marked Implemented there.

### 1. Identify changed trust boundaries

Map every file in the change to the boundaries in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "Trust boundaries"; a file that belongs to no boundary is mapped to the implementation-fact section that covers it, and a documentation file is recorded as "no boundary". For the code that exists today:

| Files | Boundary |
|---|---|
| `backend/config/settings.py`, `backend/config/urls.py`, `backend/core/urls.py`, `backend/core/views.py`, `backend/core/serializers.py` | Browser to control plane (Django, DRF, Django middleware) |
| `backend/config/asgi.py`, `backend/agent/fastapi/` | Control plane to agent surface (`/agent/*` receives no Django middleware) |
| `backend/config/database.py`, `backend/core/storage/s3.py` | Control plane to stores |
| `backend/agent/runtime/`, `backend/agent/llm/`, `backend/agent/rag/`, `backend/agent/orchestration/`, `backend/agent/tools/` | Runtimes, and model to content (docstring-only packages or a lazy factory today) |
| `frontend/src/lib/api.ts`, `frontend/next.config.ts`, `frontend/src/proxy.ts`, `frontend/src/components/`, `frontend/src/app/` | Browser to control plane (the client side of it; `next.config.ts` and `src/proxy.ts` run in the Next.js server, which forwards `/core/*` and `/agent/*` to the backend and redirects UI paths, and authorizes nothing) |
| `.env.example`, `.gitignore`, `backend/requirements.txt`, `backend/locks/*`, `frontend/package.json`, `frontend/package-lock.json`, `.nvmrc`, `.python-version` | No boundary: the "Secrets hygiene" implementation facts, plus the dependency surface |

Record:

- Which boundaries the change touches, and whether it creates a new one: a route, endpoint, environment variable, storage path, outbound call, runtime, or model call.
- The trust level on each side, and on which side the new code runs.
- The triggers matched and, from the table above, the areas and IDs to examine, plus any ID the change itself cites.

A new file under `backend/agent/fastapi/routes/` or a new `path()` in `backend/core/urls.py` is always a new boundary. A dependency added to `requirements.txt` or `package.json` widens the trust surface and needs the explicit approval that [CONTEXT.md](CONTEXT.md) requires, cited in the change. A change to `backend/locks/*` or `frontend/package-lock.json` needs the same cited approval, because [CONTEXT.md](CONTEXT.md) requires it for lockfile changes.

### 2. Trace input, output, and state-change flows

For each entry point the change adds or alters, write the flow. When the change adds or alters none, write "none" and go on; steps 3 to 7 then examine only what the change cites or preserves.

```text
entry -> validation -> authentication -> authorization -> approval -> operation -> side effects -> outputs
```

- Entry: request body, query, path, header, cookie, environment variable, file content, retrieved text, tool input, tool output, runtime output, or a message from another user. Everything from the browser, a file, retrieval, a tool, a runtime, an external event or webhook, or another user is untrusted input and is validated (`SEC-INJ-001`). For a message, also record whether it is the direct request being handled (its server-authenticated sender, a user, asks the AI for something in a context the user may act in, or accepts a proposal or answers a question the AI put to that user, for exactly the operation and targets shown; a reply that declines, asks back, or is ambiguous requests nothing) or material the AI reads (history, other members' earlier requests, messages from AI participants, quotes, attachments, referenced content); only a direct request carries intent, and only its sender's ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md), "Direct requests and referenced content").
- Validation: what is checked (type, length, charset, allowlist), where, and what happens on failure. Note a check that is missing or that runs after a side effect.
- Side effects: database writes, object storage writes, outbound calls, log lines, cache or process state, files on disk, messages to other users.
- Outputs: what is returned and to whom, what is logged, what reaches a model, what reaches another context.

Trace the failure paths too: an exception midway, a retry, a concurrent duplicate, a cancelled Task. Every flow that crosses a boundary from step 1 is examined in steps 3 to 7.

### 3. Verify authentication, authorization, and context separation

IDs: `SEC-AUTH-001` to `SEC-AUTH-004`, `SEC-SCOPE-001` to `SEC-SCOPE-003`, `SEC-SHARE-001`, `SEC-SHARE-002`, `SEC-AGENT-001`, `SEC-AGENT-003`.

- A new DRF view beyond health declares its permission classes (`SEC-AUTH-003`). No project-wide default is configured, so a view that declares nothing gets DRF's `AllowAny`. Blocker.
- A new `/agent/*` route beyond health has its own authentication and authorization dependency tied to control-plane identity (`SEC-AUTH-002`, `SEC-AUTH-004`). Django's session, CSRF, CORS, and auth middleware do not run there. Blocker without it.
- Authorization runs on the server, against the actor (the acting user or automation identity) and, for an AI participant the server established, its effective permissions, before the operation (`SEC-AUTH-001`). A check that exists only in the frontend is UX and does not count (`SEC-AUTH-001`); text in a prompt or a tool description is not a check at all (`SEC-INJ-002`, `SEC-AGENT-002`).
- Every File, Knowledge reference, Task, Artifact, and tool permission carries exactly one of the five scopes (`SEC-SCOPE-001`); reads, retrievals, and tool calls check scope server-side (`SEC-SCOPE-003`); a shared AI participant has no path to members' personal context (`SEC-SCOPE-002`).
- Nothing moves from personal to team scope except by a recorded explicit share, and sharing stays separate from indexing (`SEC-SHARE-001`, `SEC-SHARE-002`).
- The interaction origin (`InteractionContext.origin` in [STATE-SCHEMA.md](STATE-SCHEMA.md)) is recorded and never consulted to grant access, and a value the server cannot verify, such as `human_ui` or `webmcp` for a request under a user's browser session, is recorded as client-reported, not as fact (`SEC-AGENT-001`). An AI participant is one the server itself established, never one named by a client value; a caller in the user's browser session, an external browser agent included, acts as that user and gains no AI participant's permissions (`SEC-AGENT-003`).

### 4. Check approval and side effects

IDs: `SEC-APPROVE-001` to `SEC-APPROVE-003`, `SEC-AGENT-002`, `SEC-AGENT-004`, `SEC-FAIL-001`.

- Classify every operation in the traced flows as read, untrusted read, mutation, or consequential, the classes defined in [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) "Classification and approval". Consequential covers at least irreversible deletion, publishing personal material into a team or organization scope, posting or sending outside Claus, external work with sensitive credentials, and high-risk Browser, Terminal, or Workspace execution (`SEC-APPROVE-001`).
- A consequential operation has a recorded human approval before execution, bound to the operation, its `operation_id`, actor, target, parameters, and scope (`SEC-APPROVE-001`, `SEC-APPROVE-002`); the Task stops in `waiting_for_approval`, and denial or timeout leaves no side effect (`SEC-APPROVE-003`). An approval step that the agent requesting or running the operation, or an agent acting in the person's browser session, could complete by itself, or one that accepts a UI click, an agent's reply, or an approval flag sent by a client alone as a verified approval, is a finding (`SEC-APPROVE-001`).
- The approval path is the same for every origin; an agent-originated invocation without it is rejected server-side (`SEC-AGENT-004`).
- An ordinary mutation needs server-side authentication, authorization, scope, and policy checks, not an approval (`SEC-APPROVE-001`). Deciding whether an approval is required from a tool annotation, a browser or agent prompt, a client claim, or the interaction origin is a finding (`SEC-APPROVE-001`, `SEC-AGENT-002`, `SEC-AGENT-004`). An approval step added to an ordinary mutation without a server policy classifying it as consequential is a non-blocking finding: it stalls agents on low-risk work and adds no control.
- A side effect that occurs before authorization or approval completes, or that survives a denial, is a finding (`SEC-FAIL-001`). A partial write left behind by a failure midway is a finding.

### 5. Review prompt injection and untrusted content handling

IDs: `SEC-INJ-001` to `SEC-INJ-003`, `SEC-RAG-002`, `SEC-SCOPE-002`, `SEC-AGENT-002`, `SEC-AGENT-005`.

- Find every path by which text from an untrusted source (step 2) reaches a model, a decision, or another user. No such path exists at HEAD; a change that introduces one is in scope in full.
- Untrusted content is delimited and labelled before it reaches a model, and instructions inside it change nothing about authorization, approval, scope, or what the system does (`SEC-INJ-001`, `SEC-INJ-002`).
- Messages are told apart by authenticated sender and directness, not by their wording. A design that treats every team member's message as untrusted, so members cannot direct a shared AI they may use, is a finding; so is one that carries out an operation, target, recipient, or scope found only in quoted or referenced material and not named or designated by the member's request, or that lets a member's request reach any member's personal context, the member's own included (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-SCOPE-002`).
- A scheduled or triggered run acts on a standing authorization the server can verify, not on a direct request ([SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md) "Basis for state changes"). A design that refuses such a run only because no person sent a new message is a finding; so is one that lets a trigger, an event payload, retrieved or tool content, or the AI's own judgment add an operation, target, recipient, or scope the stored definition does not cover, that skips checking the authorization, permissions, and scope when the run executes, or that counts setting up or scheduling the automation as approval of a consequential operation (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-APPROVE-001`).
- Retrieved chunks keep their provenance and are never treated as policy (`SEC-RAG-002`).
- Tool names, descriptions, and schemas exposed to agents are authored in the repository, short, and reviewed; outputs that carry user content are marked untrusted (`SEC-INJ-003`).
- Once a change registers any WebMCP tool, pages that expose none send `Permissions-Policy: tools=()` (`SEC-AGENT-005`). No registration code exists today, and WebMCP is Experimental ([WEBMCP.md](WEBMCP.md)).
- Locale and preference values from cookies, headers, or request bodies are validated against the supported list before use (`SEC-INJ-001`, [I18N.md](I18N.md)); IDs, enum values, error codes, and tool names are never derived from translated strings (tool names under `SEC-AGENT-002`; the rest per [I18N.md](I18N.md)).

### 6. Review file, knowledge, external tool, and execution runtime boundaries

IDs: `SEC-FILE-001` to `SEC-FILE-006`, `SEC-RAG-001`, `SEC-RAG-003`, `SEC-RUNTIME-001` to `SEC-RUNTIME-004`, `SEC-TOOL-001`, `SEC-TOOL-002`, `SEC-IDEM-004`, `SEC-FAIL-003`.

Files and storage (`SEC-FILE-002`, `SEC-FILE-003`, `SEC-FILE-004`, and `SEC-IDEM-004` are Implemented; check that they are preserved):

- Object names still pass the validation in `core/storage/s3.py` before any client call (`SEC-FILE-002`); saves still use conditional create and allocate a new name on collision (`SEC-FILE-003`, `SEC-IDEM-004`); configuration validation and lazy, clear failure are intact (`SEC-FILE-004`). `backend/core/test_storage.py` is the baseline; a weakened or removed case is a finding.
- A presigned URL is issued only after the server authorized the requester, is not written to logs, messages, Artifacts, or indexed content, and its lifetime is justified for the use case (`SEC-FILE-005`). No authorization layer exists today, so a new code path that returns `url()` output to a request is a blocker until one does.
- File, Artifact, and Knowledge stay separate records with provenance (`SEC-FILE-001`); runtime-local files are not promoted to persistent Files or Artifacts implicitly (`SEC-FILE-006`).

Knowledge (Planned):

- Retrieval filters by effective scope before ranking (`SEC-RAG-001`); indexing is explicit, scoped, and auditable, and never a consequence of upload or share (`SEC-RAG-003`).

Runtimes (Planned; only the lazy Playwright factory exists, `SEC-RUNTIME-004`):

- Task-scoped, isolated, no long-lived credentials (`SEC-RUNTIME-001`); outputs re-enter only as Messages, Files, Artifacts, or Task state through authorized writes, and runtime-local state is discarded afterwards (`SEC-RUNTIME-002`); viewing or controlling a live Browser session is authorized per participant and handoff is exclusive (`SEC-RUNTIME-003`); a failed or cancelled Task cleans up runtime-local state (`SEC-FAIL-003`).

External tools (Planned):

- Credentials stay server-side and scoped, and appear in no prompt, tool input, tool output, or browser payload (`SEC-TOOL-001`); every call is logged with a safe summary (`SEC-TOOL-002`).

### 7. Check secrets, audit, and duplicate-execution risks

IDs: `SEC-SECRET-001` to `SEC-SECRET-004`, `SEC-IDEM-001` to `SEC-IDEM-003`, `SEC-IDEM-005`, `SEC-FAIL-002`, `SEC-API-001` to `SEC-API-003`, `SEC-AGENT-003`.

- The diff contains no secret, token, key, or real credential; a new environment variable gets an empty placeholder in `.env.example` and nothing more (`SEC-SECRET-001`). Search the added text and the file names of every source of change: the committed range for a committed change; unstaged edits, staged edits, and untracked files for an uncommitted one. The check below uses only git and the Python standard library. It reads file names NUL-delimited, so spaces, tabs, newlines, and non-ASCII characters in names are handled, and it prints each name as a Python string literal. It reports the location and the matched keyword, never the matching line, so a real secret is not copied into the review record. `-I` keeps modules in the reviewed tree from shadowing the standard library.

```bash
# Run inside the work tree.
# Committed change: <base> is the target commit, <head> is the change tip.
# Uncommitted change: leave out <base> <head>; unstaged, staged, and untracked files are scanned.
python3 -I - <base> <head> <<'PY'
import os, re, subprocess, sys

WORD = re.compile(rb'password|secret|token|api[_-]?key|private key', re.I)
ENV = re.compile(rb'(^|/)\.env')
DIFF = ['--no-renames', '--no-ext-diff', '--no-textconv', '--diff-filter=d']
found = 0

def git(*args):
    r = subprocess.run(['git', '--literal-pathspecs', *args], capture_output=True)
    if r.returncode:
        sys.stderr.buffer.write(r.stderr)
        sys.exit(2)
    return r.stdout

def report(source, path, what):
    global found
    found = 1
    print(source, repr(os.fsdecode(path)), what)

def scan(source, path, lines):
    for n, text in lines:
        m = WORD.search(text)
        if m:
            report(source, path, 'line %d: %s' % (n, m.group().decode().lower()))

def added(diff):
    n = None
    for d in diff.split(b'\n'):
        if d.startswith(b'diff '):
            n = None
        elif d.startswith(b'@@'):
            m = re.match(rb'@@ -\S+ \+(\d+)', d)
            if not m:
                raise ValueError('unsupported diff hunk; resolve unmerged paths first')
            n = int(m.group(1))
        elif n is not None and d.startswith(b'+'):
            yield n, d[1:]
            n += 1

def scan_diff(source, rng):
    for rec in git('diff', *DIFF, '--numstat', '-z', *rng).split(b'\0'):
        if rec:
            count, _, path = rec.split(b'\t', 2)
            if ENV.search(path):
                report(source, path, '.env name')
            if count == b'-':
                report(source, path, 'binary, not scanned')
            else:
                scan(source, path, added(git('diff', *DIFF, '--no-color', '-U0', '--inter-hunk-context=0', *rng, '--', path)))

def main(args):
    os.chdir(git('rev-parse', '--show-toplevel')[:-1])
    if len(args) == 2:
        return scan_diff('committed', [args[0] + '...' + args[1]])
    if args:
        raise ValueError('expected no arguments or <base> <head>')
    if git('ls-files', '-u', '-z'):
        raise ValueError('unmerged paths; resolve them first')
    scan_diff('unstaged', [])
    scan_diff('staged', ['--cached'])
    for path in git('ls-files', '-z', '--others', '--exclude-standard').split(b'\0'):
        if not path:
            continue
        if ENV.search(path):
            report('untracked', path, '.env name')
        if os.path.islink(path) or not os.path.isfile(path):
            report('untracked', path, 'not a regular file, not scanned')
            continue
        with open(path, 'rb') as f:
            data = f.read()
        if b'\0' in data[:8000]:
            report('untracked', path, 'binary, not scanned')
        else:
            scan('untracked', path, enumerate(data.split(b'\n'), 1))

try:
    main(sys.argv[1:])
except Exception as e:
    print('check failed:', e, file=sys.stderr)
    sys.exit(2)
sys.exit(found)
PY
echo "exit status: $?"
```

Exit status 0 means nothing matched and every changed file was scanned. 1 means at least one line needs review: a keyword match, a `.env` name, or a file the check could not scan (binary, symlink, or not a regular file). 2 means the check itself failed (not a repository, an unknown revision, unmerged paths, an unreadable file) and is never a clean result. Only added lines are scanned; deleted files are skipped. Committed mode scans the net diff from the merge base to `<head>`, not each commit, so a secret added and later removed inside the range is not found; for a multi-commit change, also run it with `<commit>^ <commit>` for each commit in `git rev-list --no-merges <base>..<head>`. When the head is the working tree and the branch also has commits after `<base>`, run it twice, once with `<base> HEAD` and once with no arguments; the result is clean only when both exit 0. Matches are leads: the keywords also match ordinary words such as `token` in prose, and a secret that contains none of them is not found. This is a heuristic, not a dedicated secret scanner, and none is configured in this repository. Whether a match is a real secret is the reviewer's judgment, and exit status 0 shows only that no keyword matched in the scanned change: it says nothing about the rest of the tree, earlier history, or a secret without a keyword. `.gitignore` rules and `.env.example` placeholders do not settle that either (`SEC-SECRET-001`). A secret that reached a commit is treated as exposed even after it is removed from the branch.

- A new configuration value fails clearly when missing or invalid; a new silent fallback to an insecure default is a finding (`SEC-FAIL-002`). The `SECRET_KEY` and `DEBUG` fallbacks are the documented exception (`SEC-SECRET-002`), not a precedent.
- New log lines, Task records, and tool run summaries carry no credentials, presigned URLs, raw tokens, or full prompts containing personal data (`SEC-SECRET-003`).
- A state-changing operation produces an audit record with actor, operation, scope and resource, origin, time, approval, and outcome (`SEC-SECRET-004`).
- No tool or endpoint accepts credentials, tokens, user identifiers, or AI participant identifiers as input in order to act as someone else (`SEC-AGENT-003`).
- Duplicate execution: every state-changing operation takes an `operation_id` that the caller creates before the first attempt and reuses on every retry; the server keys it per actor, binds it to AI participant, context, operation, and input, returns the recorded outcome for a repeat, rejects a different binding without touching the bound operation, and claims the id atomically under concurrency (`SEC-IDEM-001`). Each try is its own attempt record, separate from Task state (`SEC-IDEM-002`). A repeat of an approved request with the same id returns the recorded outcome without executing again, an approval presented for another id or binding is rejected, and a new `operation_id` never inherits an approval unless, after a recorded failure, an explicit re-execution policy for that operation lets it reference the earlier one (`SEC-IDEM-003`). Deduplicating by comparing input or timing (`SEC-IDEM-001`), creating a new `operation_id` to retry a call whose outcome is `pending` or an `outcome_unknown` other than `partially_applied` (`SEC-IDEM-002`), or reporting a sent request with no confirmed answer, or a stopped or refused retry under an id already sent, as success or failure instead of `outcome_unknown` (`SEC-IDEM-005`) is a finding. So is binding an id the caller has no issuance record for (recovered after a reload) to a new operation (`SEC-IDEM-001`), or identifying a lost operation by matching input, picking one of several candidates without the person, issuing a new `operation_id` for an unresolved operation or executing it again without the person's new request, or executing a consequential operation requested anew in that state without its state check (`SEC-IDEM-005`).
- API surface: the health endpoints still return nothing sensitive (`SEC-API-001`; a new endpoint's payload is examined under steps 3 and 5); the browsable API, `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc` remain development conveniences whose exposure is decided before deployment (`SEC-API-002`); `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS` are not widened in code (`SEC-API-003`).

### 8. Require normal, denial, boundary, and retry tests

For each area marked `checked` in the coverage table, tests in these four classes cover the change's behavior in that area (new tests for new behavior, the existing baseline for preserved behavior, each class where the behavior admits it), and the reviewer runs them and reads the output:

- Normal: an authorized actor in scope succeeds, with the intended side effect and no other.
- Denial: a refused invocation gets the intended status and leaves no side effect; the test asserts both.
- Boundary: inputs and states at the edge of what is allowed (scope and context, input size and shape, object names, configuration, embedded instructions).
- Retry: repeated, replayed, resumed, or concurrent invocations.

The required cases per feature are in [TESTING.md](TESTING.md) ("Security-negative testing", "Approval and retry testing", "Personal and team boundary testing", "WebMCP testing", "Internationalization testing"); do not re-list them in the report. For the code that exists, `backend/core/tests.py`, `backend/agent/tests.py`, `backend/core/test_database.py`, and `backend/core/test_storage.py` are the baseline and must still pass unchanged.

Rules:

- Never claim a test passed that was not executed. "A test exists" and "the test passed in this review" are different statements; the second needs the command, the working directory, the exit status, and the summary line, as [CODE-REVIEW.md](CODE-REVIEW.md) "Verification evidence" defines.
- A test that could not run (Django not installed, `node_modules` absent, no browser binary, no object store, no browser build exposing `document.modelContext`) is reported as not run with the reason. The area becomes `could not verify`, and findings that depend on it are suspected at most.
- A test whose code the change cannot affect is reported as not applicable, with the reason; the baseline rule above applies when the change touches backend code.
- Author statements and pasted output without a command and a summary line are claims, not evidence.

### 9. Report residual risk and items that could not be verified

List, each with the IDs concerned:

- Controls that depend on a Planned component that does not exist yet (for example, `SEC-FILE-005` cannot be enforced before an authorization layer exists), and what the change does meanwhile.
- Checks blocked by the environment, with the command that would run them.
- Behavior that only an integration environment shows: a real object store, PostgreSQL, a browser build exposing `document.modelContext`, an external API.
- Assumptions the review relied on that no code enforces, for example that `/agent/*` is reachable only from a local network.

Each item names what would verify it (a test, a command, an environment, or a decision by the user). The result line states the number of blocking findings and that the review is evidence, not approval.

## Report format

Use the finding fields of [CODE-REVIEW.md](CODE-REVIEW.md) "Report format" with one addition: every finding carries a ninth field, `sec_ids`, listing at least one `SEC-*` ID it maps to. The report structure keeps the code review's six items except that "Scope comparison" is replaced by the flows traced and "Security review" by the coverage table.

```text
Security review of <base>..<head>  (or: working tree at <sha> plus N untracked files)
Reviewer: <agent or person>; independent of the author: yes | no (self-assessment)
Triggers matched: <from CODE-REVIEW.md step 9, or: cites SEC-* IDs | user request>
Boundaries changed: <from step 1>

1. Findings            most severe first; CODE-REVIEW fields plus sec_ids
2. Flows traced        entry -> ... -> outputs, one per entry point (step 2)
3. Coverage            the table below, one row per SEC area
4. Verification        commands run with output summaries, inspections performed, checks not applicable or not run and why (step 8)
5. Residual risk       items that could not be verified and what would verify them (step 9)
6. Result              no blocking findings | N blocking findings. Not an approval.
```

### Coverage table

One row per area of [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md). `Result` is one of:

- `checked`: the change was examined against the area's IDs and the tests for it were run; findings, if any, are listed above.
- `not applicable`: the change touches nothing the area covers; the note says why in a few words.
- `could not verify`: the area applies, but evidence is missing (environment limit, dependency on a Planned component, external service); the note names what would verify it, and the row is repeated under residual risk.

| Area | Result | IDs examined | Note |
|---|---|---|---|
| AUTH | | | |
| SCOPE | | | |
| SHARE | | | |
| FILE | | | |
| RAG | | | |
| INJ | | | |
| AGENT | | | |
| APPROVE | | | |
| RUNTIME | | | |
| TOOL | | | |
| SECRET | | | |
| IDEM | | | |
| FAIL | | | |
| API | | | |

A finding never makes an area `not applicable`: the area is `checked` when its tests ran and `could not verify` when they did not, and the finding is listed either way. An empty findings list with every area `not applicable` means no code trigger matched; say so in the result line, and when the review ran because the change cites `SEC-*` IDs or the user asked, name that reason and record the consistency check as what was examined.

### Illustrative example

Illustrative example, not implemented. The change described here does not exist at HEAD; it shows the format only. Assume a diff that adds a DRF view under `/core/` which takes an object name from the query string and returns the presigned download URL for it, with no permission classes declared.

Finding, in the [CODE-REVIEW.md](CODE-REVIEW.md) fields plus `sec_ids`:

- `severity`: blocker
- `confidence`: confirmed
- `location`: `backend/core/views.py`, the new view; `backend/core/urls.py`, the new `path()`.
- `observed problem`: the view declares no permission classes, so DRF's default `AllowAny` applies, and it returns the storage backend's `url()` result for any `name` given in the query string.
- `impact`: any client that knows or guesses an object name, without a session, obtains a bearer URL valid for 3600 s to that object; the URL also passes through access logs and proxies.
- `reproduction conditions`: with the change applied, an unauthenticated `GET` to the new route with `?name=<existing object name>` returns 200 and a presigned URL.
- `required fix`: explicit permission classes plus a server-side check that the object belongs to a scope the requester may read, before `url()` is called; or do not ship the view until that layer exists.
- `regression verification`: a denial test asserting the rejecting status and no URL in the body for an unauthenticated request, and a normal test for an authorized one; `cd backend && python manage.py test core`, summary line cited.
- `sec_ids`: `SEC-AUTH-001`, `SEC-AUTH-003`, `SEC-FILE-005`, `SEC-SECRET-003`.

Coverage rows for this example (other rows omitted):

| Area | Result | IDs examined | Note |
|---|---|---|---|
| AUTH | checked | `SEC-AUTH-001`, `SEC-AUTH-003` | finding above |
| FILE | checked | `SEC-FILE-002`, `SEC-FILE-003`, `SEC-FILE-004`, `SEC-FILE-005` | storage tests ran unchanged; `SEC-FILE-005` finding above |
| APPROVE | not applicable | | read-only operation |
| RUNTIME | not applicable | | no runtime code touched |

## Related documents

- [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md): trust boundaries, the only definition of `SEC-*` requirements, and their current status.
- [CODE-REVIEW.md](CODE-REVIEW.md): the general procedure that precedes this one, the trigger list, and the finding format.
- [TESTING.md](TESTING.md): the tests that exist and the denial, boundary, approval, and retry cases each feature must add.
- [CONTEXT.md](CONTEXT.md): the rules and guardrails every change is held to.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): `InteractionContext`, `AgentTaskState`, and `ToolRun`, the shapes audit and approval records refer to.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md) and [WEBMCP.md](WEBMCP.md): background for agent-originated actions.
- [I18N.md](I18N.md): locale input handling and the separation of translated strings from machine values.
