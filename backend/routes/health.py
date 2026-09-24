"""Liveness and readiness endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from dependencies import DockerDependency
from models.db import get_session
from models.scenario import ScenarioRecord


router = APIRouter(prefix="/api", tags=["health"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness(
    session: SessionDependency,
    manager: DockerDependency,
) -> dict[str, str]:
    session.exec(select(ScenarioRecord.id).limit(1)).first()
    del manager  # Construction already verifies connectivity with Docker.
    return {"status": "ready", "database": "ok", "docker": "ok"}
