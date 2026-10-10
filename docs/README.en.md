# Claus

Claus is an AI collaboration project where people and AI work from personal and team context, share files and knowledge, and use browser, terminal, and workspace tools when a task needs execution.

## Project direction

Claus is designed around topics and threads rather than treating a single group-chat timeline as the entire workspace.

- **Personal AI**: private conversations, personal topics, and user-scoped work.
- **Team topics and threads**: SNS-style posts and threaded discussion where people and shared AI participants collaborate in the same context.
- **Shared AI**: uses only the topic/thread, files, and knowledge that the current permissions allow, and connects public work and results back to that context.
- **Files and knowledge**: file sharing and RAG indexing are separate actions. Uploading a file does not automatically promote it to team or organization knowledge.
- **Background tasks**: long-running AI work executes independently from the conversation that requested it.
- **Execution tools**: browser, terminal, and workspace runtimes are attached only when a task needs them.
- **Shared result surface**: documents, images, video, charts, tables, notebook/HTML output, and live browser sessions can be presented for collaborative viewing.

Full desktop/OS streaming and control are outside the current project scope. Browser-based Computer Use is the primary interactive execution target.

The Next.js frontend is intended to grow into the user-facing surface for personal AI conversations, team topics/threads, files and artifacts, AI task status, shared results, and browser work. Django/DRF remains the primary backend/control plane for users, permissions, collaboration context, files, knowledge, and task state, while Django Admin remains reserved for internal operator/admin workflows.

Long-running AI execution and browser/terminal/workspace runtimes should be separated from normal web request handling.

## Current runnable scaffold

The repository currently contains this initial scaffold:

- Frontend: Next.js user UI at `/`, a separate product Console under `/console` (reserved as `/console/*`; only the `/console` index page exists today), React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright tests.
- Backend: Django 6.1 using one Django project (`config`) with two Django apps (`core`, `agent`). The only checked lane is CPython 3.12.15 on Linux x86-64; other interpreters, operating systems, and CPUs need separate verification, and the static evidence for them is in [Platform and accelerator lanes](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes).
- URLs: core DRF at `/core/*`, Agent FastAPI at `/agent/*`, and Django Admin at `/admin/*`.
- Composition: `config.asgi.application` mounts FastAPI and Django into one ASGI application, served identically by Daphne-backed `manage.py runserver` or Uvicorn.
- Local integration: the Next.js dev server rewrites `/core/*` and `/agent/*` to the backend at `http://127.0.0.1:8000`.
- Health endpoints: `GET /core/health/` and `GET /agent/health/`.
- Database configuration: `DATABASE_URL` (PostgreSQL URLs only; an invalid value fails at startup) configures the Django database, and SQLite at `backend/db.sqlite3` is the fallback when it is unset or blank.
- File storage: the default Django storage is a private S3-compatible object storage backend (`core/storage/s3.py`) that requires the object storage variables listed in `.env.example`.
- Browser foundation: backend Python Playwright provides the async Agent Browser Computer Use package boundary; it is separate from frontend Playwright tests.

The scaffold implements the health endpoints, the ASGI composition, the `DATABASE_URL`-based database configuration, and the S3-compatible storage backend, each with tests in the repository. It does not implement collaboration domain models, authentication or authorization for product features, RAG, LLM orchestration, background execution, browser sessions, Terminal, Workspace, realtime transport, WebMCP, or frontend i18n.

This initial scaffold does not include Docker, Nginx, K8s, Helm, production deployment manifests, custom domain models, custom migrations, or SQL schema work.

## Technical documents

Technical documents for AI coding agents and contributors are written in English, and [docs/CONTEXT.md](CONTEXT.md) is the entry point. It says which document to read for each kind of task.

- [ARCHITECTURE.md](ARCHITECTURE.md): overall structure and the implemented versus planned breakdown.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): conceptual state shapes (not a database schema).
- [TESTING.md](TESTING.md): the tests that exist today and the verification still required.
- [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md): the applied dependency update and its compatibility/security exceptions, plus Python 3.15 Limited API/`abi3t` wheel qualification, performance admission, and maintenance under the existing free-threading direction. Python 3.15 support remains unqualified.
- [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md): trust boundaries and the `SEC-*` security requirements.
- [CODE-REVIEW.md](CODE-REVIEW.md), [SECURITY-REVIEW.md](SECURITY-REVIEW.md): independent code review and security review procedures for code changes.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [WEBMCP.md](WEBMCP.md), [I18N.md](I18N.md): the not-yet-implemented interaction architecture, the WebMCP (experimental technology) contract, and the frontend i18n direction.

