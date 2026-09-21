# Claus

Claus is an AI collaboration project where people and AI work from personal and team context, share files and knowledge, and use browser, terminal, and workspace tools when a task needs execution.

## Collaboration model

Claus is designed around topics and threads rather than treating a single group-chat timeline as the entire workspace.

- **Personal AI**: private conversations, personal topics, and user-scoped work.
- **Team topics and threads**: SNS-style posts and threaded discussion where people and shared AI participants collaborate in the same context.
- **Shared AI**: uses only the topic/thread, files, and knowledge that the current permissions allow, and connects public work and results back to that context.
- **Files and knowledge**: file sharing and RAG indexing are separate actions. Uploading a file does not automatically promote it to team or organization knowledge.
- **Background tasks**: long-running AI work executes independently from the conversation that requested it.
- **Execution tools**: browser, terminal, and workspace runtimes are attached only when a task needs them.
- **Shared result surface**: documents, images, video, charts, tables, notebook/HTML output, and live browser sessions can be presented for collaborative viewing.

Full desktop/OS streaming and control are outside the current project scope. Browser-based Computer Use is the primary interactive execution target.

## Current initial runnable scaffold

The repository currently contains this initial scaffold:

- Frontend: Next.js, React, TypeScript, Tailwind CSS, axios, SweetAlert2, Playwright.
- Backend: one Django project (`config`) with `core` (DRF) and `agent` (Django app + FastAPI), plus Django Admin, Django ORM, and django-cors-headers.
- Local integration: the Next.js dev server rewrites `/core/*` and `/agent/*` to the backend at `http://127.0.0.1:8000`.
- Health endpoints: `GET /core/health/` and `GET /agent/health/`.
- Django Admin: `/admin/`.

The collaboration, RAG, agent-task, browser/terminal/workspace, and shared-result concepts above describe project direction and are not all implemented by the current scaffold.

This initial scaffold does not include Docker, Nginx, K8s, Helm, production deployment manifests, custom domain models, custom migrations, or SQL schema work.

## Local development

```bash
# Backend
python -m pip install -r backend/requirements.txt
playwright install chromium
python backend/manage.py check
python backend/manage.py runserver 127.0.0.1:8000

# Frontend
cd frontend
npm install
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000` to view the current frontend scaffold and the `/core/health/` and `/agent/health/` responses. SweetAlert2 alerts are used when backend health checks fail.

## Direction

The Next.js frontend is intended to grow into the user-facing surface for personal AI conversations, team topics/threads, files and artifacts, AI task status, shared results, and browser work. Django/DRF remains the primary backend/control plane for users, permissions, collaboration context, files, knowledge, and task state, while Django Admin remains reserved for internal operator/admin workflows.

Long-running AI execution and browser/terminal/workspace runtimes should be separated from normal web request handling.
