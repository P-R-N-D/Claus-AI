# Claus Backend

Claus uses Django 6 on Python 3.12–3.14 with one Django project, `config`, and exactly two product apps: `core` and `agent`.

- `core` is the persistent product control plane and exposes Django REST Framework APIs under `/core/*`.
- `agent` shares Django settings, ORM, migrations, auth, and Admin, while its FastAPI surface under `/agent/*` is reserved for AI/RAG/agent/runtime execution.
- Django Admin remains separate at `/admin/*`.
- `config.asgi.application` initializes Django first, then mounts Agent FastAPI at `/agent` before mounting Django at `/`.

The backend dependency baseline is Django 6, Django REST Framework 3.17, django-cors-headers 4.9, Daphne 4.2, FastAPI, and Uvicorn.

No product domain models, RAG pipeline, LLM provider, orchestration framework, or browser-session behavior is implemented yet.

## Local development

```bash
python -m pip install -r backend/requirements.txt
playwright install chromium
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000
```

Daphne is first in `INSTALLED_APPS`, so Django's `runserver` serves `ASGI_APPLICATION` and therefore exposes both Django and FastAPI. Direct ASGI execution has the same surface:

```bash
cd backend
uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

Endpoints: `GET /core/health/`, `GET /agent/health/`, `GET /agent/docs`, `GET /agent/openapi.json`, and `/admin/`.

## Optional Django Admin setup

The Next.js product Console at `/console/*` is separate from Django's internal ORM-backed Admin at `/admin/*`. For a new checkout, initialize Django's built-in tables and create a local administrator before signing in:

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3` is a local development artifact and must not be committed. No custom domain migrations are included.

Python Playwright is the async Browser Computer Use runtime foundation in `agent/runtime/browser`; installing its package does not install Chromium. Only Chromium is required at this stage. Django's SQLite development setting remains unchanged, and no custom migrations are included.
