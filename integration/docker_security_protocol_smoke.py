"""Deploy confined S7 and Modbus runtimes and exercise both protocols over Docker."""
from __future__ import annotations

import os
import socket
import uuid
from ipaddress import ip_network

import docker
from docker.errors import APIError
from snap7.client import Client

from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.plc_operations import read_device_values as read_s7, write_device_point as write_s7
from services.modbus_operations import read_device_values as read_modbus, write_device_point as write_modbus
from services.scenario_service import ScenarioService
from services.field_operations import field_operation


def free_loopback_port(excluded: set[int] | None = None) -> int:
    excluded = excluded or set()
    for _ in range(10):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in excluded:
            return port
    raise RuntimeError("Could not allocate distinct loopback ports for protocol publication")


def free_subnet(client):
    existing = [
        item.get("Subnet")
        for network in client.networks.list()
        for item in (network.attrs.get("IPAM") or {}).get("Config") or []
        if item.get("Subnet")
    ]
    for third in range(180, 250):
        subnet = ip_network(f"10.248.{third}.0/24")
        if all(not subnet.overlaps(ip_network(used)) for used in existing):
            return subnet
    raise RuntimeError("No free test subnet in 10.248.180.0/24–10.248.249.0/24")


def main() -> None:
    if not all(os.environ.get(name) for name in ("ANVIL_PLC_IMAGE", "ANVIL_MODBUS_IMAGE", "ANVIL_FIELD_IMAGE")):
        raise RuntimeError("Set ANVIL_PLC_IMAGE, ANVIL_MODBUS_IMAGE and ANVIL_FIELD_IMAGE to local test images")
    client = docker.from_env()
    manager = DockerManager(client=client)
    try:
        client.networks.list()
    except APIError as error:
        if error.status_code != 403:
            raise
    else:
        raise AssertionError("Docker API gateway allowed an unfiltered network inventory")
    # The gateway intentionally rejects unfiltered Docker inventory requests.
    # In the integration job, use the runner's Docker client only to discover
    # existing subnets; all scenario mutations and runtime operations still go
    # through DOCKER_HOST (the gateway under test).
    inspect_host = os.environ.get("ANVIL_DOCKER_INSPECT_HOST")
    inspect_client = docker.DockerClient(base_url=inspect_host) if inspect_host else client
    try:
        subnet = free_subnet(inspect_client)
    finally:
        if inspect_client is not client:
            inspect_client.close()
    scenario_id = str(uuid.uuid4())
    s7_loopback_port = free_loopback_port()
    modbus_loopback_port = free_loopback_port({s7_loopback_port})
    payload = ScenarioCreate.model_validate({
        "name": "Runtime least-privilege protocol smoke",
        "network": {"subnet": str(subnet)},
        "devices": [
            {"node_id": "s7", "device_type": "plc-s7", "name": "S7 confined", "ip": str(subnet.network_address + 10),
             "config": {"components": [{"type": "valve", "name": "V1"}]}},
            {"node_id": "mb", "device_type": "modbus-tcp", "name": "Modbus confined", "ip": str(subnet.network_address + 11),
             "config": {"unit_id": 1, "port": 1502, "points": [
                 {"type": "coil", "name": "C1", "initial_value": False},
                 {"type": "holding_register", "name": "HR1", "initial_value": 12},
            ]}},
            {"node_id": "s7-loopback", "device_type": "plc-s7", "name": "S7 loopback export",
             "ip": str(subnet.network_address + 12), "external_access": True,
             "external_host_ip": "127.0.0.1", "external_port": s7_loopback_port,
             "config": {"components": [{"type": "valve", "name": "V2"}]}},
            {"node_id": "mb-loopback", "device_type": "modbus-tcp", "name": "Modbus loopback export",
             "ip": str(subnet.network_address + 13), "external_access": True,
             "external_host_ip": "127.0.0.1", "external_port": modbus_loopback_port,
             "config": {"unit_id": 1, "port": 1502, "points": [
                 {"type": "coil", "name": "C2", "initial_value": False},
             ]}},
        ],
        "field_objects": [{"object_id": "valve-field", "type": "valve", "name": "Field valve"}],
    })
    runtime_devices = ScenarioService._build_runtime_devices(payload)
    try:
        result = manager.deploy(scenario_id, payload.network.subnet, runtime_devices)
        if result["status"] != "running":
            raise AssertionError(f"runtimes did not become healthy: {result}")
        containers = {container.labels["anvil.node_id"]: container for container in client.containers.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"}
        )}
        for container in containers.values():
            container.reload()
            host = container.attrs["HostConfig"]
            assert host["ReadonlyRootfs"] is True
            assert container.attrs["Config"]["User"] == "65534:65534"
            assert host["CapDrop"] == ["ALL"]
            assert "no-new-privileges:true" in host["SecurityOpt"]
            assert host["PidsLimit"] == 32
            assert host["Memory"] > 0
        assert "NET_BIND_SERVICE" in containers["s7"].attrs["HostConfig"]["CapAdd"]
        assert not containers["mb"].attrs["HostConfig"]["CapAdd"]
        assert containers["__anvil_field__"].attrs["Config"]["User"] == "65534:65534"
        initial_field = field_operation(manager, scenario_id)
        assert initial_field["objects"][0]["state"] is False
        field_operation(manager, scenario_id, "control", {"object_id": "valve-field", "fault": True})
        assert field_operation(manager, scenario_id)["objects"][0]["fault"] is True

        by_id = {device["node_id"]: device for device in runtime_devices}
        for node_id in ("s7", "mb"):
            assert not containers[node_id].attrs["HostConfig"].get("PortBindings"), node_id
        for node_id, internal_port, host_port in (
            ("s7-loopback", 102, s7_loopback_port), ("mb-loopback", 1502, modbus_loopback_port),
        ):
            bindings = containers[node_id].attrs["HostConfig"]["PortBindings"]
            assert bindings == {f"{internal_port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(host_port)}]}, bindings
        # Published host bindings are verified above. WSL hosts may not route a
        # Docker loopback publication back into the Linux client namespace, so
        # exercise the actual wire protocols from isolated helper containers on
        # the same internal network. No host port is needed for these clients.
        s7_external = by_id["s7-loopback"]
        s7_probe = containers["s7"].exec_run(
            ["python", "-c",
             "import snap7; c=snap7.client.Client(); c.connect('s7-loopback',0,1,tcp_port=102); assert c.get_connected(); x=c.db_read(1,0,1)[0]; c.db_write(1,0,bytearray([x^1])); assert c.db_read(1,0,1)[0]==(x^1); c.disconnect()"],
            user="65534:65534", stdout=True, stderr=True,
        )
        if s7_probe.exit_code:
            raise AssertionError(f"S7 protocol client failed: {s7_probe.output!r}")
        from pymodbus.client import ModbusTcpClient

        modbus_probe = containers["mb"].exec_run(
            ["python", "-c",
             "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient('mb-loopback',port=1502,timeout=3); assert c.connect(); assert c.read_coils(0,count=1,device_id=1).bits[0] is False; assert not c.write_coil(0,True,device_id=1).isError(); assert c.read_coils(0,count=1,device_id=1).bits[0] is True; c.close()"],
            user="65534:65534", stdout=True, stderr=True,
        )
        if modbus_probe.exit_code:
            raise AssertionError(f"Modbus protocol client failed: {modbus_probe.output!r}")

        s7_snapshot = read_s7(manager, scenario_id, by_id["s7"])
        valve = by_id["s7"]["config"]["components"][0]
        write_s7(manager, scenario_id, by_id["s7"], valve, True)
        assert read_s7(manager, scenario_id, by_id["s7"])["values"][0]["value"] is True

        modbus_snapshot = read_modbus(manager, scenario_id, by_id["mb"])
        coil = by_id["mb"]["config"]["points"][0]
        write_modbus(manager, scenario_id, by_id["mb"], coil, True)
        assert read_modbus(manager, scenario_id, by_id["mb"])["values"][0]["value"] is True
        assert s7_snapshot["values"][0]["value"] is False
        assert modbus_snapshot["values"][0]["value"] is False
        print("PASS: confined S7/Modbus/field runtimes work; S7 and Modbus support loopback host bindings and standard in-network clients")
    finally:
        manager.stop(scenario_id)
        client.close()


if __name__ == "__main__":
    main()
