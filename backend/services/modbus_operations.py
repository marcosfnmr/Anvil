"""Modbus TCP operations against a managed simulator container."""

from __future__ import annotations

import json
from typing import Any

from docker.errors import DockerException

from services.docker_manager import DockerManagerError

_MODBUS_SCRIPT = r'''import json, sys
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
action, transport, port, unit_id, kind, address, value, baudrate, bytesize, parity, stopbits = sys.argv[1:]
if transport == "rtu":
    client = ModbusSerialClient(f"socket://127.0.0.1:{int(port)}", baudrate=int(baudrate), bytesize=int(bytesize), parity=parity, stopbits=int(stopbits), timeout=2)
else:
    client = ModbusTcpClient("127.0.0.1", port=int(port), timeout=2)
if not client.connect():
    raise SystemExit("Unable to connect to Modbus server")
try:
    kwargs = {"device_id": int(unit_id)}
    if action == "read":
        if kind == "coil": result = client.read_coils(int(address), count=1, **kwargs).bits[0]
        elif kind == "discrete_input": result = client.read_discrete_inputs(int(address), count=1, **kwargs).bits[0]
        elif kind == "holding_register": result = client.read_holding_registers(int(address), count=1, **kwargs).registers[0]
        else: result = client.read_input_registers(int(address), count=1, **kwargs).registers[0]
    elif kind == "coil":
        result = client.write_coil(int(address), value == "true", **kwargs).isError()
        if result: raise SystemExit("Modbus coil write failed")
        result = client.read_coils(int(address), count=1, **kwargs).bits[0]
    elif kind == "holding_register":
        result = client.write_register(int(address), int(value), **kwargs).isError()
        if result: raise SystemExit("Modbus register write failed")
        result = client.read_holding_registers(int(address), count=1, **kwargs).registers[0]
    else:
        raise SystemExit("Modbus discrete inputs and input registers are read-only")
    bit_point = kind in {"coil", "discrete_input"}
    response = {"value": result, "raw_hex": ("01" if result else "00") if bit_point else f"{int(result):04x}"}
    try:
        from field_bridge import quality_for
        communication = quality_for(f'{kind}:{address}')
        if communication is not None: response['communication'] = communication
    except ImportError:
        pass
    print(json.dumps(response))
finally:
    client.close()
'''

_RTU_READ_ALL_SCRIPT = r'''import json, sys
from pymodbus.client import ModbusSerialClient
config = json.loads(sys.argv[1])
client = ModbusSerialClient(
    f"socket://127.0.0.1:{int(config['port'])}", baudrate=int(config.get('baudrate', 19200)),
    bytesize=8, parity='N', stopbits=1, timeout=2, retries=1,
)
if not client.connect():
    raise SystemExit("Unable to connect to Modbus RTU server")
try:
    values = []
    kwargs = {"device_id": int(config["unit_id"])}
    for point in config["points"]:
        kind, address = point["type"], int(point["address"])
        if kind == "coil": response = client.read_coils(address, count=1, **kwargs)
        elif kind == "discrete_input": response = client.read_discrete_inputs(address, count=1, **kwargs)
        elif kind == "holding_register": response = client.read_holding_registers(address, count=1, **kwargs)
        else: response = client.read_input_registers(address, count=1, **kwargs)
        if response is None or response.isError(): raise SystemExit(f"Modbus exception for {kind}:{address}")
        bit_point = kind in {"coil", "discrete_input"}
        value = bool(response.bits[0]) if bit_point else int(response.registers[0])
        values.append({"type": kind, "address": address, "name": point["name"], "value": value,
                       "raw_hex": ("01" if value else "00") if bit_point else f"{value:04x}",
                       "unit": point.get("unit") or None})
    print(json.dumps({"values": values}))
finally:
    client.close()
'''


