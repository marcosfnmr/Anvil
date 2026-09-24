"""Scenario CRUD, lifecycle, and live-event endpoints."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlmodel import Session

from models.db import get_session
from models.scenario import ScenarioCreate, ScenarioRead, ScenarioStatusRead, ScenarioUpdate
from services.docker_manager import DockerManager, DockerManagerError
from services.event_stream import stream_scenario_events
from services.scenario_service import (
    ScenarioConflictError,
    ScenarioNotFoundError,
    ScenarioService,
)


router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])
SessionDependency = Annotated[Session, Depends(get_session)]


def get_docker_manager() -> DockerManager:
    try:
        return DockerManager()
    except DockerManagerError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


DockerDependency = Annotated[DockerManager, Depends(get_docker_manager)]


def _not_found(error: ScenarioNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


def _conflict(error: ScenarioConflictError | DockerManagerError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.post("", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def create_scenario(payload: ScenarioCreate, session: SessionDependency) -> ScenarioRead:
    try:
        return ScenarioService(session).create(payload)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get("", response_model=list[ScenarioRead])
def list_scenarios(session: SessionDependency) -> list[ScenarioRead]:
    return ScenarioService(session).list()


@router.get("/{scenario_id}", response_model=ScenarioRead)
def get_scenario(scenario_id: UUID, session: SessionDependency) -> ScenarioRead:
    try:
        return ScenarioService(session).get(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.put("/{scenario_id}", response_model=ScenarioRead)
def update_scenario(
    scenario_id: UUID,
    payload: ScenarioUpdate,
    session: SessionDependency,
) -> ScenarioRead:
    try:
        return ScenarioService(session).update(str(scenario_id), payload)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ScenarioConflictError as error:
        raise _conflict(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: UUID, session: SessionDependency) -> Response:
    try:
        ScenarioService(session).delete(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ScenarioConflictError as error:
        raise _conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{scenario_id}/deploy", response_model=ScenarioStatusRead)
def deploy_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    try:
        record = service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error

    if record.status != "stopped":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stop the scenario before deploying it again",
        )

    service.set_status(scenario_key, "deploying")
    try:
        result = manager.deploy(
            scenario_key,
            record.to_read_model().network.subnet,
            record.runtime_devices(),
        )
    except DockerManagerError as error:
        service.set_status(scenario_key, "error")
        raise _conflict(error) from error

    service.set_status(scenario_key, result["status"])
    return ScenarioStatusRead.model_validate(result)


@router.post("/{scenario_id}/stop", response_model=ScenarioStatusRead)
def stop_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    try:
        service.get_record(scenario_key)
        result = manager.stop(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error

    service.set_status(scenario_key, "stopped")
    return ScenarioStatusRead.model_validate(result)


@router.get("/{scenario_id}/status", response_model=ScenarioStatusRead)
def scenario_status(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    try:
        service.get_record(scenario_key)
        result = manager.status(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error

    service.set_status(scenario_key, result["status"])
    return ScenarioStatusRead.model_validate(result)


@router.websocket("/{scenario_id}/events")
async def scenario_events(
    websocket: WebSocket,
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> None:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    await websocket.accept()

    try:
        service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        await websocket.send_json({"type": "error", "detail": str(error)})
        await websocket.close(code=4404)
        return

    def status_provider() -> dict[str, Any]:
        session.expire_all()
        record = service.get_record(scenario_key)
        runtime = manager.status(scenario_key)
        docker_status = runtime["status"]

        if docker_status == "stopped" and record.status in {"deploying", "error"}:
            runtime["status"] = record.status
        elif record.status != docker_status:
            service.set_status(scenario_key, docker_status)
        return runtime

    try:
        await stream_scenario_events(websocket, scenario_key, status_provider)
    except ScenarioNotFoundError as error:
        await websocket.send_json({"type": "error", "detail": str(error)})
        await websocket.close(code=4404)
    except DockerManagerError as error:
        await websocket.send_json({"type": "error", "detail": str(error)})
        await websocket.close(code=1011)
    except WebSocketDisconnect:
        return
