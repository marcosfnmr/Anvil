"""Verify host-local S7 and Modbus TCP clients through explicit loopback binds."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from ipaddress import ip_network
from uuid import uuid4

import docker

from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.scenario_service import ScenarioService


PROTOCOL_PROBE = r'''import json,sys
import socket,struct
from pymodbus.client import ModbusTcpClient
from snap7.client import Client as Snap7Client
plc_port,modbus_port=map(int,sys.argv[1:3])
cr=bytes([17,0xe0,0,0,0,1,0,0xc1,2,1,0,0xc2,2,1,1,0xc0,1,0x0a])
packet=b"\x03\x00"+struct.pack(">H",len(cr)+4)+cr
with socket.create_connection(("127.0.0.1",plc_port),timeout=3) as raw:
 raw.sendall(packet); header=raw.recv(4)
 if len(header)!=4: raise AssertionError(f"short TPKT response: {header!r}")
 size=struct.unpack(">H",header[2:])[0]; body=b""
 while len(body)<size-4: body+=raw.recv(size-4-len(body))
 if not body or body[1]!=0xd0: raise AssertionError(f"COTP connection failed: {body.hex()}")
s7=Snap7Client()
try:
 s7.connect("127.0.0.1",0,1,tcp_port=plc_port)
 if not s7.get_connected(): raise AssertionError("Snap7 did not connect")
 before=s7.db_read(1,0,1)[0]
 s7.db_write(1,0,bytearray([before^1]))
 if s7.db_read(1,0,1)[0] != (before^1): raise AssertionError("S7 write/readback failed")
finally:
 if s7.get_connected(): s7.disconnect()
modbus=ModbusTcpClient("127.0.0.1",port=modbus_port,timeout=3)
try:
 if not modbus.connect(): raise AssertionError("PyModbus did not connect")
 initial=modbus.read_coils(0,count=1,device_id=1)
 if initial.isError() or initial.bits[0] is not False: raise AssertionError(f"unexpected initial coil: {initial!r}")
 written=modbus.write_coil(0,True,device_id=1)
 readback=modbus.read_coils(0,count=1,device_id=1)
 if written.isError() or readback.isError() or readback.bits[0] is not True: raise AssertionError(f"Modbus write/readback failed: {written!r}, {readback!r}")
finally:
 modbus.close()
print(json.dumps({"s7":"COTP and Snap7 read/write passed","modbus":"coil read/write passed"}))
'''

WINDOWS_HOST_PROBE = r'''const net=require("net");
const plcPort=Number(process.argv[1]), modbusPort=Number(process.argv[2]);
function tpkt(payload){const out=Buffer.alloc(4+payload.length);out[0]=3;out[1]=0;out.writeUInt16BE(out.length,2);payload.copy(out,4);return out;}
function exchange(port,frame,isModbus=false){return new Promise((resolve,reject)=>{
 let data=Buffer.alloc(0),remaining=30,socket;
 const timer=setTimeout(()=>{if(socket)socket.destroy();reject(new Error("protocol response timed out"));},10000);
 function open(){socket=net.createConnection({host:"127.0.0.1",port});
  socket.on("connect",()=>socket.write(frame));
  socket.on("data",chunk=>{data=Buffer.concat([data,chunk]);if(data.length>=6){const size=isModbus?data.readUInt16BE(4)+6:data.readUInt16BE(2);if(data.length>=size){clearTimeout(timer);socket.end();resolve(data.subarray(0,size));}}});
  socket.on("error",error=>{if(error.code==="ECONNREFUSED"&&remaining-->0){setTimeout(open,250);return;}clearTimeout(timer);reject(error);});
 }
 open();
 });}
function modbusFrame(tid,fc,payload){const pdu=Buffer.from([fc,...payload]);const out=Buffer.alloc(7+pdu.length);out.writeUInt16BE(tid,0);out.writeUInt16BE(0,2);out.writeUInt16BE(1+pdu.length,4);out[6]=1;pdu.copy(out,7);return out;}
(async()=>{
 const cr=Buffer.from([17,0xe0,0,0,0,1,0,0xc1,2,1,0,0xc2,2,1,1,0xc0,1,0x0a]);
 const cc=await exchange(plcPort,tpkt(cr));if(cc[0]!==3||cc[5]!==0xd0)throw new Error("S7 COTP connection was not confirmed");
 let response=await exchange(modbusPort,modbusFrame(1,1,[0,0,0,1]),true);
 if(response[7]!==1||response[8]!==1||(response[9]&1)!==0)throw new Error("unexpected initial Modbus coil response: "+response.toString("hex"));
 response=await exchange(modbusPort,modbusFrame(2,5,[0,0,0xff,0]),true);if(response[7]!==5)throw new Error("Modbus coil write failed");
 response=await exchange(modbusPort,modbusFrame(3,1,[0,0,0,1]),true);if(response[7]!==1||(response[9]&1)!==1)throw new Error("Modbus coil readback failed");
 console.log(JSON.stringify({s7:"COTP handshake passed",modbus:"coil read/write passed"}));
})().catch(error=>{console.error(error.stack||String(error));process.exitCode=1;});
'''


def free_subnet(client: docker.DockerClient) -> str:
    used = {
        item["Subnet"]
        for network in client.networks.list()
        for item in (network.attrs.get("IPAM") or {}).get("Config") or []
        if item.get("Subnet")
    }
    for third_octet in range(180, 251):
        candidate = ip_network(f"10.247.{third_octet}.0/24")
        if all(not candidate.overlaps(ip_network(value)) for value in used):
            return str(candidate)
    raise RuntimeError("No free isolated test subnet in 10.247.180.0/24–10.247.250.0/24")


def free_loopback_port(excluded: set[int]) -> int:
    for _ in range(20):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            candidate = probe.getsockname()[1]
        if candidate not in excluded:
            return candidate
    raise RuntimeError("Could not reserve distinct loopback ports for S7 and Modbus")


def main() -> None:
    if not all(os.environ.get(key) for key in ("ANVIL_PLC_IMAGE", "ANVIL_MODBUS_IMAGE")):
        raise RuntimeError("Set ANVIL_PLC_IMAGE and ANVIL_MODBUS_IMAGE to local runtime images")

    docker_client = docker.from_env()
    manager = DockerManager(client=docker_client)
    scenario_id = str(uuid4())
    try:
        requested_subnet = os.environ.get("ANVIL_TEST_SUBNET")
        if requested_subnet:
            selected_subnet = ip_network(requested_subnet, strict=True)
            if selected_subnet.version != 4 or selected_subnet.prefixlen != 24:
                raise ValueError("ANVIL_TEST_SUBNET must be an IPv4 /24 subnet")
            subnet = str(selected_subnet)
        else:
            subnet = free_subnet(docker_client)
        plc_port = free_loopback_port(set())
        modbus_port = free_loopback_port({plc_port})
        network = ip_network(subnet)
        payload = ScenarioCreate.model_validate({
            "name": "Host loopback protocol smoke",
            "network": {"subnet": subnet},
            "devices": [
                {
                    "node_id": "host-s7",
                    "device_type": "plc-s7",
                    "name": "S7 loopback",
                    "ip": str(network.network_address + 10),
                    "external_access": True,
                    "external_host_ip": "127.0.0.1",
                    "external_port": plc_port,
                    "config": {"components": [{"type": "valve", "name": "test", "initial_state": False}]},
                },
                {
                    "node_id": "host-modbus",
                    "device_type": "modbus-tcp",
                    "name": "Modbus loopback",
                    "ip": str(network.network_address + 11),
                    "external_access": True,
                    "external_host_ip": "127.0.0.1",
                    "external_port": modbus_port,
                    "config": {"unit_id": 1, "port": 1502, "points": [
                        {"type": "coil", "name": "test", "address": 0, "initial_value": False},
                    ]},
                },
            ],
        })
        runtime_devices = ScenarioService._build_runtime_devices(payload)
        manager.pin_runtime_images(runtime_devices)
        result = manager.deploy(scenario_id, subnet, runtime_devices)
        if result.get("status") != "running":
            raise AssertionError(f"scenario did not become healthy: {result}")
        containers_by_node = {
            container.labels.get("anvil.node_id"): container
            for container in docker_client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        }
        bindings = {
            node_id: (container.reload() or container.attrs).get("HostConfig", {}).get("PortBindings", {})
            for node_id, container in containers_by_node.items()
        }

        probe_context = "host-process"
        try:
            sys.argv = ["host-probe", str(plc_port), str(modbus_port)]
            exec(compile(PROTOCOL_PROBE, "<host-probe>", "exec"), {})
        except Exception as host_error:
            windows_node = os.environ.get("ANVIL_WINDOWS_NODE")
            if not windows_node:
                details = {}
                for node_id, container in containers_by_node.items():
                    try:
                        container.reload()
                        logs = container.logs(stdout=True, stderr=True, tail=30)
                        details[node_id] = {
                            "status": container.status,
                            "logs": logs.decode("utf-8", errors="replace")[-1500:],
                            "networks": list((container.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys()),
                        }
                    except Exception as diagnostic_error:
                        details[node_id] = {"diagnostic_error": str(diagnostic_error)}
                raise RuntimeError(
                    f"Host protocol probe failed; Docker bindings={bindings}; container diagnostics={details}. "
                    "For WSL with Windows localhost forwarding, set ANVIL_WINDOWS_NODE "
                    "to a Windows node.exe path and retry."
                ) from host_error
            result = subprocess.run(
                [windows_node, "-e", WINDOWS_HOST_PROBE, str(plc_port), str(modbus_port)],
                capture_output=True,
                timeout=20,
            )
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace")[-1200:]
                diagnostics = {}
                for node_id, container in containers_by_node.items():
                    try:
                        logs = container.logs(stdout=True, stderr=True, tail=30)
                        diagnostics[node_id] = logs.decode("utf-8", errors="replace")[-1800:]
                    except Exception as diagnostic_error:
                        diagnostics[node_id] = str(diagnostic_error)
                raise RuntimeError(
                    f"Windows-host protocol probe failed: {detail}; Docker port bindings={bindings}; logs={diagnostics}"
                ) from host_error
            print(result.stdout.decode("utf-8", errors="replace").strip())
            probe_context = "windows-host-node"

        print(json.dumps({
            "status": "passed",
            "s7": f"Snap7 read/write through 127.0.0.1:{plc_port}",
            "modbus": f"PyModbus coil read/write through 127.0.0.1:{modbus_port}",
            "probe_context": probe_context,
            "scope": "same-host loopback only; no claim of remote-host reachability",
        }))
    finally:
        if os.environ.get("ANVIL_KEEP_EXTERNAL_PROBE") != "1":
            active_error = sys.exc_info()[1]
            cleanup_failures = []
            try:
                manager.stop(scenario_id)
            except Exception as stop_error:
                cleanup_failures.append(f"stop: {stop_error}")
            cleanup_failures.extend(manager._cleanup_failed_deploy(scenario_id, [], []))
            docker_client.close()
            if cleanup_failures:
                detail = "External protocol probe cleanup incomplete: " + "; ".join(cleanup_failures)
                if active_error is not None:
                    print(detail, file=sys.stderr)
                else:
                    raise RuntimeError(detail)
        else:
            print(json.dumps({"preserved_scenario_id": scenario_id}))
            docker_client.close()


if __name__ == "__main__":
    main()
