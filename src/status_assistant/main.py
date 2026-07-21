"""FastAPI application factory.

Wires the pieces together: create tables on startup, mount the JSON API and the web UI, and
serve static assets. Kept thin — all behavior lives in the modules it composes.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from status_assistant.api.routes import router as api_router
from status_assistant.db import create_db_and_tables
from status_assistant.web.views import router as web_router

_STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure the database schema exists before serving requests."""
    create_db_and_tables()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Engineering Status Assistant", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app


app = create_app()
