"""Scenario-scoped membership management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.auth import PlatformUser, ScenarioMembership, ScenarioMembershipCreate, ScenarioMembershipRead
from models.db import get_session
from models.scenario import ScenarioRecord


router = APIRouter(prefix="/api/scenarios/{scenario_id}/members", tags=["scenario access"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[ScenarioMembershipRead])
def list_members(scenario_id: UUID, request: Request, session: SessionDependency) -> list[ScenarioMembershipRead]:
    _authorize_manager(scenario_id, request, session)
    rows = session.exec(select(ScenarioMembership, PlatformUser)
                        .join(PlatformUser, PlatformUser.id == ScenarioMembership.user_id)
                        .where(ScenarioMembership.scenario_id == str(scenario_id))
                        .order_by(PlatformUser.email)).all()
    return [ScenarioMembershipRead(user_id=user.id, email=user.email, role=membership.role, created_at=membership.created_at)
            for membership, user in rows]


@router.post("", response_model=ScenarioMembershipRead, status_code=201)
def add_member(scenario_id: UUID, payload: ScenarioMembershipCreate, request: Request, session: SessionDependency) -> ScenarioMembershipRead:
    key = str(scenario_id)
    actor, scenario = _authorize_manager(scenario_id, request, session)
    if actor and actor.role == "designer" and payload.role == "instructor":
        raise HTTPException(status_code=403, detail="Designers cannot assign the instructor role")
    user = session.exec(select(PlatformUser).where(PlatformUser.email == payload.email)).first()
    if user is None or not user.active:
        raise HTTPException(status_code=404, detail="Active user not found")
    if user.id == scenario.owner_user_id:
        raise HTTPException(status_code=409, detail="The scenario owner already has access")
    membership = session.exec(select(ScenarioMembership).where(
        ScenarioMembership.scenario_id == key, ScenarioMembership.user_id == user.id,
    )).first()
    if membership is None:
        membership = ScenarioMembership(scenario_id=key, user_id=user.id, role=payload.role)
    else:
        membership.role = payload.role
    session.add(membership)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        membership = session.exec(select(ScenarioMembership).where(
            ScenarioMembership.scenario_id == key, ScenarioMembership.user_id == user.id,
        )).first()
        if membership is None:
            raise HTTPException(status_code=409, detail="Scenario membership changed concurrently")
        membership.role = payload.role
        session.add(membership)
        session.commit()
    session.refresh(membership)
    return ScenarioMembershipRead(user_id=user.id, email=user.email, role=membership.role, created_at=membership.created_at)


@router.delete("/{user_id}", status_code=204)
def remove_member(scenario_id: UUID, user_id: str, request: Request, session: SessionDependency) -> None:
    key = str(scenario_id)
    _authorize_manager(scenario_id, request, session)
    membership = session.exec(select(ScenarioMembership).where(
        ScenarioMembership.scenario_id == key, ScenarioMembership.user_id == user_id,
    )).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="Scenario membership not found")
    session.delete(membership)
    session.commit()


def _authorize_manager(scenario_id: UUID, request: Request, session: Session) -> tuple[object, ScenarioRecord]:
    actor = getattr(request.state, "current_user", None)
    scenario = session.get(ScenarioRecord, str(scenario_id))
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if actor is None or actor.role != "admin" and scenario.owner_user_id != actor.id and getattr(request.state, "effective_role", None) != "instructor":
        raise HTTPException(status_code=403, detail="Only the scenario owner or an instructor can manage members")
    return actor, scenario
