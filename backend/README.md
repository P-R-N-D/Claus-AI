# Claus Backend

Claus uses Django 6 on Python 3.12 or newer (Django 6.0 officially supports 3.12 through 3.14) with one Django project, `config`, and exactly two product apps: `core` and `agent`.

- `core` is the persistent product control plane and exposes Django REST Framework APIs under `/core/*`.
- `agent` shares Django settings, ORM, migrations, auth, and Admin, while its FastAPI surface under `/agent/*` is reserved for AI/RAG/agent/runtime execution.
- Django Admin remains separate at `/admin/*`.
- `config.asgi.application` initializes Django first, then mounts Agent FastAPI at `/agent` before mounting Django at `/`.

The backend dependency baseline is Django 6, Django REST Framework 3.17, django-cors-headers 4.9, Daphne 4.2, FastAPI, Uvicorn, Python Playwright, psycopg (PostgreSQL driver), and boto3 (S3-compatible storage client). See `requirements.txt` for the pinned ranges.

Implemented, with tests in the repository: the health endpoints, the ASGI composition, `DATABASE_URL` parsing with SQLite fallback (`config/database.py`), and the S3-compatible storage backend (`core/storage/s3.py`). No product domain models, authentication or authorization for product features, RAG pipeline, LLM provider, orchestration framework, background execution, or browser-session behavior is implemented yet. The `/agent/*` FastAPI surface runs outside Django middleware and currently exposes only the health route plus `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc`; see [docs/SECURITY-ARCHITECTURE.md](../docs/SECURITY-ARCHITECTURE.md) for the security facts of the current configuration.

## Configuration

Environment variables are listed in `.env.example` at the repository root (do not commit a `.env` file):

- `DJANGO_SECRET_KEY`: falls back to a development placeholder when unset or empty. `DJANGO_DEBUG`: defaults to `true`. Both must be set explicitly outside local development.
- `DATABASE_URL`: only `postgres://` or `postgresql://` URLs are accepted; the hostname and database name are required, query parameters become connection `OPTIONS`, and any invalid URL raises `ImproperlyConfigured` at startup instead of falling back. When unset or blank, Django uses SQLite at `backend/db.sqlite3`.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_REGION`, `OBJECT_STORAGE_BUCKET`: required by the default file storage backend on first use. The endpoint must be an `http(s)://host` base URL without credentials, path, query, or fragment. Saves never overwrite an existing object, and download URLs are presigned and expire after one hour.

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

Endpoints: `GET /core/health/`, `GET /agent/health/`, `GET /agent/docs`, `GET /agent/openapi.json`, `GET /agent/redoc` (FastAPI's default, not set in code), and `/admin/`.

## Optional Django Admin setup

The Next.js product Console at `/console/*` is separate from Django's internal ORM-backed Admin at `/admin/*`. For a new checkout, initialize Django's built-in tables and create a local administrator before signing in:

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3` is a local development artifact and must not be committed. No custom domain migrations are included.

Python Playwright is the async Browser Computer Use runtime foundation in `agent/runtime/browser`; installing its package does not install Chromium. Only Chromium is required at this stage. The database is selected by `DATABASE_URL` as described under Configuration (SQLite only when it is unset or blank), and no custom migrations are included.
