"""Deploy an S7 PLC and field VFD, then verify REAL setpoint and process flow."""
from __future__ import annotations

import os
import time
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


def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list(filters={"label": "anvil.managed=true"})
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(170, 240):
        candidate = ip_network(f"10.247.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free integration subnet in 10.247.170.0/24–10.247.239.0/24")


def main() -> None:
    names = ("ANVIL_PLC_IMAGE", "ANVIL_FIELD_IMAGE")
    if not all(os.environ.get(name) for name in names):
        raise RuntimeError("Set ANVIL_PLC_IMAGE and ANVIL_FIELD_IMAGE to local images")

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
        subnet = free_subnet(client)
        with TestClient(app) as api:
            created = api.post("/api/scenarios", json={
                "name": "S7 VFD process smoke",
                "network": {"subnet": str(subnet)},
                "devices": [{
                    "node_id": "plc", "device_type": "plc-s7", "name": "S7 controller",
                    "ip": str(subnet.network_address + 10),
                    "config": {"components": [{
                        "type": "io_channel", "channel_id": "drive-speed", "name": "Drive speed",
                        "direction": "output", "data_type": "REAL", "unit": "%",
                        "min_value": 0, "max_value": 100,
                    }]},
                }, {
                    "node_id": "hmi", "device_type": "hmi", "name": "Operator panel",
                    "ip": str(subnet.network_address + 11),
                    "config": {"role": "hmi", "summary": "VFD setpoint", "target_node_ids": "plc"},
                }],
                "field_objects": [
                    {"object_id": "vfd", "type": "variable_speed_drive", "name": "Feed drive",
                     "initial_speed_pct": 0, "response_seconds": 0, "max_flow_lpm": 80},
                    {"object_id": "pipe", "type": "pipe", "name": "Transfer pipe", "max_flow_lpm": 30},
                    {"object_id": "tank", "type": "tank", "name": "Receiver tank",
                     "capacity_liters": 1000, "initial_level_liters": 100, "outlet_flow_lpm": 0},
                ],
                "signal_connections": [{
                    "connection_id": "plc-to-vfd",
                    "source": {"kind": "device", "node_id": "plc", "channel_id": "drive-speed", "address": "DB1.DBD0"},
                    "target": {"kind": "field", "object_id": "vfd", "port": "command"},
                }],
                "process_connections": [
                    {"connection_id": "drive-to-pipe", "source": {"object_id": "vfd", "port": "flow"},
                     "target": {"object_id": "pipe", "port": "process_in"}},
                    {"connection_id": "pipe-to-tank", "source": {"object_id": "pipe", "port": "process_out"},
                     "target": {"object_id": "tank", "port": "inlet"}},
                ],
            })
            if created.status_code != 201:
                raise AssertionError(f"scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise AssertionError(f"scenario deployment failed: {deployed.status_code} {deployed.text}")

            write = api.post(f"/api/scenarios/{scenario_id}/hmi/hmi/write", json={
                "target_node_id": "plc", "address": "DB1.DBD0", "value": 50,
            })
            if write.status_code != 200:
                raise AssertionError(f"S7 REAL setpoint write failed: {write.status_code} {write.text}")

            def snapshot_until(predicate, message):
                deadline = time.monotonic() + 12
                latest = None
                while time.monotonic() < deadline:
                    response = api.get(f"/api/scenarios/{scenario_id}/field/values")
                    if response.status_code == 200:
                        latest = {item["object_id"]: item for item in response.json()["objects"]}
                        if predicate(latest):
                            return latest
                    time.sleep(.2)
                raise AssertionError(f"{message}; last field state: {latest}")

            running = snapshot_until(
                lambda state: abs(state["vfd"]["speed_pct"] - 50) < 0.1 and
                              abs(state["vfd"]["flow"] - 40) < 0.1 and
                              abs(state["pipe"]["process_out"] - 30) < 0.1 and
                              state["tank"]["inflow_lpm"] > 0,
                "S7 setpoint did not drive VFD and capped process flow",
            )
            fault = api.post(f"/api/scenarios/{scenario_id}/field/vfd/control", json={"fault": True})
            if fault.status_code != 200:
                raise AssertionError(f"VFD fault control failed: {fault.status_code} {fault.text}")
            stopped = snapshot_until(lambda state: state["vfd"]["fault"] and state["vfd"]["flow"] == 0,
                                     "VFD fault did not stop process flow")
            restore = api.post(f"/api/scenarios/{scenario_id}/field/vfd/control", json={"fault": False})
            if restore.status_code != 200:
                raise AssertionError(f"VFD restore failed: {restore.status_code} {restore.text}")
            recovered = snapshot_until(lambda state: not state["vfd"]["fault"] and
                                       abs(state["vfd"]["flow"] - 40) < .1,
                                       "VFD did not recover after fault reset")
            print("PASS: S7 REAL 50% -> VFD 40 L/min -> pipe capped at 30 L/min -> tank; fault stops flow and reset restores it")
            print(f"Observed: VFD {running['vfd']['speed_pct']:.1f}%/{running['vfd']['flow']:.1f} L/min, "
                  f"pipe {running['pipe']['process_out']:.1f} L/min, fault {stopped['vfd']['flow']:.1f} L/min, "
                  f"recovered {recovered['vfd']['flow']:.1f} L/min")
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
