"""Exercise protocol-originated S7/Modbus traffic and score its impact."""
from __future__ import annotations

import json
import os
import re
import socket
import time
from datetime import timedelta
from ipaddress import ip_network

import docker
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from main import create_app
from models.db import get_session
from models.exercise import ExerciseRun
from models.audit import ScenarioAuditEvent
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager
from services.exercise_runtime import run_due_exercises
from services.field_operations import field_operation
from services.plc_operations import read_device_values
from services.modbus_operations import read_device_values as read_modbus_values
from models.scenario import ScenarioRecord


def free_subnet(client) -> str:
    used = [ip_network(entry["Subnet"]) for network in client.networks.list()
            for entry in ((network.attrs.get("IPAM") or {}).get("Config") or []) if entry.get("Subnet")]
    for third in range(20, 240):
        candidate = ip_network(f"10.246.{third}.0/24")
        if all(not candidate.overlaps(item) for item in used):
            return str(candidate)
    raise RuntimeError("No free subnet in the exercise smoke range")


def main() -> None:
    if not os.environ.get("ANVIL_PLC_IMAGE"):
        raise RuntimeError("Set ANVIL_PLC_IMAGE to a locally available S7 image")
    if not os.environ.get("ANVIL_SCADA_IMAGE"):
        raise RuntimeError("Set ANVIL_SCADA_IMAGE to a locally available SCADA runtime image")
    if not os.environ.get("ANVIL_MODBUS_IMAGE"):
        raise RuntimeError("Set ANVIL_MODBUS_IMAGE to a locally available Modbus runtime image")
    client = docker.from_env()
    manager = DockerManager(client=client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = create_app(initialize_database=False)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_docker_manager] = lambda: manager
    scenario_id = None
    try:
        with TestClient(app) as api:
            subnet = free_subnet(client)
            control_zone, operations_zone = ip_network(subnet).subnets(new_prefix=25)
            created = api.post("/api/scenarios", json={
                "name": "Exercise S7 and route smoke", "network": {"subnet": subnet, "zones": [
                    {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": str(control_zone)},
                    {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": str(operations_zone)},
                ]},
                "field_objects": [{"object_id": "tank", "name": "Tank", "type": "tank",
                                   "capacity_liters": 1000, "initial_level_liters": 500,
                                   "outlet_flow_lpm": 0, "zone_id": "control"},
                                  {"object_id": "pump", "name": "Pump", "type": "pump",
                                   "flow_min": 0, "flow_max": 100, "zone_id": "control"},
                                  {"object_id": "valve", "name": "Independent field valve", "type": "valve",
                                   "initial_state": False, "flow_rate_lpm": 100, "zone_id": "control"}],
                "signal_connections": [
                    {"connection_id": "participant-s7-valve", "source": {"kind": "device", "node_id": "plc", "address": "DB1.DBX0.0"},
                     "target": {"kind": "field", "object_id": "valve", "port": "command"}},
                    {"connection_id": "participant-modbus-pump", "source": {"kind": "device", "node_id": "modbus", "address": "coil:0"},
                     "target": {"kind": "field", "object_id": "pump", "port": "command"}},
                ],
                "process_connections": [
                    {"connection_id": "pump-to-valve", "source": {"object_id": "pump", "port": "flow"},
                     "target": {"object_id": "valve", "port": "process_in"}},
                    {"connection_id": "valve-to-tank", "source": {"object_id": "valve", "port": "process_out"},
                     "target": {"object_id": "tank", "port": "inlet"}},
                ],
                "devices": [{
                    "node_id": "plc", "device_type": "plc-s7", "name": "PLC exercise target",
                    "ip": str(control_zone.network_address + 10), "zone_id": "control",
                    "config": {"components": [{"type": "valve", "name": "Válvula"}]},
                }, {
                    "node_id": "scada", "device_type": "scada", "name": "SCADA route source",
                    "ip": str(operations_zone.network_address + 10), "zone_id": "operations",
                    "config": {"target_node_ids": "plc", "poll_interval_ms": 500},
                }, {
                    "node_id": "participant", "device_type": "attacker-station", "name": "Participant station",
                    "ip": str(operations_zone.network_address + 11), "zone_id": "operations",
                    "config": {"role": "attacker", "summary": "Protocol attack test",
                               "participant_access_enabled": True},
                }, {
                    "node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus exercise target",
                    "ip": str(control_zone.network_address + 11), "zone_id": "control",
                    "config": {"unit_id": 1, "points": [
                        {"type": "coil", "name": "Pump command", "address": 0, "initial_value": False},
                        {"type": "holding_register", "name": "Speed setpoint", "address": 0, "initial_value": 0},
                        {"type": "holding_register", "name": "Auxiliary setpoint", "address": 1, "initial_value": 0},
                        {"type": "holding_register", "name": "Unmodified setpoint", "address": 2, "initial_value": 0},
                    ]},
                }],
                "network_routes": [{"route_id": "scada-to-plc", "source_node_id": "scada",
                                    "target_node_id": "plc", "protocol": "s7", "enabled": True},
                                    {"route_id": "participant-to-plc", "source_node_id": "participant",
                                     "target_node_id": "plc", "protocol": "tcp", "tcp_port": 102,
                                     "enabled": True},
                                    {"route_id": "participant-to-modbus", "source_node_id": "participant",
                                     "target_node_id": "modbus", "protocol": "tcp", "tcp_port": 502,
                                     "enabled": True}],
            })
            if created.status_code != 201:
                raise AssertionError(f"scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200:
                raise AssertionError(f"deployment failed: {deployed.status_code} {deployed.text}")
            time.sleep(2)
            with Session(engine) as session:
                scenario = session.get(ScenarioRecord, scenario_id)
                runtimes = {item["node_id"]: item for item in scenario.runtime_devices()}
            route_endpoint = runtimes["scada"]["config"]["network_routes"]["plc"]
            participant_endpoint = runtimes["participant"]["config"]["tcp_routes"]["participant-to-plc"]
            participant_modbus_endpoint = runtimes["participant"]["config"]["tcp_routes"]["participant-to-modbus"]

            def probe_route():
                return manager.probe_network_route(
                    scenario_id,
                    source_node_id="scada",
                    host=route_endpoint["host"],
                    port=route_endpoint["port"],
                    protocol="s7",
                    target_config=runtimes["plc"]["config"],
                )

            def wait_for_route_status(expected: str, timeout: float = 12):
                deadline = time.monotonic() + timeout
                result = probe_route()
                while result["status"] != expected and time.monotonic() < deadline:
                    time.sleep(0.25)
                    result = probe_route()
                return result

            route_before = wait_for_route_status("reachable", timeout=15)
            if route_before["status"] != "reachable":
                raise AssertionError(f"cross-zone S7 route did not become reachable: {route_before}")
            participant_route = manager.probe_network_route(
                scenario_id, source_node_id="participant", host=participant_endpoint["host"],
                port=participant_endpoint["port"], protocol="tcp", target_config=runtimes["plc"]["config"],
            )
            if participant_route["status"] != "reachable":
                raise AssertionError(f"participant protocol route did not become reachable: {participant_route}")
            participant_modbus_route = manager.probe_network_route(
                scenario_id, source_node_id="participant", host=participant_modbus_endpoint["host"],
                port=participant_modbus_endpoint["port"], protocol="tcp", target_config=runtimes["modbus"]["config"],
            )
            if participant_modbus_route["status"] != "reachable":
                raise AssertionError(f"participant Modbus route did not become reachable: {participant_modbus_route}")
            terminal_exec, terminal = manager.open_participant_terminal(scenario_id, "participant")
            terminal.settimeout(1.0)
            try:
                greeting = bytearray()
                greeting_deadline = time.monotonic() + 5
                while b"anvil$" not in greeting and time.monotonic() < greeting_deadline:
                    try:
                        greeting.extend(terminal.recv(16_384))
                    except socket.timeout:
                        continue
                if b"anvil$" not in greeting:
                    raise AssertionError(f"participant shell did not become ready: {greeting!r}")
                terminal.sendall(b"stty -echo\n")
                prompt = bytearray()
                prompt_deadline = time.monotonic() + 3
                while b"anvil$" not in prompt and time.monotonic() < prompt_deadline:
                    try:
                        prompt.extend(terminal.recv(16_384))
                    except socket.timeout:
                        continue

                def participant_holding_read(label: str, count: int = 1) -> int | tuple[int, ...]:
                    marker = f"ANVIL_{label}"
                    client_host = json.dumps(participant_modbus_endpoint["host"])
                    code = (
                        "from pymodbus.client import ModbusTcpClient; "
                        f"c=ModbusTcpClient({client_host},port={participant_modbus_endpoint['port']},timeout=3); "
                        "assert c.connect(), 'connect failed'; "
                        f"r=c.read_holding_registers(address=0,count={count},device_id=1); "
                        "assert r is not None and not r.isError(), str(r); "
                        f"print('{marker}='+','.join(str(value) for value in r.registers)); c.close()"
                    )
                    terminal.sendall(f"python -c {json.dumps(code)}\n".encode())
                    output = bytearray()
                    deadline = time.monotonic() + 12
                    result_pattern = rb"ANVIL_" + label.encode() + rb"=([\d,]+)"
                    while re.search(result_pattern, output) is None and time.monotonic() < deadline:
                        try:
                            output.extend(terminal.recv(16_384))
                        except socket.timeout:
                            continue
                    match = re.search(result_pattern, output)
                    if match is None:
                        raise AssertionError(
                            f"participant Modbus read did not return its marker: "
                            f"{output.decode(errors='replace')[-1000:]}"
                        )
                    values = tuple(int(value) for value in match.group(1).split(b","))
                    return values[0] if count == 1 else values

                def participant_holding_write_multiple(label: str, values: list[int]) -> int:
                    marker = f"ANVIL_{label}"
                    client_host = json.dumps(participant_modbus_endpoint["host"])
                    code = (
                        "from pymodbus.client import ModbusTcpClient; "
                        f"c=ModbusTcpClient({client_host},port={participant_modbus_endpoint['port']},timeout=3); "
                        "assert c.connect(), 'connect failed'; "
                        f"r=c.write_registers(address=0,values={values!r},device_id=1); "
                        "assert r is not None and not r.isError(), str(r); "
                        f"print('{marker}=1'); c.close()"
                    )
                    terminal.sendall(f"python -c {json.dumps(code)}\n".encode())
                    output = bytearray()
                    deadline = time.monotonic() + 12
                    result_pattern = rb"ANVIL_" + label.encode() + rb"=(\d+)"
                    while re.search(result_pattern, output) is None and time.monotonic() < deadline:
                        try:
                            output.extend(terminal.recv(16_384))
                        except socket.timeout:
                            continue
                    match = re.search(result_pattern, output)
                    if match is None:
                        raise AssertionError(
                            f"participant Modbus FC16 write did not return its marker: "
                            f"{output.decode(errors='replace')[-1000:]}"
                        )
                    return int(match.group(1))

                def participant_holding_write(label: str, value: int) -> int:
                    marker = f"ANVIL_{label}"
                    client_host = json.dumps(participant_modbus_endpoint["host"])
                    code = (
                        "from pymodbus.client import ModbusTcpClient; "
                        f"c=ModbusTcpClient({client_host},port={participant_modbus_endpoint['port']},timeout=3); "
                        "assert c.connect(), 'connect failed'; "
                        f"r=c.write_register(address=0,value={value},device_id=1); "
                        "assert r is not None and not r.isError(), str(r); "
                        f"print('{marker}=1'); c.close()"
                    )
                    terminal.sendall(f"python -c {json.dumps(code)}\n".encode())
                    output = bytearray()
                    deadline = time.monotonic() + 12
                    result_pattern = rb"ANVIL_" + label.encode() + rb"=(\d+)"
                    while re.search(result_pattern, output) is None and time.monotonic() < deadline:
                        try:
                            output.extend(terminal.recv(16_384))
                        except socket.timeout:
                            continue
                    match = re.search(result_pattern, output)
                    if match is None:
                        raise AssertionError(
                            f"participant Modbus write did not return its marker: "
                            f"{output.decode(errors='replace')[-1000:]}"
                        )
                    return int(match.group(1))

                original_via_route = participant_holding_read("BEFORE")
                if original_via_route != 0:
                    raise AssertionError(f"unexpected original Modbus value: {original_via_route}")
                interception_definition = {
                    "name": "Read a rewritten holding register through the participant route",
                    "events": [
                        {"event_id": "rewrite-window", "at_seconds": 0,
                         "kind": "modbus_interception", "route_id": "participant-to-modbus",
                         "action": "set", "rules": [{
                             "rule_id": "smoke-register", "unit_id": 1, "function_code": 3,
                             "address": 0, "replacement_value": 65000,
                         }]},
                        {"event_id": "restore-window", "at_seconds": 10,
                         "kind": "modbus_interception", "route_id": "participant-to-modbus",
                         "action": "clear"},
                    ],
                    "objectives": [{
                        "objective_id": "interception-observed",
                        "description": "A read response matched the scheduled rewrite",
                        "kind": "audit_event",
                        "event_type": "security.modbus_interception_match",
                        "details_match": {"event_id": "rewrite-window",
                                          "rule_id": "smoke-register",
                                          "route_id": "participant-to-modbus"},
                    }],
                }
                interception_created = api.post(
                    f"/api/scenarios/{scenario_id}/exercises", json=interception_definition,
                )
                if interception_created.status_code != 201:
                    raise AssertionError(
                        f"interception exercise creation failed: {interception_created.status_code} "
                        f"{interception_created.text}"
                    )
                interception_id = interception_created.json()["id"]
                interception_started = api.post(
                    f"/api/scenarios/{scenario_id}/exercises/{interception_id}/start",
                )
                if interception_started.status_code != 200:
                    raise AssertionError(
                        f"interception exercise start failed: {interception_started.status_code} "
                        f"{interception_started.text}"
                    )
                with Session(engine) as session:
                    interception_run = session.get(ExerciseRun, interception_id)
                    run_due_exercises(session, manager,
                                      now=interception_run.started_at + timedelta(seconds=1))
                    session.refresh(interception_run)
                    if interception_run.next_event_index != 1:
                        raise AssertionError("scheduled rewrite event was not applied")
                altered_via_route = participant_holding_read("DURING")
                if altered_via_route != 65000:
                    raise AssertionError(f"participant did not read the scheduled rewrite: {altered_via_route}")
                with Session(engine) as session:
                    interception_run = session.get(ExerciseRun, interception_id)
                    run_due_exercises(session, manager,
                                      now=interception_run.started_at + timedelta(seconds=11))
                    session.refresh(interception_run)
                    if (interception_run.status != "completed"
                            or interception_run.passed is not True
                            or interception_run.score != 100):
                        raise AssertionError(
                            f"interception objective did not pass: {interception_run.objective_results_json}"
                        )
                restored_via_route = participant_holding_read("AFTER")
                if restored_via_route != original_via_route:
                    raise AssertionError(f"participant read was not restored: {restored_via_route}")

                write_interception_definition = {
                    "name": "Rewrite Modbus FC06 and FC16 setpoints through the participant route",
                    "events": [
                        {"event_id": "rewrite-write", "at_seconds": 0,
                         "kind": "modbus_interception", "route_id": "participant-to-modbus",
                         "action": "set", "rules": [{
                             "rule_id": "smoke-write-register", "unit_id": 1, "function_code": 6,
                             "address": 0, "replacement_value": 52000,
                         }, {
                             "rule_id": "smoke-multiple-register-first", "unit_id": 1, "function_code": 16,
                             "address": 0, "replacement_value": 51000,
                         }, {
                             "rule_id": "smoke-multiple-register-second", "unit_id": 1, "function_code": 16,
                             "address": 1, "replacement_value": 53000,
                         }]},
                        {"event_id": "clear-write", "at_seconds": 10,
                         "kind": "modbus_interception", "route_id": "participant-to-modbus",
                         "action": "clear"},
                    ],
                    "objectives": [{
                        "objective_id": "write-interception-observed",
                        "description": "An FC06 request matched the scheduled rewrite",
                        "kind": "audit_event",
                        "event_type": "security.modbus_interception_match",
                        "details_match": {"event_id": "rewrite-write",
                                          "rule_id": "smoke-write-register",
                                          "route_id": "participant-to-modbus",
                                          "function_code": 6, "direction": "request"},
                    }, {
                        "objective_id": "multiple-write-first-register-observed",
                        "description": "The first FC16 register rewrite was audited separately",
                        "kind": "audit_event",
                        "event_type": "security.modbus_interception_match",
                        "details_match": {"event_id": "rewrite-write",
                                          "rule_id": "smoke-multiple-register-first",
                                          "route_id": "participant-to-modbus",
                                          "function_code": 16, "address": 0, "direction": "request"},
                    }, {
                        "objective_id": "multiple-write-second-register-observed",
                        "description": "The second FC16 register rewrite was audited separately",
                        "kind": "audit_event",
                        "event_type": "security.modbus_interception_match",
                        "details_match": {"event_id": "rewrite-write",
                                          "rule_id": "smoke-multiple-register-second",
                                          "route_id": "participant-to-modbus",
                                          "function_code": 16, "address": 1, "direction": "request"},
                    }],
                }
                write_interception_created = api.post(
                    f"/api/scenarios/{scenario_id}/exercises", json=write_interception_definition,
                )
                if write_interception_created.status_code != 201:
                    raise AssertionError(
                        f"FC06 interception exercise creation failed: "
                        f"{write_interception_created.status_code} {write_interception_created.text}"
                    )
                write_interception_id = write_interception_created.json()["id"]
                started = api.post(
                    f"/api/scenarios/{scenario_id}/exercises/{write_interception_id}/start",
                )
                if started.status_code != 200:
                    raise AssertionError(f"FC06 interception exercise start failed: {started.text}")
                with Session(engine) as session:
                    write_run = session.get(ExerciseRun, write_interception_id)
                    run_due_exercises(session, manager, now=write_run.started_at + timedelta(seconds=1))
                    session.refresh(write_run)
                    if write_run.next_event_index != 1:
                        raise AssertionError("scheduled FC06 rewrite event was not applied")
                write_ack = participant_holding_write("WRITE_ACK", 42)
                value_after_rewrite = participant_holding_read("WRITE_READBACK")
                if write_ack != 1 or value_after_rewrite != 52000:
                    raise AssertionError(
                        f"FC06 rewrite did not change the Modbus server value: "
                        f"ack={write_ack}, readback={value_after_rewrite}"
                    )
                multiwrite_ack = participant_holding_write_multiple("MULTIWRITE_ACK", [43, 44, 45])
                multiwrite_readback = participant_holding_read("MULTIWRITE_READBACK", count=3)
                if multiwrite_ack != 1 or multiwrite_readback != (51000, 53000, 45):
                    raise AssertionError(
                        f"FC16 rewrite did not change only the two targeted Modbus registers: "
                        f"ack={multiwrite_ack}, readback={multiwrite_readback}"
                    )
                with Session(engine) as session:
                    write_run = session.get(ExerciseRun, write_interception_id)
                    run_due_exercises(session, manager, now=write_run.started_at + timedelta(seconds=11))
                    session.refresh(write_run)
                    if (write_run.status != "completed" or write_run.passed is not True
                            or write_run.score != 100):
                        raise AssertionError(
                            f"FC06 interception objective did not pass: {write_run.objective_results_json}"
                        )
                modbus_runtime = next(item for item in scenario.runtime_devices() if item["node_id"] == "modbus")
                final_values = read_modbus_values(manager, scenario_id, modbus_runtime)["values"]
                setpoint = next(item for item in final_values if item["address"] == "holding_register:0")
                auxiliary = next(item for item in final_values if item["address"] == "holding_register:1")
                unmodified = next(item for item in final_values if item["address"] == "holding_register:2")
                if (setpoint["value"] != 51000 or auxiliary["value"] != 53000
                        or unmodified["value"] != 45):
                    raise AssertionError(
                        f"FC16 writes were not retained without changing the non-target: "
                        f"{setpoint}, {auxiliary}, {unmodified}"
                    )
            finally:
                terminal.close()
                manager.close_participant_terminal(terminal_exec)
            definition = {
                "name": "S7 and Modbus protocol writes with route interruption/restoration",
                "events": [{"event_id": "open", "at_seconds": 0, "kind": "protocol_write",
                            "source_node_id": "participant", "target_node_id": "plc",
                            "route_id": "participant-to-plc", "address": "DB1.DBX0.0", "value": True},
                           {"event_id": "modbus-coil", "at_seconds": 0, "kind": "protocol_write",
                            "source_node_id": "participant", "target_node_id": "modbus",
                            "route_id": "participant-to-modbus", "address": "coil:0", "value": True},
                           {"event_id": "jam-pump", "at_seconds": 0, "kind": "field_control",
                            "object_id": "pump", "fault": True},
                           {"event_id": "interrupt-route", "at_seconds": 0, "kind": "network_route",
                            "route_id": "scada-to-plc", "action": "interrupt"},
                           {"event_id": "restore-route", "at_seconds": 2, "kind": "network_route",
                            "route_id": "scada-to-plc", "action": "restore"},
                           {"event_id": "restore-pump", "at_seconds": 2, "kind": "field_control",
                            "object_id": "pump", "fault": False}],
                "objectives": [{"objective_id": "opened", "description": "PLC output is true",
                                "kind": "point_value", "node_id": "plc", "address": "DB1.DBX0.0",
                                "operator": "eq", "value": True, "duration_seconds": 2},
                               {"objective_id": "tank-level", "description": "Tank remains above 400 L",
                                "kind": "field_value", "object_id": "tank", "property": "volume_liters",
                                "operator": "gte", "value": 400, "duration_seconds": 2},
                               {"objective_id": "valve-opened", "description": "Native S7 write opened the independent field valve",
                                "kind": "field_value", "object_id": "valve", "property": "state",
                                "operator": "eq", "value": True},
                               {"objective_id": "pump-running", "description": "Native Modbus coil write started the independent pump",
                                "kind": "field_value", "object_id": "pump", "property": "state",
                                "operator": "eq", "value": True},
                               {"objective_id": "pump-fault-and-recovery", "description": "Pump fault was injected and cleared in order",
                                "kind": "field_sequence", "steps": [
                                    {"object_id": "pump", "property": "fault", "operator": "eq", "value": True},
                                    {"object_id": "pump", "property": "fault", "operator": "eq", "value": False},
                                ], "max_gap_seconds": 5},
                               {"objective_id": "flow-reaches-tank", "description": "Open valve allows process flow into the tank",
                                "kind": "field_value", "object_id": "tank", "property": "inflow_lpm",
                                "operator": "gt", "value": 0},
                               {"objective_id": "tank-level-rises", "description": "Process flow raises tank volume",
                                "kind": "field_value", "object_id": "tank", "property": "volume_liters",
                                "operator": "gt", "value": 500},
                               {"objective_id": "write-audited", "description": "Protocol write from participant was audited",
                                "kind": "audit_event", "event_type": "attack.protocol_write",
                                "details_match": {"source_node_id": "participant", "target_node_id": "plc",
                                                  "protocol": "s7", "route_id": "participant-to-plc"}},
                               {"objective_id": "modbus-write-audited", "description": "Modbus protocol write from participant was audited",
                                "kind": "audit_event", "event_type": "attack.protocol_write",
                                "details_match": {"source_node_id": "participant", "target_node_id": "modbus",
                                                  "protocol": "modbus-tcp", "route_id": "participant-to-modbus"}},
                               {"objective_id": "detect-s7-wire-write", "description": "Detect native S7 write in protocol audit",
                                "kind": "audit_event", "event_type": "protocol.s7_operation",
                                "details_match": {"node_id": "plc", "protocol": "s7", "operation": "write",
                                                  "area": "DB", "db": 1, "byte_offset": 0,
                                                  "result": "ok"}},
                               {"objective_id": "detect-modbus-wire-write", "description": "Detect native Modbus coil write",
                                "kind": "audit_event", "event_type": "protocol.modbus_operation",
                                "details_match": {"node_id": "modbus", "function_code": 5,
                                                  "operation": "write_coil", "space": "coil",
                                                  "address": 0, "result": "ok"}},
                               {"objective_id": "detect-unapproved-s7-peer", "description": "Detect S7 write outside the peer allowlist",
                                "kind": "protocol_write_policy", "target_node_id": "plc", "protocol": "s7",
                                "allowed_source_ips": [], "expectation": "detected"},
                               {"objective_id": "detect-unapproved-modbus-peer", "description": "Detect Modbus write outside the peer allowlist",
                                "kind": "protocol_write_policy", "target_node_id": "modbus", "protocol": "modbus-tcp",
                                "allowed_source_ips": [], "expectation": "detected"},
                               {"objective_id": "route-restored", "description": "S7 route was restored",
                                "kind": "audit_event", "event_type": "scenario.network_route_restored"},
                               {"objective_id": "no-rejected-request", "description": "No request was rejected",
                                "kind": "audit_event", "event_type": "api.request_rejected",
                                "expectation": "absent"},
                               {"objective_id": "route-sequence", "description": "Route was interrupted and restored in order",
                                "kind": "audit_sequence", "event_types": [
                                    "scenario.network_route_interrupted", "scenario.network_route_restored"],
                                "max_gap_seconds": 10}],
            }
            created_run = api.post(f"/api/scenarios/{scenario_id}/exercises", json=definition)
            if created_run.status_code != 201:
                raise AssertionError(f"exercise creation failed: {created_run.status_code} {created_run.text}")
            exercise_id = created_run.json()["id"]
            started = api.post(f"/api/scenarios/{scenario_id}/exercises/{exercise_id}/start")
            if started.status_code != 200:
                raise AssertionError(f"exercise start failed: {started.status_code} {started.text}")
            with Session(engine) as session:
                run = session.get(ExerciseRun, exercise_id)
                run_due_exercises(session, manager, now=run.started_at + timedelta(seconds=1))
                session.refresh(run)
                field = field_operation(manager, scenario_id)
                pump = next(item for item in field["objects"] if item["object_id"] == "pump")
                if pump["fault"] is not True or run.status != "running":
                    raise AssertionError(f"scheduled pump fault did not remain active before recovery event: {pump}")
                interrupted = wait_for_route_status("unreachable")
                if interrupted["status"] != "unreachable":
                    raise AssertionError(f"scheduled route interruption left S7 traffic reachable: {interrupted}")
                if run.status == "running":
                    run_due_exercises(session, manager, now=run.started_at + timedelta(seconds=4))
                    session.refresh(run)
                if run.status != "completed" or run.score != 100 or run.passed is not True:
                    protocol_audit = session.exec(
                        select(ScenarioAuditEvent).where(
                            ScenarioAuditEvent.scenario_id == scenario_id,
                            ScenarioAuditEvent.event_type.in_(
                                ("protocol.s7_operation", "protocol.modbus_operation")
                            ),
                        )
                    ).all()
                    protocol_details = [
                        {"event_type": item.event_type, "details": json.loads(item.details_json)}
                        for item in protocol_audit
                    ]
                    raise AssertionError(
                        f"exercise result was {run.status}: {run.objective_results_json}; "
                        f"protocol audit: {protocol_details}"
                    )
                restored = wait_for_route_status("reachable")
                if restored["status"] != "reachable":
                    raise AssertionError(f"scheduled route restoration did not recover S7 traffic: {restored}")
                outcomes = json.loads(run.objective_results_json)
                audited = next(item for item in outcomes if item["objective_id"] == "write-audited")
                if audited.get("passed") is not True:
                    raise AssertionError(f"audit-event objective was not met: {audited}")
                absence = next(item for item in outcomes if item["objective_id"] == "no-rejected-request")
                if absence.get("passed") is not True or absence.get("actual") is not False:
                    raise AssertionError(f"audit-absence objective was not met: {absence}")
                sequence = next(item for item in outcomes if item["objective_id"] == "route-sequence")
                if sequence.get("passed") is not True or sequence.get("matched_sequence") != sequence.get("expected_sequence"):
                    raise AssertionError(f"audit-sequence objective was not met: {sequence}")
                for objective_id in ("detect-unapproved-s7-peer", "detect-unapproved-modbus-peer"):
                    policy_result = next(item for item in outcomes if item["objective_id"] == objective_id)
                    if policy_result.get("passed") is not True or policy_result.get("detection_count", 0) < 1:
                        raise AssertionError(f"peer allowlist policy did not detect a write: {policy_result}")
                detections = session.exec(select(ScenarioAuditEvent).where(
                    ScenarioAuditEvent.scenario_id == scenario_id,
                    ScenarioAuditEvent.event_type == "security.protocol_write_policy_violation",
                )).all()
                if not any(json.loads(item.details_json).get("protocol") == "s7" for item in detections):
                    raise AssertionError("S7 peer policy emitted no durable detection event")
                if not any(json.loads(item.details_json).get("protocol") == "modbus-tcp" for item in detections):
                    raise AssertionError("Modbus peer policy emitted no durable detection event")
                fault_sequence = next(item for item in outcomes if item["objective_id"] == "pump-fault-and-recovery")
                matched_faults = [item.get("actual") for item in fault_sequence.get("matched_sequence", [])]
                if fault_sequence.get("passed") is not True or matched_faults != [True, False]:
                    raise AssertionError(f"field fault/recovery sequence was not met: {fault_sequence}")
                scenario = session.get(ScenarioRecord, scenario_id)
                runtime = next(item for item in scenario.runtime_devices() if item["node_id"] == "plc")
                values = read_device_values(manager, scenario_id, runtime)["values"]
                valve = next(item for item in values if item["address"] == "DB1.DBX0.0")
                if valve["value"] is not True:
                    raise AssertionError(f"PLC output did not retain the exercise write: {valve}")
                modbus_runtime = next(item for item in scenario.runtime_devices() if item["node_id"] == "modbus")
                modbus_values = read_modbus_values(manager, scenario_id, modbus_runtime)["values"]
                pump_command = next(item for item in modbus_values if item["address"] == "coil:0")
                if pump_command["value"] is not True:
                    raise AssertionError(f"Modbus coil did not retain the participant write: {pump_command}")
                field = field_operation(manager, scenario_id)
                field_objects = {item["object_id"]: item for item in field["objects"]}
                pump = field_objects["pump"]
                field_valve = field_objects["valve"]
                tank = field_objects["tank"]
                if pump["fault"] is not False:
                    raise AssertionError(f"scheduled pump recovery event did not clear the fault: {pump}")
                if pump["state"] is not True or field_valve["state"] is not True:
                    raise AssertionError(
                        f"native protocol writes did not reach independent field actuators: "
                        f"pump={pump}, valve={field_valve}"
                    )
                if tank["inflow_lpm"] <= 0:
                    raise AssertionError(f"open field valve did not pass process flow to the tank: {tank}")
                outcomes = {item["objective_id"]: item for item in json.loads(run.objective_results_json)}
                for objective_id in ("valve-opened", "pump-running", "flow-reaches-tank", "tank-level-rises"):
                    if outcomes[objective_id].get("passed") is not True:
                        raise AssertionError(f"physical effect objective failed: {outcomes[objective_id]}")
                if tank["volume_liters"] <= 500:
                    raise AssertionError(f"tank level did not rise under process flow: {tank}")
            pause_definition = {
                "name": "Suspend timeline with scenario pause",
                "events": [{"event_id": "pause-stop", "at_seconds": 5, "kind": "node_lifecycle",
                            "node_id": "plc", "action": "stop"},
                           {"event_id": "pause-restart", "at_seconds": 6, "kind": "node_lifecycle",
                            "node_id": "plc", "action": "restart"}],
                "objectives": [{"objective_id": "restart-audited", "description": "Restart was audited",
                                "kind": "audit_event", "event_type": "scenario.node_restart"}],
            }
            pause_created = api.post(f"/api/scenarios/{scenario_id}/exercises", json=pause_definition)
            if pause_created.status_code != 201:
                raise AssertionError(f"pause exercise creation failed: {pause_created.status_code} {pause_created.text}")
            pause_id = pause_created.json()["id"]
            pause_started = api.post(f"/api/scenarios/{scenario_id}/exercises/{pause_id}/start")
            if pause_started.status_code != 200:
                raise AssertionError(f"pause exercise start failed: {pause_started.status_code} {pause_started.text}")
            scenario_paused = api.post(f"/api/scenarios/{scenario_id}/pause")
            if scenario_paused.status_code != 200 or scenario_paused.json()["status"] != "paused":
                raise AssertionError(f"scenario pause failed: {scenario_paused.status_code} {scenario_paused.text}")
            time.sleep(1)
            with Session(engine) as session:
                pause_run = session.get(ExerciseRun, pause_id)
                if pause_run.status != "paused" or pause_run.next_event_index != 0:
                    raise AssertionError(f"exercise advanced while scenario was paused: {pause_run.to_read_model()}")
            scenario_resumed = api.post(f"/api/scenarios/{scenario_id}/resume")
            if scenario_resumed.status_code != 200 or scenario_resumed.json()["status"] != "running":
                raise AssertionError(f"scenario resume failed: {scenario_resumed.status_code} {scenario_resumed.text}")
            with Session(engine) as session:
                pause_run = session.get(ExerciseRun, pause_id)
                elapsed_base = pause_run.started_at + timedelta(seconds=pause_run.paused_duration_seconds)
                run_due_exercises(session, manager, now=elapsed_base + timedelta(seconds=4))
                session.refresh(pause_run)
                if pause_run.status != "running" or pause_run.next_event_index != 0:
                    raise AssertionError(f"paused timeline lost its remaining delay: {pause_run.to_read_model()}")
                run_due_exercises(session, manager, now=elapsed_base + timedelta(seconds=7))
                session.refresh(pause_run)
                if pause_run.status != "completed" or pause_run.passed is not True:
                    raise AssertionError(f"paused timeline did not resume to completion: {pause_run.to_read_model()}")
            fail_fast_definition = {
                "name": "Fail fast on forbidden PLC stop",
                "events": [{"event_id": "forbidden-stop", "at_seconds": 0, "kind": "node_lifecycle",
                            "node_id": "plc", "action": "stop"},
                           {"event_id": "must-not-run", "at_seconds": 1, "kind": "node_lifecycle",
                            "node_id": "plc", "action": "restart"}],
                "objectives": [{"objective_id": "forbid-stop", "description": "PLC stop is forbidden",
                                "kind": "audit_event", "event_type": "scenario.node_stop",
                                "expectation": "absent", "fail_fast": True}],
            }
            fail_fast_created = api.post(f"/api/scenarios/{scenario_id}/exercises", json=fail_fast_definition)
            if fail_fast_created.status_code != 201:
                raise AssertionError(f"fail-fast exercise creation failed: {fail_fast_created.status_code} {fail_fast_created.text}")
            fail_fast_id = fail_fast_created.json()["id"]
            fail_fast_started = api.post(f"/api/scenarios/{scenario_id}/exercises/{fail_fast_id}/start")
            if fail_fast_started.status_code != 200:
                raise AssertionError(f"fail-fast exercise start failed: {fail_fast_started.status_code} {fail_fast_started.text}")
            with Session(engine) as session:
                fail_fast_run = session.get(ExerciseRun, fail_fast_id)
                run_due_exercises(session, manager, now=fail_fast_run.started_at + timedelta(seconds=2))
                session.refresh(fail_fast_run)
                fail_fast_result = fail_fast_run.to_read_model()
                if (fail_fast_run.status != "failed" or fail_fast_run.score != 0
                        or fail_fast_run.next_event_index != 1
                        or not fail_fast_result.objective_results[0]["terminated_run"]):
                    raise AssertionError(f"fail-fast did not stop before the next event: {fail_fast_result}")
            print("PASS: native Snap7 opened an independent field valve; PyModbus started the field pump; flow reached and raised the tank; FC03 response and FC06/FC16 writes were intercepted through the participant route; FC16 rewrote two selected registers, preserved a third and produced separate audit objectives; protocol audit objectives, S7/Modbus peer allowlist detections, pump fault/recovery sequence, route interruption/restoration, pause/resume and fail-fast passed; score 100")
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception:
                pass
        engine.dispose()
        client.close()


if __name__ == "__main__":
    main()
