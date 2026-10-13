"""Protocol operations initiated inside an HMI container's network namespace."""

from __future__ import annotations

import json
import math
import re
import struct
import sys
from typing import Any


def _read_s7(client: Any, point: dict[str, Any]) -> dict[str, Any]:
    address = point["address"]
    bool_match = re.fullmatch(r"DB(\d+)\.DBX(\d+)\.([0-7])", address)
    real_match = re.fullmatch(r"DB(\d+)\.DBD(\d+)", address)
    area_match = re.fullmatch(r"([MIQ])(\d+)\.([0-7])", address)
    area_real_match = re.fullmatch(r"([MIQ])D(\d+)", address)
    from snap7.type import Area
    read_area = lambda area, offset, size: client.read_area({"I": Area.PE, "Q": Area.PA, "M": Area.MK}[area], 0, offset, size)
    if bool_match:
        db, offset, bit = map(int, bool_match.groups())
        raw = bytes(client.db_read(db, offset, 1))
        value = bool(raw[0] & (1 << bit))
    elif area_match:
        area, offset, bit = area_match.group(1), int(area_match.group(2)), int(area_match.group(3))
        raw = bytes(read_area(area, offset, 1)); value = bool(raw[0] & (1 << bit))
    elif real_match:
        db, offset = map(int, real_match.groups())
        raw = bytes(client.db_read(db, offset, 4))
        value = struct.unpack(">f", raw)[0]
        if not math.isfinite(value):
            raise ValueError(f"S7 returned a non-finite value for {address}")
    elif area_real_match:
        area, offset = area_real_match.group(1), int(area_real_match.group(2))
        raw = bytes(read_area(area, offset, 4)); value = struct.unpack(">f", raw)[0]
        if not math.isfinite(value): raise ValueError(f"S7 returned a non-finite value for {address}")
    else:
        raise ValueError(f"unsupported S7 address {address}")
    return {**point, "value": value, "raw_hex": raw.hex(" ")}


def _operate_s7(target: dict[str, Any], action: str, address: str | None, value: Any) -> list[dict[str, Any]]:
    from snap7.client import Client

    client = Client()
    client.connect(target["host"], int(target["rack"]), int(target["slot"]), tcp_port=int(target["port"]))
    try:
        points = target["points"] if action == "read" else [
            item for item in target["points"] if item["address"] == address
        ]
        if action == "write":
            if not points:
                raise LookupError(f"S7 address {address} is not in the HMI map")
            point = points[0]
            bool_match = re.fullmatch(r"DB(\d+)\.DBX(\d+)\.([0-7])", address or "")
            real_match = re.fullmatch(r"DB(\d+)\.DBD(\d+)", address or "")
            area_bool = re.fullmatch(r"([MQ])(\d+)\.([0-7])", address or "")
            area_real = re.fullmatch(r"([MQ])D(\d+)", address or "")
            from snap7.type import Area
            if bool_match and point["data_type"] == "BOOL" and type(value) is bool:
                db, offset, bit = map(int, bool_match.groups())
                raw = bytearray(client.db_read(db, offset, 1))
                raw[0] = raw[0] | (1 << bit) if value else raw[0] & ~(1 << bit)
                client.db_write(db, offset, raw)
            elif area_bool and point["data_type"] == "BOOL" and type(value) is bool:
                area, offset, bit = area_bool.group(1), int(area_bool.group(2)), int(area_bool.group(3))
                memory_area = {"M": Area.MK, "Q": Area.PA}[area]
                raw = bytearray(client.read_area(memory_area, 0, offset, 1))
                raw[0] = raw[0] | (1 << bit) if value else raw[0] & ~(1 << bit)
                client.write_area(memory_area, 0, offset, raw)
            elif real_match and point["data_type"] == "REAL" and type(value) in (int, float) and math.isfinite(value):
                db, offset = map(int, real_match.groups())
                low, high = point.get("min_value"), point.get("max_value")
                if low is not None and value < low or high is not None and value > high:
                    raise ValueError("S7 REAL value is outside the configured range")
                client.db_write(db, offset, bytearray(struct.pack(">f", float(value))))
            elif area_real and point["data_type"] == "REAL" and type(value) in (int, float) and math.isfinite(value):
                area, offset = area_real.group(1), int(area_real.group(2))
                low, high = point.get("min_value"), point.get("max_value")
                if low is not None and value < low or high is not None and value > high: raise ValueError("S7 REAL value is outside the configured range")
                client.write_area({"M": Area.MK, "Q": Area.PA}[area], 0, offset, bytearray(struct.pack(">f", float(value))))
            else:
                raise ValueError("S7 value type does not match the mapped output")
        return [_read_s7(client, point) for point in points]
    finally:
        if client.get_connected():
            client.disconnect()


