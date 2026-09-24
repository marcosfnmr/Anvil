"""Reconcile persisted scenario state with Docker runtime state."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from models.scenario import ScenarioRecord
from services.docker_manager import DockerManager, DockerManagerError


def reconcile_runtime(
    session: Session,
    manager: DockerManager,
    *,
    cleanup_orphans: bool = False,
) -> dict[str, Any]:
    records = session.exec(select(ScenarioRecord)).all()
    known_ids = {record.id for record in records}
    removed = manager.cleanup_orphans(known_ids) if cleanup_orphans else {
        "containers": 0,
        "networks": 0,
    }
    errors: list[str] = []
    updated = 0

    for record in records:
        try:
            runtime = manager.status(record.id)
        except DockerManagerError as error:
            errors.append(f"{record.id}: {error}")
            continue
        if record.status != runtime["status"]:
            record.status = runtime["status"]
            session.add(record)
            updated += 1

    if updated:
        session.commit()

    return {
        "scenarios": len(records),
        "updated": updated,
        "removed": removed,
        "errors": errors,
    }
