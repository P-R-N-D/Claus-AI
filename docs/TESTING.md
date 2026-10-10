# Claus Testing Strategy

This document separates checks for the current runnable scaffold from future feature-level verification. Do not claim planned behavior or platform support is tested when the corresponding implementation or runtime is absent.

## Tests that exist today

The repository contains these automated tests. Their existence is a fact; whether they pass must be shown by running them and citing the output. The backend has 23 test methods, run by both `python manage.py test core agent` and pytest (with pytest-django), which collect the same 23 test IDs. The frontend has 12 Playwright tests (one `home.spec.ts` scenario and two `proxy.spec.ts` tests, each run in four projects) and 13 Vitest unit tests in two files. The CI script `scripts/ci/check_test_lock.py` has 42 `unittest` regression tests.

| Location | What it covers |
|---|---|
| `backend/core/tests.py` | `GET /core/health/` returns 200 and the expected JSON. `CorsPreflightTests` (2 methods): a preflight from the allowed origin `http://127.0.0.1:3000` receives `Access-Control-Allow-Origin`, `GET` in `Access-Control-Allow-Methods`, and no credentials header; one from a disallowed origin still gets 200, because django-cors-headers answers every preflight, but no CORS headers. |
| `backend/core/test_database.py` | `DATABASE_URL` parsing: SQLite fallback for unset/blank values, PostgreSQL URL parsing with percent-decoding and query preservation, `postgres` alias and default port, nine invalid URLs rejected with `ImproperlyConfigured`; `SECRET_KEY` placeholder fallback. |
| `backend/core/test_storage.py` | `S3CompatibleStorage` contract against a fake S3 client: default backend wiring, save/open/exists/size/delete/url, binary-only reads, content type handling, collision retry (including `max_length` and concurrent saves), no implicit overwrite, missing-object errors, dangerous object names rejected, lazy and clear configuration errors, path-style SigV4 client, endpoint validation. Not an integration test against a real object store. |
| `backend/agent/tests.py` | ASGI routing through `config.asgi.application`: `/core/health/`, `/agent/health/`, `/agent/openapi.json` are 200; `/api/health/` is 404. `test_agent_slash_mismatch_is_404_without_redirect`: `/agent/health`, `/agent/docs/`, `/agent/redoc/`, and `/agent/openapi.json/` return 404 with no `Location` header, while `/agent/docs`, `/agent/redoc`, `/agent/openapi.json`, and `/agent/docs/oauth2-redirect` return 200. |
| `frontend/tests/visual/home.spec.ts` | One scenario in four Chromium projects (desktop/mobile × light/dark), through the Next.js server: `/core/health/`, `/agent/health/`, and `/agent/openapi.json` return 200 without redirects; `/console/`, `/core-ui/`, and `/agent-ui/` return a 308 to the slashless path with the query preserved; `//evil.example/` (Next's own repeated-slash 308, before the Proxy) and `/%2F%2Fevil.example/` (the Proxy's trailing-slash 308) return a 308 with a `Location` that resolves exactly to `/evil.example/` and `/%2F%2Fevil.example` on the frontend origin, kept as a canary; `/agent/docs` returns 200, while `/agent/docs/` and `/agent/health` return 404 with no `Location`; `/core/health` returns Django's 301 to `/core/health/` on the same origin with the query preserved; `POST /_next/mcp` returns 404. `/` and `/console` render, including navigation through `/console/`; both health cards show their expected JSON; during a retry activated with the keyboard while the Core request is held, Retry is `aria-disabled` with `aria-busy="true"` and keeps focus, and afterwards both cards show Connected and Retry is enabled and still focused; a pointer click on Retry then completes another check. Horizontal overflow is measured as `documentElement.scrollWidth - documentElement.clientWidth`, and a self-check injects a double-width element to prove the measurement can fail in each project. No console/page errors, failed requests, or HTTP errors. Saves `home.png`, `home-retrying.png`, and `console.png`. |
| `frontend/tests/visual/proxy.spec.ts` | Two browserless tests, run in each of the four projects. Direct `proxy(new NextRequest(...))` calls: `//evil.example/`, `///evil.example/x/`, and `/console/?view=tasks&filter=a%2Fb` return a 308 to the request's own origin with a non-scheme-relative path and the query preserved; `/`, `/console`, and `/core/health/` are not redirected. Matcher checks through Next's version-pinned `unstable_doesMiddlewareMatch` helper (recheck when Next is upgraded): `/core/health/`, `/agent/docs/`, and `/_next/static/chunk.js` bypass the Proxy; `/core-ui/`, `/agent-ui/`, `/console/`, and `/` reach it. |
| `frontend/tests/unit/api.test.ts` | Vitest, 5 tests. `coreApi` and `agentApi` target the same-origin `/core/` and `/agent/` prefixes with a 5000 ms timeout and are separate instances; a health request through each client, answered by a local adapter without network, goes to `/core/health/` or `/agent/health/` with the trailing slash kept. |
| `frontend/tests/unit/HealthCard.test.tsx` | Vitest with React Testing Library in jsdom, 8 tests; `@/lib/api` and `sweetalert2` are mocked, so no request leaves the test. The card checks both endpoints on mount and shows each response; a failed service shows Disconnected without an alert on the initial check; a failed retry raises one SweetAlert2 alert naming each failed service (plural and singular wording), and a successful retry raises none; Retry is ignored while a check is in flight; a retry that fails after unmount raises no alert; and under React `StrictMode` a superseded check that settles later cannot overwrite the newest result. |
| `scripts/ci/tests/test_check_test_lock.py` | `unittest`, 42 tests of the lock check described under "Current scaffold checks". 36 run the check with `python -I` in a subprocess on copies of the two backend locks and their requirement inputs, most of them edited, and expect a given failure message or a pass: missing, mismatched, duplicate, or unhashed pins; unexpected lock lines; a header for another target, or in uv's `--flag=value` form; test-only packages from the wrong origin; requirements that are unpinned, excluded by their specifier, dropped, or added without regenerating; unsupported input lines, including archive file names; direct URLs; dropped, added, swapped, and unknown extras; markers that hold or are false on the target, including on a package the locks do not pin; unsupported marker forms; and an environment that lacks a pin or holds another version. Six call its functions in-process on made-up inputs: the metadata walk (markers evaluated for the target even when the running machine reports Windows, extras requested through other extras or after their package was first reached, and dependency problems), input markers evaluated for the target when the running machine reports Windows, and the supported and unsupported marker forms. |

