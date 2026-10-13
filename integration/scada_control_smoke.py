"""Exercise permission-checked SCADA API writes against real S7/Modbus containers."""
from __future__ import annotations

import os
import uuid
from ipaddress import ip_network

import docker
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from main import create_app
from models.db import get_session
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager
from services.modbus_operations import read_device_values as read_modbus_values
from services.plc_operations import read_device_values as read_plc_values
from services.scenario_service import ScenarioService


def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list(filters={"label": "anvil.managed=true"})
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(170, 240):
        candidate = ip_network(f"10.246.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free integration subnet in 10.246.170.0/24–10.246.239.0/24")


def main() -> None:
    names = ("ANVIL_PLC_IMAGE", "ANVIL_MODBUS_IMAGE", "ANVIL_SCADA_IMAGE", "ANVIL_FIELD_IMAGE")
    if not all(os.environ.get(name) for name in names):
        raise RuntimeError("Set ANVIL_PLC_IMAGE, ANVIL_MODBUS_IMAGE and ANVIL_SCADA_IMAGE to local images")

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
    scenario_key = str(uuid.uuid4())

    try:
        subnet = free_subnet(client)
        with TestClient(app) as api:
            created = api.post("/api/scenarios", json={
                "name": "SCADA native control smoke",
                "network": {"subnet": str(subnet)},
                "devices": [
                {"node_id": "scada", "device_type": "scada", "name": "SCADA",
                     "ip": str(subnet.network_address + 10),
                     "config": {"target_node_ids": "plc,modbus", "poll_interval_ms": 250, "history_capacity": 4, "alarms": []}},
                    {"node_id": "plc", "device_type": "plc-s7", "name": "S7 PLC",
                     "ip": str(subnet.network_address + 11),
                     "config": {"components": [
                         {"type": "valve", "name": "Inlet valve"},
                         {"type": "io_channel", "channel_id": "valve-setpoint", "name": "Valve opening setpoint", "direction": "output", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
                     ]}},
                    {"node_id": "hmi", "device_type": "hmi", "name": "HMI",
                     "ip": str(subnet.network_address + 14),
                     "config": {"role": "hmi", "summary": "Operator panel", "target_node_ids": "plc,modbus"}},
                    {"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus device",
                     "ip": str(subnet.network_address + 12),
                     "config": {"unit_id": 1, "port": 1502, "points": [
                         {"type": "coil", "name": "Pump start"},
                         {"type": "holding_register", "name": "Setpoint", "initial_value": 10},
                         {"type": "input_register", "name": "Read only", "initial_value": 42},
                     ]}},
                    {"node_id": "unselected", "device_type": "modbus-tcp", "name": "Unselected device",
                     "ip": str(subnet.network_address + 13),
                     "config": {"unit_id": 1, "port": 1502, "points": [
                         {"type": "coil", "name": "Unselected coil"},
                     ]}},
                ],
                "field_objects": [
                    {"object_id": "pump-field", "type": "pump", "name": "Feed pump", "initial_state": False,
                     "response_seconds": 0, "flow_min": 0, "flow_max": 120, "unit": "L/min"},
                    {"object_id": "valve-field", "type": "proportional_valve", "name": "Proportional valve",
                     "initial_opening_pct": 0, "response_seconds": 0, "flow_rate_lpm": 60},
                    {"object_id": "modbus-valve-field", "type": "proportional_valve", "name": "Modbus proportional valve",
                     "initial_opening_pct": 0, "response_seconds": 0, "flow_rate_lpm": 40},
                    {"object_id": "tank-field", "type": "tank", "name": "Receiver tank",
                     "capacity_liters": 100, "initial_level_liters": 0, "outlet_flow_lpm": 0},
                ],
                "signal_connections": [
                    {"connection_id": "pump-command", "source": {"kind": "device", "node_id": "plc", "address": "DB1.DBX0.0"}, "target": {"kind": "field", "object_id": "pump-field", "port": "command"}},
                    {"connection_id": "valve-setpoint", "source": {"kind": "device", "node_id": "plc", "channel_id": "valve-setpoint", "address": "DB1.DBD2"}, "target": {"kind": "field", "object_id": "valve-field", "port": "command"}},
                    {"connection_id": "modbus-valve-setpoint", "source": {"kind": "device", "node_id": "modbus", "address": "holding_register:0"}, "target": {"kind": "field", "object_id": "modbus-valve-field", "port": "command"}, "conversion": {"input_min": 0, "input_max": 1000, "output_min": 0, "output_max": 100}},
                ],
                "process_connections": [
                    {"connection_id": "pump-to-valve", "source": {"object_id": "pump-field", "port": "flow"}, "target": {"object_id": "valve-field", "port": "process_in"}},
                    {"connection_id": "valve-to-tank", "source": {"object_id": "valve-field", "port": "process_out"}, "target": {"object_id": "tank-field", "port": "inlet"}},
                ],
            })
            if created.status_code != 201:
                raise AssertionError(f"scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise AssertionError(f"scenario deployment failed: {deployed.status_code} {deployed.text}")
            runtime_containers = deployed.json().get("containers", [])
            metric_fields = {"cpu_percent", "memory_usage_bytes", "memory_limit_bytes", "pids"}
            expected_nodes = {"scada", "plc", "hmi", "modbus", "unselected"}
            runtime_node_ids = {item.get("node_id") for item in runtime_containers}
            if (not expected_nodes.issubset(runtime_node_ids)
                    or any(not metric_fields.issubset(item) for item in runtime_containers)):
                raise AssertionError(f"Docker runtime resource metrics were not exposed for every node: {runtime_containers}")

            base = f"/api/scenarios/{scenario_id}/scada/scada"
            for point in (
                {"target_node_id": "plc", "address": "DB1.DBX0.0", "value": True},
                {"target_node_id": "plc", "address": "DB1.DBD2", "value": 37.5},
                {"target_node_id": "modbus", "address": "coil:0", "value": True},
                {"target_node_id": "modbus", "address": "holding_register:0", "value": 789},
            ):
                response = api.post(base + "/write", json=point)
                if response.status_code != 200:
                    raise AssertionError(f"SCADA write failed for {point}: {response.status_code} {response.text}")

            hmi_write = api.post(f"/api/scenarios/{scenario_id}/hmi/hmi/write", json={
                "target_node_id": "plc", "address": "DB1.DBD2", "value": 51.25,
            })
            if hmi_write.status_code != 200 or hmi_write.json().get("value") != 51.25:
                raise AssertionError(f"HMI S7 REAL write failed: {hmi_write.status_code} {hmi_write.text}")
            out_of_range = api.post(base + "/write", json={
                "target_node_id": "plc", "address": "DB1.DBD2", "value": 100.1,
            })
            if out_of_range.status_code != 409:
                raise AssertionError(f"SCADA must reject REAL command outside 0–100%: {out_of_range.status_code} {out_of_range.text}")

            # A BOOL PLC output starts the pump while the REAL output sets valve opening.
            for _ in range(30):
                field = api.get(f"/api/scenarios/{scenario_id}/field/values")
                if field.status_code == 200:
                    states = {item["object_id"]: item for item in field.json()["objects"]}
                    if states["tank-field"]["inflow_lpm"] > 30:
                        break
                import time
                time.sleep(.2)
            if field.status_code != 200 or states["tank-field"]["inflow_lpm"] <= 30 or abs(states["modbus-valve-field"]["opening_pct"] - 78.9) > 0.1:
                raise AssertionError(f"S7 REAL setpoint did not drive field flow: {field.status_code} {field.text if field.status_code != 200 else states}")

            for point in (
                {"target_node_id": "modbus", "address": "input_register:0", "value": 99},
                {"target_node_id": "unselected", "address": "coil:0", "value": True},
            ):
                response = api.post(base + "/write", json=point)
                if response.status_code != 403:
                    raise AssertionError(f"SCADA must reject {point}: got {response.status_code} {response.text}")

            with Session(engine) as session:
                record = ScenarioService(session).get_record(scenario_id)
                runtime = {device["node_id"]: device for device in record.runtime_devices()}
                plc_values = read_plc_values(manager, scenario_id, runtime["plc"])["values"]
                modbus_values = read_modbus_values(manager, scenario_id, runtime["modbus"])["values"]
            value_map = {(item["node_id"], item["address"]): item["value"] for item in plc_values + modbus_values}
            assert value_map[("plc", "DB1.DBX0.0")] is True
            assert value_map[("plc", "DB1.DBD2")] == 51.25
            assert value_map[("modbus", "coil:0")] is True
            assert value_map[("modbus", "holding_register:0")] == 789
            assert value_map[("modbus", "input_register:0")] == 42

            events = api.get(f"/api/scenarios/{scenario_id}/audit-events?limit=100")
            if events.status_code != 200:
                raise AssertionError(f"audit query failed: {events.status_code} {events.text}")
            writes = [event for event in events.json()["events"]
                      if event["event_type"] == "operator.point_written" and event["details"].get("source") == "scada"]
            if len(writes) != 4:
                raise AssertionError(f"expected 4 audited SCADA writes, found {len(writes)}")
            if states["valve-field"]["opening_pct"] != 51.25 or states["tank-field"]["inflow_lpm"] <= 30 or abs(states["modbus-valve-field"]["opening_pct"] - 78.9) > 0.1:
                raise AssertionError(f"REAL setpoint did not reach the proportional valve/process: {states}")
            print("PASS: HMI/SCADA APIs wrote real S7 BOOL/REAL and Modbus outputs, read them back and audited writes; S7 REAL drove a proportional valve and measurable flow, Modbus UINT16 scaled to a second proportional valve; SCADA rejected unselected/read-only points")
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
