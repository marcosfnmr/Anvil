"""S7 console and semantic protocol-trace API."""

from __future__ import annotations

import re
import logging
import csv
import io
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlmodel import Session

from dependencies import DockerDependency, OptionalDockerDependency
from models.db import get_session
from models.plc import (
    DeviceValuesRead,
    HmiWriteRequest,
    ModbusDeviceValuesRead,
    ModbusWriteRead,
    ModbusWriteRequest,
    PlcWriteRead,
    PlcWriteRequest,
    ScenarioProtocolRead,
)
from models.scenario import ScenarioRecord
from models.scada import ScenarioScadaSample, ScadaWriteRequest
from models.hmi import ScenarioHmiAlarmTransition
from services.docker_manager import DockerManagerError
from services.scada_historian import (
    merge_persisted_scada_alarms,
    persist_scada_alarm_acknowledgement,
    persist_scada_alarms,
    read_scada_alarm_pending,
    persist_scada_samples,
    read_scada_aggregates,
    read_scada_samples,
)
from services.io_map import build_scenario_io_map
from services.modbus_operations import read_device_values as read_modbus_values, write_device_point as write_modbus_point
from services.plc_operations import _component_address, read_device_values as read_plc_values, write_device_point as write_plc_point
from services.scada_operations import scada_operation
from services.scenario_service import ScenarioNotFoundError, ScenarioService
from services.audit import record_audit_event, record_protocol_read_transition
from services.io_policy import enforce_firewall_policy as _enforce_firewall_policy
from services.io_policy import diagnose_reading_range
from services.io_policy import validate_component_range as _validate_component_range
from services.io_policy import validate_point_range
from services.io_policy import validate_s7_write_value as _validate_s7_write_value
from services.hmi_operations import acknowledge_hmi_alarm_state, evaluate_hmi_alarms, hmi_operation


router = APIRouter(prefix="/api/scenarios", tags=["S7 console"])
SessionDependency = Annotated[Session, Depends(get_session)]
_TRACE = re.compile(
    r"S7 (?P<operation>READ|WRITE) source=(?P<source>\S+) "
    r"(?P<area_label>DB(?P<db>\d+)|(?P<area>[MIQ])) bytes\[(?P<start>\d+):(?P<end>\d+)\] "
    r"data=(?P<raw>.*?) result=(?P<result>\w+) code=(?P<code>\d+)"
)
LOGGER = logging.getLogger("anvil.audit")


def _protocol_quality(values: list[dict[str, Any]]) -> str:
    qualities = {str(item.get("quality", "good")).lower() for item in values}
    if any(value in {"bad", "failed", "invalid", "stale", "error"} for value in qualities):
        return "degraded"
    return "good"


def _record_read(session: Session, scenario_id: str, source_node_id: str, target_node_id: str,
                 protocol: str, values: list[dict[str, Any]], *, failed: bool = False) -> None:
    record_protocol_read_transition(
        session, scenario_id, source_node_id, target_node_id, protocol,
        "failed" if failed else _protocol_quality(values), len(values),
    )


