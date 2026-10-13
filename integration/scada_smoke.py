"""Deploy a local SCADA collector and verify protocol acquisition/history/alarms."""
from __future__ import annotations

import os
import time
import uuid
from ipaddress import ip_network

import docker

from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.scada_operations import scada_operation
from services.scenario_service import ScenarioService


def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list()
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(170, 240):
        candidate = ip_network(f"10.247.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free integration subnet in 10.247.170.0/24–10.247.239.0/24")


def main() -> None:
    names = ("ANVIL_PLC_IMAGE", "ANVIL_MODBUS_IMAGE", "ANVIL_SCADA_IMAGE")
    if not all(os.environ.get(name) for name in names):
        raise RuntimeError("Set ANVIL_PLC_IMAGE, ANVIL_MODBUS_IMAGE and ANVIL_SCADA_IMAGE to local images")
    client = docker.from_env()
    manager = DockerManager(client=client)
    subnet = free_subnet(client)
    scenario_id = str(uuid.uuid4())
    payload = ScenarioCreate.model_validate({
        "name": "SCADA native acquisition smoke", "network": {"subnet": str(subnet)},
        "devices": [
            {"node_id": "plc", "device_type": "plc-s7", "name": "S7 source", "ip": str(subnet.network_address + 10),
             "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
            {"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus source", "ip": str(subnet.network_address + 11),
             "config": {"unit_id": 1, "port": 1502, "points": [{"type": "holding_register", "name": "Pressure", "initial_value": 0}]}},
            {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": str(subnet.network_address + 12),
             "config": {"target_node_ids": "plc,modbus", "poll_interval_ms": 250, "history_capacity": 8,
                        "alarms": [{"target_node_id": "modbus", "address": "holding_register:0", "above": 20, "delay_ms": 1500}]}},
        ],
    })
    runtime = ScenarioService._build_runtime_devices(payload)
    try:
        result = manager.deploy(scenario_id, str(subnet), runtime)
        if result["status"] != "running":
            raise AssertionError(f"scenario did not become healthy: {result}")
        deadline = time.monotonic() + 20
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = scada_operation(manager, scenario_id, "scada", "snapshot")
            if snapshot.get("timestamp") and all(value.get("quality") == "good" for value in snapshot["values"]):
                break
            time.sleep(0.25)
        values = {(item["node_id"], item["address"]): item for item in snapshot["values"]}
        assert values[("plc", "DB1.DBD0")]["quality"] == "good"
        assert values[("modbus", "holding_register:0")]["value"] == 0
        assert snapshot["alarms"][0]["active"] is False
        activation_started = time.monotonic()
        scada_operation(manager, scenario_id, "scada", "write", {
            "target_node_id": "modbus", "address": "holding_register:0", "value": 25})
        deadline = activation_started + 10
        saw_pending = False
        while time.monotonic() < deadline:
            snapshot = scada_operation(manager, scenario_id, "scada", "snapshot")
            alarm = snapshot["alarms"][0]
            if alarm["pending"]:
                saw_pending = True
                assert not alarm["active"], alarm
            if alarm["active"]:
                break
            time.sleep(0.1)
        assert alarm["active"] and saw_pending, snapshot
        assert time.monotonic() - activation_started >= 1.5
        history = scada_operation(manager, scenario_id, "scada", "history", {"limit": 8})
        assert len(history["samples"]) >= 1
        acknowledged = scada_operation(manager, scenario_id, "scada", "alarms/acknowledge", {
            "target_node_id": "modbus", "address": "holding_register:0"})
        assert acknowledged["acknowledged"] is True
        print("PASS: SCADA polled live S7/Modbus, enforced a pending alarm delay, recorded history and acknowledged it")
    finally:
        manager.stop(scenario_id)
        client.close()


if __name__ == "__main__":
    main()
