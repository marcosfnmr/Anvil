"""Verify Modbus RTU bytes through the scenario's private raw serial tunnel."""
from __future__ import annotations

import json
import os
import time
import uuid
from ipaddress import ip_network

import docker
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from main import create_app
from models.audit import ScenarioAuditEvent
from models.scenario import ScenarioCreate
from models.db import get_session
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager
from services.modbus_operations import read_device_values, write_device_point
from services.hmi_operations import hmi_operation
from services.scada_operations import scada_operation
from services.protocol_trace_collector import collect_protocol_traces_once
from devices.modbus_tcp import ModbusTcpDevice
from devices.scada import ScadaDevice
from devices.virtual import HmiDevice
from services.scenario_service import ScenarioService


def free_subnet(client: docker.DockerClient) -> str:
    used = []
    for network in client.networks.list(filters={"label": "anvil.managed=true"}):
        for item in (network.attrs.get("IPAM") or {}).get("Config") or []:
            if item.get("Subnet"):
                used.append(ip_network(item["Subnet"]))
    for third in range(170, 240):
        candidate = ip_network(f"10.248.{third}.0/24")
        if all(not candidate.overlaps(item) for item in used):
            return str(candidate)
    raise RuntimeError("No free integration subnet in 10.248.170.0/24–10.248.239.0/24")


CLIENT_SCRIPT = r'''
import json, sys
from pymodbus.client import ModbusSerialClient
host, port, unit_id, baudrate, parity = sys.argv[1:]
client = ModbusSerialClient(f"socket://{host}:{port}", baudrate=int(baudrate), bytesize=8,
                            parity=parity, stopbits=1, timeout=2, retries=1)
if not client.connect():
    raise SystemExit("Unable to connect to virtual RTU serial tunnel")
try:
    kwargs = {"device_id": int(unit_id)}
    coil = client.read_coils(0, count=1, **kwargs)
    holding = client.read_holding_registers(4, count=1, **kwargs)
    input_register = client.read_input_registers(7, count=1, **kwargs)
    discrete_input = client.read_discrete_inputs(2, count=1, **kwargs)
    if coil.isError() or holding.isError() or input_register.isError() or discrete_input.isError():
        raise SystemExit("Initial RTU read returned a Modbus exception")
    assert coil.bits[0] is False
    assert holding.registers[0] == 1234
    assert input_register.registers[0] == 42
    assert discrete_input.bits[0] is True
    assert not client.write_coil(0, True, **kwargs).isError()
    assert not client.write_register(4, 5432, **kwargs).isError()
    assert client.read_coils(0, count=1, **kwargs).bits[0] is True
    assert client.read_holding_registers(4, count=1, **kwargs).registers[0] == 5432
    coils = client.write_coils(0, [False, True], **kwargs)
    if coils.isError() or coils.function_code != 15:
        raise SystemExit(f"RTU FC15 Write Multiple Coils failed: {coils}")
    registers = client.write_registers(4, [4321, 5432], **kwargs)
    if registers.isError() or registers.function_code != 16:
        raise SystemExit(f"RTU FC16 Write Multiple Registers failed: {registers}")
    coil_values = client.read_coils(0, count=2, **kwargs)
    register_values = client.read_holding_registers(4, count=2, **kwargs)
    assert not coil_values.isError() and coil_values.bits[:2] == [False, True], coil_values
    assert not register_values.isError() and register_values.registers == [4321, 5432], register_values
    masked = client.mask_write_register(address=4, and_mask=0xFF00, or_mask=0x0005, **kwargs)
    if masked.isError() or masked.function_code != 22:
        raise SystemExit(f"RTU FC22 Mask Write Register failed: {masked}")
    masked_value = client.read_holding_registers(4, count=1, **kwargs)
    assert not masked_value.isError() and masked_value.registers == [0x1005], masked_value
    combined = client.readwrite_registers(read_address=4, read_count=2, write_address=4,
                                          values=[4321, 5432], **kwargs)
    if combined.isError() or combined.function_code != 23:
        raise SystemExit(f"RTU FC23 Read/Write Multiple Registers failed: {combined}")
    assert combined.registers == [4321, 5432], combined
    print(json.dumps({"read_functions": [1, 2, 3, 4], "single_write_functions": [5, 6],
                      "multiple_write_functions": [coils.function_code, registers.function_code],
                      "mask_write_function": masked.function_code, "readwrite_function": combined.function_code,
                      "multiple_coils": coil_values.bits[:2], "multiple_registers": register_values.registers}))
finally:
    client.close()
'''


