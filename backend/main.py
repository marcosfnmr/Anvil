"""Anvil FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from models.db import create_db_and_tables
from routes.health import router as health_router
from routes.scenarios import router as scenarios_router


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(*, initialize_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if initialize_database:
            create_db_and_tables()
        yield

    application = FastAPI(title="Anvil API", version="0.4.0", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(scenarios_router)
    application.mount(
        "/",
        StaticFiles(directory=STATIC_DIR, html=True),
        name="frontend",
    )
    return application


app = create_app()