def _container(manager: Any, scenario_id: str, node_id: str) -> Any:
    try:
        containers = manager.client.containers.list(all=False, filters={"label": f"anvil.scenario_id={scenario_id}"})
        for container in containers:
            if container.labels.get("anvil.node_id") == node_id:
                return container
    except DockerException as error:
        raise DockerManagerError(f"Unable to locate Modbus container: {error}") from error
    raise LookupError(f"Running Modbus device '{node_id}' was not found in this scenario")


def _run(manager: Any, scenario_id: str, runtime: dict[str, Any], point: dict[str, Any], value: bool | int | None = None) -> dict[str, Any]:
    kind = point["type"]
    action = "read" if value is None else "write"
    config = runtime["config"]
    args = ["python", "-c", _MODBUS_SCRIPT, action, config.get("transport", "tcp"),
            str(config["port"]), str(config["unit_id"]), kind, str(point["address"]),
            "" if value is None else str(value).lower(), str(config.get("baudrate", 19200)),
            str(config.get("bytesize", 8)), config.get("parity", "N"), str(config.get("stopbits", 1))]
    container = _container(manager, scenario_id, runtime["node_id"])
    try:
        result = container.exec_run(args, stdout=True, stderr=True, demux=False)
    except DockerException as error:
        raise DockerManagerError(f"Unable to communicate with Modbus device: {error}") from error
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code != 0:
        raise DockerManagerError(f"Modbus operation failed: {output.strip()}")
    try:
        response = json.loads(output.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise DockerManagerError(f"Modbus device returned an invalid response: {output.strip()}") from error
    prefix = {"coil": "coil", "discrete_input": "discrete_input", "holding_register": "holding_register", "input_register": "input_register"}[kind]
    return {"node_id": runtime["node_id"], "address": f"{prefix}:{point['address']}", "component_name": point["name"], "data_type": "BOOL" if kind in {"coil", "discrete_input"} else "UINT16", "value": response["value"], "raw_hex": response["raw_hex"], "unit": point.get("unit") or None, **({'communication':response['communication']} if 'communication' in response else {})}


def read_device_values(manager: Any, scenario_id: str, runtime: dict[str, Any]) -> dict[str, Any]:
    if runtime["config"].get("transport", "tcp") == "rtu":
        container = _container(manager, scenario_id, runtime["node_id"])
        try:
            result = container.exec_run([
                "python", "-c", _RTU_READ_ALL_SCRIPT,
                json.dumps(runtime["config"], separators=(",", ":")),
            ], stdout=True, stderr=True, demux=False)
        except DockerException as error:
            raise DockerManagerError(f"Unable to communicate with Modbus RTU device: {error}") from error
        output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
        if result.exit_code != 0:
            raise DockerManagerError(f"Modbus RTU operation failed: {output.strip()}")
        try:
            values = json.loads(output.strip().splitlines()[-1])["values"]
        except (json.JSONDecodeError, IndexError, KeyError) as error:
            raise DockerManagerError(f"Modbus RTU device returned invalid data: {output.strip()}") from error
        formatted = []
        for item in values:
            kind = item["type"]
            formatted.append({
                "node_id": runtime["node_id"],
                "address": f"{kind}:{item['address']}",
                "component_name": item["name"],
                "data_type": "BOOL" if kind in {"coil", "discrete_input"} else "UINT16",
                "value": item["value"], "raw_hex": item["raw_hex"], "unit": item.get("unit"),
            })
        return {"scenario_id": scenario_id, "node_id": runtime["node_id"], "values": formatted}
    values = [_run(manager, scenario_id, runtime, point) for point in runtime["config"]["points"]]
    return {"scenario_id": scenario_id, "node_id": runtime["node_id"], "values": values}


def write_device_point(manager: Any, scenario_id: str, runtime: dict[str, Any], point: dict[str, Any], value: bool | int) -> dict[str, Any]:
    return {"scenario_id": scenario_id, **_run(manager, scenario_id, runtime, point, value)}