No test covers authentication, authorization, scope separation, approval, Tasks, retrieval, Browser sessions, realtime, i18n, or WebMCP, because none of those features exist.

## Current scaffold checks

The checked backend baseline is Django 6.1 on CPython 3.12.15, Linux x86-64. Use uv 0.12.24 and the matching hash lock; [backend/locks/README.md](../backend/locks/README.md) describes regeneration, artifact evidence, and other-platform limits. Which other platforms the lock can serve is in [Platform and accelerator lanes](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes): Linux arm64 (glibc) and macOS 15 or later on Apple Silicon are statically resolvable but have not been run, and native Windows, x64 or Arm, cannot produce a working environment from the lock today, so on Windows run these commands in WSL2 (Ubuntu). `--managed-python` makes uv use its python-build-standalone CPython rather than a system interpreter, whose SQLite can be older than Django 6.1's 3.37.0 floor. Record `python -VV` and `sys.executable` with the results.

```bash
# Linux, macOS, or WSL2 (POSIX shell), from the repository root
uv venv --managed-python --python 3.12.15
source .venv/bin/activate
python -c "import sqlite3; print(sqlite3.sqlite_version)"  # must print 3.37.0 or newer
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
python backend/manage.py check

cd backend
python manage.py test core agent
python manage.py makemigrations --check --dry-run
```

The same suite also runs under pytest. Its environment comes from the integrated test lock, `backend/locks/cp312-linux-x86_64-test.txt`: the backend lock's 50 pins with identical versions and hashes, plus five test-only packages (pytest 9.1.1, pytest-django 4.14.0, pluggy 1.6.0, iniconfig 2.3.1, and Pygments 2.21.0). It is Linux x86-64 only, like the backend lock; [backend/locks/README.md](../backend/locks/README.md) records how it is generated. `uv pip sync` removes every package that is not in the lock it installs, so syncing the backend lock again removes the test tools. pytest-django 4.14.0 declares support for Django 5.2 and 6.0, not 6.1; see the exception in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md#compatibility-and-security-exceptions).

```bash
# From the repository root, in the environment created above
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64-test.txt
python -I scripts/ci/check_test_lock.py backend/locks/cp312-linux-x86_64.txt backend/locks/cp312-linux-x86_64-test.txt backend/requirements.txt backend/requirements-test.txt
python -I -m unittest discover --start-directory scripts/ci/tests --verbose

cd backend
python ../scripts/ci/check_test_parity.py
python -m pytest
```

`backend/pytest.ini` sets `DJANGO_SETTINGS_MODULE`, collects `core` and `agent` with Django's `test*.py` file pattern (pytest's default would skip `tests.py`), and turns on `--strict-markers` and `--strict-config`. `check_test_parity.py` fails unless pytest collects exactly the test IDs that Django's test runner discovers for `core agent`; it lists the IDs only one runner has. pytest exits 5 when it collects no test. With `-q` its summary also counts passed `subTest` blocks (`23 passed, 19 subtests passed` for this suite). pytest-django orders tests the way Django's runner does (`TestCase` classes first), so `python -m pytest` and `manage.py test core agent` run the 23 tests in the same order.

