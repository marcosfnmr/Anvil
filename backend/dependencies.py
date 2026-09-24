"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from services.docker_manager import DockerManager, DockerManagerError


def get_docker_manager() -> DockerManager:
    try:
        return DockerManager()
    except DockerManagerError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


DockerDependency = Annotated[DockerManager, Depends(get_docker_manager)]
