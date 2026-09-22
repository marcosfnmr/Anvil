"""Anvil FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from models.db import create_db_and_tables
from routes.health import router as health_router
from routes.scenarios import router as scenarios_router


def create_app(*, initialize_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if initialize_database:
            create_db_and_tables()
        yield

    application = FastAPI(title="Anvil API", version="0.2.0", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(scenarios_router)

    @application.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"name": "Anvil", "phase": "2", "docs": "/docs"}

    return application


app = create_app()