`check_test_lock.py` compares the two locks with each other, with their requirement inputs, and with the package metadata of the environment it runs in, for the one target both locks are compiled for: CPython 3.12.15 on `x86_64-manylinux_2_31`. It evaluates markers with that target's values, not the running machine's. It fails when any of the following is true:

- A lock has a line other than a `name==version` pin, its sha256 hashes, and comments such as uv's `# via` annotations; a pin repeats or has no hash; or the header's `uv pip compile` command does not record `--python-version 3.12.15` and `--python-platform x86_64-manylinux_2_31` once each, as `--flag value` or `--flag=value`.
- A backend lock pin is missing from the test lock or has another version or hash set there.
- A package only in the test lock has no `# via` annotation, or is pulled in by anything other than `requirements-test.txt` or another test-only package.
- An input has no requirement, or a line that is not a named requirement with optional extras, version specifier and marker: a direct URL (`name @ url`), a path, a name ending in an archive suffix such as `.whl`, `.zip`, or `.tar.gz` (pip and uv read it as a local file), `-e`, `-r`, `-c`, or another option. As in pip and uv, `#` starts a comment only at the start of a line or after whitespace.
- A marker in an input, or in the metadata of a package the walk below reads, uses a form outside those that `packaging`, which evaluates markers here, and uv, which compiles the locks, evaluate alike on the target. The supported forms are `and`, `or`, and parentheses over comparisons of a variable on the left with a quoted value on the right, where the variable is `sys_platform`, `os_name`, `platform_system`, `platform_machine`, `implementation_name`, or `platform_python_implementation` compared with `==` or `!=`, or `python_version` or `python_full_version` compared with `==`, `!=`, `<`, `<=`, `>`, or `>=` to a plain release such as `3.12` or `3.12.15`; package metadata may also use `extra == "<name>"`. Each form except the metadata `extra` comparison, which a requirements file cannot express, was checked on 2026-10-10 by compiling `six ; <marker>` with uv 0.12.24 for the target, 224 markers in all. The two tools give different results for other forms, such as `in`, `platform_release`, a version with `*` or a local part, and `extra` in a requirements file, so a requirement could count as absent here while uv adds it, or the reverse, and one of them rejects some forms the other accepts, such as `~=` and `===`. The check rejects every form outside the list, whether or not a disagreement is known for it.
- For each lock and each input it is compiled from (the backend lock from `requirements.txt`, the test lock from both inputs), a requirement whose marker holds on the target is not pinned, is pinned at a version its specifier excludes, or is not recorded in the `# via` annotations as a direct requirement of that input; or the lock records as a direct requirement of that input a package that the input no longer lists, or lists only with a marker that is false on the target.
- A package in the test lock is not installed in the running environment, or is installed at another version.
- Following the installed packages' `Requires-Dist` metadata from each lock's inputs, with the requested extras and with markers evaluated for the target, reaches a package the lock does not pin or never reaches one it does pin; a requested extra is not one the package declares in `Provides-Extra`; or a dependency's specifier excludes the pinned version.

So an input change fails, until the locks are regenerated, when on the target it adds or drops a direct requirement, changes which packages the requirements, extras, and markers reach, or excludes a pinned version, and it always fails when it adds a line the input rules above reject: a direct URL, a path, an archive file name, an option such as `-e`, `-r`, `-c`, or `--index-url`, a `#` without whitespace before it, or an unsupported marker form. Any other input change passes without regenerating: a widened or removed version range, or a lowered minimum, that still allows the pinned version; a marker that holds on the target; or an extra whose packages the lock already pins. While the index offers the same files, regenerating with the README's commands for such a change alters at most the locks' `# via` annotations, because uv keeps the existing pins, so the check cannot tell the difference. In particular it does not notice a lowered minimum version, such as a security floor in `requirements.txt`, while the pin still satisfies it; review range changes in the diff. The check does not establish that the pins are the newest allowed releases or the ones a fresh resolution would pick, that the hashes belong to the files on the index (`uv pip sync --require-hashes` checks the files it downloads), that the `# via` annotations list every dependent, or anything about another platform or Python version. It reads the metadata of the environment it runs in, so run it in the environment installed from the test lock, as CI does, and with `python -I`, which keeps files in `scripts/ci/` or the current directory from standing in for installed packages or their metadata.

`scripts/ci/tests/test_check_test_lock.py` holds 42 regression tests for these rules, which use the standard library's `unittest`; `python -m unittest discover` exits 5 when it finds no test. 36 copy the two locks and two inputs into a temporary directory, change them, for example by dropping `psycopg`'s `binary` extra, adding `; sys_platform == "win32"` to `boto3`, adding `requests>=2.32 ; platform_release < "6"`, or replacing `Django` with a direct URL (a few change several files, and one leaves them as committed), run the check there with `python -I`, and expect it to fail with a given message or to pass; a test whose edit no longer changes its file fails. The other six call the check's functions on made-up inputs, including with the running machine reporting itself as Windows.

