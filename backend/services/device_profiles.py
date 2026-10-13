"""Build portable device profiles from persisted, validated device inputs."""

from __future__ import annotations

from typing import Any

from devices import DEVICE_REGISTRY
from models.device_profile import DeviceProfile


_MAPPING_FIELDS = (
    "component_name", "component_type", "address", "data_type", "access", "unit",
    "area", "db", "direction", "min_value", "max_value",
)


def build_device_profile(device: Any) -> DeviceProfile:
    adapter_class = DEVICE_REGISTRY.get(device.device_type)
    if adapter_class is None:
        raise ValueError(f"Unsupported device type '{device.device_type}'")
    adapter = adapter_class()
    runtime = adapter.build_runtime_device(device)
    points = adapter.build_io_map(runtime)
    if device.device_type == "plc-s7":
        protocol = "S7comm"
    elif device.device_type == "modbus-tcp":
        protocol = "Modbus RTU" if runtime["config"].get("transport") == "rtu" else "Modbus TCP"
    else:
        protocol = "none"
    mapping = [{key: point[key] for key in _MAPPING_FIELDS if key in point} for point in points]
    return DeviceProfile(
        schema_version=1,
        device_type=device.device_type,
        name=device.name,
        protocol=protocol,
        config=device.config.model_dump(mode="json"),
        resolved_mapping=mapping,
        fidelity_note=(
            "El perfil contiene configuración y direcciones de Anvil; no es un archivo de fabricante. "
            "Verifica áreas, permisos, escalas, orden de bytes y límites con la documentación del equipo real."
        ),
    )
