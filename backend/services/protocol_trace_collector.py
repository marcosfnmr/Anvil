"""Persist selected semantic S7 and Modbus operations observed in runtime logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from models.audit import ScenarioProtocolTraceCursor
from models.scenario import ScenarioRecord
from services.audit import record_audit_event
from services.docker_manager import DockerManager
from services.process_trace_collector import ingest_field_snapshot


LOGGER = logging.getLogger("anvil.protocol_trace_collector")
_TRACE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) "
    r".*?anvil\.modbus: Modbus request source=(?P<source>\S+) "
    r"unit_id=(?P<unit_id>\d+) function=(?P<function>\d+) "
    r"operation=(?P<operation>[a-z0-9_]+) space=(?P<space>[a-z0-9_]+) "
    r"address=(?P<address>\d+|\?) count=(?P<count>\d+) "
    r"result=(?P<result>ok|exception code=\d+|disconnect|protocol_error(?: response_function=\d+)?)$"
)
_AUDIT_OPERATIONS = {"write_coil", "write_holding_register", "write_coils",
                     "write_holding_registers", "mask_write_register", "read_write_registers"}
_S7_TRACE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) "
    r".*?anvil\.plc\.protocol: S7 audit operation=(?P<operation>read|write) source=(?P<source>\S+) "
    r"area=(?P<area>DB|M|I|Q) db=(?P<db>\d+|-) byte_offset=(?P<byte_offset>\d+) "
    r"byte_count=(?P<byte_count>\d+) result=(?P<result>ok|error) result_code=(?P<result_code>-?\d+)$"
)


def _parse_line(line: str) -> dict[str, Any] | None:
    match = _TRACE.match(line)
    if not match:
        return None
    item = match.groupdict()
    try:
        timestamp = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        return None
    item["parsed_timestamp"] = timestamp.astimezone(timezone.utc)
    item["fingerprint"] = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()
    return item


def _parse_s7_line(line: str) -> dict[str, Any] | None:
    match = _S7_TRACE.match(line)
    if not match:
        return None
    item = match.groupdict()
    try:
        timestamp = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        return None
    item["parsed_timestamp"] = timestamp.astimezone(timezone.utc)
    item["fingerprint"] = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()
    return item


def _is_audit_queue_event(line: str, protocol: str) -> bool:
    if protocol == "modbus-interception":
        return _parse_interception_line(line) is not None
    trace = _parse_s7_line(line) if protocol == "s7" else _parse_line(line)
    if trace is None:
        return False
    if protocol == "s7":
        return trace["operation"] == "write" or trace["result"] != "ok"
    return trace["operation"] in _AUDIT_OPERATIONS or trace["result"] != "ok"


def _parse_interception_line(line: str) -> dict[str, Any] | None:
    try:
        item = json.loads(line)
        legacy_fields = {
            "event", "route_id", "rule_ids", "count", "exercise_id", "event_id",
            "unit_id", "function_code", "address", "timestamp",
        }
        if not isinstance(item, dict):
            return None
        # Existing queued read matches predate direction metadata. They are
        # unambiguously response rewrites, so continue ingesting them.
        if set(item) == legacy_fields:
            item["direction"] = "response"
        if (set(item) != legacy_fields | {"direction"}
                or item["event"] not in {"modbus_response_rewritten", "modbus_request_rewritten"}
                or not isinstance(item["route_id"], str) or not item["route_id"]
                or not isinstance(item["rule_ids"], list) or not item["rule_ids"]
                or len(item["rule_ids"]) > 32
                or any(not isinstance(value, str) or not value for value in item["rule_ids"])
                or type(item["count"]) is not int or item["count"] != len(item["rule_ids"])
                or (item["exercise_id"] is None) != (item["event_id"] is None)
                or any(item[key] is not None and (not isinstance(item[key], str) or not item[key])
                       for key in ("exercise_id", "event_id"))
                or type(item["unit_id"]) is not int or not 0 <= item["unit_id"] <= 255
                or type(item["function_code"]) is not int or item["function_code"] not in {3, 4, 5, 6, 15, 16, 22, 23}
                or item["direction"] not in {"request", "response"}
                or (item["function_code"] in {5, 6, 15, 16, 22}) != (item["direction"] == "request")
                or (item["event"] == "modbus_request_rewritten") != (item["direction"] == "request")
                or type(item["address"]) is not int or not 0 <= item["address"] <= 65535):
            return None
        timestamp = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        item["parsed_timestamp"] = timestamp.astimezone(timezone.utc)
        item["fingerprint"] = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()
        return item
    except (TypeError, ValueError, AttributeError):
        return None


def ingest_container_lines(session: Session, scenario_id: str, node_id: str,
                           lines: list[str], protocol: str = "modbus", *,
                           dropped_events: int | None = None,
                           queue_events: list[dict[str, Any]] | None = None) -> int:
    """Idempotently append writes/failures from a bounded ordered log snapshot."""
    cursor = session.get(ScenarioProtocolTraceCursor, (scenario_id, node_id))
    if cursor is None:
        cursor = ScenarioProtocolTraceCursor(scenario_id=scenario_id, node_id=node_id)
    last_timestamp = cursor.last_timestamp
    same_timestamp_hashes = set(json.loads(cursor.same_timestamp_hashes_json or "[]"))
    inserted = 0
    queue_ids = [item.get("id") for item in (queue_events or [])
                 if type(item.get("id")) is int]
    if queue_ids:
        lines = [item["line"] for item in queue_events
                 if type(item.get("id")) is int and item["id"] > cursor.last_queue_id
                 and isinstance(item.get("line"), str)]
    if protocol == "modbus-interception":
        for line in lines:
            trace = _parse_interception_line(line)
            if trace is None:
                continue
            cursor.last_timestamp = max(cursor.last_timestamp, trace["parsed_timestamp"].isoformat())
            record_audit_event(session, scenario_id, "security.modbus_interception_match", {
                "source": "modbus-interception-gateway", "node_id": node_id,
                "protocol": "modbus-tcp", "route_id": trace["route_id"],
                "exercise_id": trace["exercise_id"], "event_id": trace["event_id"],
                "rule_id": trace["rule_ids"][0] if len(trace["rule_ids"]) == 1 else None,
                "unit_id": trace["unit_id"], "function_code": trace["function_code"],
                "address": trace["address"], "count": trace["count"],
                "direction": trace["direction"],
                "trace_id": trace["fingerprint"],
            }, actor="modbus-interception-gateway", timestamp=trace["parsed_timestamp"], commit=False)
            inserted += 1
        if dropped_events is not None and dropped_events > cursor.dropped_events_seen:
            lost = dropped_events - cursor.dropped_events_seen
            record_audit_event(session, scenario_id, "protocol.audit_queue_overflow", {
                "node_id": node_id, "protocol": protocol, "dropped_events": lost,
                "total_dropped_events": dropped_events,
            }, actor="protocol-audit-collector", commit=False)
            cursor.dropped_events_seen = dropped_events
        if queue_ids:
            cursor.last_queue_id = max(cursor.last_queue_id, max(queue_ids))
        session.add(cursor)
        session.commit()
        return inserted
    for line in lines:
        trace = _parse_s7_line(line) if protocol == "s7" else _parse_line(line)
        if trace is None:
            continue
        timestamp = trace["timestamp"]
        fingerprint = trace["fingerprint"]
        if timestamp < last_timestamp:
            continue
        if timestamp == last_timestamp and fingerprint in same_timestamp_hashes:
            continue
        if timestamp > last_timestamp:
            last_timestamp = timestamp
            same_timestamp_hashes = set()
        same_timestamp_hashes.add(fingerprint)

        operation = trace["operation"]
        result = trace["result"]
        if operation not in _AUDIT_OPERATIONS and result == "ok":
            if not (protocol == "s7" and operation == "write"):
                continue
        if protocol == "s7":
            details = {
                "node_id": node_id, "protocol": "s7", "source": trace["source"],
                "operation": operation, "area": trace["area"],
                "db": int(trace["db"]) if trace["db"] != "-" else None,
                "byte_offset": int(trace["byte_offset"]),
                "byte_count": int(trace["byte_count"]), "result": result,
                "result_code": int(trace["result_code"]), "trace_id": fingerprint,
            }
            record_audit_event(session, scenario_id, "protocol.s7_operation", details,
                               actor="s7-client", timestamp=trace["parsed_timestamp"], commit=False)
            inserted += 1
            continue
        details = {
            "node_id": node_id,
            "source": trace["source"],
            "unit_id": int(trace["unit_id"]),
            "function_code": int(trace["function"]),
            "operation": operation,
            "space": trace["space"],
            "address": int(trace["address"]) if trace["address"] != "?" else None,
            "count": int(trace["count"]),
            "result": result,
            "trace_id": fingerprint,
        }
        record_audit_event(session, scenario_id, "protocol.modbus_operation", details,
                           actor="modbus-client", timestamp=trace["parsed_timestamp"], commit=False)
        inserted += 1
    cursor.last_timestamp = last_timestamp
    # Multiple decoded requests can share a Docker timestamp. Keep a bounded
    # set for that timestamp while the monotonic cursor suppresses older lines.
    cursor.same_timestamp_hashes_json = json.dumps(sorted(same_timestamp_hashes)[-256:])
    if dropped_events is not None and dropped_events > cursor.dropped_events_seen:
        lost = dropped_events - cursor.dropped_events_seen
        record_audit_event(
            session, scenario_id, "protocol.audit_queue_overflow",
            {"node_id": node_id, "protocol": protocol, "dropped_events": lost,
             "total_dropped_events": dropped_events},
            actor="protocol-audit-collector", commit=False,
        )
        cursor.dropped_events_seen = dropped_events
    if queue_ids:
        cursor.last_queue_id = max(cursor.last_queue_id, max(queue_ids))
    session.add(cursor)
    session.commit()
    return inserted


def collect_protocol_traces_once(database_engine: Any, manager: Any | None = None,
                                 scenario_id: str | None = None,
                                 target_node_id: str | None = None) -> int:
    """Collect retained scenario S7/Modbus TCP/RTU logs and append protocol events."""
    query = select(ScenarioRecord).where(
        ScenarioRecord.status.in_(
            ("stopped", "deploying", "running", "paused", "partial", "error")
        )
    )
    if scenario_id is not None:
        query = query.where(ScenarioRecord.id == scenario_id)
    with Session(database_engine) as session:
        # Docker retains logs for exited/paused containers. Scan every persisted
        # lifecycle state so the last protocol events are collected after a
        # stop, pause, partial deploy, or failed deploy too. This only reads
        # existing container logs; it never starts a stopped scenario.
        scenarios = session.exec(query).all()
        scenario_devices = [(item.id, item.runtime_devices()) for item in scenarios]
    if not scenario_devices:
        return 0
    owns_manager = manager is None
    docker_manager = manager or DockerManager()
    inserted = 0
    try:
        for scenario_id, devices in scenario_devices:
            protocols = {
                item["node_id"]: ("s7" if item.get("device_type") == "plc-s7" else "modbus")
                for item in devices
                if item.get("device_type") == "plc-s7"
                or item.get("device_type") == "modbus-tcp"
            }
            protocols.update({
                item["node_id"]: "modbus-interception"
                for item in devices if item.get("device_type") == "tcp-gateway"
                and item.get("config", {}).get("inspect_protocol") == "modbus-tcp"
            })
            if target_node_id is not None:
                protocols = {key: value for key, value in protocols.items() if key == target_node_id}
                if not protocols:
                    continue
            field_device = next((item for item in devices
                                 if item.get("device_type") == "field-runtime"), None)
            if not protocols and field_device is None:
                continue
            if protocols:
                queue_nodes: set[str] = set()
                queue_method_available = callable(getattr(docker_manager, "protocol_audit_queue_nodes", None))
                if queue_method_available:
                    try:
                        reported_queue_nodes = docker_manager.protocol_audit_queue_nodes(scenario_id)
                        if isinstance(reported_queue_nodes, (set, list, tuple)):
                            queue_nodes = set(reported_queue_nodes) & set(protocols)
                    except Exception as error:
                        LOGGER.debug("Protocol audit queue discovery failed for %s: %s", scenario_id, error)
                queue_nodes_with_fallback = set(queue_nodes)
                for node_id in sorted(queue_nodes):
                    try:
                        batch = docker_manager.read_protocol_audit_queue(scenario_id, node_id, limit=1000)
                        if not isinstance(batch, dict):
                            continue
                        queue_events = [item for item in batch.get("events", [])
                                        if isinstance(item, dict)]
                        lines = [item["line"] for item in queue_events
                                 if isinstance(item.get("line"), str)]
                        if len(lines) != len(queue_events) or any(
                                not _is_audit_queue_event(line, protocols[node_id]) for line in lines):
                            raise ValueError("protocol audit queue contains an unrecognized event")
                        with Session(database_engine) as session:
                            inserted += ingest_container_lines(
                                session, scenario_id, node_id, lines, protocol=protocols[node_id],
                                dropped_events=int(batch.get("dropped_events", 0)),
                                queue_events=queue_events,
                            )
                        cursor = batch.get("cursor")
                        if type(cursor) is int:
                            docker_manager.acknowledge_protocol_audit_queue(scenario_id, node_id, cursor)
                    except Exception as error:
                        # Keep the queue node out of the Docker log fallback: queue
                        # delivery is the authoritative path and its lines also
                        # appear in logs, which could bypass the durable cursor.
                        LOGGER.debug("Protocol audit queue collection failed for %s/%s: %s",
                                     scenario_id, node_id, error)
                legacy_protocols = {node_id: protocol for node_id, protocol in protocols.items()
                                    if node_id not in queue_nodes_with_fallback}
                if not legacy_protocols:
                    legacy_protocols = {}
                since_by_node: dict[str, float] = {}
                with Session(database_engine) as session:
                    for node_id in legacy_protocols:
                        cursor = session.get(ScenarioProtocolTraceCursor, (scenario_id, node_id))
                        if cursor is None or not cursor.last_timestamp:
                            continue
                        try:
                            timestamp = datetime.fromisoformat(
                                cursor.last_timestamp.replace("Z", "+00:00")
                            ).timestamp()
                        except ValueError:
                            continue
                        # Docker's since filter has second-level edge cases across
                        # engines. Overlap one second and let the persisted exact
                        # timestamp/fingerprint cursor remove repeated lines.
                        since_by_node[node_id] = max(1.0, timestamp - 1.0)
                try:
                    if not legacy_protocols:
                        snapshot = {"containers": []}
                    else:
                        snapshot = docker_manager.logs(
                            scenario_id, tail=1000, since_by_node=since_by_node
                        )
                    for container in snapshot.get("containers", []):
                        node_id = container.get("node_id")
                        if node_id not in legacy_protocols:
                            continue
                        try:
                            with Session(database_engine) as session:
                                inserted += ingest_container_lines(
                                    session, scenario_id, node_id, container.get("lines", []),
                                    protocol=legacy_protocols[node_id],
                                )
                        except Exception:
                            LOGGER.exception("Could not persist protocol trace for %s/%s", scenario_id, node_id)
                except Exception as error:
                    LOGGER.debug("Protocol trace collection failed for %s: %s", scenario_id, error)
            if field_device is not None:
                try:
                    from services.field_operations import field_operation

                    field_snapshot = field_operation(docker_manager, scenario_id)
                    with Session(database_engine) as session:
                        inserted += ingest_field_snapshot(
                            session, scenario_id,
                            field_device.get("config", {}).get("objects", []),
                            field_snapshot,
                            field_device.get("config", {}).get("connections", []),
                        )
                except Exception as error:
                    LOGGER.debug("Field process audit collection failed for %s: %s", scenario_id, error)
    finally:
        if owns_manager:
            docker_manager.client.close()
    return inserted


def flush_protocol_traces_best_effort(database_engine: Any, manager: Any | None = None,
                                      scenario_id: str | None = None,
                                      target_node_id: str | None = None) -> int:
    """Try to persist logs before lifecycle cleanup without blocking that cleanup."""
    try:
        return collect_protocol_traces_once(
            database_engine, manager=manager, scenario_id=scenario_id,
            target_node_id=target_node_id,
        )
    except Exception:
        LOGGER.exception("Protocol trace flush failed before lifecycle cleanup")
        return 0


async def protocol_trace_scheduler(stop_event: asyncio.Event, database_engine: Any,
                                   interval_seconds: float | None = None) -> None:
    interval = interval_seconds or float(os.environ.get("ANVIL_PROTOCOL_TRACE_INTERVAL_SECONDS", 2.0))
    interval = max(1.0, min(interval, 60.0))
    while not stop_event.is_set():
        try:
            await run_in_threadpool(collect_protocol_traces_once, database_engine)
        except Exception:
            LOGGER.exception("Protocol trace collection cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