In Windows PowerShell the environment is activated with `.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`, and the other commands are unchanged. Native Windows cannot produce a working environment from this Linux-resolved lock until a Windows lane exists: on Windows x64 the sync installs without `tzdata`, which Django and psycopg require on Windows, so `uv pip check` and time-zone handling fail; on Windows on Arm the sync itself fails because the pinned `autobahn`, `cryptography`, and `psycopg-binary` releases have no `win_arm64` wheels. Use WSL2 (Ubuntu) instead.

The ASGI integration tests must exercise `config.asgi.application`, including `/core/health/`, `/agent/health/`, `/agent/openapi.json`, the removed `/api/health/` route, and wrong-slash `/agent/*` paths, which must return 404 without a `Location` header.

Verify both server entrypoints independently:

```bash
cd backend
python manage.py runserver 127.0.0.1:8000 --noreload
uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

For each server, verify `/core/health/`, `/agent/health/`, `/agent/docs`, `/agent/openapi.json`, `/agent/redoc` (FastAPI's default, not set in code), and `/admin/`; `/api/health/` must remain absent, and `/agent/health` and `/agent/docs/` must return 404 without a redirect.

Python Playwright package installation and the Chromium binary lifecycle are separate:

```bash
playwright install chromium
```

Frontend checks remain:

```bash
cd frontend
npm ci
npx playwright install chromium
npm run lint
npm run test:unit
npm run build
CI=1 npm run test:visual
CI=1 PLAYWRIGHT_NEXT_SERVER=production npm run test:visual
```

`npm run test:unit` runs `vitest run` with `frontend/vitest.config.mts`: jsdom, the `@/*` path alias from `tsconfig.json`, and only `tests/unit/**/*.test.{ts,tsx}`, so Vitest never collects the Playwright files under `tests/visual`. It needs no browser and no backend, and it exits 1 when it finds no test file.

Use Node 24.21.0 and npm 11.21.0. Node 24.21.0 bundles npm 11.19.0 and the `engines` pin only warns (`EBADENGINE`), so install npm 11.21.0 explicitly (`npm install --global npm@11.21.0`). Activate the backend environment in the terminal that starts Playwright: its web-server command invokes `python`.

`PLAYWRIGHT_NEXT_SERVER` selects the Next.js server. Unset or `dev`, the default, runs `npm run dev`; `production` runs `npm run build` and then `next start` on 127.0.0.1:3000 and never reuses a running backend or Next.js server, so the result describes the current checkout (it fails if either port is already in use); any other value is an error. In dev mode outside CI, Playwright reuses a backend or `next dev` server already listening on its port, which may be serving other code. For evidence runs, set `CI=1`, which disables reuse of both servers, and record the server mode. In PowerShell, set the variables before the command, for example `$env:CI = "1"` and `$env:PLAYWRIGHT_NEXT_SERVER = "production"`, then run `npm run test:visual`. Both variables are listed empty in `.env.example` for reference; Playwright does not load that file. `npm run dev` binds to 127.0.0.1; use `npm run dev -- --hostname 0.0.0.0` only deliberately, for example to test from a phone on the local network.

The four projects check desktop/mobile widths, light/dark rendering, retries, horizontal overflow (with a self-check that the measurement can fail), the redirect and 404 contracts, and network/page/console failures. Inspect the saved screenshots for clipping and visual regressions; accessibility and pixel-diff baselines remain unimplemented.

`frontend/next-env.d.ts` is generated and not tracked. On a fresh clone, running `npx next typegen` (or `npm run dev` or `npm run build`) after `npm ci` is recommended before `npx tsc --noEmit` or editor type checking, so Next's route and image type references are available; `npx tsc --noEmit` writes `frontend/tsconfig.tsbuildinfo`, which is ignored.

On Linux arm64, Playwright supports Chromium on Ubuntu 22.04, 24.04, and 26.04 and Debian 12 and 13; the Google Chrome channel is not available there. On native Windows on Arm, Playwright ships only x64 browsers, which run under Prism emulation (native Windows Arm64 browsers were declined upstream: [microsoft/playwright#40202](https://github.com/microsoft/playwright/issues/40202) was closed as not planned); such a run is emulated-browser evidence, not native Arm evidence. Native Windows also cannot produce the working backend environment that Playwright's backend web server needs (see above); WSL2 avoids both limits. None of these platforms has been run for this baseline.

The default browser is the binary paired with `@playwright/test`. If its download is unavailable, `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/absolute/path/to/chromium` explicitly selects a separately installed browser. Record its version and the failed download; that run is supplemental UI evidence, not qualification of Playwright's paired browser. Environment-limited failures must never be reported as passing. The dated results and remaining checks are in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md#applied-baseline-and-verification).

## Continuous integration

Added 2026-10-10. `.github/workflows/ci.yml` runs the backend and frontend commands of "Current scaffold checks", plus `npx next typegen` and `npx tsc --noEmit`, on every pull request to `main`, every push to `main`, and on manual dispatch. It covers the checked lane only: GitHub-hosted `ubuntu-24.04` x86-64 runners, uv 0.12.24 with CPython 3.12.15 and the hash lock, and Node 24.21.0 (from `.nvmrc`) with npm 11.21.0. The first version of the workflow changed no test, lock, or package manifest; the pytest and Vitest jobs, added later on 2026-10-10, came with the backend test lock and the Vitest packages, and left the four original jobs unchanged. A workflow file is not evidence that anything ran or passed: cite the run, its commit SHA, the job, and the summary line from its log, as for any other check. A `pull_request` run tests the merge of the pull request's head into its base as they were when the run started (`refs/pull/<number>/merge`), not the head commit alone: record both the head SHA and the merge commit SHA that the checkout step logs, and treat the run as stale once the head or the base moves.

| Job (check name) | What it runs |
|---|---|
| `Backend / CPython 3.12.15 / Linux x64` | `uv venv --managed-python --python 3.12.15`, `uv pip sync --require-hashes --only-binary :all:` of the lock, and `uv pip check`; records `python -VV`, `sys.executable`, and the SQLite version and fails on any other Python version; then, in `backend/`, `python manage.py check`, `python manage.py test core agent`, and `python manage.py makemigrations --check --dry-run`. The test step also fails when no test ran, because Django exits 0 in that case. |
| `Frontend / Node 24 / Linux x64` | `npm ci`, `npm run lint`, `npx next typegen`, `npx tsc --noEmit`, and `npm run build`. |
| `E2E / Playwright Chromium / production Next` | The same uv and hash-lock install as the backend job, without its interpreter record and version check; `npm ci`, `npx playwright install --with-deps chromium` (the paired browser and its system packages), then `CI=1 PLAYWRIGHT_NEXT_SERVER=production npm run test:visual`. |
| `E2E / Playwright Chromium / dev Next` | The same with `PLAYWRIGHT_NEXT_SERVER=dev`. Only `next dev` can serve `/_next/mcp`, so this run, not the production one, is the check that `experimental.mcpServer: false` still holds. |
| `Backend / pytest / CPython 3.12.15 / Linux x64` | The backend job's install with the integrated test lock instead of the backend lock; records `python -VV` and the pytest version and fails on any other Python version; runs `scripts/ci/check_test_lock.py` with `python -I` against the two locks, their requirement inputs, and the installed packages' metadata, then its regression tests with `python -I -m unittest`; then, in `backend/`, `scripts/ci/check_test_parity.py` and `python -m pytest`. |
| `Frontend / Vitest / Node 24 / Linux x64` | The frontend job's npm 11.21.0 install and `npm ci`, then `npm run test:unit`. |

- Both E2E jobs upload `frontend/test-results/`, which holds the saved screenshots, as a run artifact kept for 7 days, whether the tests passed or failed. Inspecting the screenshots stays a reviewer's step, as above.
- No job depends on another, so a failure in one never turns another into a skipped check. When the four original jobs first passed on `main`, their check names became eligible as required status checks; the pytest and Vitest checks become eligible once they have passed. Making a check required is a GitHub repository setting, not a file in this repository, and changing it needs admin access to the repository.
- A newer run for the same pull request cancels the older one. Every push to `main` and every manual dispatch runs in its own concurrency group, so none of those runs is cancelled or dropped from the queue.
- Not covered by CI: other operating systems, CPU architectures, and accelerators; Python 3.15 and 3.15t; the manual route checks against `runserver` and Uvicorn above, for which no API collection exists (see "API collection testing" below); the Python Playwright browser install; accessibility and pixel-diff checks; secret scanning; and dependency alerts.
- A green run shows that these commands passed on that commit and runner. It is not a review and not an approval ([CODE-REVIEW.md](CODE-REVIEW.md) "Independence and authority"). A change to any workflow file goes through the security review ([CODE-REVIEW.md](CODE-REVIEW.md) step 9).

## API collection testing

Blocked, recorded 2026-10-10. No Postman collection, Postman CLI, or Newman is in the repository. The Postman CLI was checked as a candidate for the route checks above, in an isolated scratch install (`npm install --ignore-scripts`, a throwaway `HOME`, no login, API key, `postman init`, or cloud workspace) of `postman-cli` 1.73.0 and its `@postman/pm-bin-linux-x64` 1.73.0 binary package. Two conditions set for adopting it were not met:

- External transmission. A local collection run against `127.0.0.1` with `--no-report-events` passed, but the CLI still tried to reach `events.getpostman.com` and `dl-cli.pstmn.io` (an update check). The [Postman CLI options](https://learning.postman.com/docs/postman-cli/postman-cli-options/) page (fetched 2026-10-10) says `--no-report-events` turns event reporting off for that run, and the CLI's `--help` says it stops dispatching results and analytics, yet both attempts happened with it set; no documented option stopped them. A network guard blocked both, so nothing was sent; without it they would have been.
- Usage terms. Neither package has a `license` field or a license file, and npm shows no provenance attestation for either. The [Postman Terms of Service](https://www.postman.com/legal/terms/) license downloadable Software for use as part of the Service and in association with a Postman Account, so use without an account in a public repository's CI is not clearly permitted.

Also recorded: on a global install (`npm install -g`), the package's `postinstall` appends a block to `CLAUDE.md` in the Claude Code configuration directory (`~/.claude` by default) and to `AGENTS.md` (or `AGENTS.override.md`) in the Codex one (`~/.codex`) when those directories exist, and `postman update` performs a global install; a local install skips that step. Newman was not evaluated. Until the terms are clarified and a documented setting stops these connections, API route checks stay in the Django/ASGI tests and the Playwright scenario above.

## Topic/Thread and context testing

When collaboration features are implemented, verify:

- Personal AI context is not exposed automatically to a team context.
- Team AI receives only the permitted Topic/Thread history, Files, and Knowledge.
- Navigation preserves the intended context.
- Topics do not leak Messages, Files, Task state, or retrieval results into each other.

## File, Artifact, and RAG testing

When these features are implemented, verify:

- Upload, download, and preview permissions.
- Personal versus team visibility.
- File upload does not automatically create team/organization Knowledge.
- Explicit indexing preserves source and scope metadata.
- Artifact provenance remains traceable to the source Task/context.
- Runtime-local temporary Files do not become persistent automatically.

## Background Task testing

When background execution is implemented, verify:

- Long-running work does not block the requesting conversation.
- Product Task state transitions are observable and consistent.
- Each agent execution attempt remains distinct from its product Task.
- Cancellation and failure do not corrupt conversation or persistent Files.
- Persistent output returns explicitly as a Message, File, Artifact, or Task result.
- Approval-required actions stop for the required human decision.

## Browser Computer Use testing

The current repository contains only the async Python Playwright package boundary, not Browser Computer Use product behavior. When implemented, verify:

- Session start and stop cleanup.
- Task/context association.
- Prevention of cross-context session access.
- Actions operate on the intended page and browser state.
- User/AI control handoff is exclusive and visible.
- Screenshots are generated and reviewed.
- Browser console errors and failed network/resource requests are checked.

## Shared result and view testing

When shared presentation is implemented, verify:

- The correct Artifact or live session is displayed.
- Intended presentation state remains consistent.
- Closing a shared view does not delete the underlying Artifact.
- Responsive layouts avoid unintended overflow and clipping.
- Supported light and dark modes remain usable.

## API testing

For future API changes, cover:

- Normal requests
- Missing required input and invalid formats
- Authentication and authorization failures
- Missing resources and boundary values
- Expected status codes and response schemas
- Stable error formats
- Intended state changes and regression risk

Do not issue state-changing requests against production without explicit approval or place real secrets in test assets.

## Free-threading compatibility testing

The architecture already requires free-threading compatibility. [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md) defines Python 3.15 Limited API/`abi3t` artifact selection, the dated wheel gaps, and performance admission budgets. These qualification checks and benchmarks are planned; the current test suite does not establish Python 3.15/3.15t support.

When a suitable environment and dependency set are available:

- Use separate environments for the existing CPython 3.12 lane, regular `cp315`, and free-threaded `cp315t`, with each lane's resolved lock, platform (OS, CPU, libc, and accelerator, if any), artifacts, and installer versions recorded. Each platform lane in [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes) qualifies separately.
- Check actual supported wheel tags and binary-only installation, including `abi3t` and dual `abi3.abi3t` wheels. A cross-version resolver probe or `py3-none-any` wheel is not runtime/thread-safety evidence.
- Verify `Py_GIL_DISABLED` and actual GIL state before and after imports, lazy initialization, and workloads; detect automatic GIL re-enablement instead of masking it with a forced-off flag.
- Run the existing backend checks and both ASGI server entrypoints on each candidate. Exercise concurrency, cancellation, GC/shutdown, storage collisions, and context propagation; database and real object-storage/browser integrations need separate environments and evidence.
- Compare Limited API versus version-specific artifacts on the same runtime separately from regular-versus-free-threaded runtime performance. Apply each budget to the comparison defined for it in [Performance admission](DEPENDENCY-STRATEGY.md#performance-admission), with the strategy's workloads, thread counts, repetitions, median decision statistic, and absolute limits; the regular-versus-free-threaded runtime comparison has its own pass rule instead of the relative ABI budgets. A comparison with the CPython 3.12 lane is a separate interpreter-migration measurement, not an admission baseline.
- Preserve the known-good GIL-enabled fallback and record failed or unavailable lanes. No synthetic tag check, wheel inventory, or benchmark of only health endpoints qualifies the full stack.

## Security-negative testing

Every feature that introduces authentication, authorization, scope checks, approvals, or agent-originated actions must ship with tests that prove denial, not only success. Minimum cases, mapped to the requirement IDs in [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md):

- Unauthenticated and unauthorized requests are rejected with the intended status and no side effect (`SEC-AUTH-001`, `SEC-AUTH-003`, `SEC-FAIL-001`).
- A user or AI participant in one scope cannot read, retrieve, or act on another scope's Files, Knowledge, Tasks, or Artifacts (`SEC-SCOPE-001` to `SEC-SCOPE-003`).
- Personal material is invisible to a team context until an explicit share, and the share is recorded (`SEC-SHARE-001`, `SEC-SHARE-002`).
- Upload does not index; indexing is explicit and scoped (`SEC-RAG-001`, `SEC-RAG-003`).
- Instructions embedded in retrieved or external content do not change what the system does (`SEC-INJ-001`, `SEC-INJ-002`).
- A member's direct request to a shared AI in a team context runs with that member as the actor and is not refused as untrusted content; the same request from someone without access to the context is denied; a request to read any member's personal context, the requester's own included, returns nothing; a message from an AI participant is not treated as a direct request; the reply of the member the AI asked, accepting its proposal ("yes"), is a direct request for exactly the operation and targets the AI showed, nothing more, and still passes server authorization, classification, and approval; another member's reply, or a reply that declines or asks back, requests nothing (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-SCOPE-002`, `SEC-SCOPE-003`).
- An operation, target, recipient, or scope that appears only in quoted, attached, retrieved, or web content inside a direct request, and that the request neither names nor designates, causes no state change; values the request designates ("invite the addresses in this file") are used as data and still pass authorization, classification, and approval; no message overrides authorization, classification, or approval requirements (`SEC-INJ-001`, `SEC-INJ-002`).
- A run under a valid standing authorization, such as a scheduled Task, executes the operations its stored definition covers, within its targets and scope, with no new message from a person; a run with no authorization, or with one that was revoked or is no longer valid, executes nothing; an operation, target, recipient, or scope outside the definition is refused; a forged trigger, or an event payload that claims a role, a permission, or an approval, widens nothing; retrieved, attached, or tool-output text read during the run changes neither the definition nor what it targets; permissions or scope narrowed before a run narrow or stop it; a consequential operation the run reaches waits for its own approval, and setting up or scheduling the automation is not accepted as one (`SEC-INJ-001`, `SEC-INJ-002`, `SEC-AUTH-001`, `SEC-SCOPE-003`, `SEC-APPROVE-001`).
- Agent-originated requests receive the same authorization result as the Human UI for the same actor and operation, and the recorded origin changes nothing (`SEC-AGENT-001`).
- Requests under one browser session from the Human UI and from a WebMCP tool get the same authorization result for the same actor, scope, and operation; a forged `origin` is recorded at most as client-reported and grants nothing; an AI participant name or `ai_participant_ref` sent by a client or an external browser agent is not accepted as an AI participant and grants no participant's permissions, while an AI participant the server runs keeps its own effective permissions; a call from the browser session with no verifiable delegation runs as the user alone, with no AI participant; the audit record marks a client-reported origin as such (`SEC-AGENT-001`, `SEC-AGENT-003`).
- A presigned URL is only issued to an authorized requester and expires (`SEC-FILE-005`).

