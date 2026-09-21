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

- Frontend: Next.js user UI at `/`, a separate product Console at `/console/*`, React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright tests.
- Backend: Django 6 on Python 3.12–3.14, using one Django project (`config`) with two Django apps (`core`, `agent`).
- URLs: core DRF at `/core/*`, Agent FastAPI at `/agent/*`, and Django Admin at `/admin/*`.
- Composition: `config.asgi.application` mounts FastAPI and Django into one ASGI application, served identically by Daphne-backed `manage.py runserver` or Uvicorn.
- Local integration: the Next.js dev server rewrites `/core/*` and `/agent/*` to the backend at `http://127.0.0.1:8000`.
- Health endpoints: `GET /core/health/` and `GET /agent/health/`.
- Browser foundation: backend Python Playwright provides the async Agent Browser Computer Use package boundary; it is separate from frontend Playwright tests.

The scaffold implements health endpoints and package boundaries only. It does not implement domain models, RAG, LLM orchestration, browser sessions, Terminal, or Workspace behavior.

This initial scaffold does not include Docker, Nginx, K8s, Helm, production deployment manifests, custom domain models, custom migrations, or SQL schema work.

## Local development

```bash
# Backend
python -m pip install -r backend/requirements.txt
playwright install chromium
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000

# Frontend
cd frontend
npm install
npm run lint
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000` for the user surface and `http://127.0.0.1:3000/console` for the product Console. Django Admin remains at `http://127.0.0.1:8000/admin/`.

### Optional Django Admin setup

The product Console at `/console/*` and Django's ORM-backed internal Admin at `/admin/*` are separate UIs. Initialize the built-in Django tables and create a local administrator before signing in to Django Admin:

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3` is a local development artifact and must not be committed. The scaffold includes no custom domain migrations.
