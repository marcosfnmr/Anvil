"""Curated scenario library, shared template revisions, and instantiation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlmodel import Session, select

from models.db import get_session
from models.scenario import ScenarioCreate, ScenarioRead
from models.template import (
    SharedScenarioTemplate, SharedScenarioTemplateRevision, SharedTemplateCreate,
    SharedTemplateRead, SharedTemplateRevisionRead, SharedTemplateUpdate,
)
from models.auth import ScenarioMembership
from services.scenario_service import ScenarioService
from services.template_catalog import (
    TemplateCatalogError,
    TemplateNotFoundError,
    list_templates,
    load_template,
)


router = APIRouter(prefix="/api/templates", tags=["templates"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("")
def scenario_templates(session: SessionDependency) -> list[dict]:
    try:
        curated = list_templates()
        shared = session.exec(select(SharedScenarioTemplate).order_by(SharedScenarioTemplate.title)).all()
        curated.extend(_template_read(item).model_dump(mode="json") for item in shared)
        return curated
    except TemplateCatalogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def _template_read(item: SharedScenarioTemplate) -> SharedTemplateRead:
    return SharedTemplateRead(
        id=item.id, title=item.title, category=item.category, summary=item.summary,
        complexity=item.complexity, tags=json.loads(item.tags_json),
        created_at=item.created_at, created_by=item.created_by, revision=item.revision,
    )


def _can_manage(record: SharedScenarioTemplate, request: Request) -> bool:
    user = getattr(request.state, "current_user", None)
    return bool(user and (user.role == "admin" or record.created_by == user.id))


def _scenario_snapshot(session: Session, scenario_id: str, request: Request) -> str:
    user = getattr(request.state, "current_user", None)
    try:
        source_record = ScenarioService(session).get_record(scenario_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Source scenario not found") from error
    if source_record.owner_user_id != user.id and user.role != "admin":
        membership = session.exec(select(ScenarioMembership).where(
            ScenarioMembership.scenario_id == scenario_id,
            ScenarioMembership.user_id == user.id,
        )).first()
        if membership is None:
            raise HTTPException(status_code=404, detail="Source scenario not found")
    source = source_record.to_read_model()
    scenario = ScenarioCreate.model_validate({
        "name": source.name,
        "network": source.network.model_dump(mode="json"),
        "devices": [device.model_dump(mode="json") for device in source.devices],
        "canvas": source.canvas,
        "field_objects": [item.model_dump(mode="json") for item in source.field_objects],
        "signal_connections": [item.model_dump(mode="json") for item in source.signal_connections],
        "process_connections": [item.model_dump(mode="json") for item in source.process_connections],
        "network_routes": [item.model_dump(mode="json") for item in source.network_routes],
        "network_connections": [item.model_dump(mode="json") for item in source.network_connections],
    })
    return json.dumps({
        "format": "anvil-scenario-template", "schema_version": 1,
        "scenario": ScenarioService._snapshot(scenario),
    }, ensure_ascii=False)


def _save_template_revision(session: Session, template: SharedScenarioTemplate, actor_id: str) -> None:
    session.add(SharedScenarioTemplateRevision(
        template_id=template.id, version=template.revision, title=template.title,
        category=template.category, summary=template.summary, complexity=template.complexity,
        tags_json=template.tags_json, schema_version=template.schema_version,
        scenario_json=template.scenario_json, created_by=actor_id, created_at=template.updated_at,
    ))


def _revision_read(item: SharedScenarioTemplateRevision) -> SharedTemplateRevisionRead:
    return SharedTemplateRevisionRead(
        version=item.version, title=item.title, category=item.category, summary=item.summary,
        complexity=item.complexity, tags=json.loads(item.tags_json), schema_version=item.schema_version,
        created_by=item.created_by, created_at=item.created_at,
    )


@router.post("", response_model=SharedTemplateRead, status_code=status.HTTP_201_CREATED)
def create_shared_template(payload: SharedTemplateCreate, session: SessionDependency, request: Request) -> SharedTemplateRead:
    user = getattr(request.state, "current_user", None)
    if user is None or user.role not in {"admin", "instructor"}:
        raise HTTPException(status_code=403, detail="Only administrators and instructors can publish templates")
    scenario_json = _scenario_snapshot(session, payload.scenario_id, request)
    tags = [tag.strip() for tag in payload.tags if tag.strip()]
    if len(tags) != len(set(tag.casefold() for tag in tags)):
        raise HTTPException(status_code=422, detail="Template tags must be unique")
    now = datetime.now(timezone.utc)
    record = SharedScenarioTemplate(
        id=f"custom-{uuid4().hex}", title=payload.title.strip(), category=payload.category.strip(),
        summary=payload.summary.strip(), complexity=payload.complexity.strip(), tags_json=json.dumps(tags),
        schema_version=1,
        scenario_json=scenario_json,
        created_by=user.id, created_at=now, updated_at=now,
    )
    session.add(record)
    _save_template_revision(session, record, user.id)
    session.commit()
    session.refresh(record)
    return _template_read(record)


@router.put("/{template_id}", response_model=SharedTemplateRead)
def update_shared_template(template_id: str, payload: SharedTemplateUpdate, session: SessionDependency,
                           request: Request) -> SharedTemplateRead:
    if not re.fullmatch(r"custom-[0-9a-f]{32}", template_id):
        raise HTTPException(status_code=404, detail="Shared template not found")
    record = session.get(SharedScenarioTemplate, template_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared template not found")
    if not _can_manage(record, request):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can update this template")
    updates = payload.model_dump(exclude_unset=True, exclude={"expected_revision", "scenario_id"})
    values: dict[str, object] = {
        "revision": payload.expected_revision + 1,
        "updated_at": datetime.now(timezone.utc),
    }
    for field, value in updates.items():
        if field == "tags":
            values["tags_json"] = json.dumps(value, ensure_ascii=False)
        else:
            values[field] = value
    if payload.scenario_id is not None:
        values["scenario_json"] = _scenario_snapshot(session, payload.scenario_id, request)
    result = session.execute(update(SharedScenarioTemplate).where(
        SharedScenarioTemplate.id == template_id,
        SharedScenarioTemplate.revision == payload.expected_revision,
    ).values(**values))
    if result.rowcount != 1:
        session.rollback()
        current_revision = session.exec(select(SharedScenarioTemplate.revision).where(
            SharedScenarioTemplate.id == template_id,
        )).first()
        if current_revision is None:
            raise HTTPException(status_code=404, detail="Shared template not found")
        raise HTTPException(status_code=409, detail=f"Template changed; current revision is {current_revision}")
    user = request.state.current_user
    session.refresh(record)
    _save_template_revision(session, record, user.id)
    session.commit()
    session.refresh(record)
    return _template_read(record)


@router.get("/{template_id}/revisions", response_model=list[SharedTemplateRevisionRead])
def shared_template_revisions(template_id: str, session: SessionDependency) -> list[SharedTemplateRevisionRead]:
    if session.get(SharedScenarioTemplate, template_id) is None:
        raise HTTPException(status_code=404, detail="Shared template not found")
    versions = session.exec(select(SharedScenarioTemplateRevision).where(
        SharedScenarioTemplateRevision.template_id == template_id,
    ).order_by(SharedScenarioTemplateRevision.version.desc())).all()
    return [_revision_read(item) for item in versions]


@router.post("/{template_id}/revisions/{version}/restore", response_model=SharedTemplateRead)
def restore_shared_template_revision(template_id: str, version: int, session: SessionDependency,
                                     request: Request) -> SharedTemplateRead:
    record = session.get(SharedScenarioTemplate, template_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared template not found")
    if not _can_manage(record, request):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can restore this template")
    source = session.exec(select(SharedScenarioTemplateRevision).where(
        SharedScenarioTemplateRevision.template_id == template_id,
        SharedScenarioTemplateRevision.version == version,
    )).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Template revision not found")
    expected_revision = record.revision
    result = session.execute(update(SharedScenarioTemplate).where(
        SharedScenarioTemplate.id == template_id,
        SharedScenarioTemplate.revision == expected_revision,
    ).values(
        revision=expected_revision + 1, title=source.title, category=source.category,
        summary=source.summary, complexity=source.complexity, tags_json=source.tags_json,
        schema_version=source.schema_version, scenario_json=source.scenario_json,
        updated_at=datetime.now(timezone.utc),
    ))
    if result.rowcount != 1:
        session.rollback()
        current_revision = session.exec(select(SharedScenarioTemplate.revision).where(
            SharedScenarioTemplate.id == template_id,
        )).first()
        if current_revision is None:
            raise HTTPException(status_code=404, detail="Shared template not found")
        raise HTTPException(status_code=409, detail=f"Template changed; current revision is {current_revision}")
    actor = request.state.current_user
    session.refresh(record)
    _save_template_revision(session, record, actor.id)
    session.commit()
    session.refresh(record)
    return _template_read(record)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shared_template(template_id: str, session: SessionDependency, request: Request) -> None:
    if not re.fullmatch(r"custom-[0-9a-f]{32}", template_id):
        raise HTTPException(status_code=404, detail="Shared template not found")
    record = session.get(SharedScenarioTemplate, template_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared template not found")
    user = getattr(request.state, "current_user", None)
    if user is None or (user.role != "admin" and record.created_by != user.id):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can delete this template")
    for revision in session.exec(select(SharedScenarioTemplateRevision).where(
        SharedScenarioTemplateRevision.template_id == template_id,
    )).all():
        session.delete(revision)
    session.delete(record)
    session.commit()


@router.post("/{template_id}/instantiate", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def instantiate_template(template_id: str, session: SessionDependency, request: Request) -> ScenarioRead:
    try:
        if template_id.startswith("custom-"):
            template = session.get(SharedScenarioTemplate, template_id)
            if template is None:
                raise TemplateNotFoundError(f"Scenario template '{template_id}' was not found")
            if template.schema_version != 1:
                raise TemplateCatalogError(f"Unsupported shared template schema version {template.schema_version}")
            stored = json.loads(template.scenario_json)
            if stored.get("format") != "anvil-scenario-template" or stored.get("schema_version") != 1:
                raise TemplateCatalogError("Shared template declares an unsupported schema")
            payload = ScenarioCreate.model_validate(stored["scenario"])
        else:
            payload = load_template(template_id)
        user = getattr(request.state, "current_user", None)
        return ScenarioService(session).create(payload, owner_user_id=user.id if user else None)
    except TemplateNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TemplateCatalogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
