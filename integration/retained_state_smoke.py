"""Verify an SR output survives PLC container recreation and is cleaned on deletion."""
from __future__ import annotations

import os
import time
import uuid
from ipaddress import ip_network

import docker

from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.scenario_service import ScenarioService


def free_subnet(client):
    used = [item["Subnet"] for network in client.networks.list()
            for item in (network.attrs.get("IPAM") or {}).get("Config") or [] if item.get("Subnet")]
    for third in range(220, 250):
        candidate = ip_network(f"10.247.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free retention smoke subnet in 10.247.220.0/24–10.247.249.0/24")


def read_write_sr(container, set_offset, set_mask, output_offset, output_mask):
    command = ["python", "-c", f'''import snap7, time
import json
c=snap7.client.Client(); c.connect("127.0.0.1",0,1)
b=bytearray(c.db_read(1,{set_offset},1)); b[0]|={set_mask}; c.db_write(1,{set_offset},b)
deadline=time.monotonic()+2
while time.monotonic()<deadline:
 if c.db_read(1,{output_offset},1)[0]&{output_mask}: break
 time.sleep(.02)
assert c.db_read(1,{output_offset},1)[0]&{output_mask}, "SR output did not set"
state=json.load(open("/var/lib/anvil/retention/state.json", encoding="utf-8"))
assert state["state"]["latch_values"]["Motor memory"] is True, "SR state was not checkpointed immediately"
c.disconnect()
''']
    result = container.exec_run(command)
    if result.exit_code:
        raise RuntimeError(f"Snap7 SR operation failed: {result.output!r}")


def assert_sr_output(container, output_offset, output_mask):
    result = container.exec_run(["python", "-c", f"import snap7; c=snap7.client.Client(); c.connect('127.0.0.1',0,1); assert c.db_read(1,{output_offset},1)[0]&{output_mask}; c.disconnect()"])
    if result.exit_code:
        raise AssertionError(f"Retained SR output was lost after PLC recreation: {result.output!r}")


def main() -> None:
    if not os.environ.get("ANVIL_PLC_IMAGE"):
        raise RuntimeError("Set ANVIL_PLC_IMAGE to the locally built S7 runtime image")
    client = docker.from_env()
    manager = DockerManager(client=client)
    scenario_id = str(uuid.uuid4())
    subnet = free_subnet(client)
    payload = ScenarioCreate.model_validate({
        "name": "PLC retention smoke", "network": {"subnet": str(subnet)},
        "devices": [{"node_id": "retentive-plc", "device_type": "plc-s7", "name": "Retentive PLC",
                     "ip": str(subnet.network_address + 10), "config": {
                         "retain_logic_state": True,
                         "components": [{"type": "digital_input", "name": "Set"},
                                        {"type": "digital_input", "name": "Reset"},
                                        {"type": "motor", "name": "Motor"}],
                         "latches": [{"name": "Motor memory", "set_component": "Set",
                                      "reset_component": "Reset", "output_component": "Motor"}],
                     }}],
    })
    runtime = ScenarioService._build_runtime_devices(payload)
    try:
        manager.deploy(scenario_id, str(subnet), runtime)
        container = next(item for item in client.containers.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"})
            if item.labels.get("anvil.node_id") == "retentive-plc")
        latch = runtime[0]["config"]["latches"][0]
        read_write_sr(container, latch["set_byte_offset"], 1 << latch["set_bit_offset"],
                      latch["output_byte_offset"], 1 << latch["output_bit_offset"])
        container.remove(force=True)
        manager.recreate_node(scenario_id, runtime[0])
        recreated = next(item for item in client.containers.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"})
            if item.labels.get("anvil.node_id") == "retentive-plc")
        assert_sr_output(recreated, latch["output_byte_offset"], 1 << latch["output_bit_offset"])
        print("PASS: SR state survived PLC container recreation")
    finally:
        try:
            manager.stop(scenario_id)
        finally:
            manager.delete_retention_volumes(scenario_id)
            leftovers = client.volumes.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
            if leftovers:
                raise AssertionError("Scenario retention volume was not removed during cleanup")
            client.close()


if __name__ == "__main__":
    main()
