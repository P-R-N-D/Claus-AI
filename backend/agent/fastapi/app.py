from fastapi import FastAPI

from .routes.health import router as health_router

application = FastAPI(
    title="Claus Agent",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
application.include_router(health_router)
