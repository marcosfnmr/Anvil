"""Anvil FastAPI application."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from models.db import create_db_and_tables, engine
from routes.health import router as health_router
from routes.scenarios import router as scenarios_router
from services.docker_manager import DockerManager, DockerManagerError
from services.event_stream import scenario_event_hub
from services.reconciliation import reconcile_runtime


LOGGER = logging.getLogger("anvil.backend")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app(*, initialize_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if initialize_database:
            create_db_and_tables()
            try:
                with Session(engine) as session:
                    result = reconcile_runtime(
                        session,
                        DockerManager(),
                        cleanup_orphans=_enabled("ANVIL_CLEANUP_ORPHANS"),
                    )
                LOGGER.info("Runtime reconciliation completed: %s", result)
            except DockerManagerError as error:
                LOGGER.warning("Runtime reconciliation skipped: %s", error)
        try:
            yield
        finally:
            await scenario_event_hub.shutdown()

    application = FastAPI(title="Anvil API", version="0.5.0", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(scenarios_router)
    application.mount(
        "/",
        StaticFiles(directory=STATIC_DIR, html=True),
        name="frontend",
    )
    return application


app = create_app()
