"""Verify cross-zone PLC peer S7 links through a single TCP gateway."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:srs-v2")
os.environ.setdefault("ANVIL_VIRTUAL_IMAGE", os.environ["ANVIL_PLC_IMAGE"])

import docker

from devices.plc_s7 import PlcS7Device
from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.plc_operations import read_device_values, write_device_point
from services.scenario_service import ScenarioService


def main() -> None:
    client = docker.from_env()
    existing = {
        item.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
        for network in client.networks.list()
        for item in [network.attrs]
        if item.get("IPAM", {}).get("Config")
    }
    subnets = next(((f"172.28.{octet}.0/24", f"172.28.{octet + 1}.0/24")
                    for octet in range(200, 240, 2)
                    if f"172.28.{octet}.0/24" not in existing and f"172.28.{octet + 1}.0/24" not in existing), None)
    if subnets is None:
        raise RuntimeError("No free isolated subnet pair in 172.28.200.0/24–172.28.239.0/24")
    control_subnet, operations_subnet = subnets
    control_prefix = control_subnet.rsplit(".", 1)[0]
    operations_prefix = operations_subnet.rsplit(".", 1)[0]
    scenario_id = str(uuid4())
    payload = {
        "name": "Cross-zone PLC peer S7 smoke",
        "network": {"subnet": control_subnet, "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": control_subnet},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": operations_subnet},
        ]},
        "devices": [
            {"node_id": "sender", "device_type": "plc-s7", "name": "PLC sender",
             "ip": f"{control_prefix}.10", "zone_id": "control", "config": {"components": [
                 {"type": "valve", "name": "run", "initial_state": False},
                 {"type": "analog_sensor", "name": "pressure", "unit": "bar",
                  "min_value": 0, "max_value": 60},
             ]}},
            {"node_id": "receiver", "device_type": "plc-s7", "name": "PLC receiver",
             "ip": f"{operations_prefix}.10", "zone_id": "operations", "config": {"components": [
                 {"type": "remote_input", "name": "run_remote", "source_node_id": "sender",
                  "source_address": "DB1.DBX0.0", "data_type": "BOOL", "period_ms": 150,
                  "timeout_ms": 500, "stale_after_ms": 600},
                 {"type": "remote_input", "name": "pressure_remote", "source_node_id": "sender",
                  "source_address": "DB1.DBD2", "data_type": "REAL", "period_ms": 150,
                  "timeout_ms": 500, "stale_after_ms": 600},
             ]}},
        ],
        "network_routes": [{"route_id": "receiver-to-sender", "source_node_id": "receiver",
                            "target_node_id": "sender", "protocol": "s7", "enabled": True}],
    }
    scenario = ScenarioCreate.model_validate(payload)
    runtime = ScenarioService._build_runtime_devices(scenario)
    by_id = {item["node_id"]: item for item in runtime}
    manager = DockerManager(client)
    try:
        manager.deploy(scenario_id, control_subnet, runtime)
        containers = manager._scenario_containers(scenario_id)
        sender = next(item for item in containers if (item.labels or {}).get("anvil.node_id") == "sender")
        receiver = next(item for item in containers if (item.labels or {}).get("anvil.node_id") == "receiver")
        gateway = next(item for item in containers if (item.labels or {}).get("anvil.role") == "tcp-gateway")
        sender.reload(); receiver.reload(); gateway.reload()
        assert list(sender.attrs["NetworkSettings"]["Networks"]) == [f"anvil-{scenario_id[:8]}-control"]
        assert list(receiver.attrs["NetworkSettings"]["Networks"]) == [f"anvil-{scenario_id[:8]}-operations"]
        assert set(gateway.attrs["NetworkSettings"]["Networks"]) == {
            f"anvil-{scenario_id[:8]}-control", f"anvil-{scenario_id[:8]}-operations"}
        assert not gateway.attrs.get("HostConfig", {}).get("PortBindings")
        source_gateway_ip = by_id["receiver"]["config"]["components"][0]["source_ip"]
        assert source_gateway_ip == gateway.attrs["NetworkSettings"]["Networks"][f"anvil-{scenario_id[:8]}-operations"]["IPAddress"]
        assert by_id["receiver"]["config"]["components"][0]["source_ip"] != by_id["sender"]["ip"]

        def values() -> dict[str, dict]:
            response = read_device_values(manager, scenario_id, by_id["receiver"])
            return {item["address"]: item for item in response["values"]}

        def until(predicate, timeout: float = 15) -> dict[str, dict]:
            deadline = time.monotonic() + timeout
            latest: dict[str, dict] = {}
            while time.monotonic() < deadline:
                latest = values()
                if predicate(latest):
                    return latest
                time.sleep(0.15)
            diagnostics = {}
            for container in manager._scenario_containers(scenario_id):
                container.reload()
                key = (container.labels or {}).get("anvil.node_id", container.name)
                diagnostics[key] = {"state": container.attrs.get("State", {}).get("Status"),
                                    "networks": container.attrs.get("NetworkSettings", {}).get("Networks", {}),
                                    "logs": container.logs(tail=20).decode("utf-8", errors="replace")[-1000:]}
            raise AssertionError(f"S7 peer state not reached: {latest}; runtime={diagnostics}")

        initial = until(lambda sample: all(point.get("communication", {}).get("quality") == "good"
                                           for point in sample.values()))
        assert initial["DB1.DBX0.0"]["value"] is False
        assert initial["DB1.DBD2"]["value"] == 30.0, initial
        sender_component = by_id["sender"]["config"]["components"][0]
        write_device_point(manager, scenario_id, by_id["sender"], sender_component, True)
        updated = until(lambda sample: sample["DB1.DBX0.0"]["value"] is True
                        and sample["DB1.DBX0.0"]["communication"]["quality"] == "good")
        assert updated["DB1.DBD2"]["value"] == 30.0
        remote_access = next(point for point in PlcS7Device().build_io_map(by_id["receiver"])
                             if point["address"] == "DB1.DBX0.0")
        assert remote_access["access"] == "read"

        direct = receiver.exec_run(
            ["python", "-c", "import socket,sys\ntry: socket.create_connection((sys.argv[1],102),timeout=.5)\n"
             "except OSError: print('blocked'); raise SystemExit(0)\nraise SystemExit('direct peer path succeeded')\n",
             by_id["sender"]["ip"]], stdout=True, stderr=True)
        assert direct.exit_code == 0 and b"blocked" in direct.output, direct.output

        sender.stop(timeout=5)
        stale = until(lambda sample: all(point.get("communication", {}).get("quality") == "stale"
                                         for point in sample.values()), timeout=8)
        assert stale["DB1.DBX0.0"]["value"] is True
        sender.start()
        recovered = until(lambda sample: sample["DB1.DBX0.0"]["communication"]["quality"] == "good"
                          and sample["DB1.DBX0.0"]["value"] is False)
        assert recovered["DB1.DBD2"]["value"] == 30.0
        print(json.dumps({"status": "passed", "transport": "S7 via explicit two-zone TCP gateway",
                          "direct_path": "blocked", "peer_write": "visible", "recovery": "passed",
                          "remote_input_access": remote_access["access"]}))
    finally:
        manager.stop(scenario_id)
        client.close()


if __name__ == "__main__":
    main()
