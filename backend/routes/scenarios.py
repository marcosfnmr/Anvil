"""Scenario CRUD, lifecycle, I/O map, logs, and live-event endpoints."""

from __future__ import annotations

import io
import asyncio
import hashlib
import json
import socket
import zipfile
from datetime import datetime
from urllib.parse import urlsplit
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import Session, select

from dependencies import DockerDependency, OptionalDockerDependency, get_docker_manager
from devices import DEVICE_REGISTRY, list_device_catalog
from models.db import get_session
from models.scenario import (
    ScenarioCreate,
    ScenarioCloneRequest,
    ScenarioExport,
    ScenarioImportEnvelope,
    ScenarioIoMapRead,
    ScenarioLogsRead,
    ScenarioRead,
    ScenarioStatusRead,
    ScenarioNodeOperation,
    ScenarioUpdate,
)
from models.audit import AuditEventPage, AuditIntegrityRead, ScenarioAuditArchive
from models.revision import ScenarioRevisionDetail, ScenarioRevisionRead
from models.device_profile import DeviceProfile, DeviceProfileImportRequest
from services.docker_manager import DockerManagerError
from services.event_stream import stream_scenario_events
from services.audit import iter_audit_events, list_audit_events, record_node_runtime_transitions, verify_audit_integrity
from services.audit import record_audit_event
from services.io_map import build_scenario_io_map
from services.log_stream import stream_scenario_logs
from services.protocol_trace_collector import flush_protocol_traces_best_effort
from services.scenario_service import (
    ScenarioConflictError,
    ScenarioNotFoundError,
    ScenarioService,
)
from models.field import FieldControl
from services.field_operations import field_operation
from services.compose_export import build_compose_bundle
from services.scenario_schema import CURRENT_SCENARIO_SCHEMA_VERSION, migrate_scenario_payload
from services.evidence_export import EvidenceBundleTooLarge, build_evidence_bundle
from services.exercise_runtime import pause_active_exercises, resume_paused_exercises
from services.topology_diagnostics import diagnose_scenario
from services.runtime_status import include_missing_runtime_nodes
from services.device_profiles import build_device_profile
from services.terminal_leases import (
    TERMINAL_RENEW_SECONDS,
    acquire_terminal_lease,
    make_worker_id,
    release_terminal_lease,
    renew_terminal_lease,
)
from services.scenario_leases import (
    OperationLeaseHeartbeat,
    acquire_operation_lease,
    make_operation_owner_id,
    release_operation_lease,
)
from services.node_operation_journal import (
    begin_node_operation,
    mark_node_operation_complete,
    mark_node_operation_retryable,
)


router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])
SessionDependency = Annotated[Session, Depends(get_session)]
_terminal_worker_id = make_worker_id()


class NodeLifecycleRequest(BaseModel):
    action: Literal["start", "stop", "restart", "pause", "resume", "recreate"]


class PacketCaptureStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str | None = None
    zone_id: str | None = None
    link_source_node_id: str | None = None
    link_target_node_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> "PacketCaptureStart":
        has_link = self.link_source_node_id is not None or self.link_target_node_id is not None
        if has_link:
            if (self.node_id is not None or self.zone_id is not None
                    or self.link_source_node_id is None or self.link_target_node_id is None):
                raise ValueError("provide both link endpoints and no device/zone target")
            if self.link_source_node_id == self.link_target_node_id:
                raise ValueError("link capture requires two different devices")
        elif (self.node_id is None) == (self.zone_id is None):
            raise ValueError("provide exactly one of node_id, zone_id, or a link endpoint pair")
        return self


class PacketCaptureRead(BaseModel):
    capture_id: UUID
    node_id: str | None = None
    zone_id: str | None = None
    source_node_id: str | None = None
    target_node_id: str | None = None
    scope: Literal["device", "segment", "link"]
    status: Literal["capturing"]


class ScenarioNodeOperationRead(BaseModel):
    id: str
    node_id: str
    action: str
    source: str
    status: str
    attempts: int
    requested_at: datetime
    updated_at: datetime
    result: dict[str, Any]
    last_error: str | None = None


class NetworkRouteEndpointRead(BaseModel):
    route_id: str
    protocol: Literal["s7", "modbus-tcp", "tcp"]
    source_node_id: str
    target_node_id: str
    latency_ms: int = 0
    target_port: int
    enabled: bool
    listen_host: str | None = None
    listen_port: int


class DeviceServiceRead(BaseModel):
    node_id: str
    device_name: str
    device_type: str
    hostname: str
    ip: str
    zone_id: str
    service: str
    protocol: str
    transport: str
    port: int
    role: Literal["server"]
    scope: Literal["ot", "internal"] = "ot"
    description: str
    host_bind_ip: str | None = None
    host_port: int | None = None


class DeviceCatalogEntry(BaseModel):
    type: str
    label: str
    description: str
    icon: str
    protocols: list[str]
    capabilities: list[str]


@router.get("/catalog", response_model=list[DeviceCatalogEntry])
def device_catalog() -> list[dict[str, Any]]:
    return list_device_catalog()


