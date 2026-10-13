"""Verify the logical Ethernet switch maps to a working Docker L2 bridge."""

from __future__ import annotations

import ipaddress
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from devices import DEVICE_REGISTRY  # noqa: E402
from models.scenario import ScenarioPayload  # noqa: E402
from services.docker_manager import DockerManager  # noqa: E402


def free_subnet(manager: DockerManager) -> str:
    occupied = []
    for network in manager.client.networks.list():
        for config in (network.attrs.get("IPAM") or {}).get("Config") or []:
            if config.get("Subnet"):
                occupied.append(ipaddress.ip_network(config["Subnet"]))
    for third_octet in range(240, 255):
        candidate = ipaddress.ip_network(f"172.29.{third_octet}.0/24")
        if not any(candidate.overlaps(item) for item in occupied):
            return str(candidate)
    raise RuntimeError("No free 172.29.240.0/20 subnet is available for this smoke")


def main() -> None:
    manager = DockerManager()
    subnet = free_subnet(manager)
    network = ipaddress.ip_network(subnet)
    scenario_id = f"switch{uuid.uuid4().hex[:12]}"
    switch_ip, hmi_ip, client_ip = (str(network.network_address + offset) for offset in (10, 11, 12))
    payload = {
        "name": "Ethernet switch integration smoke", "network": {"subnet": subnet},
        "devices": [
            {"node_id": "switch-1", "device_type": "ethernet-switch", "name": "Switch",
             "ip": switch_ip, "config": {"role": "switch", "summary": "L2 smoke", "switch_port_capacity": 4}},
            {"node_id": "hmi-1", "device_type": "hmi", "name": "HMI server",
             "ip": hmi_ip, "config": {"role": "hmi", "summary": "TCP listener"}},
            {"node_id": "station-1", "device_type": "attacker-station", "name": "Client station",
             "ip": client_ip, "config": {"role": "attacker", "summary": "TCP client",
                                           "participant_access_enabled": True}},
        ],
        "network_connections": [
            {"connection_id": "switch-hmi", "source_node_id": "switch-1", "target_node_id": "hmi-1"},
            {"connection_id": "switch-client", "source_node_id": "switch-1", "target_node_id": "station-1"},
        ],
    }
    scenario = ScenarioPayload.model_validate(payload)
    runtime = []
    for device in scenario.devices:
        adapter = DEVICE_REGISTRY[device.device_type]()
        item = adapter.build_runtime_device(device)
        item.update({"zone_id": device.zone_id, "zone_subnet": subnet, "zoned": False,
                     "hostname": device.hostname, "network_mode": adapter.network_mode(item),
                     "external_access": False, "external_port": None})
        runtime.append(item)

    try:
        result = manager.deploy(scenario_id, subnet, runtime)
        if result["status"] != "running":
            raise RuntimeError(f"Unexpected scenario status: {result['status']}")
        switch = next(item for item in result["containers"] if item["node_id"] == "switch-1")
        if switch.get("runtime_kind") != "network" or switch.get("status") != "running":
            raise RuntimeError(f"Switch bridge was not reported as active: {switch}")
        containers = {container.labels.get("anvil.node_id"): container
                      for container in manager._scenario_containers(scenario_id)}
        listener = (
            "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
            f"s.bind(({hmi_ip!r},24001)); s.listen(1); c,_=s.accept(); d=c.recv(32); c.sendall(d+b'_OK'); c.close(); s.close()"
        )
        containers["hmi-1"].exec_run(["python", "-c", listener], detach=True)
        client = (
            "import socket,time; s=None\n"
            "for _ in range(30):\n"
            " try: s=socket.create_connection((%r,24001),timeout=.5); break\n"
            " except OSError: time.sleep(.2)\n"
            "if s is None: raise SystemExit('TCP listener unavailable')\n"
            "s.sendall(b'ANVIL_L2'); data=s.recv(32); s.close(); print(data.decode())\n"
            "if data != b'ANVIL_L2_OK': raise SystemExit('unexpected TCP response')"
        ) % hmi_ip
        execution = containers["station-1"].exec_run(["python", "-c", client], demux=True)
        stdout, stderr = execution.output if isinstance(execution.output, tuple) else (execution.output, b"")
        if execution.exit_code != 0 or b"ANVIL_L2_OK" not in (stdout or b""):
            raise RuntimeError(f"TCP through zone bridge failed: exit={execution.exit_code}, stderr={stderr!r}")
        print(f"PASS L2 TCP exchange through switch bridge ({scenario_id}, {subnet})")
    finally:
        manager.stop(scenario_id)


if __name__ == "__main__":
    main()
