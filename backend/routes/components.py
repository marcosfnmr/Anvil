"""Shared, schema-validated component presets (configuration only)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from devices import device_supports_component, get_device_component_config
from models.db import get_session
from models.template import (
    SharedComponentCreate, SharedComponentPreset, SharedComponentPresetRevision,
    SharedComponentRead, SharedComponentRevisionRead, SharedComponentUpdate,
)


router = APIRouter(prefix="/api/components/templates", tags=["components"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _unknown_fields(raw: object, parsed: object, path: str = "config") -> list[str]:
    """Reject fields silently ignored by permissive nested Pydantic models."""
    if isinstance(parsed, BaseModel) and isinstance(raw, dict):
        fields = type(parsed).model_fields
        unknown = [f"{path}.{key}" for key in raw.keys() - fields.keys()]
        for key in raw.keys() & fields.keys():
            unknown.extend(_unknown_fields(raw[key], getattr(parsed, key), f"{path}.{key}"))
        return unknown
    if isinstance(parsed, list) and isinstance(raw, list):
        return [field for index, (raw_item, parsed_item) in enumerate(zip(raw, parsed))
                for field in _unknown_fields(raw_item, parsed_item, f"{path}[{index}]")]
    return []


def _read(record: SharedComponentPreset) -> SharedComponentRead:
    return SharedComponentRead(
        id=record.id, title=record.title, description=record.description,
        device_type=record.device_type, component_type=record.component_type,
        config=json.loads(record.config_json), schema_version=record.schema_version,
        revision=record.revision, created_by=record.created_by,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _can_manage(record: SharedComponentPreset, request: Request) -> bool:
    user = getattr(request.state, "current_user", None)
    return bool(user and (user.role == "admin" or record.created_by == user.id))


def _validated_config(device_type: str, component_type: str, raw_config: object) -> str:
    if not device_supports_component(device_type, component_type):
        raise HTTPException(status_code=422, detail="This component type is not supported by the selected device")
    if component_type == "remote_input":
        raise HTTPException(status_code=422, detail="S7 remote inputs reference scenario-specific PLCs and cannot be shared as presets")
    component_schema = get_device_component_config(device_type)
    try:
        component = TypeAdapter(component_schema).validate_python(raw_config)
    except (ValidationError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"Invalid component configuration: {error}") from error
    if component.type != component_type:
        raise HTTPException(status_code=422, detail="Component type does not match its configuration")
    unknown = _unknown_fields(raw_config, component)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown component configuration fields: {', '.join(unknown)}")
    config = component.model_dump(mode="json")
    if component.type == "io_channel":
        # Channel IDs bind field signal connections, so a preset must not carry
        # an identifier from the publisher's scenario into another scenario.
        config["channel_id"] = None
    return json.dumps(config, ensure_ascii=False)


def _save_revision(session: Session, preset: SharedComponentPreset, actor_id: str) -> None:
    session.add(SharedComponentPresetRevision(
        preset_id=preset.id, version=preset.revision, title=preset.title,
        description=preset.description, device_type=preset.device_type,
        component_type=preset.component_type, config_json=preset.config_json,
        schema_version=preset.schema_version, created_by=actor_id,
        created_at=preset.updated_at,
    ))


def _revision_read(record: SharedComponentPresetRevision) -> SharedComponentRevisionRead:
    return SharedComponentRevisionRead(
        version=record.version, title=record.title, description=record.description,
        device_type=record.device_type, component_type=record.component_type,
        config=json.loads(record.config_json), schema_version=record.schema_version,
        created_by=record.created_by, created_at=record.created_at,
    )


@router.get("")
def list_shared_components(session: SessionDependency) -> list[dict]:
    records = session.exec(select(SharedComponentPreset).order_by(SharedComponentPreset.title)).all()
    return [_read(record).model_dump(mode="json") for record in records]


@router.post("", response_model=SharedComponentRead, status_code=status.HTTP_201_CREATED)
def publish_component(payload: SharedComponentCreate, session: SessionDependency, request: Request) -> SharedComponentRead:
    user = getattr(request.state, "current_user", None)
    if user is None or user.role not in {"admin", "instructor"}:
        raise HTTPException(status_code=403, detail="Only administrators and instructors can publish component presets")
    config_json = _validated_config(payload.device_type, payload.component_type, payload.config)
    now = datetime.now(timezone.utc)
    record = SharedComponentPreset(
        id=f"component-{uuid4().hex}", title=payload.title, description=payload.description,
        device_type=payload.device_type, component_type=payload.component_type,
        config_json=config_json, schema_version=1, revision=1,
        created_by=user.id, created_at=now, updated_at=now,
    )
    session.add(record)
    _save_revision(session, record, user.id)
    session.commit()
    session.refresh(record)
    return _read(record)


@router.put("/{preset_id}", response_model=SharedComponentRead)
def update_shared_component(preset_id: str, payload: SharedComponentUpdate,
                            session: SessionDependency, request: Request) -> SharedComponentRead:
    if not re.fullmatch(r"component-[0-9a-f]{32}", preset_id):
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    record = session.get(SharedComponentPreset, preset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    if not _can_manage(record, request):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can update this preset")
    values: dict[str, object] = {"revision": payload.expected_revision + 1,
                                "updated_at": datetime.now(timezone.utc)}
    if "title" in payload.model_fields_set:
        values["title"] = payload.title
    if "description" in payload.model_fields_set:
        values["description"] = payload.description
    if payload.config is not None:
        values["config_json"] = _validated_config(record.device_type, record.component_type, payload.config)
    result = session.execute(update(SharedComponentPreset).where(
        SharedComponentPreset.id == preset_id,
        SharedComponentPreset.revision == payload.expected_revision,
    ).values(**values))
    if result.rowcount != 1:
        session.rollback()
        current_revision = session.exec(select(SharedComponentPreset.revision).where(
            SharedComponentPreset.id == preset_id,
        )).first()
        if current_revision is None:
            raise HTTPException(status_code=404, detail="Shared component preset not found")
        raise HTTPException(status_code=409, detail=f"Preset changed; current revision is {current_revision}")
    session.refresh(record)
    _save_revision(session, record, request.state.current_user.id)
    session.commit()
    session.refresh(record)
    return _read(record)


@router.get("/{preset_id}/revisions", response_model=list[SharedComponentRevisionRead])
def shared_component_revisions(preset_id: str, session: SessionDependency) -> list[SharedComponentRevisionRead]:
    if session.get(SharedComponentPreset, preset_id) is None:
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    revisions = session.exec(select(SharedComponentPresetRevision).where(
        SharedComponentPresetRevision.preset_id == preset_id,
    ).order_by(SharedComponentPresetRevision.version.desc())).all()
    return [_revision_read(item) for item in revisions]


@router.post("/{preset_id}/revisions/{version}/restore", response_model=SharedComponentRead)
def restore_shared_component_revision(preset_id: str, version: int, session: SessionDependency,
                                      request: Request) -> SharedComponentRead:
    record = session.get(SharedComponentPreset, preset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    if not _can_manage(record, request):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can restore this preset")
    source = session.exec(select(SharedComponentPresetRevision).where(
        SharedComponentPresetRevision.preset_id == preset_id,
        SharedComponentPresetRevision.version == version,
    )).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Component preset revision not found")
    expected_revision = record.revision
    result = session.execute(update(SharedComponentPreset).where(
        SharedComponentPreset.id == preset_id,
        SharedComponentPreset.revision == expected_revision,
    ).values(
        revision=expected_revision + 1, title=source.title, description=source.description,
        device_type=source.device_type, component_type=source.component_type,
        config_json=source.config_json, schema_version=source.schema_version,
        updated_at=datetime.now(timezone.utc),
    ))
    if result.rowcount != 1:
        session.rollback()
        current_revision = session.exec(select(SharedComponentPreset.revision).where(
            SharedComponentPreset.id == preset_id,
        )).first()
        if current_revision is None:
            raise HTTPException(status_code=404, detail="Shared component preset not found")
        raise HTTPException(status_code=409, detail=f"Preset changed; current revision is {current_revision}")
    session.refresh(record)
    _save_revision(session, record, request.state.current_user.id)
    session.commit()
    session.refresh(record)
    return _read(record)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_shared_component(preset_id: str, session: SessionDependency, request: Request) -> None:
    if not re.fullmatch(r"component-[0-9a-f]{32}", preset_id):
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    record = session.get(SharedComponentPreset, preset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Shared component preset not found")
    user = getattr(request.state, "current_user", None)
    if user is None or (user.role != "admin" and record.created_by != user.id):
        raise HTTPException(status_code=403, detail="Only the publisher or an administrator can remove this preset")
    for revision in session.exec(select(SharedComponentPresetRevision).where(
        SharedComponentPresetRevision.preset_id == preset_id,
    )).all():
        session.delete(revision)
    session.delete(record)
    session.commit()
