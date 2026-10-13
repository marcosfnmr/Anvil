"""Scenario-scoped exercise definitions and execution state."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from dependencies import DockerDependency
from models.db import get_session
from models.exercise import ExerciseDefinition, ExerciseRun, ExerciseRunStatus
from services.docker_manager import DockerManagerError
from services.exercise_runtime import cancel_run, create_run, start_run
from services.scenario_service import ScenarioService, ScenarioNotFoundError


router = APIRouter(prefix="/api/scenarios/{scenario_id}/exercises", tags=["exercises"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("", response_model=ExerciseRunStatus, status_code=status.HTTP_201_CREATED)
def create_exercise(scenario_id: UUID, payload: ExerciseDefinition, session: SessionDependency) -> ExerciseRunStatus:
    scenario_key = str(scenario_id)
    try:
        ScenarioService(session).get_record(scenario_key)
        return create_run(session, scenario_key, payload).to_read_model()
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("", response_model=list[ExerciseRunStatus])
def list_exercises(scenario_id: UUID, session: SessionDependency) -> list[ExerciseRunStatus]:
    scenario_key = str(scenario_id)
    try:
        ScenarioService(session).get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    runs = session.exec(select(ExerciseRun).where(ExerciseRun.scenario_id == scenario_key)
                        .order_by(ExerciseRun.created_at.desc())).all()
    return [run.to_read_model() for run in runs]


@router.get("/{exercise_id}", response_model=ExerciseRunStatus)
def get_exercise(scenario_id: UUID, exercise_id: UUID, session: SessionDependency) -> ExerciseRunStatus:
    run = _get_run(session, str(scenario_id), str(exercise_id))
    return run.to_read_model()


@router.post("/{exercise_id}/start", response_model=ExerciseRunStatus)
def start_exercise(scenario_id: UUID, exercise_id: UUID, session: SessionDependency) -> ExerciseRunStatus:
    try:
        return start_run(session, str(scenario_id), str(exercise_id)).to_read_model()
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{exercise_id}/cancel", response_model=ExerciseRunStatus)
def cancel_exercise(
    scenario_id: UUID, exercise_id: UUID, session: SessionDependency, manager: DockerDependency,
) -> ExerciseRunStatus:
    try:
        return cancel_run(session, str(scenario_id), str(exercise_id), manager=manager).to_read_model()
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _get_run(session: Session, scenario_id: str, exercise_id: str) -> ExerciseRun:
    run = session.get(ExerciseRun, exercise_id)
    if run is None or run.scenario_id != scenario_id:
        raise HTTPException(status_code=404, detail="Exercise was not found for this scenario")
    return run
