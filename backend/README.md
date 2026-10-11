# Claus Backend

Claus uses Django 6.1 with one Django project, `config`, and exactly two product apps: `core` and `agent`. CPython 3.12.15 is pinned in `.python-version`, and the only checked lane is that interpreter on Linux x86-64. Django supports Python 3.12–3.14; other Claus interpreter/ABI/platform combinations require their own verification. The lock's static, unexecuted status on other operating systems and CPUs, including Arm machines with and without CUDA, is in [Platform and accelerator lanes](../docs/DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes).

- `core` is the persistent product control plane and exposes Django REST Framework APIs under `/core/*`.
- `agent` shares Django settings, ORM, migrations, auth, and Admin, while its FastAPI surface under `/agent/*` is reserved for AI/RAG/agent/runtime execution.
- Django Admin remains separate at `/admin/*`.
- `config.asgi.application` initializes Django first, then mounts Agent FastAPI at `/agent` before mounting Django at `/`.

The backend dependency baseline is Django 6.1, Django REST Framework 3.18, django-cors-headers 4.9, Daphne 4.2, FastAPI 0.143, Starlette 1.7, Uvicorn 0.54, Python Playwright, psycopg (PostgreSQL driver), and boto3 (S3-compatible storage client). FastAPI also brings in `opentelemetry-api` (1.45.1 in the lock) and imports it whenever FastAPI is imported; its native telemetry stays inactive because no OpenTelemetry SDK is installed. An `OTEL_PROPAGATORS` value naming a propagator that is not installed makes that import, and therefore `config.asgi`, fail; see [SECURITY-ARCHITECTURE.md](../docs/SECURITY-ARCHITECTURE.md#asgi-composition-backendconfigasgipy) and [DEPENDENCY-STRATEGY.md](../docs/DEPENDENCY-STRATEGY.md#applied-baseline-and-verification) before enabling observability. `requirements.txt` preserves direct dependency ranges as resolver input; [locks/cp312-linux-x86_64.txt](locks/cp312-linux-x86_64.txt) records the 50 exact transitive versions and hashes and is what you install. `requirements-test.txt` holds the test-only inputs (pytest and pytest-django), and [locks/cp312-linux-x86_64-test.txt](locks/cp312-linux-x86_64-test.txt) is the integrated test lock: the same 50 pins plus 5 test packages. Use [locks/README.md](locks/README.md) to regenerate either lock or qualify another platform.

Implemented, with tests in the repository: the health endpoints, the ASGI composition, `DATABASE_URL` parsing with SQLite fallback (`config/database.py`), and the S3-compatible storage backend (`core/storage/s3.py`). No product domain models, authentication or authorization for product features, RAG pipeline, LLM provider, orchestration framework, background execution, or browser-session behavior is implemented yet. The `/agent/*` FastAPI surface runs outside Django middleware and currently exposes only the health route plus `/agent/docs`, `/agent/openapi.json`, and FastAPI's default `/agent/redoc`; see [docs/SECURITY-ARCHITECTURE.md](../docs/SECURITY-ARCHITECTURE.md) for the security facts of the current configuration.

## Configuration

Environment variables are listed in `.env.example` at the repository root (do not commit a `.env` file):

- `DJANGO_SECRET_KEY`: falls back to a development placeholder when unset or empty. `DJANGO_DEBUG`: defaults to `true`. Both must be set explicitly outside local development.
- `DATABASE_URL`: only `postgres://` or `postgresql://` URLs are accepted; the hostname and database name are required, query parameters become connection `OPTIONS`, and any invalid URL raises `ImproperlyConfigured` at startup instead of falling back. When unset or blank, Django uses SQLite at `backend/db.sqlite3`. Django 6.1 requires PostgreSQL 15 or newer for `DATABASE_URL` and SQLite 3.37.0 or newer for the fallback database, and checks the version when it connects.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_REGION`, `OBJECT_STORAGE_BUCKET`: required by the default file storage backend on first use. The endpoint must be an `http(s)://host` base URL without credentials, path, query, or fragment. Saves never overwrite an existing object, and download URLs are presigned and expire after one hour.

## Local development

```bash
# From the repository root, using uv 0.12.24, in a POSIX shell (Linux, macOS, or WSL2):
uv venv --managed-python --python 3.12.15
source .venv/bin/activate
python -c "import sqlite3; print(sqlite3.sqlite_version)"  # must print 3.37.0 or newer
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000
```

The same tests run under pytest after syncing the integrated test lock; `backend/pytest.ini` configures pytest-django, and the commands, including the lock and test-collection checks that CI runs, are in [TESTING.md](../docs/TESTING.md#current-scaffold-checks):

```bash
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64-test.txt
cd backend
python -m pytest
```

`--managed-python` makes uv use its python-build-standalone CPython rather than a system interpreter, whose SQLite can be older than Django 6.1's 3.37.0 floor. In Windows PowerShell, `.venv\Scripts\Activate.ps1` replaces `source .venv/bin/activate`, but native Windows cannot produce a working environment from this Linux-resolved lock today (on Windows x64 it installs without `tzdata`; on Windows on Arm the install fails because `autobahn`, `cryptography`, and `psycopg-binary` have no `win_arm64` wheels), so use WSL2 (Ubuntu). Linux arm64, including NVIDIA DGX Spark, and macOS 15 or later on Apple Silicon are statically resolvable but have not been run; see [Platform and accelerator lanes](../docs/DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes) for every lane.

Daphne is first in `INSTALLED_APPS`, so Django's `runserver` serves `ASGI_APPLICATION` and therefore exposes both Django and FastAPI. Direct ASGI execution has the same surface:

```bash
cd backend
uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

Endpoints: `GET /core/health/`, `GET /agent/health/`, `GET /agent/docs`, `GET /agent/openapi.json`, `GET /agent/redoc` (FastAPI's default, not set in code), and `/admin/`.

The agent app sets `redirect_slashes=False` (`agent/fastapi/app.py`), so an `/agent/*` path that differs from a route only by its trailing slash, such as `/agent/health` or `/agent/docs/`, returns 404 instead of a redirect. FastAPI's slash redirect would be an absolute URL built from the upstream `Host` header, which behind the Next.js rewrite points the browser at the backend origin. Django's `APPEND_SLASH` under `/core/*` is unchanged and uses a relative redirect (`/core/health` answers 301 to `/core/health/`).

## Optional Django Admin setup

The Next.js product Console at `/console/*` is separate from Django's internal ORM-backed Admin at `/admin/*`. For a new checkout, initialize Django's built-in tables and create a local administrator before signing in:

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3` is a local development artifact and must not be committed. No custom domain migrations are included.

Python Playwright is the async Browser Computer Use runtime foundation in `agent/runtime/browser`; installing its package does not install Chromium. Run `playwright install chromium` separately before browser-runtime experiments; the current factory does not launch a browser. Its binaries are independent of frontend `npx playwright install chromium`. The database is selected by `DATABASE_URL` as described under Configuration (SQLite only when it is unset or blank), and no custom migrations are included.