## Personal and team boundary testing

Extend the Topic/Thread cases above with explicit negative tests: a shared AI participant asked about a member's personal conversation returns nothing from it; cross-Topic retrieval returns nothing; navigation between personal and team surfaces does not carry drafts, files, or retrieval context across.

## Approval and retry testing

When approval, background execution, or any state-changing operation that takes an `operation_id` exists, verify:

- A high-impact (consequential) operation performs no side effect before a bound approval: a call carried by a Task stops in `waiting_for_approval` until the decision, and a person's own Human UI action without a Task executes only after that person's confirmation is recorded for its `operation_id`; neither the agent that requested or runs the operation nor an agent acting in the person's browser session completes the approval step by itself, and a UI click, an agent's reply, or an approval flag sent by a client alone is not accepted as a verified approval; denial and timeout leave no side effect (`SEC-APPROVE-001`, `SEC-APPROVE-003`).
- An ordinary mutation, such as a settings change or a draft, completes with server-side authorization and no approval step; an annotation, prompt, or origin neither adds nor removes an approval requirement (`SEC-APPROVE-001`, `SEC-AGENT-004`).
- An approval is recorded with the `operation_id`, actor, target, parameters, and scope it covers and cannot be reused for a different operation, target, parameters, scope, or actor, or for another `operation_id` except after a recorded failure under an explicit re-execution policy; a repeat of an approved request with the same `operation_id` returns the recorded outcome without executing again; executing an approved operation again after a recorded failure takes a new `operation_id` and a new approval unless a re-execution policy allows it (`SEC-APPROVE-002`, `SEC-IDEM-003`).
- Requests with the same `operation_id` and binding, sent one after the other and concurrently, execute once and return the recorded outcome, each as its own attempt record; the same id with a different input is rejected with `idempotency_conflict` and leaves the bound operation unchanged; the same id from another actor neither returns nor reveals the first actor's operation; after a confirmed `not_executed`, a new `operation_id` executes; a new `operation_id` with identical input executes again; a request that claims a new id and is rejected records `not_executed`, which a late duplicate receives (`SEC-IDEM-001`, `SEC-IDEM-002`).
- When the response is lost after the server applied a change, the caller reports `outcome_unknown` and recovers the recorded outcome by status lookup or by retrying with the same `operation_id`, without a second side effect; a lookup that finds no record is not treated as not executed; a retry under an id already sent that is stopped before sending, or refused for that request alone, is reported as `outcome_unknown`, never `not_executed`; a failure recorded with effects remaining returns `outcome_unknown` with `partially_applied` on every repeat and lookup and is not retried (`SEC-IDEM-005`).
- After a reload, a caller with no local record of the id recovers the `operation_id` through a status lookup; a retry under it with the same actor, AI participant, context, operation, and input returns the recorded outcome with no second side effect; the same id with another input is rejected with `idempotency_conflict`; another actor's id, an id in a context the actor can no longer access, and an id the server has no record of each reveal nothing, execute nothing, and bind nothing; when several entries could be the lost operation, the caller picks none and starts nothing new; while the operation stays unresolved, no new `operation_id` is issued and nothing executes until the person requests it anew, the message to the person says that the earlier operation may have taken effect or may still take effect, and a consequential operation requested anew does not execute without its state check and its own approval (`SEC-IDEM-001`, `SEC-IDEM-003`, `SEC-IDEM-005`, `SEC-SCOPE-003`).
- Storage collision and retry behavior stays correct under concurrency (existing `test_storage.py` cases are the baseline).

