#!/usr/bin/env python3
"""Docker end-to-end test of the bounded inline Modbus TCP rewrite relay."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import secrets
import socket
import time
import uuid

import docker
from docker.types import IPAMConfig, IPAMPool


IMAGE = os.environ.get("ANVIL_RF38_IMAGE", "anvil/plc-s7:rf38-gateway-local")
SERVER_PORT = 1502
GATEWAY_PORT = 24000

SERVER = r'''import socket,struct,threading
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("0.0.0.0",1502));s.listen(16)
register=123
coils=[False]*64
discrete_inputs=[False]*64
discrete_inputs[3]=True
def exact(c,n):
 b=b""
 while len(b)<n:
  d=c.recv(n-len(b))
  if not d: raise EOFError()
  b+=d
 return b
while True:
 c,_=s.accept()
 try:
  with c:
   h=exact(c,6);n=struct.unpack(">H",h[4:6])[0];p=exact(c,n)
   if len(p)<6:continue
   unit,fc=p[:2];start,qty=struct.unpack(">HH",p[2:6])
   if fc in (3,4):
    values=b"".join(struct.pack(">H",register) for _ in range(qty));body=bytes([unit,fc,len(values)])+values
    c.sendall(h[:4]+struct.pack(">H",len(body))+body)
   elif fc in (1,2):
    values=bytearray((qty+7)//8)
    points=coils if fc==1 else discrete_inputs
    for i in range(qty):
     if points[start+i]: values[i//8]|=1<<(i%8)
    body=bytes([unit,fc,len(values)])+values
    c.sendall(h[:4]+struct.pack(">H",len(body))+body)
   elif fc==15:
    count=struct.unpack(">H",p[2:4])[0];byte_count=p[4]
    for i in range(count): coils[start+i]=bool((p[6+i//8]>>(i%8))&1)
    body=p[:6]
    c.sendall(h[:4]+struct.pack(">H",len(body))+body)
   elif fc==5:
    coils[start]=(p[4:6]==b"\xff\x00")
    c.sendall(h+p)
   elif fc==6:
    register=struct.unpack(">H",p[4:6])[0]
    c.sendall(h+p)
   elif fc==22:
    and_mask,or_mask=struct.unpack(">HH",p[4:8]);register=(register&and_mask)|(or_mask&(~and_mask&0xffff))
    c.sendall(h+p)
   elif fc==23:
    read_start,read_qty,write_start,write_qty=struct.unpack(">HHHH",p[2:10]);byte_count=p[10]
    if byte_count!=write_qty*2 or len(p)!=11+byte_count:continue
    if write_start<=10<write_start+write_qty:
     offset=11+(10-write_start)*2;register=struct.unpack(">H",p[offset:offset+2])[0]
    values=b"".join(struct.pack(">H",register) for _ in range(read_qty))
    body=bytes([unit,fc,len(values)])+values
    c.sendall(h[:4]+struct.pack(">H",len(body))+body)
 except (EOFError,OSError,struct.error):pass
'''

CLIENT = r'''import json,socket,sys
gateway,server=sys.argv[1:3]
operation=sys.argv[3]
def exchange(host,port,frame):
 s=socket.create_connection((host,port),timeout=1.5);s.settimeout(1.5);s.sendall(frame)
 h=b""
 while len(h)<6:h+=s.recv(6-len(h))
 n=int.from_bytes(h[4:6],"big");rest=b""
 while len(rest)<n:rest+=s.recv(n-len(rest))
 s.close();return h+rest
def read(host,port):
 return int.from_bytes(exchange(host,port,bytes.fromhex("000c000000060103000a0001"))[-2:],"big")
def read_coil(host,port):
 return bool(exchange(host,port,bytes.fromhex("000f00000006010100020001"))[9]&1)
def read_bit(host,port,function_code,address):
 frame=(0x21).to_bytes(2,"big")+b"\x00\x00\x00\x06"+bytes([1,function_code])+address.to_bytes(2,"big")+b"\x00\x01"
 return bool(exchange(host,port,frame)[9]&1)
write_ack=None
coil_value=None
fc23_response=None
through_route=read(gateway,24000)
if operation=="readcoil":
 through_route=int(read_bit(gateway,24000,1,2))
elif operation=="readdiscrete":
 through_route=int(read_bit(gateway,24000,2,3))
elif operation=="seedcoil":
 exchange(gateway,24000,bytes.fromhex("00200000000601050002ff00"))
 through_route=int(read_bit(gateway,24000,1,2))
elif operation=="write":
 value=int(sys.argv[4]);response=exchange(gateway,24000,b"\x00\x0d\x00\x00\x00\x06\x01\x06\x00\x0a"+value.to_bytes(2,"big"))
 write_ack=int.from_bytes(response[-2:],"big")
elif operation=="coilwrite":
 response=exchange(gateway,24000,bytes.fromhex("000e00000008010f000200010100"))
 write_ack=int.from_bytes(response[-2:],"big")
 coil_value=read_coil(gateway,24000)
elif operation=="singlecoilwrite":
 response=exchange(gateway,24000,bytes.fromhex("001000000006010500020000"))
 write_ack=int.from_bytes(response[-2:],"big") == 0xff00
 coil_value=read_coil(gateway,24000)
elif operation=="maskwrite":
 response=exchange(gateway,24000,bytes.fromhex("0011000000080116000affff0000"))
 write_ack=response[10:14].hex()
elif operation=="readwrite":
 response=exchange(gateway,24000,bytes.fromhex("00120000000d0117000a0001000a0001025678"))
 fc23_response=int.from_bytes(response[9:11],"big")
if operation not in ("readcoil","readdiscrete","seedcoil"):
 through_route=read(gateway,24000)
print(json.dumps({"through_route":through_route,"write_ack":write_ack,"coil_value":coil_value,"fc23_response":fc23_response}))
try:
 read(server,1502);print("DIRECT=reachable")
except OSError:print("DIRECT=blocked")
'''


def _choose_subnets(client) -> tuple[str, str]:
    used = []
    for network in client.networks.list():
        for item in (network.attrs.get("IPAM") or {}).get("Config") or []:
            subnet = item.get("Subnet")
            if subnet:
                try:
                    used.append(ipaddress.ip_network(subnet, strict=False))
                except ValueError:
                    continue
    for third in range(210, 255, 2):
        first = ipaddress.ip_network(f"172.31.{third}.0/24")
        second = ipaddress.ip_network(f"172.31.{third + 1}.0/24")
        if not any(first.overlaps(item) or second.overlaps(item) for item in used):
            return str(first), str(second)
    raise RuntimeError("No free 172.31.210.0/24–172.31.254.0/24 pair is available")


def _network(client, name: str, subnet: str, scenario_id: str):
    return client.networks.create(
        name, driver="bridge", internal=True,
        ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
        labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                "anvil.zone_id": name.rsplit("-", 1)[-1]},
    )


def _create(client, name: str, command: list[str], scenario_id: str):
    return client.containers.create(
        IMAGE, name=name, command=command, detach=True, user="65534:65534",
        read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
        tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"}, mem_limit="96m", pids_limit=32,
        labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                "anvil.node_id": name, "anvil.role": name},
    )


def _client_observation(container, gateway_ip: str, server_ip: str,
                        write_value: int | None = None, *, coil_write: bool = False,
                        single_coil_write: bool = False,
                        mask_write: bool = False, read_write: bool = False,
                        bit_function: int | None = None, seed_coil: bool = False
                        ) -> tuple[int, int | str | None, bool | None, int | None]:
    result = container.exec_run(
        ["python", "-c", CLIENT, gateway_ip, server_ip,
         "seedcoil" if seed_coil else "readcoil" if bit_function == 1 else "readdiscrete" if bit_function == 2 else
         "readwrite" if read_write else "maskwrite" if mask_write else "singlecoilwrite" if single_coil_write else
         "coilwrite" if coil_write else "write" if write_value is not None else "read", str(write_value or 0)], user="65534:65534",
        stdout=True, stderr=True,
    )
    output = result.output.decode("utf-8", errors="replace")
    if result.exit_code != 0 or "DIRECT=blocked" not in output:
        raise AssertionError(f"client request failed or direct cross-zone access was possible: {output}")
    value = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    return value["through_route"], value["write_ack"], value["coil_value"], value["fc23_response"]


def main() -> None:
    client = docker.from_env()
    scenario_id = str(uuid.uuid4())
    prefix = f"anvil-rf38-{scenario_id[:8]}"
    created: list[object] = []
    networks: list[object] = []
    volumes: list[object] = []
    try:
        client.images.get(IMAGE)
        source_subnet, target_subnet = _choose_subnets(client)
        source_net = _network(client, f"{prefix}-src", source_subnet, scenario_id)
        networks.append(source_net)
        target_net = _network(client, f"{prefix}-dst", target_subnet, scenario_id)
        networks.append(target_net)
        source_addresses = list(ipaddress.ip_network(source_subnet).hosts())
        target_addresses = list(ipaddress.ip_network(target_subnet).hosts())
        client_ip, gateway_source_ip = map(str, source_addresses[1:3])
        gateway_target_ip, server_ip = map(str, target_addresses[1:3])

        server = _create(client, f"{prefix}-server", ["python", "-u", "-c", SERVER], scenario_id)
        created.append(server)
        target_net.connect(server, ipv4_address=server_ip)
        server.start()

        token = secrets.token_urlsafe(32)
        audit_node = "__anvil_route_rf38-smoke"
        audit_volume_name = (
            f"anvil-protocol-audit-{scenario_id}-"
            f"{hashlib.sha256(audit_node.encode()).hexdigest()[:16]}"
        )
        audit_volume = client.volumes.create(name=audit_volume_name, labels={
            "anvil.managed": "true", "anvil.purpose": "protocol-audit",
            "anvil.scenario_id": scenario_id, "anvil.node_id": audit_node,
            "anvil.volume_name": audit_volume_name,
        })
        volumes.append(audit_volume)
        ack_token = secrets.token_urlsafe(32)
        route_config = json.dumps({
            "route_id": "rf38-smoke", "listen_ip": gateway_source_ip,
            "listen_port": GATEWAY_PORT, "source_ip": client_ip,
            "target_ip": server_ip, "target_port": SERVER_PORT, "latency_ms": 0,
            "inspect_protocol": "modbus-tcp", "control_token": token,
        }, separators=(",", ":"))
        gateway = client.containers.create(
            IMAGE, name=f"{prefix}-route", command=["python", "-u", "tcp_gateway.py"],
            environment={"ANVIL_TCP_GATEWAY_CONFIG": route_config,
                         "ANVIL_TCP_GATEWAY_CONTROL_TOKEN": token,
                         "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": ack_token},
            detach=True, user="65534:65534", read_only=True, cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"}, mem_limit="96m", pids_limit=32,
            labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                    "anvil.node_id": audit_node, "anvil.role": "tcp-gateway"},
            volumes={audit_volume.name: {"bind": "/var/lib/anvil/protocol-audit", "mode": "rw"}},
        )
        created.append(gateway)
        source_net.connect(gateway, ipv4_address=gateway_source_ip)
        target_net.connect(gateway, ipv4_address=gateway_target_ip)
        gateway.start()

        station = _create(client, f"{prefix}-client", ["python", "-u", "-c", "import time;time.sleep(3600)"], scenario_id)
        created.append(station)
        source_net.connect(station, ipv4_address=client_ip)
        station.start()

        deadline = time.monotonic() + 15
        while True:
            try:
                original, _, _, _ = _client_observation(station, gateway_source_ip, server_ip)
                break
            except (docker.errors.APIError, AssertionError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        if original != 123:
            raise AssertionError(f"unexpected unmodified server value: {original}")

        seeded_coil, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=1, seed_coil=True,
        )
        original_discrete, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=2,
        )
        if seeded_coil != 1 or original_discrete != 1:
            raise AssertionError(f"FC01/FC02 baseline state is wrong: coil={seeded_coil}, discrete_input={original_discrete}")
        bit_rules = [
            {"rule_id": "hide-coil", "unit_id": 1, "function_code": 1,
             "address": 2, "replacement_value": 0,
             "exercise_id": "exercise-smoke", "event_id": "bit-read-window"},
            {"rule_id": "hide-discrete-input", "unit_id": 1, "function_code": 2,
             "address": 3, "replacement_value": 0,
             "exercise_id": "exercise-smoke", "event_id": "bit-read-window"},
        ]
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps(bit_rules, separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC01/FC02 activation: {update.output!r}")
        altered_coil, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=1,
        )
        altered_discrete, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=2,
        )
        if altered_coil != 0 or altered_discrete != 0:
            raise AssertionError(f"FC01/FC02 response bits were not substituted: coil={altered_coil}, discrete_input={altered_discrete}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway FC01/FC02 audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        bit_evidence = [json.loads(item["line"]) for item in queue.get("events", [])]
        for function_code, rule_id in ((1, "hide-coil"), (2, "hide-discrete-input")):
            if not any(item.get("function_code") == function_code
                       and item.get("direction") == "response"
                       and item.get("rule_ids") == [rule_id]
                       and item.get("event_id") == "bit-read-window"
                       for item in bit_evidence):
                raise AssertionError(f"FC{function_code} bit rewrite lacks durable response audit evidence: {bit_evidence!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC01/FC02 clear: {clear.output!r}")
        restored_coil, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=1,
        )
        restored_discrete, _, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, bit_function=2,
        )
        if restored_coil != 1 or restored_discrete != 1:
            raise AssertionError(f"FC01/FC02 clear changed stored server state: coil={restored_coil}, discrete_input={restored_discrete}")

        rule = {"rule_id": "pressure", "unit_id": 1, "function_code": 3,
                "address": 10, "replacement_value": 65000,
                "exercise_id": "exercise-smoke", "event_id": "rewrite-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused authenticated activation: {update.output!r}")
        altered, _, _, _ = _client_observation(station, gateway_source_ip, server_ip)
        if altered != 65000:
            raise AssertionError(f"client did not observe substituted value: {altered}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        evidence = next((json.loads(item["line"]) for item in queue.get("events", [])
                         if json.loads(item["line"]).get("rule_ids") == ["pressure"]), None)
        if evidence is None:
            raise AssertionError(f"gateway did not durably retain the FC03 read match: {queue!r}")
        if (evidence.get("exercise_id") != "exercise-smoke"
                or evidence.get("event_id") != "rewrite-window"
                or evidence.get("rule_ids") != ["pressure"]
                or evidence.get("direction") != "response"):
            raise AssertionError(f"gateway audit evidence is not linked to the rewrite: {evidence!r}")

        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused authenticated clear: {clear.output!r}")
        write_rule = {"rule_id": "forced-setpoint", "unit_id": 1, "function_code": 6,
                      "address": 10, "replacement_value": 65000,
                      "exercise_id": "exercise-smoke", "event_id": "write-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([write_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC06 activation: {update.output!r}")
        after_write, write_ack, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, write_value=42,
        )
        if write_ack != 65000 or after_write != 65000:
            raise AssertionError(f"FC06 write was not replaced at the server: ack={write_ack}, read={after_write}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway write audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        write_evidence = next((item for item in events if item.get("direction") == "request"), None)
        if (write_evidence is None or write_evidence.get("function_code") != 6
                or write_evidence.get("event_id") != "write-window"
                or write_evidence.get("rule_ids") != ["forced-setpoint"]):
            raise AssertionError(f"FC06 write rewrite lacks durable audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC06 clear: {clear.output!r}")
        coil_rule = {"rule_id": "open-valve-coil", "unit_id": 1, "function_code": 15,
                     "address": 2, "replacement_value": 1,
                     "exercise_id": "exercise-smoke", "event_id": "coil-write-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([coil_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC15 activation: {update.output!r}")
        _, coil_ack, coil_value, _ = _client_observation(
            station, gateway_source_ip, server_ip, coil_write=True,
        )
        if coil_ack != 1 or coil_value is not True:
            raise AssertionError(f"FC15 coil write was not replaced at the server: ack={coil_ack}, coil={coil_value}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway coil audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        coil_evidence = next((item for item in events if item.get("function_code") == 15), None)
        if (coil_evidence is None or coil_evidence.get("direction") != "request"
                or coil_evidence.get("event_id") != "coil-write-window"
                or coil_evidence.get("rule_ids") != ["open-valve-coil"]):
            raise AssertionError(f"FC15 coil rewrite lacks durable audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC15 clear: {clear.output!r}")
        single_coil_rule = {"rule_id": "open-single-coil", "unit_id": 1, "function_code": 5,
                            "address": 2, "replacement_value": 1,
                            "exercise_id": "exercise-smoke", "event_id": "single-coil-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([single_coil_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC05 activation: {update.output!r}")
        _, single_coil_ack, single_coil_value, _ = _client_observation(
            station, gateway_source_ip, server_ip, single_coil_write=True,
        )
        if single_coil_ack is not True or single_coil_value is not True:
            raise AssertionError(f"FC05 coil write was not replaced at the server: ack={single_coil_ack}, coil={single_coil_value}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway single coil audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        single_coil_evidence = next((item for item in events if item.get("function_code") == 5), None)
        if (single_coil_evidence is None or single_coil_evidence.get("direction") != "request"
                or single_coil_evidence.get("event_id") != "single-coil-window"
                or single_coil_evidence.get("rule_ids") != ["open-single-coil"]):
            raise AssertionError(f"FC05 coil rewrite lacks durable audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC05 clear: {clear.output!r}")
        mask_rule = {"rule_id": "force-mask-register", "unit_id": 1, "function_code": 22,
                     "address": 10, "replacement_value": 42330,
                     "exercise_id": "exercise-smoke", "event_id": "mask-write-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([mask_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC22 activation: {update.output!r}")
        register_after_mask, mask_ack, _, _ = _client_observation(
            station, gateway_source_ip, server_ip, mask_write=True,
        )
        if mask_ack != "0000a55a" or register_after_mask != 42330:
            raise AssertionError(f"FC22 mask write did not force the server register: ack={mask_ack}, value={register_after_mask}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway FC22 audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        mask_evidence = next((item for item in events if item.get("function_code") == 22), None)
        if (mask_evidence is None or mask_evidence.get("direction") != "request"
                or mask_evidence.get("event_id") != "mask-write-window"
                or mask_evidence.get("rule_ids") != ["force-mask-register"]):
            raise AssertionError(f"FC22 mask rewrite lacks durable audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC22 clear: {clear.output!r}")
        read_write_rule = {"rule_id": "force-combined-read", "unit_id": 1, "function_code": 23,
                           "address": 10, "replacement_value": 0xC0DE,
                           "exercise_id": "exercise-smoke", "event_id": "read-write-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([read_write_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC23 activation: {update.output!r}")
        register_after_read_write, _, _, fc23_response = _client_observation(
            station, gateway_source_ip, server_ip, read_write=True,
        )
        if fc23_response != 0xC0DE or register_after_read_write != 0x5678:
            raise AssertionError(
                f"FC23 read response was not rewritten independently of its write: "
                f"response={fc23_response}, server={register_after_read_write}")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "10"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway FC23 audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        read_write_evidence = next((item for item in events if item.get("function_code") == 23), None)
        if (read_write_evidence is None or read_write_evidence.get("direction") != "response"
                or read_write_evidence.get("event_id") != "read-write-window"
                or read_write_evidence.get("rule_ids") != ["force-combined-read"]):
            raise AssertionError(f"FC23 rewrite lacks durable response audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC23 clear: {clear.output!r}")
        final, _, _, _ = _client_observation(station, gateway_source_ip, server_ip)
        if final != 0x5678:
            raise AssertionError(f"FC23 write portion did not update the server register: {final}")
        combined_write_rule = {"rule_id": "force-combined-write", "unit_id": 1,
                               "function_code": 23, "phase": "write", "address": 10,
                               "replacement_value": 0xBEEF, "exercise_id": "exercise-smoke",
                               "event_id": "read-write-request-window"}
        update = gateway.exec_run(
            ["python", "tcp_route_control.py", "set", json.dumps([combined_write_rule], separators=(",", ":"))],
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app", stdout=True, stderr=True,
        )
        if update.exit_code != 0:
            raise AssertionError(f"gateway refused FC23 write activation: {update.output!r}")
        register_after_combined_write, _, _, combined_write_response = _client_observation(
            station, gateway_source_ip, server_ip, read_write=True,
        )
        if register_after_combined_write != 0xBEEF or combined_write_response != 0xBEEF:
            raise AssertionError("FC23 write request was not rewritten before reaching the server")
        queued = gateway.exec_run(
            ["python", "protocol_audit.py", "pending", "20"], user="65534:65534",
            workdir="/app", stdout=True, stderr=True,
        )
        if queued.exit_code != 0:
            raise AssertionError(f"gateway FC23 write audit queue could not be read: {queued.output!r}")
        queue = json.loads(queued.output.decode("utf-8", errors="replace").splitlines()[-1])
        events = [json.loads(item["line"]) for item in queue.get("events", [])]
        evidence = next((item for item in events
                         if item.get("event_id") == "read-write-request-window"), None)
        if (evidence is None or evidence.get("direction") != "request"
                or evidence.get("rule_ids") != ["force-combined-write"]):
            raise AssertionError(f"FC23 write rewrite lacks durable request audit evidence: {events!r}")
        clear = gateway.exec_run(
            ["python", "tcp_route_control.py", "clear"], user="65534:65534",
            environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            stdout=True, stderr=True,
        )
        if clear.exit_code != 0:
            raise AssertionError(f"gateway refused FC23 write clear: {clear.output!r}")
        restored, _, _, _ = _client_observation(station, gateway_source_ip, server_ip)
        if restored != 0xBEEF:
            raise AssertionError(f"FC23 write result did not persist in the server: {restored}")
        print(json.dumps({"result": "passed", "before": original, "read_response_during": altered,
                          "requested_write": 42, "server_after_write": final,
                          "requested_coil_write": False, "server_coil_after_write": coil_value,
                          "requested_single_coil_write": False, "server_after_single_coil_write": single_coil_value,
                          "requested_mask_write": "0xFFFF/0x0000", "mask_write_echo": mask_ack,
                          "server_after_mask_write": register_after_mask,
                          "requested_fc23_write": "0x5678", "fc23_response_during": fc23_response,
                          "server_after_fc23": register_after_read_write,
                          "fc23_request_rewritten_to": "0xBEEF",
                          "server_after_fc23_request_rewrite": restored,
                          "fc01_coil_before_during_after": [seeded_coil, altered_coil, restored_coil],
                          "fc02_discrete_before_during_after": [original_discrete, altered_discrete, restored_discrete],
                          "direct_cross_zone": "blocked",
                          "durable_match_audit": True,
                          "published_ports": 0}, separators=(",", ":")))
    finally:
        for container in reversed(created):
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        for network in reversed(networks):
            try:
                network.remove()
            except docker.errors.NotFound:
                pass
        for volume in reversed(volumes):
            try:
                volume.remove(force=True)
            except docker.errors.NotFound:
                pass
        client.close()


if __name__ == "__main__":
    main()