def _scada_read(scenario_id: UUID, node_id: str, path: str, session: Session, manager: Any, payload: dict | None = None) -> dict[str, Any]:
    record: ScenarioRecord | None = None
    scada: dict[str, Any] | None = None
    try:
        record, scada = _scenario_and_device(session, str(scenario_id), node_id)
        if scada["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        if path in {"snapshot", "alarms"}:
            pending = read_scada_alarm_pending(session, str(scenario_id), node_id)
            if pending:
                scada_operation(manager, str(scenario_id), node_id, "alarms/restore", {"alarms": pending})
        result = scada_operation(manager, str(scenario_id), node_id, path, payload)
        if path in {"snapshot", "alarms"}:
            alarms = result.get("alarms", [])
            ready = result.get("ready", True)
            persist_scada_alarms(session, str(scenario_id), node_id, alarms, ready=ready)
            result["alarms"] = merge_persisted_scada_alarms(
                session, str(scenario_id), node_id, alarms, ready=ready,
            )
        if path == "snapshot":
            runtime_devices = record.runtime_devices()
            selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
            targets = [item for item in runtime_devices if item["node_id"] in selected]
            io_map = build_scenario_io_map(str(scenario_id), targets)
            access = {(point.node_id, point.address): {
                key: getattr(point, key) for key in ("access", "min_value", "max_value", "unit")
                if getattr(point, key) is not None
            } for point in io_map.points}
            result["values"] = [
                diagnose_reading_range(
                    {**value, **access.get((value["node_id"], value["address"]), {"access": "read"})},
                    access.get((value["node_id"], value["address"])),
                )
                for value in result.get("values", [])
            ]
            values_by_target: dict[str, list[dict[str, Any]]] = {}
            for value in result["values"]:
                values_by_target.setdefault(str(value.get("node_id", "")), []).append(value)
            for target in targets:
                protocol = "modbus-tcp" if target["device_type"] == "modbus-tcp" else "s7"
                values = values_by_target.get(target["node_id"], [])
                _record_read(session, str(scenario_id), node_id, target["node_id"], protocol, values)
        return {"scenario_id": str(scenario_id), "node_id": node_id, **result}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        if path == "snapshot" and record is not None and scada is not None:
            selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
            for target in record.runtime_devices():
                if target["node_id"] in selected and target["device_type"] in {"plc-s7", "modbus-tcp"}:
                    protocol = "modbus-tcp" if target["device_type"] == "modbus-tcp" else "s7"
                    _record_read(session, str(scenario_id), node_id, target["node_id"], protocol, [], failed=True)
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/{scenario_id}/scada/{node_id}/snapshot")
def read_scada_snapshot(scenario_id: UUID, node_id: str, session: SessionDependency, manager: DockerDependency) -> dict[str, Any]:
    return _scada_read(scenario_id, node_id, "snapshot", session, manager)


@router.get("/{scenario_id}/scada/{node_id}/history")
def read_scada_history(scenario_id: UUID, node_id: str, session: SessionDependency, manager: OptionalDockerDependency,
                       limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        scada = next((item for item in record.runtime_devices() if item["node_id"] == node_id), None)
        if scada is None:
            raise LookupError(f"Device '{node_id}' was not found in this scenario")
        if scada["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        live = False
        if manager is not None and record.status in {"running", "partial", "error"}:
            try:
                result = scada_operation(manager, scenario_key, node_id, "history", {"limit": limit})
                persist_scada_samples(session, scenario_key, node_id, result.get("samples", []))
                live = True
            except (DockerManagerError, ValueError):
                # Return the durable buffer if the runtime is unavailable or being restarted.
                pass
        samples = read_scada_samples(session, scenario_key, node_id, limit)
        selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
        targets = [item for item in record.runtime_devices() if item["node_id"] in selected]
        io_map = build_scenario_io_map(scenario_key, targets)
        metadata = {
            (point.node_id, point.address): {
                key: getattr(point, key)
                for key in ("data_type", "unit", "min_value", "max_value")
                if getattr(point, key) is not None
            }
            for point in io_map.points
        }
        for sample in samples:
            sample["values"] = [
                diagnose_reading_range(
                    {**value, **metadata.get((value["node_id"], value["address"]), {}), "access": "read"},
                    metadata.get((value["node_id"], value["address"])),
                )
                for value in sample["values"]
            ]
        return {"scenario_id": scenario_key, "node_id": node_id, "samples": samples, "live": live}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{scenario_id}/scada/{node_id}/history/aggregate")
def read_scada_history_aggregate(
    scenario_id: UUID,
    node_id: str,
    session: SessionDependency,
    start: datetime | None = None,
    end: datetime | None = None,
    bucket_seconds: int = Query(default=5, ge=1, le=86_400),
) -> dict[str, Any]:
    """Return bounded time-bucket statistics over durable SCADA observations."""
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        scada = next((item for item in record.runtime_devices() if item["node_id"] == node_id), None)
        if scada is None:
            raise LookupError(f"Device '{node_id}' was not found in this scenario")
        if scada["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        if start is None and end is None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=10)
        elif start is None or end is None:
            raise HTTPException(status_code=422, detail="start and end must be provided together")
        if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
            raise HTTPException(status_code=422, detail="start and end must include a timezone")
        start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        duration = (end_utc - start_utc).total_seconds()
        if duration <= 0 or duration > 7 * 24 * 60 * 60:
            raise HTTPException(status_code=422, detail="time range must be positive and no longer than 7 days")
        if math.ceil(duration / bucket_seconds) > 1000:
            raise HTTPException(status_code=422, detail="choose a larger bucket_seconds for this time range")
        selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
        try:
            result = read_scada_aggregates(
                session, scenario_key, node_id, start_utc, end_utc, bucket_seconds, selected,
            )
        except ValueError as error:
            if str(error).startswith("aggregation range"):
                raise HTTPException(status_code=413, detail=str(error)) from error
            raise
        return {"scenario_id": scenario_key, "node_id": node_id, **result}
    except HTTPException:
        raise
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{scenario_id}/scada/{node_id}/history/export.csv")
def export_scada_history_csv(
    scenario_id: UUID,
    node_id: str,
    session: SessionDependency,
    limit: int = Query(default=1_000_000, ge=1, le=1_000_000),
) -> Response:
    """Download persisted SCADA point observations, including stopped scenarios."""
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        device = next((item for item in record.runtime_devices() if item["node_id"] == node_id), None)
        if device is None:
            raise LookupError(f"Device '{node_id}' was not found in this scenario")
        if device["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        rows = session.execute(
            select(ScenarioScadaSample)
            .where(ScenarioScadaSample.scenario_id == scenario_key,
                   ScenarioScadaSample.scada_node_id == node_id)
            .order_by(ScenarioScadaSample.sampled_at, ScenarioScadaSample.id)
            .limit(limit)
        ).scalars().all()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("timestamp", "target_node_id", "address", "name", "data_type", "unit", "value", "quality"))
        for row in rows:
            # Prevent spreadsheet applications from interpreting user-controlled labels as formulas.
            label_values = (row.target_node_id, row.address, row.name, row.data_type, row.unit, row.quality)
            safe_labels = tuple(
                f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value
                for value in label_values
            )
            writer.writerow((row.sampled_at, *safe_labels[:5], row.value_json, safe_labels[5]))
        filename_node = re.sub(r"[^A-Za-z0-9_.-]+", "_", node_id)[:80] or "scada"
        filename = f"anvil-scada-{scenario_key}-{filename_node}.csv"
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "X-Exported-Rows": str(len(rows))},
        )
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{scenario_id}/scada/{node_id}/alarms")
def read_scada_alarms(scenario_id: UUID, node_id: str, session: SessionDependency, manager: DockerDependency) -> dict[str, Any]:
    return _scada_read(scenario_id, node_id, "alarms", session, manager)


@router.post("/{scenario_id}/scada/{node_id}/alarms/acknowledge")
def acknowledge_scada_alarm(scenario_id: UUID, node_id: str, payload: dict[str, str], session: SessionDependency, manager: DockerDependency) -> dict[str, Any]:
    if set(payload) != {"target_node_id", "address"}:
        raise HTTPException(status_code=422, detail="target_node_id and address are required")
    try:
        _record, scada = _scenario_and_device(session, str(scenario_id), node_id)
        if scada["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
        if payload["target_node_id"] not in selected:
            raise PermissionError("Alarm target is not selected by this SCADA")
        result = scada_operation(manager, str(scenario_id), node_id, "alarms/acknowledge", payload)
        persist_scada_alarm_acknowledgement(session, str(scenario_id), node_id, {**payload, **result})
        record_audit_event(session, str(scenario_id), "scada.alarm_acknowledged", payload, actor="api-client")
        return {"scenario_id": str(scenario_id), "node_id": node_id, **result}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(session, str(scenario_id), "POST", "/api/scenarios/{scenario_id}/scada/{node_id}/alarms/acknowledge", 403)
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/{scenario_id}/scada/{node_id}/write")
def write_scada_target(
    scenario_id: UUID,
    node_id: str,
    payload: ScadaWriteRequest,
    session: SessionDependency,
    manager: DockerDependency,
) -> dict[str, Any]:
    scenario_key = str(scenario_id)
    route = "/api/scenarios/{scenario_id}/scada/{node_id}/write"
    try:
        record, scada = _scenario_and_device(session, scenario_key, node_id)
        if scada["device_type"] != "scada":
            raise ValueError("This endpoint only supports SCADA devices")
        selected = {item.strip() for item in scada["config"]["target_node_ids"].split(",") if item.strip()}
        if payload.target_node_id not in selected:
            raise PermissionError("The target device is not selected by this SCADA")
        runtime_devices = record.runtime_devices()
        target = next((item for item in runtime_devices if item["node_id"] == payload.target_node_id), None)
        if target is None:
            raise LookupError(f"Target '{payload.target_node_id}' was not found")
        if target["device_type"] not in {"plc-s7", "modbus-tcp"}:
            raise ValueError("SCADA operations support S7 and Modbus TCP targets only")
        _enforce_firewall_policy(record, target)
        io_map = build_scenario_io_map(scenario_key, [target])
        mapped = next((point for point in io_map.points if point.address == payload.address), None)
        if mapped is None:
            raise LookupError(f"Address '{payload.address}' is not in the target I/O map")
        if mapped.access != "read_write":
            raise PermissionError(f"Address '{payload.address}' is read-only")

        if target["device_type"] == "plc-s7":
            _validate_s7_write_value(mapped.data_type, payload.value)
            component = next((item for item in target["config"]["components"]
                              if _component_address(item) == payload.address
                              and item["type"] in {"valve", "motor", "pump_command", "io_channel"}), None)
            if (component is None
                    or component["type"] == "io_channel" and (component["direction"] != "output" or component["data_type"] != mapped.data_type or component.get("area", "DB") == "I")
                    or component["type"] != "io_channel" and mapped.data_type != "BOOL"):
                raise PermissionError(f"Address '{payload.address}' is not a SCADA-writable S7 output")
            _validate_component_range(component, payload.value)
            # The client write runs in SCADA so cross-zone traffic traverses
            # the configured route instead of Docker exec inside the target PLC.
        else:
            if mapped.data_type == "BOOL":
                if type(payload.value) is not bool:
                    raise ValueError("Modbus coils require boolean values")
            elif type(payload.value) is not int or not 0 <= payload.value <= 65535:
                raise ValueError("Modbus registers require integer values from 0 to 65535")
            point = next((item for item in target["config"]["points"]
                          if f"{item['type']}:{item['address']}" == payload.address), None)
            if point is None or point["type"] not in {"coil", "holding_register"}:
                raise PermissionError(f"Address '{payload.address}' is not a SCADA-writable Modbus point")
            validate_point_range(point, payload.value)
        result = scada_operation(manager, scenario_key, node_id, "write", {
            "target_node_id": target["node_id"], "address": payload.address, "value": payload.value,
        })

        record_audit_event(session, scenario_key, "operator.point_written", {
            "source": "scada", "node_id": target["node_id"], "address": payload.address,
            "value": payload.value,
        }, actor="api-client")
        return result
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(
            session, scenario_key, "POST", route, 403,
            details={"source": "scada", "source_node_id": node_id,
                     "target_node_id": payload.target_node_id, "address": payload.address},
        )
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _audit_rejection(
    session: Session,
    scenario_id: str,
    method: str,
    route: str,
    status_code: int,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        event_details = {"method": method, "route": route, "status": status_code}
        if details:
            event_details.update(details)
        record_audit_event(session, scenario_id, "api.request_rejected", {
            **event_details,
        }, actor="api-client")
    except Exception:
        LOGGER.exception("Unable to persist rejected operation audit event")


@router.get("/{scenario_id}/hmi/{node_id}/values")
def read_hmi_targets(scenario_id: UUID, node_id: str, session: SessionDependency, manager: DockerDependency) -> dict[str, Any]:
    try:
        record, hmi = _scenario_and_device(session, str(scenario_id), node_id)
        if hmi["device_type"] != "hmi":
            raise ValueError("This endpoint only supports HMI devices")
        targets = [item.strip() for item in hmi["config"].get("target_node_ids", "").split(",") if item.strip()]
        all_devices = record.runtime_devices()
        by_id = {item["node_id"]: item for item in all_devices}
        values = []
        for target_id in targets:
            target = by_id.get(target_id)
            if target is None:
                raise LookupError(f"HMI target '{target_id}' was not found")
            if target["device_type"] not in {"plc-s7", "modbus-tcp"}:
                continue
            _enforce_firewall_policy(record, target)
            io_map = build_scenario_io_map(str(scenario_id), [target])
            points = [item.model_dump(mode="json") for item in io_map.points]
            route = hmi["config"].get("network_routes", {}).get(target["node_id"])
            access = {item["address"]: {key: item[key] for key in ("access", "min_value", "max_value", "unit") if key in item}
                      for item in points}
            protocol = "modbus-tcp" if target["device_type"] == "modbus-tcp" else "s7"
            try:
                target_values = hmi_operation(manager, str(scenario_id), node_id, target, points, route)
            except DockerManagerError:
                _record_read(session, str(scenario_id), node_id, target_id, protocol, [], failed=True)
                raise
            _record_read(session, str(scenario_id), node_id, target_id, protocol, target_values)
            values.extend(
                diagnose_reading_range(
                    {**item, **access.get(item["address"], {"access": "read"})},
                    access.get(item["address"]),
                )
                for item in target_values
            )
        # HMI-owned rules consume only this cycle's actual S7/Modbus read values.
        # The runtime's internal field state is never used as an alarm source.
        hmi_alarms = evaluate_hmi_alarms(
            session, str(scenario_id), node_id,
            [item for item in hmi["config"].get("alarms", [])], values,
        )
        alarms = []
        for source_id in (item.strip() for item in hmi["config"].get("alarm_source_node_ids", "").split(",")):
            if not source_id:
                continue
            source = by_id.get(source_id)
            if source is None or source["device_type"] != "scada" or source["zone_id"] != hmi["zone_id"]:
                raise ValueError(f"HMI alarm source '{source_id}' is not an associated same-zone SCADA")
            alarm_snapshot = scada_operation(manager, str(scenario_id), source_id, "alarms")
            source_alarms = alarm_snapshot.get("alarms", [])
            ready = alarm_snapshot.get("ready", True)
            persist_scada_alarms(session, str(scenario_id), source_id, source_alarms, ready=ready)
            source_alarms = merge_persisted_scada_alarms(
                session, str(scenario_id), source_id, source_alarms, ready=ready,
            )
            alarms.extend({**alarm, "scada_node_id": source_id} for alarm in source_alarms)
        alarm_history = session.exec(select(ScenarioHmiAlarmTransition).where(
            ScenarioHmiAlarmTransition.scenario_id == str(scenario_id),
            ScenarioHmiAlarmTransition.hmi_node_id == node_id,
        ).order_by(ScenarioHmiAlarmTransition.id.desc()).limit(50)).scalars().all()
        return {"scenario_id": str(scenario_id), "node_id": node_id, "values": values,
                "alarms": [*hmi_alarms, *alarms],
                "alarm_history": [{"target_node_id": item.target_node_id, "address": item.address,
                                   "transition": item.transition, "value": json.loads(item.value_json),
                                   "occurred_at": item.occurred_at}
                                  for item in reversed(alarm_history)]}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(session, str(scenario_id), "GET", "/api/scenarios/{scenario_id}/hmi/{node_id}/values", 403)
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/{scenario_id}/hmi/{node_id}/alarms/acknowledge")
def acknowledge_hmi_alarm(scenario_id: UUID, node_id: str, payload: dict[str, str], session: SessionDependency, manager: OptionalDockerDependency) -> dict[str, Any]:
    scenario_key = str(scenario_id)
    route = "/api/scenarios/{scenario_id}/hmi/{node_id}/alarms/acknowledge"
    if set(payload) not in ({"target_node_id", "address"}, {"scada_node_id", "target_node_id", "address"}):
        raise HTTPException(status_code=422, detail="target_node_id and address are required; scada_node_id is only used for SCADA alarms")
    try:
        record, hmi = _scenario_and_device(session, scenario_key, node_id)
        if hmi["device_type"] != "hmi":
            raise ValueError("This endpoint only supports HMI devices")
        if "scada_node_id" not in payload:
            rule_exists = any(rule["target_node_id"] == payload["target_node_id"]
                              and rule["address"] == payload["address"]
                              for rule in hmi["config"].get("alarms", []))
            if not rule_exists:
                raise PermissionError("Alarm does not belong to this HMI")
            state = acknowledge_hmi_alarm_state(
                session, scenario_key, node_id, payload["target_node_id"], payload["address"],
            )
            if state is None:
                raise ValueError("HMI alarm is inactive or its last reading is stale")
            record_audit_event(session, scenario_key, "hmi.alarm_acknowledged", payload, actor="api-client")
            return {"scenario_id": scenario_key, "node_id": node_id, "target_node_id": state.target_node_id,
                    "address": state.address, "acknowledged": True,
                    "acknowledged_at": state.acknowledged_at, "hmi_owned": True}
        source_id = payload["scada_node_id"]
        allowed_sources = {item.strip() for item in hmi["config"].get("alarm_source_node_ids", "").split(",") if item.strip()}
        if source_id not in allowed_sources:
            raise PermissionError("Alarm source is not configured on this HMI")
        devices = record.runtime_devices()
        source = next((item for item in devices if item["node_id"] == source_id), None)
        if source is None or source["device_type"] != "scada" or source["zone_id"] != hmi["zone_id"]:
            raise ValueError("HMI alarm source must be a same-zone SCADA device")
        rule_exists = any(rule["target_node_id"] == payload["target_node_id"] and rule["address"] == payload["address"]
                          for rule in source["config"].get("alarms", []))
        if not rule_exists:
            raise PermissionError("Alarm does not belong to the selected SCADA")
        if manager is None:
            raise DockerManagerError("Docker is unavailable for the configured SCADA alarm source")
        result = scada_operation(manager, scenario_key, source_id, "alarms/acknowledge",
                                 {"target_node_id": payload["target_node_id"], "address": payload["address"]})
        persist_scada_alarm_acknowledgement(session, scenario_key, source_id,
                                            {"target_node_id": payload["target_node_id"],
                                             "address": payload["address"], **result})
        record_audit_event(session, scenario_key, "hmi.alarm_acknowledged", payload, actor="api-client")
        return {"scenario_id": scenario_key, "node_id": node_id, **result}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(
            session, scenario_key, "POST", route, 403,
            details={"source": "hmi", "source_node_id": node_id,
                     "target_node_id": payload["target_node_id"], "address": payload["address"]},
        )
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/{scenario_id}/hmi/{node_id}/write")
def write_hmi_target(scenario_id: UUID, node_id: str, payload: HmiWriteRequest, session: SessionDependency, manager: DockerDependency) -> dict[str, Any]:
    scenario_key = str(scenario_id)
    try:
        record, hmi = _scenario_and_device(session, scenario_key, node_id)
        if hmi["device_type"] != "hmi":
            raise ValueError("This endpoint only supports HMI devices")
        targets = [item.strip() for item in hmi["config"].get("target_node_ids", "").split(",") if item.strip()]
        if payload.target_node_id not in targets:
            raise PermissionError("The target device is not configured on this HMI")
        devices = ScenarioService(session).get_record(scenario_key).runtime_devices()
        target = next((item for item in devices if item["node_id"] == payload.target_node_id), None)
        if target is None:
            raise LookupError(f"Device '{payload.target_node_id}' was not found")
        if target["device_type"] not in {"plc-s7", "modbus-tcp"}:
            raise ValueError("HMI writes support S7 and Modbus TCP devices only")
        _enforce_firewall_policy(record, target)
        io_map = build_scenario_io_map(scenario_key, [target])
        mapped = next((item for item in io_map.points if item.address == payload.address), None)
        if mapped is None:
            raise LookupError(f"Address '{payload.address}' is not in the target I/O map")
        if mapped.access != "read_write":
            raise PermissionError(f"Address '{payload.address}' is read-only")

        if target["device_type"] == "plc-s7":
            _validate_s7_write_value(mapped.data_type, payload.value)
            component = next((item for item in target["config"]["components"]
                              if _component_address(item) == payload.address
                              and item["type"] in {"valve", "motor", "pump_command", "io_channel"}), None)
            if (component is None
                    or component["type"] == "io_channel" and (component["direction"] != "output" or component["data_type"] != mapped.data_type or component.get("area", "DB") == "I")
                    or component["type"] != "io_channel" and mapped.data_type != "BOOL"):
                raise PermissionError(f"Address '{payload.address}' is not an HMI-writable S7 output")
            _validate_component_range(component, payload.value)
            hmi_runtime = next(item for item in devices if item["node_id"] == node_id)
            route = hmi_runtime["config"].get("network_routes", {}).get(target["node_id"])
            result = hmi_operation(manager, scenario_key, node_id, target,
                                   [mapped.model_dump(mode="json")], route,
                                   address=payload.address, value=payload.value)
            record_audit_event(session, scenario_key, "operator.point_written", {
                "source": "hmi", "node_id": target["node_id"], "address": payload.address,
                "value": payload.value,
            }, actor="api-client")
            return {"scenario_id": scenario_key, **result[0]}

        if mapped.data_type == "BOOL":
            if type(payload.value) is not bool:
                raise ValueError("Modbus coils require boolean values")
        elif type(payload.value) is not int or not 0 <= payload.value <= 65535:
            raise ValueError("Modbus registers require integer values from 0 to 65535")
        point = next((item for item in target["config"]["points"]
                      if f"{item['type']}:{item['address']}" == payload.address), None)
        if point is None or point["type"] not in {"coil", "holding_register"}:
            raise PermissionError(f"Address '{payload.address}' is not an HMI-writable Modbus point")
        validate_point_range(point, payload.value)
        hmi_runtime = next(item for item in devices if item["node_id"] == node_id)
        route = hmi_runtime["config"].get("network_routes", {}).get(target["node_id"])
        result = hmi_operation(manager, scenario_key, node_id, target,
                               [mapped.model_dump(mode="json")], route,
                               address=payload.address, value=payload.value)
        record_audit_event(session, scenario_key, "operator.point_written", {
            "source": "hmi", "node_id": target["node_id"], "address": payload.address,
            "value": payload.value,
        }, actor="api-client")
        return {"scenario_id": scenario_key, **result[0]}
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(
            session, scenario_key, "POST", "/api/scenarios/{scenario_id}/hmi/{node_id}/write", 403,
            details={"source": "hmi", "source_node_id": node_id,
                     "target_node_id": payload.target_node_id, "address": payload.address},
        )
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _scenario_and_device(
    session: Session,
    scenario_id: str,
    node_id: str,
) -> tuple[ScenarioRecord, dict[str, Any]]:
    record = ScenarioService(session).get_record(scenario_id)
    runtime = next(
        (device for device in record.runtime_devices() if device["node_id"] == node_id),
        None,
    )
    if runtime is None:
        raise LookupError(f"Device '{node_id}' was not found in this scenario")
    if record.status not in {"running", "partial", "error"}:
        raise ValueError("Despliega el escenario antes de consultar los dispositivos")
    return record, runtime


@router.get(
    "/{scenario_id}/plcs/{node_id}/values",
    response_model=DeviceValuesRead,
)
def read_plc_device_values(
    scenario_id: UUID,
    node_id: str,
    session: SessionDependency,
    manager: DockerDependency,
) -> DeviceValuesRead:
    try:
        record, runtime = _scenario_and_device(session, str(scenario_id), node_id)
        if runtime["device_type"] != "plc-s7":
            raise ValueError("This endpoint only supports plc-s7 devices")
        _enforce_firewall_policy(record, runtime)
        try:
            result = read_plc_values(manager, str(scenario_id), runtime)
        except DockerManagerError:
            _record_read(session, str(scenario_id), "api-client", node_id, "s7", [], failed=True)
            raise
        _record_read(session, str(scenario_id), "api-client", node_id, "s7", result.get("values", []))
        return DeviceValuesRead.model_validate(result)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(session, str(scenario_id), "GET", "/api/scenarios/{scenario_id}/plcs/{node_id}/values", 403)
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post(
    "/{scenario_id}/plcs/{node_id}/write",
    response_model=PlcWriteRead,
)
def write_plc_value(
    scenario_id: UUID,
    node_id: str,
    payload: PlcWriteRequest,
    session: SessionDependency,
    manager: DockerDependency,
) -> PlcWriteRead:
    scenario_key = str(scenario_id)
    try:
        record, runtime = _scenario_and_device(session, scenario_key, node_id)
        if runtime["device_type"] != "plc-s7":
            raise ValueError("This endpoint only supports plc-s7 devices")
        _enforce_firewall_policy(record, runtime)
        io_map = build_scenario_io_map(scenario_key, [runtime])
        point = next((item for item in io_map.points if item.address == payload.address), None)
        if point is None:
            raise LookupError(f"Address '{payload.address}' is not in the PLC I/O map")
        if point.access != "read_write" or point.data_type != "BOOL":
            raise PermissionError(f"Address '{payload.address}' is read-only")
        component = next(
            item
            for item in runtime["config"]["components"]
            if f"DB{item['db']}.DBX{item['byte_offset']}.{item.get('bit_offset', 0)}" == payload.address
            and item["type"] in {"valve", "motor", "pump_command", "io_channel"}
        )
        result = write_plc_point(
            manager, scenario_key, runtime, component, payload.value
        )
        record_audit_event(session, scenario_key, "operator.point_written", {
            "source": "s7-console", "node_id": node_id, "address": payload.address,
            "value": payload.value,
        }, actor="api-client")
        return PlcWriteRead.model_validate(result)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(
            session, scenario_key, "POST", "/api/scenarios/{scenario_id}/plcs/{node_id}/write", 403,
            details={"source": "s7-console", "node_id": node_id,
                     "target_node_id": node_id, "address": payload.address},
        )
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

@router.get("/{scenario_id}/modbus/{node_id}/values", response_model=ModbusDeviceValuesRead)
def read_modbus_device_values(scenario_id: UUID, node_id: str, session: SessionDependency, manager: DockerDependency) -> ModbusDeviceValuesRead:
    try:
        record, runtime = _scenario_and_device(session, str(scenario_id), node_id)
        if runtime["device_type"] != "modbus-tcp":
            raise ValueError("This endpoint only supports modbus-tcp devices")
        _enforce_firewall_policy(record, runtime)
        try:
            result = read_modbus_values(manager, str(scenario_id), runtime)
        except DockerManagerError:
            _record_read(session, str(scenario_id), "api-client", node_id, "modbus-tcp", [], failed=True)
            raise
        io_map = build_scenario_io_map(str(scenario_id), [runtime])
        metadata = {point.address: point.model_dump(mode="json", exclude_none=True) for point in io_map.points}
        result["values"] = [
            diagnose_reading_range(
                {**value, **{key: metadata[value["address"]][key] for key in ("min_value", "max_value", "unit", "access") if key in metadata[value["address"]]}},
                metadata.get(value["address"]),
            )
            for value in result.get("values", [])
        ]
        _record_read(session, str(scenario_id), "api-client", node_id, "modbus-tcp", result.get("values", []))
        return ModbusDeviceValuesRead.model_validate(result)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(session, str(scenario_id), "GET", "/api/scenarios/{scenario_id}/modbus/{node_id}/values", 403)
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/{scenario_id}/modbus/{node_id}/write", response_model=ModbusWriteRead)
def write_modbus_device_value(scenario_id: UUID, node_id: str, payload: ModbusWriteRequest, session: SessionDependency, manager: DockerDependency) -> ModbusWriteRead:
    scenario_key = str(scenario_id)
    try:
        record, runtime = _scenario_and_device(session, scenario_key, node_id)
        if runtime["device_type"] != "modbus-tcp":
            raise ValueError("This endpoint only supports modbus-tcp devices")
        _enforce_firewall_policy(record, runtime)
        io_map = build_scenario_io_map(scenario_key, [runtime])
        mapped = next((item for item in io_map.points if item.address == payload.address), None)
        if mapped is None:
            raise LookupError(f"Address '{payload.address}' is not in the Modbus I/O map")
        if mapped.access != "read_write":
            raise PermissionError(f"Address '{payload.address}' is read-only")
        point = next(item for item in runtime["config"]["points"] if f"{item['type']}:{item['address']}" == payload.address)
        expected_bool = point["type"] == "coil"
        if expected_bool != (type(payload.value) is bool):
            raise ValueError("Coils require boolean values and registers require integer values")
        if not expected_bool and not 0 <= payload.value <= 65535:
            raise ValueError("Register value must be between 0 and 65535")
        validate_point_range(point, payload.value)
        result = write_modbus_point(manager, scenario_key, runtime, point, payload.value)
        record_audit_event(session, scenario_key, "operator.point_written", {
            "source": "modbus-console", "node_id": node_id, "address": payload.address,
            "value": payload.value,
        }, actor="api-client")
        return ModbusWriteRead.model_validate(result)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        _audit_rejection(
            session, scenario_key, "POST", "/api/scenarios/{scenario_id}/modbus/{node_id}/write", 403,
            details={"source": "modbus-console", "node_id": node_id,
                     "target_node_id": node_id, "address": payload.address},
        )
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/{scenario_id}/protocol", response_model=ScenarioProtocolRead)
def scenario_protocol(
    scenario_id: UUID,
    session: SessionDependency,
    manager: DockerDependency,
) -> ScenarioProtocolRead:
    scenario_key = str(scenario_id)
    try:
        record = ScenarioService(session).get_record(scenario_key)
        node_ids = {device["node_id"] for device in record.runtime_devices()}
        logs = manager.logs(scenario_key, tail=100)
    except ScenarioNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DockerManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    operations: list[dict[str, Any]] = []
    for container in logs["containers"]:
        node_id = container["node_id"]
        if node_id not in node_ids:
            continue
        for line in container["lines"]:
            match = _TRACE.search(line)
            if not match:
                continue
            start = int(match["start"])
            area = "DB" if match["db"] else match["area"]
            db = int(match["db"]) if area == "DB" else None
            address = f"DB{db}.BYTE{start}" if area == "DB" else f"{area}.BYTE{start}"
            operations.append(
                {
                    "timestamp": line.split(" ", 1)[0],
                    "operation": match["operation"].lower(),
                    "source": match["source"],
                    "node_id": node_id,
                    "area": area,
                    "db": db,
                    "address": address,
                    "byte_count": int(match["end"]) - start,
                    "raw_hex": "" if match["raw"] == "-" else match["raw"],
                    "result": match["result"],
                }
            )
    return ScenarioProtocolRead(scenario_id=scenario_key, operations=operations)