## Internationalization testing

Nothing is implemented; when [I18N.md](I18N.md) is implemented, verify:

- Locale resolution order (account setting, cookie, environment language, English fallback) with each source present and absent, and with invalid values rejected.
- Server and client render the same locale (no hydration mismatch) and `<html lang>` matches.
- Every Korean message has an English source and matching placeholders and the plural branches its locale requires; missing Korean falls back to English by the documented policy.
- Dates, times, and numbers use the configured time zone and locale.
- IDs, enum values, permission names, error codes, and WebMCP tool names are not translated.
- Playwright renders `/` and `/console` in both locales with `<html lang>` matching the resolved locale, no console errors, and no overflow from longer or shorter labels, in light and dark color schemes.

## WebMCP testing

Nothing is implemented, and the API is experimental; when a tool is proposed under [WEBMCP.md](WEBMCP.md), verify in a browser build that actually exposes `document.modelContext`:

- Feature detection: the Human UI works identically when the API is absent.
- Registration failure (duplicate `name`, invalid `name`, empty `description`, non-serializable `inputSchema`, `Permissions-Policy: tools=()`) is handled per tool without affecting other tools or the UI.
- Context switch and logout unregister tools, and in-flight executions are validated against the current context before any server call and again before returning a result: a read that completes after the switch returns no data from the old context, and any server answer that arrives after the switch returns only a safe summary, and the status tool returns its details only when called from the owning context.
- The server rejects a tool-originated operation that the same user could not perform from the UI, and records the origin.
- Aborting `executeTool()` while `execute` has not yet sent the request (it is awaiting earlier work) makes it send nothing; any abort rejects `executeTool()` with the abort reason; aborting after the request left leaves the change standing, and the status tool reports the actual outcome with one side effect.
- A response dropped after the server applied a change returns `outcome_unknown`; a retry with the returned `operation_id` returns the recorded outcome with one side effect; the same id sent concurrently executes once.
- After a reload, an `operation_id` recovered through the status tool and unknown to the new page is resolved by the server: with the same input it returns the recorded outcome with one side effect in total, and an id the server has no record of binds nothing.
- Pages that expose no tools send `Permissions-Policy: tools=()`; the audit record carries the user as actor, origin `webmcp` marked client-reported, no AI participant, and the `operation_id`. The full list is in [WEBMCP.md](WEBMCP.md) "Verification requirements".

## Change-scope checks

For documentation or scaffold work, also verify changed links and paths, stale naming, `git diff --check`, absence of secrets/generated artifacts, and that planned features remain clearly distinguished from implemented behavior. Do not introduce domain models, custom migrations, infrastructure, or unrelated frameworks as incidental test work. The review procedure that consumes these checks is [CODE-REVIEW.md](CODE-REVIEW.md); security-sensitive changes also go through [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
