from fastapi import FastAPI

from .routes.health import router as health_router

application = FastAPI(
    title="Claus Agent",
    docs_url="/docs",
    openapi_url="/openapi.json",
    # Slash redirects would be absolute URLs built from the upstream Host header,
    # which leave the frontend origin behind the Next.js rewrite. Mismatches return 404.
    redirect_slashes=False,
)
application.include_router(health_router)