def _operate_modbus(target: dict[str, Any], action: str, address: str | None, value: Any) -> list[dict[str, Any]]:
    if target.get("transport", "tcp") == "rtu":
        from pymodbus.client import ModbusSerialClient
        client = ModbusSerialClient(
            f"socket://{target['host']}:{int(target['port'])}",
            baudrate=int(target.get("baudrate", 19200)),
            bytesize=int(target.get("bytesize", 8)), parity=target.get("parity", "N"),
            stopbits=int(target.get("stopbits", 1)), timeout=2,
        )
    else:
        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient(target["host"], port=int(target["port"]), timeout=2)
    if not client.connect():
        raise ConnectionError("Modbus connection failed")
    try:
        points = target["points"] if action == "read" else [
            item for item in target["points"] if item["address"] == address
        ]
        if action == "write" and not points:
            raise LookupError(f"Modbus address {address} is not in the HMI map")
        result = []
        for point in points:
            kind, raw_address = point["address"].split(":", 1)
            location = int(raw_address)
            kwargs = {"device_id": int(target["unit_id"])}
            if action == "write":
                if kind == "coil" and point["data_type"] == "BOOL" and type(value) is bool:
                    response = client.write_coil(location, value, **kwargs)
                elif kind == "holding_register" and point["data_type"] == "UINT16" and type(value) is int and 0 <= value <= 65535:
                    response = client.write_register(location, value, **kwargs)
                else:
                    raise ValueError("only writable Modbus coils and holding registers are supported")
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for write to {point['address']}")
            if kind == "coil":
                response = client.read_coils(location, count=1, **kwargs)
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for {point['address']}")
                current = bool(response.bits[0])
                raw = "01" if current else "00"
            elif kind == "discrete_input":
                response = client.read_discrete_inputs(location, count=1, **kwargs)
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for {point['address']}")
                current = bool(response.bits[0])
                raw = "01" if current else "00"
            elif kind == "holding_register":
                response = client.read_holding_registers(location, count=1, **kwargs)
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for {point['address']}")
                current = int(response.registers[0])
                raw = f"{current:04x}"
            else:
                response = client.read_input_registers(location, count=1, **kwargs)
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for {point['address']}")
                current = int(response.registers[0])
                raw = f"{current:04x}"
            result.append({**point, "value": current, "raw_hex": raw})
        return result
    finally:
        client.close()


def operate(target: dict[str, Any], action: str, address: str | None = None, value: Any = None) -> list[dict[str, Any]]:
    if action not in {"read", "write"}:
        raise ValueError("unsupported HMI action")
    if target["device_type"] == "plc-s7":
        return _operate_s7(target, action, address, value)
    if target["device_type"] == "modbus-tcp":
        return _operate_modbus(target, action, address, value)
    raise ValueError("HMI operations only support S7 and Modbus TCP")


if __name__ == "__main__":
    target = json.loads(sys.argv[1])
    action, address, raw_value = sys.argv[2:]
    value = json.loads(raw_value) if raw_value else None
    print(json.dumps(operate(target, action, address or None, value), allow_nan=False))
