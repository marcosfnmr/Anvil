"""Execute bounded native-protocol exercise writes from participant stations."""

from __future__ import annotations

import json
from typing import Any

from docker.errors import DockerException

from services.docker_manager import DockerManagerError


_MODBUS_WRITE = r'''import json,sys
from pymodbus.client import ModbusTcpClient
host,port,unit,kind,address,value=sys.argv[1:]
c=ModbusTcpClient(host,port=int(port),timeout=3)
if not c.connect(): raise SystemExit("unable to connect to Modbus target")
try:
 u=int(unit); a=int(address); v=value.lower()=="true" if kind=="coil" else int(value)
 response=c.write_coil(a,v,device_id=u) if kind=="coil" else c.write_register(a,v,device_id=u)
 if response is None or response.isError(): raise SystemExit("Modbus write was rejected")
 read=c.read_coils(a,count=1,device_id=u) if kind=="coil" else c.read_holding_registers(a,count=1,device_id=u)
 if read is None or read.isError(): raise SystemExit("Modbus readback failed")
 result=bool(read.bits[0]) if kind=="coil" else int(read.registers[0])
 print(json.dumps({"value":result}))
finally: c.close()
'''

_S7_WRITE = r'''import json,struct,sys
from snap7.client import Client
from snap7.type import Area
host,port,rack,slot,area,db,offset,bit,kind,value=sys.argv[1:]
c=Client()
if not c.connect(host,int(rack),int(slot),tcp_port=int(port)):
 raise SystemExit("unable to connect to S7 target")
try:
 size=1 if kind=="BOOL" else 4
 def read(n):
  return bytearray(c.db_read(int(db),int(offset),n)) if area=="DB" else bytearray(c.read_area(getattr(Area,{"I":"PE","Q":"PA","M":"MK"}[area]),0,int(offset),n))
 def write(raw):
  return c.db_write(int(db),int(offset),raw) if area=="DB" else c.write_area(getattr(Area,{"I":"PE","Q":"PA","M":"MK"}[area]),0,int(offset),raw)
 if kind=="BOOL":
  mask=1<<int(bit); raw=read(1)
  raw[0]=raw[0]|mask if value.lower()=="true" else raw[0]&~mask
  write(raw); result=bool(read(1)[0]&mask)
 else:
  write(bytearray(struct.pack(">f",float(value))))
  result=float(struct.unpack(">f",read(4))[0])
 print(json.dumps({"value":result}))
finally:
 if c.get_connected(): c.disconnect()
'''


def _source_container(manager: Any, scenario_id: str, node_id: str) -> Any:
    try:
        containers = manager.client.containers.list(
            all=False, filters={"label": f"anvil.scenario_id={scenario_id}"},
        )
    except DockerException as error:
        raise DockerManagerError(f"Unable to locate participant station: {error}") from error
    for container in containers:
        if container.labels.get("anvil.node_id") == node_id:
            return container
    raise LookupError(f"Running participant station '{node_id}' was not found in this scenario")


def execute_protocol_write(
    manager: Any,
    scenario_id: str,
    source: dict[str, Any],
    target: dict[str, Any],
    point: Any,
    component: dict[str, Any] | None,
    data_type: str,
    value: bool | int | float,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a real S7 or Modbus TCP request from the scenario's station container."""
    endpoint = route or {}
    host = endpoint.get("host", target["ip"])
    port = int(endpoint.get("port", 102 if target["device_type"] == "plc-s7"
                            else target["config"].get("port", 502)))
    if target["device_type"] == "modbus-tcp":
        kind, address = point.address.split(":", 1)
        args = ["python", "-c", _MODBUS_WRITE, host, str(port),
                str(target["config"]["unit_id"]), kind, address, str(value).lower()]
    else:
        if component is None:
            raise ValueError("S7 protocol writes require a configured component")
        args = ["python", "-c", _S7_WRITE, host, str(port),
                str(target["config"]["rack"]), str(target["config"]["slot"]),
                str(component.get("area", "DB")), str(component["db"]),
                str(component["byte_offset"]), str(component.get("bit_offset", 0)),
                data_type, str(value).lower()]
    container = _source_container(manager, scenario_id, source["node_id"])
    try:
        result = container.exec_run(args, stdout=True, stderr=True, demux=False)
    except DockerException as error:
        raise DockerManagerError(f"Participant protocol write failed: {error}") from error
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code != 0:
        raise DockerManagerError(f"Participant protocol write failed: {output.strip()}")
    try:
        response = json.loads(output.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise DockerManagerError(f"Participant returned invalid protocol data: {output.strip()}") from error
    return {"value": response["value"], "protocol": "s7" if target["device_type"] == "plc-s7" else "modbus-tcp",
            "source_node_id": source["node_id"], "target_node_id": target["node_id"],
            "address": point.address}
