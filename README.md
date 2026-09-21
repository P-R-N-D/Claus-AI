<h1 align="center">Claus</h1>

<p align="center">
  <strong>사람과 AI가 같은 맥락에서 대화하고, 작업하고, 결과를 만드는 AI 협업 프로젝트</strong><br>
  <strong>An AI collaboration project where people and AI share context, work, and produce results together</strong>
</p>

<p align="center">
  개인 AI 대화와 팀 Topic/Thread 협업을 바탕으로 파일·지식·백그라운드 작업과 Browser/Terminal/Workspace 실행을 연결하는 방향입니다.<br>
  See the Korean and English documentation for details.
</p>

<p align="center">
  <a href="docs/README.ko.md"><strong>한국어</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/README.en.md"><strong>English</strong></a>
</p>

---

## Project direction

Claus explores a collaboration model in which conversation is shared context rather than the only workspace.

- **Personal AI**: private AI conversations and personal topics.
- **Team topics and threads**: SNS-style posts and threaded discussion for people and shared AI participants.
- **Files and knowledge**: shared files remain separate from RAG indexing scope; upload does not automatically promote a file to team or organization knowledge.
- **Background work**: AI tasks run independently from the conversation that requested them.
- **Execution tools**: browser automation, terminal tasks, and isolated workspaces can be attached to a task when needed.
- **Shared results**: documents, images, video, charts, tables, notebook/HTML results, and live browser sessions can be presented in a shared viewing surface.

Full desktop/OS streaming and control are not part of the current project direction. Browser-based Computer Use is the primary interactive execution target.

## Current runnable scaffold

- Frontend: Next.js user UI at `/`, a separate product Console at `/console/*`, React, TypeScript, Tailwind CSS, axios, SweetAlert2, and Node Playwright tests.
- Backend: Django 6 on Python 3.12–3.14, using one Django project (`config`) with two Django apps (`core`, `agent`).
- URLs: core DRF at `/core/*`, Agent FastAPI at `/agent/*`, and Django Admin at `/admin/*`.
- Composition: `config.asgi.application` mounts FastAPI and Django into one ASGI application, served identically by Daphne-backed `manage.py runserver` or Uvicorn.
- Browser foundation: backend Python Playwright provides the async Agent Browser Computer Use package boundary; it is separate from frontend Playwright tests.

The scaffold implements health endpoints and package boundaries only. It does not implement domain models, RAG, LLM orchestration, browser sessions, Terminal, or Workspace behavior.

## Local development

```bash
python -m pip install -r backend/requirements.txt
playwright install chromium
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000

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

---

## Language

| Language | README |
|---|---|
| 한국어 | [docs/README.ko.md](docs/README.ko.md) |
| English | [docs/README.en.md](docs/README.en.md) |

---

## License

Source code is licensed under the PolyForm Noncommercial License 1.0.0. See the repository license file for details.