def _not_found(error: ScenarioNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


def _conflict(error: ScenarioConflictError | DockerManagerError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _current_user_id(request: Request) -> str | None:
    user = getattr(request.state, "current_user", None)
    return user.id if user else None


@router.post("", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def create_scenario(payload: ScenarioCreate, session: SessionDependency, request: Request) -> ScenarioRead:
    try:
        return ScenarioService(session).create(payload, owner_user_id=_current_user_id(request))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error


@router.post("/import", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def import_scenario(payload: dict[str, Any], session: SessionDependency, request: Request) -> ScenarioRead:
    try:
        if any(key in payload for key in ("format", "schema_version", "scenario")):
            exported = ScenarioImportEnvelope.model_validate(payload)
            migrated = migrate_scenario_payload(exported.schema_version, exported.scenario)
            scenario = ScenarioCreate.model_validate(migrated)
        else:
            # Unwrapped documents predate the versioned envelope and are treated
            # as v1 before they pass through the same explicit migration chain.
            migrated = migrate_scenario_payload(1, payload)
            scenario = ScenarioCreate.model_validate(migrated)
        return ScenarioService(session).create(scenario, owner_user_id=_current_user_id(request))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{scenario_id}/export", response_model=ScenarioExport)
def export_scenario(scenario_id: UUID, session: SessionDependency) -> ScenarioExport:
    try:
        record = ScenarioService(session).get_record(str(scenario_id))
        scenario = record.to_read_model()
        scenario_export = ScenarioCreate.model_validate({
            "name": scenario.name,
            "network": scenario.network.model_dump(mode="json"),
            "devices": [device.model_dump(mode="json") for device in scenario.devices],
            "canvas": scenario.canvas,
            "field_objects": [item.model_dump(mode="json") for item in scenario.field_objects],
            "signal_connections": [item.model_dump(mode="json") for item in scenario.signal_connections],
            "process_connections": [item.model_dump(mode="json") for item in scenario.process_connections],
            "network_routes": [item.model_dump(mode="json") for item in scenario.network_routes],
            "network_connections": [item.model_dump(mode="json") for item in scenario.network_connections],
        })
        return ScenarioExport(schema_version=CURRENT_SCENARIO_SCHEMA_VERSION, scenario=scenario_export)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/network-routes", response_model=list[NetworkRouteEndpointRead])
def get_network_route_endpoints(scenario_id: UUID, session: SessionDependency) -> list[dict]:
    try:
        return ScenarioService(session).network_route_endpoints(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/services", response_model=list[DeviceServiceRead])
def get_scenario_services(scenario_id: UUID, session: SessionDependency) -> list[dict[str, Any]]:
    """List the protocol listeners and effective ports declared by scenario devices."""
    try:
        scenario = ScenarioService(session).get(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    services = []
    for device in scenario.devices:
        adapter_class = DEVICE_REGISTRY.get(device.device_type)
        if adapter_class is None:
            continue
        runtime_device = adapter_class().build_runtime_device(device)
        for service in adapter_class().network_services(runtime_device):
            services.append({
                "node_id": device.node_id,
                "device_name": device.name,
                "device_type": device.device_type,
                "hostname": device.hostname or device.node_id,
                "ip": device.ip,
                "zone_id": device.zone_id,
                **service,
                "host_bind_ip": device.external_host_ip if device.external_access else None,
                "host_port": device.external_port if device.external_access else None,
            })
    return services


@router.get("/{scenario_id}/devices/{node_id}/profile", response_model=DeviceProfile)
def export_device_profile(scenario_id: UUID, node_id: str, session: SessionDependency) -> DeviceProfile:
    try:
        scenario = ScenarioService(session).get(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    device = next((item for item in scenario.devices if item.node_id == node_id), None)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{node_id}' was not found")
    try:
        return build_device_profile(device)
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{scenario_id}/devices/import", response_model=ScenarioRead)
def import_device_profile(
    scenario_id: UUID, payload: DeviceProfileImportRequest, session: SessionDependency
) -> ScenarioRead:
    service = ScenarioService(session)
    try:
        scenario = service.get(str(scenario_id))
        if payload.profile.device_type not in DEVICE_REGISTRY:
            raise ValueError(f"Unsupported device type '{payload.profile.device_type}'")
        if getattr(DEVICE_REGISTRY[payload.profile.device_type], "internal", False):
            raise ValueError("internal runtime devices cannot be imported from profiles")
        updated = scenario.model_dump(mode="json")
        updated["devices"].append({
            "node_id": payload.node_id,
            "device_type": payload.profile.device_type,
            "name": payload.profile.name,
            "ip": payload.ip,
            "zone_id": payload.zone_id,
            "config": payload.profile.config,
        })
        return service.update(str(scenario_id), ScenarioUpdate.model_validate(updated))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ScenarioConflictError as error:
        raise _conflict(error) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{scenario_id}/deployment-bundle")
def export_deployment_bundle(scenario_id: UUID, session: SessionDependency) -> StreamingResponse:
    try:
        record = ScenarioService(session).get_record(str(scenario_id))
        content = build_compose_bundle(str(scenario_id), record.to_read_model(), record.runtime_devices())
        return StreamingResponse(
            io.BytesIO(content), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="anvil-{scenario_id}-deployment.zip"'},
        )
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{scenario_id}/clone", response_model=ScenarioRead, status_code=status.HTTP_201_CREATED)
def clone_scenario(scenario_id: UUID, payload: ScenarioCloneRequest, session: SessionDependency, request: Request) -> ScenarioRead:
    try:
        return ScenarioService(session).clone(str(scenario_id), payload.name, owner_user_id=_current_user_id(request))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("", response_model=list[ScenarioRead])
def list_scenarios(session: SessionDependency, request: Request) -> list[ScenarioRead]:
    user = getattr(request.state, "current_user", None)
    return ScenarioService(session).list(
        user_id=user.id if user else None,
        admin=bool(user and user.role == "admin"),
    )


@router.get("/{scenario_id}", response_model=ScenarioRead)
def get_scenario(scenario_id: UUID, session: SessionDependency) -> ScenarioRead:
    try:
        return ScenarioService(session).get(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/revisions", response_model=list[ScenarioRevisionRead])
def list_scenario_revisions(scenario_id: UUID, session: SessionDependency) -> list[ScenarioRevisionRead]:
    try:
        return ScenarioService(session).list_revisions(str(scenario_id))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/revisions/{revision_id}", response_model=ScenarioRevisionDetail)
def get_scenario_revision(
    scenario_id: UUID, revision_id: int, session: SessionDependency
) -> ScenarioRevisionDetail:
    try:
        return ScenarioService(session).get_revision(str(scenario_id), revision_id)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.post("/{scenario_id}/revisions/{revision_id}/restore", response_model=ScenarioRead)
def restore_scenario_revision(
    scenario_id: UUID, revision_id: int, session: SessionDependency
) -> ScenarioRead:
    try:
        return ScenarioService(session).restore_revision(str(scenario_id), revision_id)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ScenarioConflictError as error:
        raise _conflict(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/{scenario_id}", response_model=ScenarioRead)
def update_scenario(
    scenario_id: UUID, payload: ScenarioUpdate, session: SessionDependency
) -> ScenarioRead:
    try:
        return ScenarioService(session).update(str(scenario_id), payload)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ScenarioConflictError as error:
        raise _conflict(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: UUID, session: SessionDependency, docker: OptionalDockerDependency) -> Response:
    scenario_key = str(scenario_id)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        service = ScenarioService(session)
        service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "delete", owner_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "delete", owner_id) as heartbeat:
            try:
                scenario = service.get_record(scenario_key)
                session.refresh(scenario)
                if scenario.status != "stopped":
                    raise ScenarioConflictError("Stop the scenario before deleting it")
                persistent_data_configured = any(
                    (item.get("device_type") == "plc-s7" and item.get("config", {}).get("retain_logic_state") is True)
                    or item.get("device_type") == "scada"
                    or item.get("device_type") == "plc-s7"
                    or item.get("device_type") == "modbus-tcp"
                    for item in scenario.runtime_devices()
                )
                if docker is None and persistent_data_configured:
                    raise DockerManagerError("Docker is unavailable; Anvil cannot verify or remove this scenario's persistent device data")
                if docker is not None:
                    docker.delete_retention_volumes(scenario_key)
                if heartbeat.lost.is_set():
                    raise ScenarioConflictError("Scenario deletion lease was lost; retry the operation")
                service.delete(scenario_key, lease_owner=owner_id)
            except ScenarioNotFoundError as error:
                raise _not_found(error) from error
            except ScenarioConflictError as error:
                raise _conflict(error) from error
            except DockerManagerError as error:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    finally:
        release_operation_lease(database_engine, scenario_key, "delete", owner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{scenario_id}/io-map", response_model=ScenarioIoMapRead)
def scenario_io_map(scenario_id: UUID, session: SessionDependency) -> ScenarioIoMapRead:
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        return build_scenario_io_map(scenario_key, record.runtime_devices())
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/diagnostics")
def scenario_diagnostics(scenario_id: UUID, session: SessionDependency,
                         manager: OptionalDockerDependency) -> dict:
    try:
        record = ScenarioService(session).get_record(str(scenario_id))
        scenario = record.to_read_model()
        service = ScenarioService(session)
        runtime_status = None
        runtime_routes: list[dict[str, Any]] = []
        if record.status in {"running", "partial", "error", "paused"} and manager is not None:
            try:
                runtime_status = manager.status(str(scenario_id))
                runtime_status = include_missing_runtime_nodes(
                    runtime_status, record.runtime_devices(), record.status
                )
            except DockerManagerError:
                # Diagnostics must remain useful when Docker is temporarily unavailable.
                runtime_status = None
        probe_route = getattr(manager, "probe_network_route", None)
        probe_field_bindings = getattr(manager, "probe_field_bindings", None)
        if runtime_status is not None and record.status in {"running", "partial"} and callable(probe_route):
            runtime_devices = {item["node_id"]: item for item in record.runtime_devices()}
            for route in service.network_route_endpoints(str(scenario_id)):
                if not route["enabled"] or not route["listen_host"]:
                    continue
                target = runtime_devices.get(route["target_node_id"], {})
                try:
                    probe = probe_route(
                        str(scenario_id),
                        source_node_id=route["source_node_id"],
                        host=route["listen_host"],
                        port=route["listen_port"],
                        protocol=route["protocol"],
                        target_config=target.get("config", {}),
                    )
                except DockerManagerError as error:
                    probe = {"status": "unreachable", "detail": str(error)}
                runtime_routes.append({
                    "route_id": route["route_id"],
                    "protocol": route["protocol"],
                    "source_node_id": route["source_node_id"],
                    "target_node_id": route["target_node_id"],
                    **probe,
                })
            from services.field_runtime import FIELD_NODE_ID

            field_runtime = runtime_devices.get(FIELD_NODE_ID)
            if field_runtime is not None:
                field_gateways = {
                    item.get("config", {}).get("source_node_id"): item
                    for item in runtime_devices.values()
                    if item.get("device_type") == "tcp-gateway"
                    and item.get("config", {}).get("protocol") == "field-http"
                }
                for source in runtime_devices.values():
                    bridge = source.get("config", {}).get("field_bridge")
                    if not bridge or not bridge.get("bindings"):
                        continue
                    gateway = field_gateways.get(source["node_id"])
                    same_zone = source.get("zone_id", "default") == field_runtime.get("zone_id", "default")
                    if not same_zone and gateway is None:
                        continue
                    endpoint = urlsplit(bridge["url"])
                    if not endpoint.hostname or endpoint.port is None:
                        continue
                    try:
                        probe = probe_route(
                            str(scenario_id),
                            source_node_id=source["node_id"],
                            host=endpoint.hostname,
                            port=endpoint.port,
                            protocol="field-http",
                            target_config=field_runtime.get("config", {}),
                        )
                    except DockerManagerError as error:
                        probe = {"status": "unreachable", "detail": str(error)}
                    runtime_routes.append({
                        "route_id": f"field-{source['node_id']}",
                        "protocol": "field-http",
                        "source_node_id": source["node_id"],
                        "target_node_id": FIELD_NODE_ID,
                        **probe,
                    })
                    if callable(probe_field_bindings):
                        try:
                            binding_probe = probe_field_bindings(
                                str(scenario_id),
                                source_node_id=source["node_id"],
                                bindings=bridge["bindings"],
                            )
                        except DockerManagerError as error:
                            binding_probe = {"status": "unreachable", "detail": str(error), "bindings": []}
                        runtime_routes.append({
                            "route_id": f"field-bindings-{source['node_id']}",
                            "protocol": "field-binding",
                            "source_node_id": source["node_id"],
                            "target_node_id": FIELD_NODE_ID,
                            **binding_probe,
                        })
        return diagnose_scenario(scenario, runtime_status, runtime_routes)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/field-migration-preview")
def field_migration_preview(scenario_id: UUID, session: SessionDependency) -> dict:
    from services.field_migration import preview_field_extraction

    try:
        record = ScenarioService(session).get_record(str(scenario_id))
        return preview_field_extraction(record.to_read_model(), record.runtime_devices())
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.post("/{scenario_id}/field-migration-copy", response_model=ScenarioRead,
             status_code=status.HTTP_201_CREATED)
def create_field_migration_copy(scenario_id: UUID, payload: ScenarioCloneRequest,
                                session: SessionDependency, request: Request) -> ScenarioRead:
    try:
        return ScenarioService(session).clone_with_field_migration(
            str(scenario_id), payload.name, owner_user_id=_current_user_id(request),
        )
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _field_record(session, scenario_id):
    record = ScenarioService(session).get_record(str(scenario_id))
    if not record.to_read_model().field_objects:
        raise LookupError("This scenario has no independent field objects")
    if record.status not in {"running", "partial", "error"}:
        raise ValueError("Deploy the scenario before reading field values")
    return record


@router.get("/{scenario_id}/field/values")
def field_values(scenario_id: UUID, session: SessionDependency, manager: DockerDependency):
    try:
        _field_record(session, scenario_id)
        return field_operation(manager, str(scenario_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/{scenario_id}/field/{object_id}/control")
def control_field(scenario_id: UUID, object_id: str, payload: FieldControl, session: SessionDependency, manager: DockerDependency):
    try:
        record = _field_record(session, scenario_id)
        if object_id not in {item.object_id for item in record.to_read_model().field_objects}:
            raise LookupError("Unknown field object")
        result = field_operation(manager, str(scenario_id), "control", {"object_id": object_id, **payload.model_dump(exclude_unset=True)})
        record_audit_event(session, str(scenario_id), "operator.field_control", {
            "object_id": object_id,
            **payload.model_dump(exclude_unset=True),
        }, actor="api-client")
        return result
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/{scenario_id}/logs", response_model=ScenarioLogsRead)
def scenario_logs(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioLogsRead:
    scenario_key = str(scenario_id)
    try:
        ScenarioService(session).get_record(scenario_key)
        return ScenarioLogsRead.model_validate(manager.logs(scenario_key))
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error


@router.get("/{scenario_id}/audit-events", response_model=AuditEventPage)
def scenario_audit_events(
    scenario_id: UUID,
    session: SessionDependency,
    limit: int = Query(default=100, ge=1, le=1000),
    before_id: int | None = Query(default=None, ge=1),
) -> AuditEventPage:
    scenario_key = str(scenario_id)
    try:
        _require_audit_history(session, scenario_key)
        events = list_audit_events(session, scenario_key, limit=limit, before_id=before_id)
        return AuditEventPage(
            scenario_id=scenario_key,
            events=events,
            next_before_id=events[-1].id if len(events) == limit else None,
        )
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/audit-events/export")
def export_scenario_audit_events(scenario_id: UUID, session: SessionDependency) -> Response:
    scenario_key = str(scenario_id)
    try:
        _require_audit_history(session, scenario_key)
        def event_lines():
            for event in iter_audit_events(session, scenario_key):
                yield f"{event.model_dump_json()}\n"

        return StreamingResponse(
            event_lines(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="anvil-{scenario_key}-events.jsonl"'},
        )
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


@router.get("/{scenario_id}/audit-events/integrity", response_model=AuditIntegrityRead)
def scenario_audit_integrity(scenario_id: UUID, session: SessionDependency) -> AuditIntegrityRead:
    scenario_key = str(scenario_id)
    try:
        _require_audit_history(session, scenario_key)
        return verify_audit_integrity(session, scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error


def _require_audit_history(session: Session, scenario_id: str) -> None:
    try:
        ScenarioService(session).get_record(scenario_id)
    except ScenarioNotFoundError:
        if session.get(ScenarioAuditArchive, scenario_id) is None:
            raise


@router.get("/{scenario_id}/evidence-bundle.zip")
def export_scenario_evidence_bundle(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
    capture_id: UUID | None = Query(default=None),
) -> StreamingResponse:
    """Download scenario audit and SCADA history, optionally consuming one PCAP capture."""
    scenario_key = str(scenario_id)
    archive_file = None
    try:
        scenario = ScenarioService(session).get_record(scenario_key)
        capture_key = str(capture_id) if capture_id else None
        logs = manager.logs(scenario_key, tail=1000)
        pcap = manager.export_packet_capture(scenario_key, capture_key) if capture_key else None
        if capture_key and pcap is not None:
            record_audit_event(session, scenario_key, "network.packet_capture_exported", {
                "capture_id": capture_key, "bytes": len(pcap), "format": "pcap",
                "included_in": "evidence-bundle",
            }, actor="api-client")
        archive_file = build_evidence_bundle(
            session, scenario, logs=logs, capture_id=capture_key, pcap=pcap,
        )
        archive_file.seek(0)
        with zipfile.ZipFile(archive_file, "r") as archive:
            manifest_sha256 = hashlib.sha256(archive.read("manifest.json")).hexdigest()
        archive_file.seek(0)
        filename = f"anvil-evidence-{scenario_key}.zip"

        def chunks():
            try:
                while chunk := archive_file.read(64 * 1024):
                    yield chunk
            finally:
                archive_file.close()

        return StreamingResponse(
            chunks(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "X-Anvil-Manifest-SHA256": manifest_sha256},
        )
    except ScenarioNotFoundError as error:
        if archive_file is not None:
            archive_file.close()
        raise _not_found(error) from error
    except DockerManagerError as error:
        if archive_file is not None:
            archive_file.close()
        raise _conflict(error) from error
    except EvidenceBundleTooLarge as error:
        if archive_file is not None:
            archive_file.close()
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error)) from error


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
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    if not acquire_operation_lease(database_engine, scenario_key, "deploy", owner_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "deploy", owner_id) as heartbeat:
            session.refresh(record)
            if record.status != "stopped":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Stop the scenario before deploying it again")
            # The browser runs the same preflight to present warnings and
            # actionable errors, but deployment must enforce schema/topology
            # validity independently of that client-side flow.
            try:
                scenario = record.to_read_model()
                preflight = diagnose_scenario(scenario)
            except (ValueError, TypeError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "message": "El escenario guardado no supera la validación previa al despliegue.",
                        "errors": [{"path": "", "message": str(error)}],
                    },
                ) from error
            if not preflight["valid"]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "message": "El escenario no supera la validación previa al despliegue.",
                        "errors": preflight["errors"],
                    },
                )
            runtime_devices = record.runtime_devices()
            try:
                pin_runtime_images = getattr(manager, "pin_runtime_images", None)
                if callable(pin_runtime_images):
                    pin_runtime_images(runtime_devices)
            except DockerManagerError as error:
                raise _conflict(error) from error
            record.runtime_devices_json = json.dumps(runtime_devices)
            session.add(record)
            session.commit()
            service.set_status(scenario_key, "deploying")
            try:
                result = manager.deploy(
                    scenario_key, record.to_read_model().network.subnet, runtime_devices
                )
            except DockerManagerError as error:
                if not heartbeat.lost.is_set() and "rollback incomplete" not in str(error).casefold():
                    service.set_status(scenario_key, "error")
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Deployment lease was lost; runtime reconciliation will inspect Docker")
            result = include_missing_runtime_nodes(
                result, runtime_devices, "deploying", reconcile_deploying=True
            )
            service.set_status(scenario_key, result["status"])
            record_node_runtime_transitions(session, scenario_key, result.get("containers", []), result["status"])
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "deploy", owner_id)


@router.post("/{scenario_id}/stop", response_model=ScenarioStatusRead)
def stop_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "stop", owner_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "stop", owner_id) as heartbeat:
            # DockerManager.stop removes the containers, so preserve their
            # retained protocol logs before invoking the cleanup operation.
            flush_protocol_traces_best_effort(database_engine, manager=manager, scenario_id=scenario_key)
            try:
                result = manager.stop(scenario_key)
            except DockerManagerError as error:
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Stop lease was lost; runtime reconciliation will inspect Docker")
            service.set_status(scenario_key, "stopped")
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "stop", owner_id)


@router.post("/{scenario_id}/restart", response_model=ScenarioStatusRead)
def restart_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        record = service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "restart", owner_id):
        raise HTTPException(status_code=409, detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "restart", owner_id) as heartbeat:
            try:
                session.refresh(record)
                if record.status not in {"running", "partial"}:
                    raise HTTPException(status_code=409, detail="Restart requires a running or partial scenario")
                flush_protocol_traces_best_effort(database_engine, manager=manager, scenario_id=scenario_key)
                result = manager.restart_scenario(scenario_key, record.runtime_devices())
                flush_protocol_traces_best_effort(database_engine, manager=manager, scenario_id=scenario_key)
            except DockerManagerError as error:
                # A multi-container restart can fail after some containers have
                # restarted. Reflect Docker's actual state for reconciliation.
                try:
                    actual = manager.status(scenario_key)
                    service.set_status(scenario_key, actual["status"])
                except DockerManagerError:
                    pass
                record_audit_event(session, scenario_key, "scenario.restart_failed", {}, actor="api-client")
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                raise HTTPException(status_code=409, detail="Restart lease was lost; runtime reconciliation will inspect Docker")
            result = include_missing_runtime_nodes(result, record.runtime_devices(), record.status)
            service.set_status(scenario_key, result["status"])
            record_node_runtime_transitions(session, scenario_key, result.get("containers", []), result["status"])
            record_audit_event(session, scenario_key, "scenario.restarted", {
                "node_count": len(record.runtime_devices()),
            }, actor="api-client")
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "restart", owner_id)


@router.post("/{scenario_id}/pause", response_model=ScenarioStatusRead)
def pause_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        record = service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "pause", owner_id):
        raise HTTPException(status_code=409, detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "pause", owner_id) as heartbeat:
            try:
                session.refresh(record)
                if record.status != "running":
                    raise HTTPException(status_code=409, detail="Pause requires a running scenario")
                result = manager.pause_scenario(scenario_key)
            except DockerManagerError as error:
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                raise HTTPException(status_code=409, detail="Pause lease was lost; runtime reconciliation will inspect Docker")
            result = include_missing_runtime_nodes(result, record.runtime_devices(), record.status)
            service.set_status(scenario_key, result["status"])
            if result["status"] == "paused":
                pause_active_exercises(session, scenario_key)
            record_audit_event(session, scenario_key, "scenario.paused", {}, actor="api-client")
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "pause", owner_id)


@router.post("/{scenario_id}/resume", response_model=ScenarioStatusRead)
def resume_scenario(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        record = service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "resume", owner_id):
        raise HTTPException(status_code=409, detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "resume", owner_id) as heartbeat:
            try:
                session.refresh(record)
                if record.status not in {"paused", "partial"}:
                    raise HTTPException(status_code=409, detail="Resume requires a paused scenario")
                result = manager.resume_scenario(scenario_key)
            except DockerManagerError as error:
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                raise HTTPException(status_code=409, detail="Resume lease was lost; runtime reconciliation will inspect Docker")
            result = include_missing_runtime_nodes(result, record.runtime_devices(), record.status)
            service.set_status(scenario_key, result["status"])
            if result["status"] in {"running", "partial"}:
                resume_paused_exercises(session, scenario_key)
            record_audit_event(session, scenario_key, "scenario.resumed", {}, actor="api-client")
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "resume", owner_id)


@router.post("/{scenario_id}/nodes/{node_id}/lifecycle", response_model=ScenarioStatusRead)
def node_lifecycle(
    scenario_id: UUID,
    node_id: str,
    payload: NodeLifecycleRequest,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    owner_id = make_operation_owner_id()
    database_engine = session.get_bind()
    try:
        service = ScenarioService(session)
        record = service.get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    if not acquire_operation_lease(database_engine, scenario_key, "node", owner_id):
        raise HTTPException(status_code=409, detail="Another lifecycle operation is active for this scenario")
    try:
        with OperationLeaseHeartbeat(database_engine, scenario_key, "node", owner_id) as heartbeat:
            journal_entry = None
            already_completed = False
            try:
                session.refresh(record)
                if record.status not in {"running", "paused", "partial", "error"}:
                    raise HTTPException(status_code=409, detail="Deploy the scenario before controlling a node")
                runtime_device = next(
                    (item for item in record.runtime_devices() if item["node_id"] == node_id), None
                )
                if runtime_device is None:
                    raise HTTPException(status_code=404, detail=f"Device '{node_id}' was not found")
                precondition = (
                    manager.node_operation_checkpoint(scenario_key, node_id)
                    if payload.action == "restart" and hasattr(manager, "node_operation_checkpoint")
                    else {}
                )
                journal_entry, already_completed = begin_node_operation(
                    database_engine, scenario_key, node_id, payload.action,
                    precondition=precondition,
                )
                if already_completed:
                    result = json.loads(journal_entry.result_json or "{}")
                else:
                    flush_protocol_logs = payload.action in {"start", "stop", "restart", "recreate"}
                    if flush_protocol_logs:
                        flush_protocol_traces_best_effort(
                            database_engine, manager=manager, scenario_id=scenario_key, target_node_id=node_id,
                        )
                    if payload.action == "recreate":
                        result = manager.recreate_node(scenario_key, runtime_device)
                    else:
                        result = manager.node_lifecycle(scenario_key, node_id, payload.action)
                    if flush_protocol_logs:
                        flush_protocol_traces_best_effort(
                            database_engine, manager=manager, scenario_id=scenario_key, target_node_id=node_id,
                        )
            except DockerManagerError as error:
                if journal_entry is not None:
                    mark_node_operation_retryable(database_engine, journal_entry.id, str(error))
                raise _conflict(error) from error
            if heartbeat.lost.is_set():
                if journal_entry is not None:
                    mark_node_operation_retryable(
                        database_engine, journal_entry.id,
                        "Lifecycle lease was lost while Docker operation was running",
                    )
                raise HTTPException(status_code=409, detail="Node lifecycle lease was lost; runtime reconciliation will inspect Docker")
            if journal_entry is not None and not already_completed:
                mark_node_operation_complete(database_engine, journal_entry.id, result)
            result = include_missing_runtime_nodes(result, record.runtime_devices(), record.status)
            service.set_status(scenario_key, result["status"])
            record_audit_event(session, scenario_key, f"scenario.node_{payload.action}", {
                "node_id": node_id, "operation_id": journal_entry.id if journal_entry else None,
            }, actor="api-client")
            return ScenarioStatusRead.model_validate(result)
    finally:
        release_operation_lease(database_engine, scenario_key, "node", owner_id)


@router.get("/{scenario_id}/node-operations", response_model=list[ScenarioNodeOperationRead])
def list_node_operations(
    scenario_id: UUID,
    session: SessionDependency,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ScenarioNodeOperationRead]:
    scenario_key = str(scenario_id)
    try:
        ScenarioService(session).get_record(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    operations = session.exec(
        select(ScenarioNodeOperation)
        .where(ScenarioNodeOperation.scenario_id == scenario_key)
        .order_by(ScenarioNodeOperation.requested_at.desc())
        .limit(limit)
    ).all()
    return [ScenarioNodeOperationRead(
        id=item.id, node_id=item.node_id, action=item.action, source=item.source,
        status=item.status, attempts=item.attempts, requested_at=item.requested_at,
        updated_at=item.updated_at, result=json.loads(item.result_json or "{}"),
        last_error=item.last_error,
    ) for item in operations]


@router.post("/{scenario_id}/captures", response_model=PacketCaptureRead, status_code=status.HTTP_202_ACCEPTED)
def start_packet_capture(
    scenario_id: UUID,
    payload: PacketCaptureStart,
    session: SessionDependency,
    manager: DockerDependency,
) -> PacketCaptureRead:
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        if record.status not in {"running", "partial", "error"}:
            raise HTTPException(status_code=409, detail="Deploy the scenario before capturing traffic")
        capture_id = str(uuid4())
        link_source = payload.link_source_node_id
        link_target = payload.link_target_node_id
        result = manager.start_packet_capture(
            scenario_key, link_source or payload.node_id, capture_id,
            zone_id=payload.zone_id, peer_node_id=link_target,
        )
        record_audit_event(session, scenario_key, "network.packet_capture_started", {
            "capture_id": capture_id, "node_id": payload.node_id, "zone_id": payload.zone_id,
            "source_node_id": link_source, "target_node_id": link_target,
            "scope": "device-link" if payload.node_id else "pair-link" if link_source else "segment-bridge",
        }, actor="api-client")
        return PacketCaptureRead.model_validate(result)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error


@router.get("/{scenario_id}/captures/{capture_id}/pcap")
def export_packet_capture(
    scenario_id: UUID,
    capture_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> Response:
    scenario_key = str(scenario_id)
    try:
        ScenarioService(session).get_record(scenario_key)
        pcap = manager.export_packet_capture(scenario_key, str(capture_id))
        record_audit_event(session, scenario_key, "network.packet_capture_exported", {
            "capture_id": str(capture_id), "bytes": len(pcap), "format": "pcap",
        }, actor="api-client")
        return Response(
            content=pcap,
            media_type="application/vnd.tcpdump.pcap",
            headers={"Content-Disposition": f'attachment; filename="anvil-capture-{capture_id}.pcap"'},
        )
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error


@router.get("/{scenario_id}/status", response_model=ScenarioStatusRead)
def scenario_status(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioStatusRead:
    scenario_key = str(scenario_id)
    service = ScenarioService(session)
    try:
        record = service.get_record(scenario_key)
        result = manager.status(scenario_key)
    except ScenarioNotFoundError as error:
        raise _not_found(error) from error
    except DockerManagerError as error:
        raise _conflict(error) from error
    result = include_missing_runtime_nodes(result, record.runtime_devices(), record.status)
    service.set_status(scenario_key, result["status"])
    record_node_runtime_transitions(session, scenario_key, result.get("containers", []), result["status"])
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
    database_bind = session.get_bind()

    def status_provider() -> dict[str, Any]:
        with Session(database_bind) as event_session:
            event_service = ScenarioService(event_session)
            record = event_service.get_record(scenario_key)
            runtime = manager.status(scenario_key)
            runtime = include_missing_runtime_nodes(runtime, record.runtime_devices(), record.status)
            docker_status = runtime["status"]
            if docker_status == "stopped" and record.status in {"deploying", "error"}:
                runtime["status"] = record.status
            elif record.status != docker_status:
                event_service.set_status(scenario_key, docker_status)
            record_node_runtime_transitions(
                event_session, scenario_key, runtime.get("containers", []), runtime["status"]
            )
            return runtime

    try:
        await stream_scenario_events(websocket, scenario_key, status_provider)
    except WebSocketDisconnect:
        return


@router.websocket("/{scenario_id}/logs")
async def scenario_log_events(
    websocket: WebSocket,
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> None:
    scenario_key = str(scenario_id)
    await websocket.accept()
    try:
        ScenarioService(session).get_record(scenario_key)
    except ScenarioNotFoundError as error:
        await websocket.send_json({"type": "error", "detail": str(error)})
        await websocket.close(code=4404)
        return

    def log_provider() -> dict[str, Any]:
        return manager.logs(scenario_key)

    try:
        await stream_scenario_logs(websocket, scenario_key, log_provider)
    except WebSocketDisconnect:
        return


@router.websocket("/{scenario_id}/participants/{node_id}/terminal")
@router.websocket("/{scenario_id}/engineering/{node_id}/terminal")
async def station_terminal(
    websocket: WebSocket,
    scenario_id: UUID,
    node_id: str,
    session: SessionDependency,
    manager: DockerDependency,
) -> None:
    """Open a bounded shell only in an opted-in participant or engineering station."""
    scenario_key = str(scenario_id)
    role = getattr(websocket.state, "effective_role", None)
    engineering = "/engineering/" in websocket.url.path
    permitted_roles = {"admin", "instructor", "designer"} if engineering else {"admin", "instructor", "participant"}
    if role not in permitted_roles:
        await websocket.close(code=4403)
        return
    try:
        record = ScenarioService(session).get_record(scenario_key)
    except ScenarioNotFoundError:
        await websocket.close(code=4404)
        return
    expected_type = "engineering-station" if engineering else "attacker-station"
    access_key = "engineering_access_enabled" if engineering else "participant_access_enabled"
    station = next((item for item in record.runtime_devices()
                    if item.get("node_id") == node_id
                    and item.get("device_type") == expected_type), None)
    if station is None or station.get("config", {}).get(access_key) is not True:
        await websocket.close(code=4403)
        return

    database_bind = session.get_bind()
    try:
        lease_acquired, lease_rejection = acquire_terminal_lease(
            database_bind, scenario_key, node_id, _terminal_worker_id,
        )
    except Exception:
        await websocket.close(code=1011, reason="Terminal capacity storage is unavailable")
        return
    if not lease_acquired:
        await websocket.close(code=4409 if lease_rejection == "station_busy" else 1013)
        return

    exec_id: str | None = None
    terminal_stream: Any | None = None
    accepted = False
    input_frames = 0
    input_bytes = 0
    input_line_breaks = 0
    terminal_started_at = asyncio.get_running_loop().time()
    try:
        exec_id, terminal_stream = await asyncio.to_thread(
            manager.open_engineering_terminal if engineering else manager.open_participant_terminal,
            scenario_key, node_id,
        )
        terminal_socket = getattr(terminal_stream, "stream", terminal_stream)
        if isinstance(terminal_socket, socket.socket):
            terminal_socket.setblocking(False)
        await websocket.accept()
        accepted = True
        event_family = "engineering" if engineering else "participant"
        record_audit_event(session, scenario_key, f"{event_family}.terminal_opened", {
            "node_id": node_id, "role": role, "user_id": getattr(getattr(websocket.state, "current_user", None), "id", None),
        }, actor="api-client")
        await websocket.send_text("Terminal conectado como UID 65534. Esta sesión no guarda los comandos.\r\n")

        async def forward_output() -> None:
            while True:
                try:
                    buffered_reader = getattr(terminal_stream, "read_prefix", None)
                    chunk = buffered_reader(4096) if callable(buffered_reader) else b""
                    if not chunk:
                        if isinstance(terminal_socket, socket.socket):
                            chunk = await asyncio.get_running_loop().sock_recv(terminal_socket, 4096)
                        else:
                            chunk = await asyncio.get_running_loop().run_in_executor(
                                None, terminal_stream.recv, 4096,
                            )
                except (socket.timeout, TimeoutError, BlockingIOError):
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                await websocket.send_bytes(chunk)

        output_task = asyncio.create_task(forward_output())
        input_task: asyncio.Task | None = None
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 3600
            next_renewal = loop.time() + TERMINAL_RENEW_SECONDS
            while not output_task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await websocket.close(code=1000, reason="Terminal session reached its one-hour limit")
                    break
                if input_task is None:
                    input_task = asyncio.create_task(websocket.receive())
                done, _ = await asyncio.wait(
                    {output_task, input_task}, timeout=min(remaining, max(0.05, next_renewal - loop.time())),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if loop.time() >= deadline:
                        await websocket.close(code=1000, reason="Terminal session reached its one-hour limit")
                        break
                    try:
                        renewed = renew_terminal_lease(
                            database_bind, scenario_key, node_id, _terminal_worker_id,
                        )
                    except Exception:
                        renewed = False
                    if not renewed:
                        await websocket.close(code=1011, reason="Terminal session lease was lost")
                        break
                    next_renewal = loop.time() + TERMINAL_RENEW_SECONDS
                    continue
                if output_task in done:
                    try:
                        output_task.result()
                    except (WebSocketDisconnect, OSError, RuntimeError):
                        pass
                    break
                message = input_task.result()
                input_task = None
                if message["type"] == "websocket.disconnect":
                    break
                payload = message.get("bytes")
                if payload is None and message.get("text") is not None:
                    payload = message["text"].encode("utf-8")
                if not payload:
                    continue
                if len(payload) > 4096:
                    await websocket.close(code=1009, reason="Terminal input exceeds 4096 bytes")
                    break
                # Audit interaction volume only. Never persist, hash, or log the
                # terminal payload because it may contain credentials or secrets.
                input_frames += 1
                input_bytes += len(payload)
                input_line_breaks += payload.count(b"\n")
                if isinstance(terminal_socket, socket.socket):
                    await asyncio.get_running_loop().sock_sendall(terminal_socket, payload)
                else:
                    await asyncio.get_running_loop().run_in_executor(
                        None, terminal_stream.sendall, payload,
                    )
        finally:
            if input_task is not None and not input_task.done():
                input_task.cancel()
            try:
                terminal_stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if input_task is not None:
                if not input_task.done():
                    input_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(input_task), timeout=0.25)
                except (asyncio.CancelledError, TimeoutError, WebSocketDisconnect, RuntimeError):
                    pass
            if not output_task.done():
                output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
            if websocket.application_state.name == "CONNECTED":
                await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass
    except DockerManagerError as error:
        if accepted:
            await websocket.send_text(f"No se pudo abrir el terminal: {error}")
            await websocket.close(code=1011)
        else:
            await websocket.close(code=1011)
    except (OSError, RuntimeError):
        if accepted:
            await websocket.close(code=1011)
    finally:
        try:
            if terminal_stream is not None:
                try:
                    terminal_stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                terminal_stream.close()
            if exec_id is not None:
                try:
                    await asyncio.to_thread(manager.close_participant_terminal, exec_id)
                except Exception:
                    pass
            if accepted:
                try:
                    record_audit_event(session, scenario_key, f"{event_family}.terminal_closed", {
                        "node_id": node_id, "role": role,
                        "input_frames": input_frames,
                        "input_bytes": input_bytes,
                        "input_line_breaks": input_line_breaks,
                        "duration_seconds": round(asyncio.get_running_loop().time() - terminal_started_at, 3),
                    }, actor="api-client")
                except Exception:
                    # Lease cleanup must continue even if audit storage fails.
                    pass
        finally:
            try:
                release_terminal_lease(database_bind, scenario_key, node_id, _terminal_worker_id)
            except Exception:
                # The lease expires automatically if SQLite is unavailable during cleanup.
                pass