def main() -> None:
    image = os.environ.get("ANVIL_MODBUS_IMAGE")
    scada_image = os.environ.get("ANVIL_SCADA_IMAGE")
    if not image or not scada_image:
        raise RuntimeError("Set ANVIL_MODBUS_IMAGE and ANVIL_SCADA_IMAGE to built runtime images")
    # The API deployment pins the configured adapter images before creating
    # containers. Keep these adapters aligned with the smoke's local images.
    ModbusTcpDevice.image = image
    ScadaDevice.image = scada_image
    HmiDevice._image = os.environ.get("ANVIL_HMI_IMAGE", scada_image)
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
    subnet = ip_network(free_subnet(client))
    payload = ScenarioCreate.model_validate({
        "name": "Modbus RTU raw tunnel smoke",
        "network": {"subnet": str(subnet)},
        "devices": [{
                "node_id": "rtu", "device_type": "modbus-tcp", "name": "RTU slave",
            "ip": str(subnet.network_address + 10),
            "config": {
                "transport": "rtu", "unit_id": 17, "port": 1502,
                "baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
                "points": [
                    {"type": "coil", "name": "Pump", "address": 0, "initial_value": False},
                    {"type": "coil", "name": "Pump auxiliary", "address": 1, "initial_value": False},
                    {"type": "discrete_input", "name": "Door", "address": 2, "initial_value": True},
                    {"type": "holding_register", "name": "Speed", "address": 4, "initial_value": 1234},
                    {"type": "holding_register", "name": "Speed auxiliary", "address": 5, "initial_value": 0},
                    {"type": "input_register", "name": "Pressure", "address": 7, "initial_value": 42},
                ],
            },
        }, {
            "node_id": "scada", "device_type": "scada", "name": "RTU SCADA",
            "ip": str(subnet.network_address + 11),
            "config": {"target_node_ids": "rtu", "poll_interval_ms": 5000, "history_capacity": 4, "alarms": []},
        }, {
            "node_id": "hmi", "device_type": "hmi", "name": "RTU HMI",
            "ip": str(subnet.network_address + 12),
            "config": {"role": "hmi", "summary": "RTU test panel", "target_node_ids": "rtu"},
        }],
    })
    scenario_id = None
    try:
        with TestClient(app) as api:
            created = api.post("/api/scenarios", json=payload.model_dump(mode="json"))
            if created.status_code != 201:
                raise AssertionError(f"RTU scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise AssertionError(f"RTU scenario did not become healthy: {deployed.status_code} {deployed.text}")
            with Session(engine) as session:
                runtime = ScenarioService(session).get_record(scenario_id).runtime_devices()
            device = next(container for container in client.containers.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"})
                          if container.labels.get("anvil.node_id") == "rtu")
        if device.attrs["HostConfig"].get("PortBindings"):
            raise AssertionError("RTU service unexpectedly published a host port")
        networks = device.attrs["NetworkSettings"]["Networks"]
        if len(networks) != 1:
            raise AssertionError(f"Expected one private scenario network, found {list(networks)}")
        network_name = next(iter(networks))
        try:
            result = client.containers.run(
                image,
                command=["python", "-c", CLIENT_SCRIPT, str(subnet.network_address + 10), "1502", "17", "9600", "N"],
                network=network_name,
                detach=False,
                remove=True,
            )
        except docker.errors.ContainerError:
            print(device.logs(tail=100).decode("utf-8", errors="replace"))
            raise
        print("PASS: separate scenario container read/wrote FC01–06, FC15/16, FC22 and FC23 over RTU raw tunnel; no host port published")
        if result:
            print(result.decode("utf-8", errors="replace").strip())
        rtu_device = next(item for item in runtime if item["node_id"] == "rtu")
        api_values = read_device_values(manager, scenario_id, rtu_device)["values"]
        if [item["value"] for item in api_values] != [False, True, True, 4321, 5432, 42]:
            raise AssertionError(f"Anvil Modbus operations did not read RTU tunnel values: {api_values}")
        write_device_point(manager, scenario_id, rtu_device, rtu_device["config"]["points"][0], False)
        api_values = read_device_values(manager, scenario_id, rtu_device)["values"]
        if [item["value"] for item in api_values] != [False, True, True, 4321, 5432, 42]:
            raise AssertionError(f"Anvil Modbus operations did not write/read RTU coil: {api_values}")
        print("PASS: Anvil's managed-device read/write operations also use the RTU serial client")

        if "rtu" not in manager.protocol_audit_queue_nodes(scenario_id):
            raise AssertionError("Modbus RTU runtime is missing its durable protocol audit queue")
        collected = collect_protocol_traces_once(engine, manager, scenario_id=scenario_id)
        with Session(engine) as session:
            events = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "protocol.modbus_operation",
            )).all()
            details = [json.loads(item.details_json) for item in events]
        if collected < 2 or not any(item.get("source") == "rtu-serial"
                                    and item.get("operation") == "write_coil" for item in details):
            raise AssertionError(f"RTU coil write did not reach backend audit: {details}")
        if not any(item.get("source") == "rtu-serial"
                   and item.get("operation") == "write_holding_register" for item in details):
            raise AssertionError(f"RTU register write did not reach backend audit: {details}")
        if any("value" in item or "5432" in json.dumps(item) or "4321" in json.dumps(item)
               for item in details):
            raise AssertionError("Backend protocol audit must not contain process values")
        if collect_protocol_traces_once(engine, manager, scenario_id=scenario_id) != 0:
            raise AssertionError("Replaying an acknowledged RTU batch must not duplicate audit events")
        pending = manager.read_protocol_audit_queue(scenario_id, "rtu", limit=1000)
        if pending.get("pending_count") != 0:
            raise AssertionError(f"Backend did not acknowledge the committed RTU audit batch: {pending}")
        print("PASS: RTU writes reached backend audit without values; collector acknowledged and deduplicated the queue")

        points = ModbusTcpDevice().build_io_map(rtu_device)
        hmi_values = hmi_operation(manager, scenario_id, "hmi", rtu_device, points)
        if [item["value"] for item in hmi_values] != [False, True, True, 4321, 5432, 42]:
            raise AssertionError(f"HMI did not read RTU values: {hmi_values}")
        hmi_write = hmi_operation(manager, scenario_id, "hmi", rtu_device, points,
                                  address="holding_register:4", value=6789)
        if hmi_write[0]["value"] != 6789:
            raise AssertionError(f"HMI did not write RTU holding register: {hmi_write}")

        # A fresh SCADA process can spend several serial timeouts on its first
        # complete poll, especially while the preceding independent clients
        # have just used the same PTY-backed RTU tunnel.
        deadline = time.monotonic() + 30
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = scada_operation(manager, scenario_id, "scada", "snapshot")
            snapshot_values = {item["address"]: item.get("value") for item in snapshot.get("values", [])}
            if (len(snapshot_values) == 6 and all(item.get("quality") == "good" for item in snapshot["values"])
                    and snapshot_values.get("holding_register:4") == 6789):
                break
            time.sleep(0.5)
        scada_values = {item["address"]: item["value"] for item in (snapshot or {}).get("values", [])}
        if scada_values != {"coil:0": False, "coil:1": True, "discrete_input:2": True,
                            "holding_register:4": 6789, "holding_register:5": 5432,
                            "input_register:7": 42}:
            for container in client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"}):
                print(f"--- {container.labels.get('anvil.node_id')} logs ---")
                print(container.logs(tail=100).decode("utf-8", errors="replace"))
            raise AssertionError(f"SCADA did not acquire RTU points through the serial tunnel: {snapshot}")
        print("PASS: HMI read/wrote RTU points and SCADA acquired all six points through PyModbus serial clients")
    finally:
        try:
            if scenario_id:
                manager.stop(scenario_id)
                manager.delete_retention_volumes(scenario_id)
        finally:
            engine.dispose()
            client.close()


if __name__ == "__main__":
    main()
