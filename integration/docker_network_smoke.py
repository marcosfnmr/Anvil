"""Check scenario isolation and real S7/Modbus clients on its Docker network."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import socket
import tarfile

import docker

from platform_smoke import request


def deployment_snapshot(client, scenario_id: str) -> dict:
    """Capture stable Docker configuration while excluding generated engine IDs."""
    containers = client.containers.list(all=True, filters={"label": f"anvil.scenario_id={scenario_id}"})
    nodes = {}
    host_keys = (
        "NetworkMode", "PortBindings", "ReadonlyRootfs", "CapDrop", "CapAdd",
        "SecurityOpt", "Memory", "NanoCpus", "PidsLimit", "Privileged", "Tmpfs",
    )
    for container in containers:
        container.reload()
        attrs = container.attrs
        config, host = attrs["Config"], attrs["HostConfig"]
        networks = attrs.get("NetworkSettings", {}).get("Networks") or {}
        stable_networks = {}
        for name, endpoint in sorted(networks.items()):
            engine_aliases = set(endpoint.get("Aliases") or []) - {container.id, container.short_id}
            stable_networks[name] = {
                "IPAddress": endpoint.get("IPAddress"),
                "IPPrefixLen": endpoint.get("IPPrefixLen"),
                "Gateway": endpoint.get("Gateway"),
                "Aliases": sorted(engine_aliases),
            }
        nodes[container.labels["anvil.node_id"]] = {
            "name": container.name,
            "image": config["Image"],
            "image_id": attrs["Image"],
            "hostname": config["Hostname"],
            "labels": config["Labels"],
            "environment": sorted(config.get("Env") or []),
            "command": config.get("Cmd"),
            "entrypoint": config.get("Entrypoint"),
            "user": config.get("User"),
            "healthcheck": config.get("Healthcheck"),
            "exposed_ports": config.get("ExposedPorts"),
            "host": {key: host.get(key) for key in host_keys},
            "networks": stable_networks,
        }
    networks = {}
    for network in client.networks.list(filters={"label": f"anvil.scenario_id={scenario_id}"}):
        network.reload()
        attrs = network.attrs
        networks[network.name] = {
            "driver": attrs.get("Driver"),
            "internal": attrs.get("Internal"),
            "labels": attrs.get("Labels"),
            "ipam": attrs.get("IPAM"),
        }
    return {"nodes": nodes, "networks": networks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    client = docker.from_env()
    scenario_id = None
    payload = {
        "name": "Docker network regression", "network": {"subnet": "172.30.242.0/24"},
        "devices": [
            {"node_id": "s7", "hostname": "control-plc", "device_type": "plc-s7", "name": "S7", "ip": "172.30.242.10",
             "config": {"components": [{"type": "valve", "name": "valve", "initial_state": False},
                                         {"type": "analog_sensor", "name": "sensor"},
                                         {"type": "io_channel", "name": "marker", "area": "M", "direction": "output"},
                                         {"type": "io_channel", "name": "digital-input", "area": "I", "direction": "input"},
                                         {"type": "io_channel", "name": "digital-output", "area": "Q", "direction": "output"}]}},
            {"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus", "ip": "172.30.242.11",
             "config": {"unit_id": 7, "port": 1502, "points": [
                 {"type": "coil", "name": "coil", "initial_value": False},
                 {"type": "discrete_input", "name": "discrete", "initial_value": False},
                 {"type": "holding_register", "name": "holding", "initial_value": 12},
                 {"type": "input_register", "name": "input", "initial_value": 42}]}},
            {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.242.12",
             "config": {"role": "hmi", "summary": "Console HMI", "target_node_ids": "s7,modbus"}},
            {"node_id": "diagnostic", "device_type": "attacker-station", "name": "Diagnostic", "ip": "172.30.242.13",
             "config": {"role": "attacker", "summary": "Participant client in control zone",
                        "participant_access_enabled": True}},
        ],
    }
    try:
        scenario_id = request(args.base_url, "/api/scenarios", "POST", payload)["id"]
        path = f"/api/scenarios/{scenario_id}"
        assert request(args.base_url, f"{path}/deploy", "POST")["status"] == "running"
        paused = request(args.base_url, f"{path}/pause", "POST")
        assert paused["status"] == "paused", paused
        assert all(item["status"] == "paused" for item in paused["containers"]), paused
        resumed = request(args.base_url, f"{path}/resume", "POST")
        assert resumed["status"] == "running", resumed
        node_paused = request(args.base_url, f"{path}/nodes/s7/lifecycle", "POST", {"action": "pause"})
        assert node_paused["status"] == "partial", node_paused
        node_resumed = request(args.base_url, f"{path}/nodes/s7/lifecycle", "POST", {"action": "resume"})
        assert node_resumed["status"] == "running", node_resumed
        containers = client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        assert len(containers) == 4
        by_node = {container.labels["anvil.node_id"]: container for container in containers}
        network = f"anvil-{scenario_id[:12]}"
        scenario_network = client.networks.get(network)
        assert scenario_network.attrs.get("Internal") is True, scenario_network.attrs
        assert scenario_network.attrs.get("Options", {}).get(
            "com.docker.network.bridge.gateway_mode_ipv4"
        ) == "isolated", scenario_network.attrs
        for device in payload["devices"]:
            container = by_node[device["node_id"]]
            container.reload()
            assert not container.attrs["HostConfig"]["PortBindings"], container.name
            if device["device_type"] in {"plc-s7", "modbus-tcp"}:
                assert container.attrs["HostConfig"]["LogConfig"] == {
                    "Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}
                }, container.attrs["HostConfig"]["LogConfig"]
            expected_hostname = device.get("hostname", device["node_id"])
            assert container.attrs["Config"]["Hostname"] == expected_hostname, container.name
            if device["node_id"] == "diagnostic":
                assert container.attrs["HostConfig"]["NetworkMode"] != "none"
                assert container.attrs["HostConfig"]["CapDrop"] == ["ALL"]
                assert container.attrs["HostConfig"]["ReadonlyRootfs"] is True
            networks = container.attrs["NetworkSettings"]["Networks"]
            assert list(networks) == [network], networks
            assert networks[network]["IPAddress"] == device["ip"]
            assert expected_hostname in networks[network].get("Aliases", []), networks
        probe_result = by_node["diagnostic"].exec_run([
            "python", "-c",
            "import snap7; c=snap7.client.Client(); c.connect('172.30.242.10',0,1); "
            "assert len(c.db_read(1,0,1)) == 1; c.disconnect()",
        ], stdout=True, stderr=True)
        assert probe_result.exit_code == 0, probe_result.output
        first_snapshot = deployment_snapshot(client, scenario_id)
        assert set(first_snapshot["nodes"]) == {item["node_id"] for item in payload["devices"]}
        print("Docker isolation OK: participant station reaches S7 in its internal zone; no host bindings or extra capabilities")

        dns_check = client.containers.create(
            by_node["hmi"].image.id,
            command=["python", "-c", "import socket; assert socket.gethostbyname('control-plc') == '172.30.242.10'; assert socket.gethostbyname('modbus') == '172.30.242.11'"],
            network=network, healthcheck={"test": ["NONE"]},
            labels={"anvil.integration": "docker-network-smoke-dns"},
        )
        try:
            dns_check.start()
            result = dns_check.wait(timeout=8)
            assert result.get("StatusCode") == 0, dns_check.logs().decode(errors="replace")
            print("Docker DNS OK: configured and default hostnames resolve to scenario IPs")
        finally:
            dns_check.remove(force=True)

        probe = client.containers.create(
            by_node["diagnostic"].image.id,
            command=["python", "-c", "import socket; s=socket.create_connection(('1.1.1.1',443),timeout=3)"],
            network=network, healthcheck={"test": ["NONE"]},
            labels={"anvil.integration": "docker-network-smoke-egress"},
        )
        try:
            probe.start()
            result = probe.wait(timeout=8)
            logs = probe.logs().decode(errors="replace")
            assert result.get("StatusCode") != 0, "scenario container unexpectedly reached public network"
            assert "timed out" in logs.lower() or "network is unreachable" in logs.lower(), logs
            print("Egress isolation OK: participant station cannot open TCP to 1.1.1.1:443")
        finally:
            probe.remove(force=True)

        # A normal internal bridge has a host-side gateway address. Engine 28's
        # isolated gateway mode must remove this host path as well as internet egress.
        default_bridge = client.networks.get("bridge")
        host_gateway = default_bridge.attrs["IPAM"]["Config"][0].get("Gateway")
        assert host_gateway, "Docker's default bridge did not report a host gateway"
        host_listener = socket.socket()
        host_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        host_listener.bind((host_gateway, 0))
        host_listener.listen(1)
        host_listener.settimeout(3)
        host_port = host_listener.getsockname()[1]
        probe = client.containers.create(
            by_node["diagnostic"].image.id,
            command=["python", "-c", (
                "import socket; "
                f"addr=('{host_gateway}',{host_port}); "
                "s=socket.socket(); s.settimeout(2); "
                "\ntry: s.connect(addr)\n"
                "except OSError: raise SystemExit(0)\n"
                "else: raise SystemExit('scenario reached a host listener')"
            )],
            network=network, healthcheck={"test": ["NONE"]},
            labels={"anvil.integration": "docker-network-smoke-host"},
        )
        try:
            probe.start()
            result = probe.wait(timeout=8)
            logs = probe.logs().decode(errors="replace")
            assert result.get("StatusCode") == 0, logs
            try:
                connection, address = host_listener.accept()
                connection.close()
                raise AssertionError(f"scenario reached host listener from {address}")
            except TimeoutError:
                pass
            print("Host isolation OK: participant station cannot reach a host bridge listener")
        finally:
            host_listener.close()
            probe.remove(force=True)

        # Capture deterministic process-image defaults before protocol writes.
        s7_baseline = by_node["s7"].exec_run([
            "python", "-c",
            "import math,struct,snap7; from snap7.type import Area; c=snap7.client.Client(); c.connect('127.0.0.1',0,1); "
            "db=c.db_read(1,0,6); valve=bool(db[0]&1); sensor=struct.unpack_from('>f',db,2)[0]; "
            "areas={name:c.read_area(area,0,0,1)[0] for name,area in [('M',Area.MK),('I',Area.PE),('Q',Area.PA)]}; c.disconnect(); "
            "assert valve is False and math.isclose(sensor,50.0,abs_tol=0.01) and areas=={'M':0,'I':0,'Q':0}, (valve,sensor,areas)",
        ], stdout=True, stderr=True)
        assert s7_baseline.exit_code == 0, s7_baseline.output
        modbus_baseline = by_node["modbus"].exec_run([
            "python", "-c",
            "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient('127.0.0.1',port=1502); assert c.connect(); "
            "assert c.read_coils(0,count=1,device_id=7).bits[0] is False; "
            "assert c.read_discrete_inputs(0,count=1,device_id=7).bits[0] is False; "
            "assert c.read_holding_registers(0,count=1,device_id=7).registers[0]==12; c.close()",
        ], stdout=True, stderr=True)
        assert modbus_baseline.exit_code == 0, modbus_baseline.output

        for node, script, arguments in [
            ("s7", "s7_smoke.py", ["--host", "172.30.242.10", "--port", "102"]),
            ("modbus", "modbus_smoke.py", ["--ip", "172.30.242.11", "--port", "1502", "--unit-id", "7"]),
        ]:
            probe = client.containers.create(
                by_node[node].image.id, command=["python", "-c", "import time; time.sleep(120)"],
                network=network, healthcheck={"test": ["NONE"]},
                labels={"anvil.integration": "docker-network-smoke"},
            )
            try:
                archive = io.BytesIO()
                content = Path(__file__).with_name(script).read_bytes()
                with tarfile.open(fileobj=archive, mode="w") as tar:
                    info = tarfile.TarInfo(script)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
                probe.put_archive("/tmp", archive.getvalue())
                probe.start()
                result = probe.exec_run(["python", f"/tmp/{script}", *arguments])
                print(result.output.decode().strip())
                assert result.exit_code == 0, f"{node} protocol client failed"
            finally:
                probe.remove(force=True)

        assert request(args.base_url, f"{path}/stop", "POST")["status"] == "stopped"
        assert request(args.base_url, f"{path}/deploy", "POST")["status"] == "running"
        second_snapshot = deployment_snapshot(client, scenario_id)
        if first_snapshot != second_snapshot:
            raise AssertionError("Docker deployment configuration changed between identical runs:\n"
                                 + json.dumps({"first": first_snapshot, "second": second_snapshot}, indent=2))
        by_node = {container.labels["anvil.node_id"]: container for container in client.containers.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"}
        )}
        s7_reset = by_node["s7"].exec_run([
            "python", "-c",
            "import math,struct,snap7; from snap7.type import Area; c=snap7.client.Client(); c.connect('127.0.0.1',0,1); "
            "db=c.db_read(1,0,6); valve=bool(db[0]&1); sensor=struct.unpack_from('>f',db,2)[0]; "
            "areas={name:c.read_area(area,0,0,1)[0] for name,area in [('M',Area.MK),('I',Area.PE),('Q',Area.PA)]}; c.disconnect(); "
            "assert valve is False and math.isclose(sensor,50.0,abs_tol=0.01) and areas=={'M':0,'I':0,'Q':0}, (valve,sensor,areas)",
        ], stdout=True, stderr=True)
        assert s7_reset.exit_code == 0, f"S7 initial process image was not restored: {s7_reset.output!r}"
        modbus_reset = by_node["modbus"].exec_run([
            "python", "-c",
            "from pymodbus.client import ModbusTcpClient; c=ModbusTcpClient('127.0.0.1',port=1502); assert c.connect(); "
            "coil=c.read_coils(0,count=1,device_id=7).bits[0]; "
            "discrete=c.read_discrete_inputs(0,count=1,device_id=7).bits[0]; "
            "holding=c.read_holding_registers(0,count=1,device_id=7).registers[0]; "
            "input_register=c.read_input_registers(0,count=1,device_id=7).registers[0]; c.close(); "
            "assert coil is False and discrete is False and holding==12 and input_register==42, (coil,discrete,holding,input_register)",
        ], stdout=True, stderr=True)
        assert modbus_reset.exit_code == 0, f"Modbus address spaces were not restored: {modbus_reset.output!r}"
        print("Reproducibility OK: Docker settings, all configured S7 process images, and all four Modbus spaces match after redeploy")
        return 0
    finally:
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
