"""Siemens S7 simulator device adapter."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from models.plc import AlarmInput, AnalogSensorInput, DigitalInputInput, MotorInput, PlcS7ConfigInput, PumpInput, RemoteInput, ValveInput, IoChannelInput

from . import register_device
from .base import Device


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-._")
    return normalized.lower() or "device"


@register_device("plc-s7")
class PlcS7Device(Device):
    label = "PLC Siemens S7"
    description = "Servidor S7 parametrizable"
    icon = "S7"
    image = os.environ.get("ANVIL_PLC_IMAGE", "anvil/plc-s7:dev")
    config_model = PlcS7ConfigInput
    protocols = ("s7comm",)
    capabilities = ("server", "db-m-i-q", "boolean-real", "scan-logic", "optional-host-forwarding")
    component_types = frozenset({"valve", "motor", "digital_input", "alarm", "pump", "analog_sensor", "remote_input", "io_channel"})

    @classmethod
    def external_port(cls, config: Any) -> int:
        del config
        return 102

    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        cursors: dict[tuple[str, int], tuple[int, int]] = {}
        runtime_components: list[dict[str, Any]] = []

        for component in device.config.components:
            key = (component.area if isinstance(component, IoChannelInput) else "DB", component.db if not isinstance(component, IoChannelInput) or component.area == "DB" else 0)
            byte_offset, bit_offset = cursors.get(key, (0, 0))
            component_data = component.model_dump(mode="json")
            if isinstance(component, (IoChannelInput, AnalogSensorInput)):
                component_data["range_explicit"] = {"min_value", "max_value"}.issubset(component.model_fields_set)

            if isinstance(component, IoChannelInput):
                if component.byte_offset is not None:
                    byte_offset = component.byte_offset
                    bit_offset = component.bit_offset or 0
                else:
                    if bit_offset:
                        byte_offset += 1
                    if component.data_type == "REAL" and byte_offset % 2:
                        byte_offset += 1
                    bit_offset = 0
                component_data.update(byte_offset=byte_offset, bit_offset=bit_offset)
                required_end = byte_offset + (4 if component.data_type == "REAL" else 1)
                if required_end > device.config.db_size:
                    raise ValueError(f"channel '{component.name}' exceeds configured memory area size")
                cursors[key] = (max(cursors.get(key, (0, 0))[0], required_end), 0)
                runtime_components.append(component_data)
                continue
            if isinstance(component, PumpInput):
                if bit_offset:
                    byte_offset += 1
                    bit_offset = 0
                if byte_offset % 2:
                    byte_offset += 1
                component_data["command_byte_offset"] = byte_offset
                component_data["command_bit_offset"] = 0
                component_data["flow_byte_offset"] = byte_offset + 2
                command = {**component_data, "type": "pump_command", "byte_offset": byte_offset, "bit_offset": 0}
                flow = {**component_data, "type": "pump_flow", "byte_offset": byte_offset + 2}
                runtime_components.extend((command, flow))
                cursors[key] = (byte_offset + 6, 0)
                if byte_offset + 6 > device.config.db_size:
                    raise ValueError(f"pump '{component.name}' exceeds DB{component.db} size ({device.config.db_size} bytes)")
                continue
            if isinstance(component, (ValveInput, MotorInput, DigitalInputInput, AlarmInput)) or isinstance(component, RemoteInput) and component.data_type == "BOOL":
                component_data["byte_offset"] = byte_offset
                component_data["bit_offset"] = bit_offset
                bit_offset += 1
                if bit_offset == 8:
                    byte_offset += 1
                    bit_offset = 0
                required_end = component_data["byte_offset"] + 1
            elif isinstance(component, (AnalogSensorInput, RemoteInput)):
                if bit_offset:
                    byte_offset += 1
                    bit_offset = 0
                if byte_offset % 2:
                    byte_offset += 1
                component_data["byte_offset"] = byte_offset
                required_end = byte_offset + 4
                byte_offset = required_end
            else:
                raise ValueError(f"Unsupported component '{component.type}'")

            if required_end > device.config.db_size:
                raise ValueError(
                    f"component '{component.name}' exceeds DB{component.db} size "
                    f"({device.config.db_size} bytes)"
                )

            cursors[key] = (byte_offset, bit_offset)
            runtime_components.append(component_data)

        logic = []
        input_types = {"analog_sensor", "pump_flow", "remote_input"}
        output_types = {"valve", "motor", "pump_command"}
        for rule in device.config.control_rules:
            input_component = next((item for item in runtime_components
                                    if item["name"] == rule.input_component
                                    and (item["type"] in input_types
                                         or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "REAL")), None)
            output_component = next((item for item in runtime_components
                                     if item["name"] == rule.output_component
                                     and (item["type"] in output_types
                                          or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
            if input_component is None or output_component is None:
                raise ValueError(f"control rule '{rule.name}' could not resolve its I/O components")
            logic.append({
                "name": rule.name,
                "input_db": input_component["db"],
                "input_byte_offset": input_component["byte_offset"],
                "output_db": output_component["db"],
                "output_byte_offset": output_component["byte_offset"],
                "output_bit_offset": output_component.get("bit_offset", 0),
                "mode": rule.mode, "setpoint": rule.setpoint, "hysteresis": rule.hysteresis,
            })

        timers = []
        for timer in device.config.timers:
            input_component = next((item for item in runtime_components
                                    if item["name"] == timer.input_component
                                    and (item["type"] == "digital_input"
                                         or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                         or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            output_component = next((item for item in runtime_components
                                     if item["name"] == timer.output_component
                                     and (item["type"] in output_types
                                          or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
            if input_component is None or output_component is None:
                raise ValueError(f"timer '{timer.name}' could not resolve its I/O components")
            timers.append({
                "name": timer.name,
                "input_db": input_component["db"],
                "input_byte_offset": input_component["byte_offset"],
                "input_bit_offset": input_component.get("bit_offset", 0),
                "output_db": output_component["db"],
                "output_byte_offset": output_component["byte_offset"],
                "output_bit_offset": output_component.get("bit_offset", 0),
                "mode": timer.mode,
                "preset_ms": timer.preset_ms,
            })

        boolean_logic = []
        boolean_input_types = {"digital_input"}
        boolean_output_types = {"valve", "motor", "pump_command"}
        for rule in device.config.boolean_rules:
            input_a = next((item for item in runtime_components
                            if item["name"] == rule.input_a_component
                            and (item["type"] in boolean_input_types
                                 or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                 or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            input_b = next((item for item in runtime_components
                            if item["name"] == rule.input_b_component
                            and (item["type"] in boolean_input_types
                                 or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                 or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            output_component = next((item for item in runtime_components
                                     if item["name"] == rule.output_component
                                     and (item["type"] in boolean_output_types
                                          or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
            if input_a is None or input_b is None or output_component is None:
                raise ValueError(f"boolean rule '{rule.name}' could not resolve its I/O components")
            boolean_logic.append({
                "name": rule.name,
                "input_a_db": input_a["db"],
                "input_a_byte_offset": input_a["byte_offset"],
                "input_a_bit_offset": input_a.get("bit_offset", 0),
                "input_b_db": input_b["db"],
                "input_b_byte_offset": input_b["byte_offset"],
                "input_b_bit_offset": input_b.get("bit_offset", 0),
                "output_db": output_component["db"],
                "output_byte_offset": output_component["byte_offset"],
                "output_bit_offset": output_component.get("bit_offset", 0),
                "operator": rule.operator,
                "input_a_negated": rule.input_a_negated,
                "input_b_negated": rule.input_b_negated,
            })

        latches = []
        for latch in device.config.latches:
            set_input = next((item for item in runtime_components
                              if item["name"] == latch.set_component
                              and (item["type"] == "digital_input"
                                   or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                   or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            reset_input = next((item for item in runtime_components
                                if item["name"] == latch.reset_component
                                and (item["type"] == "digital_input"
                                     or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                     or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            output_component = next((item for item in runtime_components
                                     if item["name"] == latch.output_component
                                     and (item["type"] in {"valve", "motor", "pump_command"}
                                          or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
            if set_input is None or reset_input is None or output_component is None:
                raise ValueError(f"latch '{latch.name}' could not resolve its I/O components")
            latches.append({
                "name": latch.name,
                "set_db": set_input["db"], "set_byte_offset": set_input["byte_offset"],
                "set_bit_offset": set_input.get("bit_offset", 0),
                "reset_db": reset_input["db"], "reset_byte_offset": reset_input["byte_offset"],
                "reset_bit_offset": reset_input.get("bit_offset", 0),
                "output_db": output_component["db"], "output_byte_offset": output_component["byte_offset"],
                "output_bit_offset": output_component.get("bit_offset", 0),
            })

        counters = []
        for counter in device.config.counters:
            count_input = next((item for item in runtime_components
                                if item["name"] == counter.count_component
                                and (item["type"] == "digital_input"
                                     or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                     or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            reset_input = next((item for item in runtime_components
                                if item["name"] == counter.reset_component
                                and (item["type"] == "digital_input"
                                     or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                     or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            output_component = next((item for item in runtime_components
                                     if item["name"] == counter.output_component
                                     and (item["type"] in {"valve", "motor", "pump_command"}
                                          or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
            if count_input is None or reset_input is None or output_component is None:
                raise ValueError(f"counter '{counter.name}' could not resolve its I/O components")
            counters.append({
                "name": counter.name,
                "count_db": count_input["db"], "count_byte_offset": count_input["byte_offset"],
                "count_bit_offset": count_input.get("bit_offset", 0),
                "reset_db": reset_input["db"], "reset_byte_offset": reset_input["byte_offset"],
                "reset_bit_offset": reset_input.get("bit_offset", 0),
                "output_db": output_component["db"], "output_byte_offset": output_component["byte_offset"],
                "output_bit_offset": output_component.get("bit_offset", 0), "preset": counter.preset,
            })

        sequences = []
        for sequence in device.config.sequences:
            advance_input = next((item for item in runtime_components
                                  if item["name"] == sequence.advance_component
                                  and (item["type"] == "digital_input"
                                       or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                       or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            reset_input = next((item for item in runtime_components
                                if item["name"] == sequence.reset_component
                                and (item["type"] == "digital_input"
                                     or item["type"] == "remote_input" and item.get("data_type") == "BOOL"
                                     or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "input" and item["data_type"] == "BOOL")), None)
            output_components = []
            for output_name in sequence.steps:
                output_component = next((item for item in runtime_components
                                         if item["name"] == output_name
                                         and (item["type"] in {"valve", "motor", "pump_command"}
                                              or item["type"] == "io_channel" and item.get("area", "DB") == "DB" and item["direction"] == "output" and item["data_type"] == "BOOL")), None)
                if output_component is None:
                    raise ValueError(f"sequence '{sequence.name}' could not resolve step output '{output_name}'")
                output_components.append({
                    "db": output_component["db"], "byte_offset": output_component["byte_offset"],
                    "bit_offset": output_component.get("bit_offset", 0),
                })
            if advance_input is None or reset_input is None:
                raise ValueError(f"sequence '{sequence.name}' could not resolve its BOOL inputs")
            sequences.append({
                "name": sequence.name,
                "advance_db": advance_input["db"], "advance_byte_offset": advance_input["byte_offset"],
                "advance_bit_offset": advance_input.get("bit_offset", 0),
                "reset_db": reset_input["db"], "reset_byte_offset": reset_input["byte_offset"],
                "reset_bit_offset": reset_input.get("bit_offset", 0),
                "steps": output_components,
            })

        return {
            "node_id": device.node_id,
            "device_type": device.device_type,
            "name": device.name,
            "ip": device.ip,
            "external_access": device.external_access,
            "external_port": device.external_port,
            "external_host_ip": device.external_host_ip,
            "config": {
                "plc_id": device.node_id,
                "rack": device.config.rack,
                "slot": device.config.slot,
                "db_size": device.config.db_size,
                "scan_cycle_ms": device.config.scan_cycle_ms,
                "retain_logic_state": device.config.retain_logic_state,
                "response_delay_ms": device.config.response_delay_ms,
                "response_jitter_ms": device.config.response_jitter_ms,
                "response_jitter_distribution": device.config.response_jitter_distribution,
                "response_jitter_seed": device.config.response_jitter_seed,
                "fault_mode": device.config.fault_mode,
                "fault_probability": device.config.fault_probability,
                "fault_seed": device.config.fault_seed,
                "components": runtime_components,
                "logic": logic,
                "boolean_logic": boolean_logic,
                "latches": latches,
                "counters": counters,
                "sequences": sequences,
                "timers": timers,
            },
        }

    def build_container_config(
        self,
        scenario_id: str,
        runtime_device: dict[str, Any],
    ) -> dict[str, Any]:
        config = {
            "image": self.image,
            "name": (
                f"anvil-{scenario_id[:8]}-"
                f"{_safe_name(runtime_device['name'])}-{_safe_name(runtime_device['node_id'])}"
            )[:63],
            "environment": {
                "PLC_CONFIG": json.dumps(runtime_device["config"], separators=(",", ":"))
            },
            "detach": True,
            "labels": {
                "anvil.managed": "true",
                "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"],
            },
        }
        return config

    def build_io_map(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        controlled_outputs = {
            (item["output_db"], item["output_byte_offset"], item["output_bit_offset"])
            for item in runtime_device["config"].get("logic", [])
        }
        controlled_outputs.update(
            (item["output_db"], item["output_byte_offset"], item["output_bit_offset"])
            for item in runtime_device["config"].get("timers", [])
        )
        controlled_outputs.update(
            (item["output_db"], item["output_byte_offset"], item["output_bit_offset"])
            for item in runtime_device["config"].get("boolean_logic", [])
        )
        controlled_outputs.update(
            (item["output_db"], item["output_byte_offset"], item["output_bit_offset"])
            for item in runtime_device["config"].get("latches", [])
        )
        controlled_outputs.update(
            (item["output_db"], item["output_byte_offset"], item["output_bit_offset"])
            for item in runtime_device["config"].get("counters", [])
        )
        controlled_outputs.update(
            (step["db"], step["byte_offset"], step["bit_offset"])
            for sequence in runtime_device["config"].get("sequences", [])
            for step in sequence["steps"]
        )
        for component in runtime_device["config"]["components"]:
            common = {
                "node_id": runtime_device["node_id"],
                "device_name": runtime_device["name"],
                "component_name": component["name"],
                "component_type": component["type"],
                "db": component["db"] if component.get("area", "DB") == "DB" else None,
                "area": component.get("area", "DB"),
            }
            if component["type"] == "io_channel":
                area = component.get("area", "DB")
                address = (f"DB{component['db']}.DBX{component['byte_offset']}.{component.get('bit_offset', 0)}" if area == "DB" else f"{area}{component['byte_offset']}.{component.get('bit_offset', 0)}") if component["data_type"] == "BOOL" else (f"DB{component['db']}.DBD{component['byte_offset']}" if area == "DB" else f"{area}D{component['byte_offset']}")
                controlled = area == "DB" and (component["db"], component["byte_offset"], component.get("bit_offset", 0)) in controlled_outputs
                access = "read_write" if component["direction"] == "output" and area != "I" and not controlled else "read"
                points.append({**common, "area": area, "address": address, "data_type": component["data_type"], "access": access, "unit": component.get("unit") or None, "channel_id": component.get('channel_id'), "direction": component['direction'],
                               **({"min_value": component["min_value"], "max_value": component["max_value"]}
                                  if component["data_type"] == "REAL" and component.get("range_explicit", True) else {})})
                continue
            if component["type"] == "remote_input":
                common.update(source_node_id=component["source_node_id"], source_address=component["source_address"])
            if component["type"] in {"valve", "motor", "digital_input", "alarm", "pump_command"} or component["type"] == "remote_input" and component["data_type"] == "BOOL":
                controlled = (component["db"], component["byte_offset"], component.get("bit_offset", 0)) in controlled_outputs
                points.append(
                    {
                        **common,
                        "address": (
                            f"DB{component['db']}.DBX{component['byte_offset']}."
                            f"{component['bit_offset']}"
                        ),
                        "data_type": "BOOL",
                        "access": "read_write" if component["type"] in {"valve", "motor", "pump_command"} and not controlled else "read",
                        "unit": None,
                    }
                )
            elif component["type"] in {"analog_sensor", "pump_flow", "remote_input"}:
                points.append(
                    {
                        **common,
                        "address": f"DB{component['db']}.DBD{component['byte_offset']}",
                        "data_type": "REAL",
                        "access": "read",
                        "unit": component.get("unit") or None,
                        **({"min_value": component["min_value"], "max_value": component["max_value"]}
                           if component["type"] == "analog_sensor" and component.get("range_explicit", True) else {}),
                    }
                )
        owned = {binding["address"] for binding in runtime_device["config"].get("field_bridge", {}).get("bindings", []) if binding["direction"] == "input"}
        for point in points:
            if point["address"] in owned:
                point["access"] = "read"
                binding = next(b for b in runtime_device['config']['field_bridge']['bindings'] if b['address'] == point['address'] and b['direction'] == 'input')
                point.update(source_node_id=binding['field_id'], source_address=binding['field_port'])
        return points

    def network_services(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        return [{
            "service": "s7comm",
            "protocol": "s7comm",
            "transport": "tcp",
            "port": 102,
            "role": "server",
            "description": "Servidor S7 para clientes Snap7/S7 compatibles",
        }]
