"""Shared type, range, and firewall checks for operator and exercise I/O."""

from __future__ import annotations

import math
from typing import Any


def validate_s7_write_value(data_type: str, value: bool | int | float) -> None:
    if data_type == "BOOL":
        if type(value) is not bool:
            raise ValueError("S7 BOOL outputs require a boolean value")
        return
    if data_type == "REAL":
        if type(value) not in (int, float):
            raise ValueError("S7 REAL outputs require a finite numeric value")
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError("S7 REAL output is outside the representable range") from error
        if not math.isfinite(number) or abs(number) > 3.402823466e38:
            raise ValueError("S7 REAL output is outside the representable range")
        return
    raise ValueError(f"Unsupported S7 output data type '{data_type}'")


def validate_component_range(component: dict[str, Any], value: bool | int | float) -> None:
    if component.get("type") == "io_channel" and component.get("data_type") == "REAL":
        minimum, maximum = component.get("min_value", 0), component.get("max_value", 100)
        if not minimum <= float(value) <= maximum:
            unit = component.get("unit") or ""
            raise ValueError(f"Value must be between {minimum:g} and {maximum:g} {unit}".strip())


def validate_point_range(point: dict[str, Any], value: bool | int | float) -> None:
    minimum, maximum = point.get("min_value"), point.get("max_value")
    if minimum is None or maximum is None:
        return
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        unit = point.get("unit") or ""
        raise ValueError(f"Value must be between {minimum} and {maximum} {unit}".strip())


def diagnose_reading_range(value: dict[str, Any], limits: dict[str, Any] | None) -> dict[str, Any]:
    """Annotate numeric reads against explicitly declared engineering limits."""
    result = dict(value)
    result.setdefault("quality", "good")
    if not limits:
        return result
    minimum, maximum = limits.get("min_value"), limits.get("max_value")
    reading = result.get("value")
    if (type(reading) not in (int, float) or minimum is None or maximum is None
            or not math.isfinite(float(reading))):
        return result
    protocol_quality = result.get("quality", "good")
    result["protocol_quality"] = protocol_quality
    if protocol_quality != "good":
        return result
    if reading < minimum or reading > maximum:
        result["quality"] = "out_of_range"
        result["range_diagnostic"] = {
            "minimum": minimum,
            "maximum": maximum,
            "unit": limits.get("unit"),
            "message": f"Measured value {reading:g} is outside configured range {minimum:g}..{maximum:g}{(' ' + limits['unit']) if limits.get('unit') else ''}",
        }
    return result


def enforce_firewall_policy(record: Any, target: dict[str, Any]) -> None:
    port = 102 if target["device_type"] == "plc-s7" else target.get("config", {}).get("port")
    for firewall in (item for item in record.runtime_devices() if item["device_type"] == "firewall"):
        allowed = firewall.get("config", {}).get("firewall_allow_tcp_ports", [])
        if port not in allowed:
            raise PermissionError(f"Firewall '{firewall['name']}' denies TCP/{port} for '{target['node_id']}'")
