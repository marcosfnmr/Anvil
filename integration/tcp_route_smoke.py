"""Exercise explicit SCADA/HMI-to-PLC routes across isolated Docker zones."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:srs-v2")
os.environ.setdefault("ANVIL_VIRTUAL_IMAGE", os.environ["ANVIL_PLC_IMAGE"])
os.environ.setdefault("ANVIL_SCADA_IMAGE", "anvil/scada:srs-v1")
os.environ.setdefault("ANVIL_HMI_IMAGE", os.environ["ANVIL_SCADA_IMAGE"])
os.environ.setdefault("ANVIL_MODBUS_IMAGE", "anvil/modbus-tcp:field-v1")

import docker

from models.scenario import ScenarioCreate
from devices import DEVICE_REGISTRY
from services.docker_manager import DockerManager
from services.hmi_operations import hmi_operation
from services.scenario_service import ScenarioService


def main() -> None:
    client = docker.from_env()
    existing = {
        item.get("IPAM", {}).get("Config", [{}])[0].get("Subnet")
        for network in client.networks.list()
        for item in [network.attrs]
        if item.get("IPAM", {}).get("Config")
    }
    subnets = next(((f"172.29.{octet}.0/24", f"172.29.{octet + 1}.0/24", f"172.29.{octet + 2}.0/24")
                    for octet in range(200, 238, 3)
                    if all(f"172.29.{value}.0/24" not in existing for value in (octet, octet + 1, octet + 2))), None)
    if subnets is None:
        raise RuntimeError("No free isolated test subnet triplet in 172.29.200.0/24–172.29.239.0/24")
    control_subnet, operations_subnet, field_subnet = subnets
    control_prefix = control_subnet.rsplit(".", 1)[0]
    operations_prefix = operations_subnet.rsplit(".", 1)[0]
    field_prefix = field_subnet.rsplit(".", 1)[0]
    scenario_id = str(uuid4())
    payload = {
        "name": "TCP route integration smoke",
        "network": {"subnet": control_subnet, "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": control_subnet},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": operations_subnet},
            {"zone_id": "field", "name": "Field", "purdue_level": "L0", "subnet": field_subnet},
        ]},
        "devices": [
            {"node_id": "route-plc", "device_type": "plc-s7", "name": "Route PLC",
             "ip": f"{control_prefix}.10", "zone_id": "control",
             "config": {"components": [{"type": "valve", "name": "route_test", "initial_state": True}]}},
            {"node_id": "route-scada", "device_type": "scada", "name": "Route SCADA",
             "ip": f"{operations_prefix}.10", "zone_id": "operations",
             "config": {"target_node_ids": "route-plc,route-modbus", "poll_interval_ms": 250}},
            {"node_id": "route-hmi", "device_type": "hmi", "name": "Route HMI",
             "ip": f"{operations_prefix}.11", "zone_id": "operations",
             "config": {"role": "hmi", "summary": "Cross-zone route smoke", "target_node_ids": "route-plc,route-modbus"}},
            {"node_id": "route-modbus", "device_type": "modbus-tcp", "name": "Route Modbus",
             "ip": f"{field_prefix}.10", "zone_id": "field",
             "config": {"unit_id": 7, "port": 1502, "points": [
                 {"type": "coil", "name": "route_test", "address": 0, "initial_value": True},
             ]}},
        ],
        "network_routes": [
            {"route_id": "scada-to-s7", "source_node_id": "route-scada",
             "target_node_id": "route-plc", "protocol": "s7", "enabled": True},
            {"route_id": "scada-to-modbus", "source_node_id": "route-scada",
             "target_node_id": "route-modbus", "protocol": "modbus-tcp", "enabled": True},
            {"route_id": "hmi-to-s7", "source_node_id": "route-hmi",
             "target_node_id": "route-plc", "protocol": "s7", "enabled": True},
            {"route_id": "hmi-to-modbus", "source_node_id": "route-hmi",
             "target_node_id": "route-modbus", "protocol": "modbus-tcp", "enabled": True},
            {"route_id": "hmi-generic-tcp-modbus", "source_node_id": "route-hmi",
             "target_node_id": "route-modbus", "protocol": "tcp", "tcp_port": 1502,
             "latency_ms": 40, "enabled": True},
        ],
    }
    scenario = ScenarioCreate.model_validate(payload)
    runtime = ScenarioService._build_runtime_devices(scenario)
    manager = DockerManager(client)
    try:
        manager.deploy(scenario_id, scenario.network.subnet, runtime)
        runtime_by_id = {item["node_id"]: item for item in runtime}
        hmi = runtime_by_id["route-hmi"]
        generic_route = hmi["config"]["tcp_routes"]["hmi-generic-tcp-modbus"]
        probe_results = []
        probe_deadline = time.monotonic() + 20
        while time.monotonic() < probe_deadline:
            probe_results = []
            for route in scenario.network_routes:
                source = runtime_by_id[route.source_node_id]
                target = runtime_by_id[route.target_node_id]
                endpoint = (source["config"]["tcp_routes"][route.route_id] if route.protocol == "tcp"
                            else source["config"]["network_routes"][route.target_node_id])
                probe_results.append({
                    "route_id": route.route_id,
                    **manager.probe_network_route(
                        scenario_id,
                        source_node_id=route.source_node_id,
                        host=endpoint["host"],
                        port=endpoint["port"],
                        protocol=route.protocol,
                        target_config=target["config"],
                    ),
                })
            if all(item["status"] == "reachable" for item in probe_results):
                break
            time.sleep(0.25)
        if any(item["status"] != "reachable" for item in probe_results):
            raise RuntimeError(f"Active route probes failed: {probe_results}")
        hmi_reads = {}
        for target_id in ("route-plc", "route-modbus"):
            target = runtime_by_id[target_id]
            points = DEVICE_REGISTRY[target["device_type"]]().build_io_map(target)
            route = hmi["config"]["network_routes"][target_id]
            hmi_reads[target_id] = hmi_operation(manager, scenario_id, "route-hmi", target, points, route)
        assert any(item["value"] is True for item in hmi_reads["route-plc"]), hmi_reads
        assert any(item["value"] is True for item in hmi_reads["route-modbus"]), hmi_reads
        scada = next(item for item in manager._scenario_containers(scenario_id)
                     if (item.labels or {}).get("anvil.node_id") == "route-scada")
        rogue_route_check = scada.exec_run(
            ["python", "-c",
             "import socket,sys\n"
             "s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=2); s.settimeout(2)\n"
             "try:\n"
             " s.sendall(bytes.fromhex('000100000006070100000001'))\n"
             " response=s.recv(260)\n"
             " if response: raise SystemExit('another same-zone node used a route assigned to HMI')\n"
             "except TimeoutError:\n raise SystemExit('unauthorized source was not rejected promptly')\n"
             "except OSError:\n pass\n"
             "finally:\n s.close()\n"
             "print('route source restricted')\n",
             generic_route["host"], str(generic_route["port"])], stdout=True, stderr=True)
        if rogue_route_check.exit_code:
            raise AssertionError("Route listener accepted a different source node: " + str(rogue_route_check.output))
        target_ip = f"{control_prefix}.10"
        script = r'''import json,socket,sys,time,urllib.request
data=json.load(urllib.request.urlopen("http://127.0.0.1:8766/snapshot",timeout=2))
values=data.get("values",[])
assert values and all(item.get("quality")=="good" for item in values), data
assert {item["node_id"] for item in values}=={"route-plc","route-modbus"}, data
assert any(item["node_id"]=="route-modbus" and item["value"] is True for item in values), data
def write(node,address,value):
 body=json.dumps({"target_node_id":node,"address":address,"value":value}).encode()
 request=urllib.request.Request("http://127.0.0.1:8766/write",data=body,headers={"Content-Type":"application/json"},method="POST")
 return json.load(urllib.request.urlopen(request,timeout=3))
assert write("route-plc","DB1.DBX0.0",False)["quality"]=="good"
assert write("route-modbus","coil:0",False)["quality"]=="good"
time.sleep(.5)
data=json.load(urllib.request.urlopen("http://127.0.0.1:8766/snapshot",timeout=2))
values=data.get("values",[])
assert all(item.get("quality")=="good" for item in values), data
assert any(item["node_id"]=="route-modbus" and item["value"] is False for item in values), data
assert any(item["node_id"]=="route-plc" and item["value"] is False for item in values), data
for address,port in ((sys.argv[1],102),(sys.argv[2],1502)):
 try:
  socket.create_connection((address,port),timeout=.5)
  raise AssertionError("direct cross-zone path unexpectedly succeeded")
 except OSError:
  pass
print(json.dumps({"quality":"good","s7_write":"passed","modbus_write":"passed","direct_path":"blocked"}))
'''
        deadline = time.monotonic() + 35
        result = None
        last_output = ""
        while time.monotonic() < deadline:
            execution = scada.exec_run(["python", "-c", script, target_ip, f"{field_prefix}.10"], stdout=True, stderr=True)
            last_output = execution.output.decode("utf-8", errors="replace") if isinstance(execution.output, bytes) else str(execution.output)
            if execution.exit_code == 0:
                result = json.loads(last_output.strip().splitlines()[-1])
                break
            time.sleep(0.4)
        if result is None:
            service_logs = {
                (item.labels or {}).get("anvil.node_id", item.name): item.logs(tail=30).decode("utf-8", errors="replace")
                for item in manager._scenario_containers(scenario_id)
            }
            service_states = {
                (item.labels or {}).get("anvil.node_id", item.name): {
                    "state": item.attrs.get("State", {}),
                    "networks": item.attrs.get("NetworkSettings", {}).get("Networks", {}),
                    "cmd": item.attrs.get("Config", {}).get("Cmd", []),
                }
                for item in manager._scenario_containers(scenario_id)
            }
            raise RuntimeError(f"SCADA routed protocol read/write failed: {last_output[-1200:]}; states={service_states}; logs={service_logs}")
        for target_id, address, value in (("route-plc", "DB1.DBX0.0", True), ("route-modbus", "coil:0", True)):
            target = runtime_by_id[target_id]
            points = [item for item in DEVICE_REGISTRY[target["device_type"]]().build_io_map(target)
                      if item["address"] == address]
            route = hmi["config"]["network_routes"][target_id]
            written = hmi_operation(manager, scenario_id, "route-hmi", target, points, route,
                                    address=address, value=value)
            assert written and written[0]["value"] is value, written
        hmi_container = next(item for item in manager._scenario_containers(scenario_id)
                             if (item.labels or {}).get("anvil.node_id") == "route-hmi")
        direct_check = hmi_container.exec_run(
            ["python", "-c",
             "import socket,sys\n"
             "for host,port in ((sys.argv[1],102),(sys.argv[2],1502)):\n"
             " try: socket.create_connection((host,int(port)),timeout=.5)\n"
             " except OSError: continue\n"
             " raise SystemExit('direct cross-zone connection unexpectedly succeeded')\n",
             f"{control_prefix}.10", f"{field_prefix}.10"], stdout=True, stderr=True)
        if direct_check.exit_code:
            raise AssertionError("HMI can bypass a cross-zone gateway: " + str(direct_check.output))
        generic_check = hmi_container.exec_run(
            ["python", "-c",
             "import socket,sys\n"
             "s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=2); s.close()\n"
             "try: socket.create_connection((sys.argv[3],int(sys.argv[4])),timeout=.5)\n"
             "except OSError: print('generic route allowed; direct route blocked'); raise SystemExit(0)\n"
             "raise SystemExit('direct cross-zone TCP unexpectedly succeeded')\n",
             generic_route["host"], str(generic_route["port"]), f"{field_prefix}.10", "1502"],
            stdout=True, stderr=True)
        if generic_check.exit_code:
            raise AssertionError("Generic TCP route allow/deny check failed: " + str(generic_check.output))
        latency_check = hmi_container.exec_run(
            ["python", "-c",
             "import socket,sys,time\n"
             "s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=3); s.settimeout(3)\n"
             "start=time.monotonic(); s.sendall(bytes.fromhex('000100000006070100000001'))\n"
             "response=b''\n"
             "while len(response)<9:\n"
             " chunk=s.recv(260)\n"
             " if not chunk: break\n"
             " response+=chunk\n"
             "elapsed=time.monotonic()-start; s.close()\n"
             "assert len(response)>=9, (response.hex(),elapsed)\n"
             "assert elapsed>=0.06, elapsed\n"
             "print('Modbus TCP response delayed through route: %.3fs'%elapsed)\n",
             generic_route["host"], str(generic_route["port"])], stdout=True, stderr=True)
        if latency_check.exit_code:
            raise AssertionError("Configured route latency was not observed: " + str(latency_check.output))
        route_containers = [item for item in manager._scenario_containers(scenario_id)
                            if (item.labels or {}).get("anvil.role") == "tcp-gateway"]
        route_networks = []
        for route_container in route_containers:
            route_container.reload()
            if route_container.attrs.get("HostConfig", {}).get("PortBindings"):
                raise AssertionError("The TCP route gateway must not publish host ports")
            route_networks.append(sorted(route_container.attrs["NetworkSettings"]["Networks"]))
        print(json.dumps({"status": "passed", **result, "active_route_probes": probe_results,
                          "hmi_routes": "read/write passed",
                          "generic_tcp_route": "allowed only from declared HMI; other same-zone source and direct cross-zone denied",
                          "generic_tcp_route_latency": "40ms per direction verified by delayed Modbus/TCP response",
                          "gateway_networks": route_networks}))
    finally:
        manager.stop(scenario_id)


if __name__ == "__main__":
    main()