## Local development

Only Linux x86-64 has been run. The other rows are static evidence from [Platform and accelerator lanes](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes), not support claims.

| Machine | How to run the steps below |
|---|---|
| Linux x86-64 | Use the backend lock as shown (the checked lane). |
| Linux arm64 (glibc), including NVIDIA DGX Spark | For local development only, use the backend lock as shown. It is statically resolvable on arm64 but has not been run there; qualifying this platform needs its own separately named lock ([backend locks](../backend/locks/README.md)). |
| Windows x64, and Windows on Arm such as NVIDIA RTX Spark or Qualcomm Snapdragon PCs | Run the steps in WSL2 (Ubuntu). Native Windows cannot produce a working environment from this Linux-resolved lock: on Windows x64 it installs without `tzdata`, which Django and psycopg require on Windows, and on Windows on Arm the install fails because `autobahn`, `cryptography`, and `psycopg-binary` have no `win_arm64` wheels. On native Windows on Arm, Playwright also runs x64 browsers under emulation. |
| macOS 15 or later on Apple Silicon | For local development only, use the backend lock as shown. It is statically resolvable but has not been run; qualifying this platform needs its own separately named lock. |
| macOS 14 or older, Intel Macs, Alpine (musl) | Not binary-installable with this lock. |

CUDA is not required. The current dependency graph contains no CUDA or other accelerator-specific package, so the same steps apply with or without an NVIDIA GPU, including Arm machines without CUDA such as Snapdragon PCs.

```bash
# Backend: Linux, macOS, or WSL2 (POSIX shell), using uv 0.12.24, from the repository root
uv venv --managed-python --python 3.12.15
source .venv/bin/activate
python -c "import sqlite3; print(sqlite3.sqlite_version)"  # must print 3.37.0 or newer
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000

# Frontend: another terminal, starting from the repository root
source .venv/bin/activate
nvm install
nvm use
npm install --global npm@11.21.0
cd frontend
npm ci
npx next typegen
npx playwright install chromium
npm run lint
npm run test:visual
npm run dev -- --port 3000
```

`--managed-python` makes uv use its own CPython build rather than a system interpreter, whose SQLite can be older than the 3.37.0 that Django 6.1 requires. In Windows PowerShell the environment is activated with `.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`, but native Windows cannot produce a working environment from the lock today, so use WSL2 as the table says.

The `npm install --global npm@11.21.0` step is required: Node 24.21.0 bundles npm 11.19.0, and the `engines` pin in `package.json` only warns (`EBADENGINE`) instead of stopping the install. `npx next typegen` writes the untracked `frontend/next-env.d.ts`, which is recommended on a fresh clone before `tsc` or editor type checking; `npm run dev` and `npm run build` write it too.

`npm run test:visual` starts the backend and `next dev` itself, so no `npm run build` is needed first; outside CI it reuses servers already listening on ports 8000 and 3000. `PLAYWRIGHT_NEXT_SERVER=production npm run test:visual` rebuilds the app, runs `next start` instead, and never reuses running servers. For evidence runs, add `CI=1`, which stops Playwright from reusing running servers, and record the server mode; see [TESTING.md](TESTING.md#current-scaffold-checks).

Open `http://127.0.0.1:3000` for the user surface and `http://127.0.0.1:3000/console` for the product Console. Django Admin remains at `http://127.0.0.1:8000/admin/`. `npm run dev` listens on 127.0.0.1 only; use `npm run dev -- --hostname 0.0.0.0` only deliberately, for example to test from a phone on the local network.

The frontend uses Node 24.21.0, npm 11.21.0, Next 16, and Tailwind 4. See [backend locks](../backend/locks/README.md) for other-platform qualification and [frontend setup](../frontend/README.md) for browser constraints. Python Playwright has a separate browser installation and is not required to render the current health scaffold. Remaining EOL/tooling advisory exceptions are explicit in the dependency strategy.

### Optional Django Admin setup

The product Console at `/console/*` and Django's ORM-backed internal Admin at `/admin/*` are separate UIs. Initialize the built-in Django tables and create a local administrator before signing in to Django Admin:

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3` is a local development artifact and must not be committed. The scaffold includes no custom domain migrations.
