"""Anvil FastAPI application."""

from __future__ import annotations

import logging
import os
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from models.db import create_db_and_tables, engine, protect_sqlite_database_files
from routes.health import router as health_router
from routes.auth import router as auth_router, platform_audit_router, users_router
from routes.access import router as access_router
from routes.exercises import router as exercises_router
from routes.plc_operations import router as plc_operations_router
from routes.scenarios import router as scenarios_router
from routes.templates import router as templates_router
from routes.components import router as components_router
from services.docker_manager import DockerManager, DockerManagerError
from services.event_stream import scenario_event_hub
from services.log_stream import scenario_log_hub
from services.reconciliation import deployment_recovery_scheduler, reconcile_runtime
from services.exercise_runtime import exercise_scheduler, interrupt_active_exercises
from services.scada_historian import scada_historian_scheduler
from services.audit import audit_retention_scheduler, configured_audit_retention_days
from services.protocol_trace_collector import protocol_trace_scheduler
from middleware.auth import PlatformAuthMiddleware


LOGGER = logging.getLogger("anvil.backend")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class FrontendFiles(StaticFiles):
    """Always refresh the module graph together after a preview update."""

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app(*, initialize_database: bool = True, auth_enabled: bool | None = None, auth_engine=None) -> FastAPI:
    if auth_enabled is None:
        auth_enabled = initialize_database
    auth_engine = auth_engine or engine
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        scheduler_stop = asyncio.Event()
        scheduler_task: asyncio.Task | None = None
        historian_task: asyncio.Task | None = None
        protocol_trace_task: asyncio.Task | None = None
        audit_retention_task: asyncio.Task | None = None
        deployment_recovery_task: asyncio.Task | None = None
        if initialize_database:
            protect_sqlite_database_files(engine)
            create_db_and_tables()
            try:
                with Session(engine) as session:
                    result = reconcile_runtime(
                        session,
                        DockerManager(),
                        cleanup_orphans=_enabled("ANVIL_CLEANUP_ORPHANS"),
                    )
                if result["errors"]:
                    LOGGER.warning("Runtime reconciliation completed with errors: %s", result)
                else:
                    LOGGER.info("Runtime reconciliation completed: %s", result)
            except DockerManagerError as error:
                LOGGER.warning("Runtime reconciliation skipped: %s", error)
            with Session(engine) as session:
                interrupted = interrupt_active_exercises(session)
            if interrupted:
                LOGGER.warning("Marked %s running exercises as interrupted after restart", interrupted)
            scheduler_task = asyncio.create_task(exercise_scheduler(scheduler_stop, engine))
            historian_task = asyncio.create_task(scada_historian_scheduler(scheduler_stop, engine))
            protocol_trace_task = asyncio.create_task(protocol_trace_scheduler(scheduler_stop, engine))
            deployment_recovery_task = asyncio.create_task(
                deployment_recovery_scheduler(scheduler_stop, engine)
            )
            retention_days = configured_audit_retention_days()
            if retention_days:
                audit_retention_task = asyncio.create_task(
                    audit_retention_scheduler(scheduler_stop, engine, retention_days)
                )
        try:
            yield
        finally:
            if (scheduler_task is not None or historian_task is not None or protocol_trace_task is not None or audit_retention_task is not None
                    or deployment_recovery_task is not None):
                scheduler_stop.set()
                tasks = [task for task in (scheduler_task, historian_task, protocol_trace_task, audit_retention_task,
                                           deployment_recovery_task) if task is not None]
                await asyncio.gather(*tasks)
            await scenario_event_hub.shutdown()
            await scenario_log_hub.shutdown()

    application = FastAPI(title="Anvil API", version="0.6.0", lifespan=lifespan)
    application.state.auth_enabled = auth_enabled
    application.state.auth_engine = auth_engine
    application.add_middleware(PlatformAuthMiddleware, enabled=auth_enabled, database_engine=auth_engine)

    application.include_router(auth_router)
    application.include_router(users_router)
    application.include_router(platform_audit_router)
    application.include_router(access_router)
    application.include_router(health_router)
    application.include_router(exercises_router)
    application.include_router(scenarios_router)
    application.include_router(templates_router)
    application.include_router(components_router)
    application.include_router(plc_operations_router)
    application.mount(
        "/", FrontendFiles(directory=STATIC_DIR, html=True), name="frontend"
    )
    return application


app = create_app()
