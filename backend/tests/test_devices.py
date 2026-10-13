from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from devices.plc_s7 import PlcS7Device  # noqa: E402
from devices.field_runtime import FieldRuntime  # noqa: E402
from devices import DEVICE_REGISTRY, list_device_catalog, register_device, unregister_device  # noqa: E402
from devices.base import Device  # noqa: E402
from devices.protocol_ingress import ProtocolIngressDevice  # noqa: E402
from devices.scada import ScadaDevice  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from models.scenario import DeviceInput  # noqa: E402


class FieldRuntimeDeviceTests(unittest.TestCase):
    def test_container_healthcheck_requires_the_declared_contract_version(self) -> None:
        runtime = {"node_id": "__anvil_field__", "config": {"contract_version": 1}}
        config = FieldRuntime().build_container_config("scenario", runtime)
        check = config["healthcheck"]
        self.assertEqual(check["test"][0:2], ["CMD", "python"])
        self.assertIn("/capabilities", check["test"][-1])
        self.assertIn("contract_version')==1", check["test"][-1])
        self.assertEqual(check["interval"], 5_000_000_000)
        self.assertEqual(check["timeout"], 4_000_000_000)
        self.assertEqual(check["retries"], 3)


class ScadaDeviceTests(unittest.TestCase):
    def test_container_config_preserves_deployment_derived_targets(self) -> None:
        targets = [{
            "node_id": "rtu", "device_type": "modbus-tcp", "host": "10.20.0.10",
            "port": 1502, "unit_id": 17, "transport": "rtu", "baudrate": 9600,
            "bytesize": 8, "parity": "N", "stopbits": 1,
            "tags": [{"address": "coil:0", "name": "Pump", "data_type": "BOOL", "access": "read_write"}],
        }]
        runtime = {
            "node_id": "scada-1", "device_type": "scada", "name": "SCADA",
            "ip": "10.20.0.11", "config": {
                "target_node_ids": "rtu", "poll_interval_ms": 1000,
                "history_capacity": 10, "alarms": [], "targets": targets,
            },
        }

        config = ScadaDevice().build_container_config("scenario", runtime)

        self.assertEqual(json.loads(config["environment"]["SCADA_CONFIG"])["targets"], targets)

    def test_container_config_rejects_missing_deployment_derived_targets(self) -> None:
        runtime = {
            "node_id": "scada-1", "device_type": "scada", "name": "SCADA",
            "ip": "10.20.0.11", "config": {
                "target_node_ids": "rtu", "poll_interval_ms": 1000,
                "history_capacity": 10, "alarms": [],
            },
        }

        with self.assertRaisesRegex(ValueError, "deployment-derived protocol targets"):
            ScadaDevice().build_container_config("scenario", runtime)


class DeviceRegistryContractTests(unittest.TestCase):
    def test_registry_validates_type_names_and_adapter_catalog_flags(self) -> None:
        class ExampleConfig(BaseModel):
            value: int

        class ExampleAdapter(Device):
            label, description, icon = "Example", "Example adapter", "EX"
            config_model = ExampleConfig

            def build_runtime_device(self, device):
                return {"config": device.config.model_dump()}

            def build_container_config(self, scenario_id, runtime_device):
                return {"name": scenario_id}

        for invalid_name in ("", "Bad Type", "Uppercase", "two--dashes", "_extension", None):
            with self.subTest(name=invalid_name), self.assertRaises(ValueError):
                register_device(invalid_name)

        for index, (field, invalid_value) in enumerate((("component_types", "valve"), ("internal", "false"),
                                                        ("network_resource", 1))):
            adapter = type(f"Invalid{field.title()}", (ExampleAdapter,), {field: invalid_value})
            type_name = f"invalid-flag-{index}"
            with self.subTest(field=field), self.assertRaisesRegex(TypeError, field):
                register_device(type_name)(adapter)
            self.assertNotIn(type_name, DEVICE_REGISTRY)

    def test_registry_rejects_abstract_adapter_before_mutating_registry(self) -> None:
        class IncompleteAdapter(Device):
            pass

        with self.assertRaisesRegex(TypeError, "unimplemented abstract methods"):
            register_device("incomplete-test")(IncompleteAdapter)
        self.assertNotIn("incomplete-test", DEVICE_REGISTRY)

    def test_registry_requires_schema_and_discoverable_metadata_for_public_adapters(self) -> None:
        class MissingSchemaAdapter(Device):
            label, description, icon = "Example", "Example adapter", "EX"

            def build_runtime_device(self, device):
                return {"config": {}}

            def build_container_config(self, scenario_id, runtime_device):
                return {"name": scenario_id}

        class MissingMetadataAdapter(MissingSchemaAdapter):
            label = ""

            class Config(BaseModel):
                value: int

            config_model = Config

        with self.assertRaisesRegex(TypeError, "Pydantic config_model"):
            register_device("missing-schema-test")(MissingSchemaAdapter)
        with self.assertRaisesRegex(TypeError, "non-empty label"):
            register_device("missing-metadata-test")(MissingMetadataAdapter)
        self.assertNotIn("missing-schema-test", DEVICE_REGISTRY)
        self.assertNotIn("missing-metadata-test", DEVICE_REGISTRY)


class PlcS7DeviceTests(unittest.TestCase):
    def test_retain_logic_state_option_reaches_runtime_and_requires_boolean(self) -> None:
        device = DeviceInput.model_validate({"node_id": "plc-retain", "device_type": "plc-s7",
                                             "name": "PLC", "ip": "172.30.10.16",
                                             "config": {"retain_logic_state": True, "components": []}})
        self.assertTrue(PlcS7Device().build_runtime_device(device)["config"]["retain_logic_state"])
        for value in ("true", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "plc-retain", "device_type": "plc-s7",
                                            "name": "PLC", "ip": "172.30.10.16",
                                            "config": {"retain_logic_state": value, "components": []}})

    def test_sequence_resolves_step_outputs_and_locks_all_outputs(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-sequence", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.20", "config": {
                "components": [
                    {"type": "digital_input", "name": "Advance"},
                    {"type": "digital_input", "name": "Reset"},
                    {"type": "valve", "name": "Step one"},
                    {"type": "motor", "name": "Step two"},
                ],
                "sequences": [{"name": "Transfer", "advance_component": "Advance",
                               "reset_component": "Reset", "steps": ["Step one", "Step two"]}],
            },
        })
        adapter = PlcS7Device()
        runtime = adapter.build_runtime_device(device)
        sequence = runtime["config"]["sequences"][0]
        self.assertEqual((sequence["advance_byte_offset"], sequence["advance_bit_offset"]), (0, 0))
        self.assertEqual((sequence["reset_byte_offset"], sequence["reset_bit_offset"]), (0, 1))
        self.assertEqual([(step["byte_offset"], step["bit_offset"]) for step in sequence["steps"]], [(0, 2), (0, 3)])
        points = adapter.build_io_map(runtime)
        self.assertTrue(all(point["access"] == "read" for point in points if point["component_name"] in {"Step one", "Step two"}))

    def test_up_counter_resolves_inputs_and_locks_done_output(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-ctu", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.18", "config": {
                "components": [
                    {"type": "digital_input", "name": "Pulse"},
                    {"type": "digital_input", "name": "Reset"},
                    {"type": "motor", "name": "Done"},
                ],
                "counters": [{"name": "Count cycles", "count_component": "Pulse",
                              "reset_component": "Reset", "output_component": "Done", "preset": 3}],
            },
        })
        adapter = PlcS7Device()
        runtime = adapter.build_runtime_device(device)
        counter = runtime["config"]["counters"][0]
        self.assertEqual((counter["count_byte_offset"], counter["count_bit_offset"]), (0, 0))
        self.assertEqual((counter["reset_byte_offset"], counter["reset_bit_offset"]), (0, 1))
        self.assertEqual(counter["preset"], 3)
        output = next(point for point in adapter.build_io_map(runtime) if point["component_name"] == "Done")
        self.assertEqual(output["access"], "read")

    def test_sr_latch_resolves_addresses_and_locks_controlled_output(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-sr", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.17", "config": {
                "components": [
                    {"type": "digital_input", "name": "Start"},
                    {"type": "digital_input", "name": "Stop"},
                    {"type": "motor", "name": "Motor"},
                ],
                "latches": [{"name": "Motor memory", "set_component": "Start",
                             "reset_component": "Stop", "output_component": "Motor"}],
            },
        })
        runtime = PlcS7Device().build_runtime_device(device)
        latch = runtime["config"]["latches"][0]
        self.assertEqual((latch["set_byte_offset"], latch["set_bit_offset"]), (0, 0))
        self.assertEqual((latch["reset_byte_offset"], latch["reset_bit_offset"]), (0, 1))
        output = next(point for point in PlcS7Device().build_io_map(runtime) if point["component_name"] == "Motor")
        self.assertEqual(output["access"], "read")

    def test_boolean_logic_resolves_two_inputs_and_protects_output(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-bool", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.12", "config": {
                "components": [
                    {"type": "digital_input", "name": "Permiso"},
                    {"type": "digital_input", "name": "Marcha remota"},
                    {"type": "valve", "name": "Válvula"},
                ],
                "boolean_rules": [{"name": "Interlock", "input_a_component": "Permiso",
                                   "input_b_component": "Marcha remota", "output_component": "Válvula",
                                   "operator": "and", "input_b_negated": True}],
            },
        })
        runtime = PlcS7Device().build_runtime_device(device)
        rule = runtime["config"]["boolean_logic"][0]
        self.assertEqual(rule["operator"], "and")
        self.assertFalse(rule["input_a_negated"])
        self.assertTrue(rule["input_b_negated"])
        self.assertEqual((rule["input_a_byte_offset"], rule["input_a_bit_offset"]), (0, 0))
        self.assertEqual((rule["input_b_byte_offset"], rule["input_b_bit_offset"]), (0, 1))
        output = next(point for point in PlcS7Device().build_io_map(runtime) if point["component_name"] == "Válvula")
        self.assertEqual(output["access"], "read")

    def test_boolean_logic_rejects_same_input_and_multiple_output_drivers(self) -> None:
        components = [
            {"type": "digital_input", "name": "A"},
            {"type": "digital_input", "name": "B"},
            {"type": "valve", "name": "V"},
        ]
        rule = {"name": "AND", "input_a_component": "A", "input_b_component": "B",
                "output_component": "V", "operator": "and"}
        bad_cases = [
            [dict(rule, input_b_component="A")],
            [rule, dict(rule, name="OR", operator="or")],
        ]
        for rules in bad_cases:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "plc-bool", "device_type": "plc-s7", "name": "PLC",
                                            "ip": "172.30.10.13", "config": {"components": components,
                                                                             "boolean_rules": rules}})

    def test_on_delay_timer_resolves_bool_io_and_locks_its_output(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-ton", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.16", "config": {
                "components": [
                    {"type": "digital_input", "name": "Permiso", "initial_state": False},
                    {"type": "valve", "name": "Válvula"},
                ],
                "timers": [{"name": "Retardo apertura", "input_component": "Permiso",
                            "output_component": "Válvula", "preset_ms": 750}],
            },
        })
        runtime = PlcS7Device().build_runtime_device(device)
        timer = runtime["config"]["timers"][0]
        self.assertEqual((timer["input_db"], timer["input_byte_offset"], timer["input_bit_offset"]), (1, 0, 0))
        self.assertEqual((timer["output_db"], timer["output_byte_offset"], timer["output_bit_offset"], timer["preset_ms"]), (1, 0, 1, 750))
        output = next(point for point in PlcS7Device().build_io_map(runtime) if point["component_name"] == "Válvula")
        self.assertEqual(output["access"], "read")

    def test_off_delay_timer_mode_reaches_runtime_configuration(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-tof", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.16", "config": {
                "components": [
                    {"type": "digital_input", "name": "Permiso", "initial_state": False},
                    {"type": "valve", "name": "Válvula"},
                ],
                "timers": [{"name": "Retardo cierre", "input_component": "Permiso",
                            "output_component": "Válvula", "mode": "tof", "preset_ms": 750}],
            },
        })
        timer = PlcS7Device().build_runtime_device(device)["config"]["timers"][0]
        self.assertEqual(timer["mode"], "tof")

    def test_on_delay_timer_rejects_non_boolean_io_and_duplicate_driver(self) -> None:
        components = [
            {"type": "analog_sensor", "name": "Nivel"},
            {"type": "digital_input", "name": "Permiso"},
            {"type": "valve", "name": "Válvula"},
        ]
        invalid = [
            ({"name": "mal", "input_component": "Nivel", "output_component": "Válvula", "preset_ms": 100}, []),
            ({"name": "duplicado", "input_component": "Permiso", "output_component": "Válvula", "preset_ms": 100},
             [{"name": "Nivel bajo", "input_component": "Nivel", "output_component": "Válvula", "setpoint": 10, "hysteresis": 2}]),
        ]
        for timer, rules in invalid:
            with self.subTest(timer=timer), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "plc-ton", "device_type": "plc-s7", "name": "PLC",
                                            "ip": "172.30.10.16", "config": {"components": components, "timers": [timer], "control_rules": rules}})

    def test_hysteresis_control_rule_resolves_configured_sensor_and_pump(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-control", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.14", "config": {
                "components": [
                    {"type": "analog_sensor", "name": "Level", "min_value": 0, "max_value": 100},
                    {"type": "pump", "name": "Pump", "flow_min": 0, "flow_max": 100},
                ],
                "control_rules": [{"name": "Low level pump", "input_component": "Level",
                                   "output_component": "Pump", "mode": "below", "setpoint": 30, "hysteresis": 5}],
            },
        })
        runtime = PlcS7Device().build_runtime_device(device)
        rule = runtime["config"]["logic"][0]
        self.assertEqual((rule["input_db"], rule["input_byte_offset"]), (1, 0))
        self.assertEqual((rule["output_db"], rule["output_byte_offset"], rule["output_bit_offset"]), (1, 4, 0))
        io_map = PlcS7Device().build_io_map(runtime)
        pump_command = next(point for point in io_map if point["component_type"] == "pump_command")
        self.assertEqual(pump_command["access"], "read")

    def test_control_rule_rejects_read_only_output_and_duplicate_driver(self) -> None:
        base = {"components": [
            {"type": "analog_sensor", "name": "Level"},
            {"type": "valve", "name": "V1"},
            {"type": "valve", "name": "V2"},
            {"type": "alarm", "name": "Alarm"},
        ]}
        cases = [
            [{"name": "bad", "input_component": "Level", "output_component": "Alarm", "setpoint": 4, "hysteresis": 1}],
            [{"name": "a", "input_component": "Level", "output_component": "V1", "setpoint": 4, "hysteresis": 1},
             {"name": "b", "input_component": "Level", "output_component": "V1", "setpoint": 5, "hysteresis": 1}],
        ]
        for rules in cases:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "plc-control", "device_type": "plc-s7", "name": "PLC",
                                            "ip": "172.30.10.14", "config": {**base, "control_rules": rules}})

    def test_control_thresholds_are_finite_numbers_not_booleans_or_strings(self) -> None:
        config = {"components": [{"type": "analog_sensor", "name": "Level"}, {"type": "valve", "name": "V1"}],
                  "control_rules": [{"name": "Level", "input_component": "Level", "output_component": "V1",
                                     "mode": "below", "setpoint": 10, "hysteresis": 2}]}
        for field, value in (("setpoint", True), ("setpoint", "10"), ("hysteresis", 0), ("hysteresis", float("inf"))):
            rule = dict(config["control_rules"][0], **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "plc-control", "device_type": "plc-s7", "name": "PLC",
                                            "ip": "172.30.10.14", "config": {"components": config["components"], "control_rules": [rule]}})

    def test_scan_cycle_is_normalized_into_plc_runtime_config(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-cycle", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.15", "config": {"scan_cycle_ms": 250, "components": []},
        })

        self.assertEqual(PlcS7Device().build_runtime_device(device)["config"]["scan_cycle_ms"], 250)

    def test_scan_cycle_rejects_invalid_values(self) -> None:
        for value in (9, 5001, True, 100.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeviceInput.model_validate({
                    "node_id": "plc-cycle", "device_type": "plc-s7", "name": "PLC",
                    "ip": "172.30.10.15", "config": {"scan_cycle_ms": value, "components": []},
                })

    def test_s7_response_delay_is_bounded_and_strict_integer(self):
        base={"node_id":"plc-latency","device_type":"plc-s7","name":"PLC","ip":"172.30.10.19","config":{"components":[]}}
        for value in (0, 10000):
            payload={**base,"config":{"components":[],"response_delay_ms":value}}
            self.assertEqual(DeviceInput.model_validate(payload).config.response_delay_ms,value)
        for value in (-1,10001,True,1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeviceInput.model_validate({**base,"config":{"components":[],"response_delay_ms":value}})

    def test_s7_response_jitter_is_bounded_and_reaches_runtime(self):
        base={"node_id":"plc-jitter","device_type":"plc-s7","name":"PLC","ip":"172.30.10.21"}
        for value in (0, 10000):
            device=DeviceInput.model_validate({**base,"config":{"components":[],"response_jitter_ms":value}})
            runtime=PlcS7Device().build_runtime_device(device)
            self.assertEqual(runtime["config"]["response_jitter_ms"],value)
        for value in (-1,10001,True,1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeviceInput.model_validate({**base,"config":{"components":[],"response_jitter_ms":value}})

    def test_s7_jitter_distribution_and_seed_reach_runtime(self):
        base = {"node_id": "plc-profile", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.22"}
        for distribution in ("deterministic", "uniform", "normal"):
            device = DeviceInput.model_validate({**base, "config": {"components": [], "response_jitter_ms": 45,
                "response_jitter_distribution": distribution, "response_jitter_seed": 91}})
            runtime = PlcS7Device().build_runtime_device(device)["config"]
            self.assertEqual(runtime["response_jitter_distribution"], distribution)
            self.assertEqual(runtime["response_jitter_seed"], 91)
        for values in ({"response_jitter_distribution": "exponential"}, {"response_jitter_seed": True},
                       {"response_jitter_seed": -1}):
            with self.assertRaises(ValueError):
                DeviceInput.model_validate({**base, "config": {"components": [], **values}})

    def test_s7_timeout_fault_mode_is_validated_and_reaches_runtime(self):
        base = {"node_id": "plc-fault", "device_type": "plc-s7", "name": "PLC",
                "ip": "172.30.10.22"}
        device = DeviceInput.model_validate({**base, "config": {"components": [], "fault_mode": "timeout"}})
        self.assertEqual(PlcS7Device().build_runtime_device(device)["config"]["fault_mode"], "timeout")
        probabilistic = DeviceInput.model_validate({**base, "config": {"components": [], "fault_mode": "timeout",
            "fault_probability": 0.25, "fault_seed": 19}})
        runtime = PlcS7Device().build_runtime_device(probabilistic)["config"]
        self.assertEqual((runtime["fault_probability"], runtime["fault_seed"]), (0.25, 19))
        self.assertEqual(DeviceInput.model_validate({**base, "config": {"components": []}}).config.fault_mode, "none")
        with self.assertRaises(ValueError):
            DeviceInput.model_validate({**base, "config": {"components": [], "fault_mode": "disconnect"}})
        for values in ({"fault_probability": True}, {"fault_probability": 10**1000}, {"fault_probability": -0.1},
                       {"fault_probability": 1.1}, {"fault_seed": True}, {"fault_seed": -1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DeviceInput.model_validate({**base, "config": {"components": [], **values}})

    def test_s7_response_delay_reaches_runtime_container_config(self):
        device=DeviceInput.model_validate({"node_id":"plc-latency","device_type":"plc-s7","name":"PLC",
            "ip":"172.30.10.20","config":{"components":[],"response_delay_ms":275}})
        runtime=PlcS7Device().build_runtime_device(device)
        container=PlcS7Device().build_container_config("scenario",runtime)
        import json
        self.assertEqual(json.loads(container["environment"]["PLC_CONFIG"])["response_delay_ms"],275)

    def test_rejects_values_that_cannot_be_encoded_as_s7_real(self) -> None:
        from models.plc import AnalogSensorInput, PumpInput, ValveInput

        for value in (float("inf"), float("nan"), 1e100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AnalogSensorInput(type="analog_sensor", name="sensor", max_value=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                PumpInput(type="pump", name="pump", flow_max=value)
        with self.assertRaises(ValueError):
            ValveInput(type="valve", name="valve", initial_state="false")

    def test_sensor_noise_seed_is_preserved_in_s7_runtime_config(self) -> None:
        payload = {"node_id": "plc-seeded-sensor", "device_type": "plc-s7", "name": "PLC",
                   "ip": "172.30.10.21", "config": {"components": [{
                       "type": "analog_sensor", "name": "Pressure", "min_value": 0,
                       "max_value": 100, "simulation": {"mode": "constant", "noise": 0.1, "seed": 29},
                   }]}}
        device = DeviceInput.model_validate(payload)
        runtime = PlcS7Device().build_runtime_device(device)
        self.assertEqual(runtime["config"]["components"][0]["simulation"]["seed"], 29)
        for seed in (-1, 4294967296, 2.5, True):
            invalid = copy.deepcopy(payload)
            invalid["config"]["components"][0]["simulation"]["seed"] = seed
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                DeviceInput.model_validate(invalid)

    def test_registered_device_config_schema_is_selected_without_changing_device_input(self) -> None:
        class ExampleConfig(BaseModel):
            level: int

        class ExampleDevice(Device):
            label = "Example device"
            description = "Test extension adapter"
            icon = "EX"
            config_model = ExampleConfig
            component_types = frozenset()

            def build_runtime_device(self, device):
                return {"config": device.config.model_dump()}

            def build_container_config(self, scenario_id, runtime_device):
                return {"name": scenario_id}

        register_device("example-test")(ExampleDevice)
        try:
            catalog_entry = next(item for item in list_device_catalog() if item["type"] == "example-test")
            self.assertEqual(catalog_entry, {"type": "example-test", "label": "Example device",
                                             "description": "Test extension adapter", "icon": "EX",
                                             "protocols": [], "capabilities": []})
            device = DeviceInput.model_validate({
                "node_id": "example", "device_type": "example-test", "name": "Example",
                "ip": "172.30.10.15", "config": {"level": 4},
            })
            self.assertEqual(device.config.level, 4)
            self.assertEqual(ExampleDevice().build_runtime_device(device), {"config": {"level": 4}})
        finally:
            unregister_device("example-test")

    def test_auxiliary_nodes_are_hardened_and_station_access_is_opt_in(self) -> None:
        from devices import DEVICE_REGISTRY

        expected = {"hmi", "gateway", "firewall", "attacker-station", "engineering-station"}
        self.assertTrue(expected.issubset(DEVICE_REGISTRY))
        for kind in expected:
            device = DeviceInput.model_validate({
                "node_id": kind, "device_type": kind, "name": kind,
                "ip": "172.30.10.30", "config": {"role": "attacker" if kind == "attacker-station" else "engineering" if kind == "engineering-station" else kind, "summary": "simulated"},
            })
            adapter = DEVICE_REGISTRY[kind]()
            runtime = adapter.build_runtime_device(device)
            container = adapter.build_container_config("scenario", runtime)
            self.assertEqual(container["labels"]["anvil.simulated"], "true")
            self.assertEqual(container["labels"]["anvil.terminal_access_enabled"], "false")
            self.assertEqual(container["read_only"], True)
            self.assertEqual(container["cap_drop"], ["ALL"])
            if kind == "attacker-station":
                self.assertEqual(adapter.network_mode(runtime), "none")
                enabled = DeviceInput.model_validate({
                    "node_id": kind, "device_type": kind, "name": kind,
                    "ip": "172.30.10.30", "config": {"role": "attacker", "summary": "participant",
                                                          "participant_access_enabled": True},
                })
                self.assertIsNone(adapter.network_mode(adapter.build_runtime_device(enabled)))
                enabled_runtime = adapter.build_runtime_device(enabled)
                self.assertEqual(adapter.build_container_config("scenario", enabled_runtime)
                                 ["labels"]["anvil.terminal_access_enabled"], "true")
                with self.assertRaises(ValueError):
                    DeviceInput.model_validate({
                        "node_id": "invalid-participant", "device_type": "hmi", "name": "HMI",
                        "ip": "172.30.10.31", "config": {"role": "hmi", "summary": "hmi",
                                                                "participant_access_enabled": True},
                    })
            elif kind == "engineering-station":
                self.assertEqual(adapter.network_mode(runtime), "none")
                enabled = DeviceInput.model_validate({
                    "node_id": kind, "device_type": kind, "name": kind,
                    "ip": "172.30.10.30", "config": {"role": "engineering", "summary": "workstation",
                                                          "engineering_access_enabled": True},
                })
                self.assertIsNone(adapter.network_mode(adapter.build_runtime_device(enabled)))
                enabled_runtime = adapter.build_runtime_device(enabled)
                self.assertEqual(adapter.build_container_config("scenario", enabled_runtime)
                                 ["labels"]["anvil.terminal_access_enabled"], "true")
                with self.assertRaises(ValueError):
                    DeviceInput.model_validate({
                        "node_id": "invalid-engineering", "device_type": "hmi", "name": "HMI",
                        "ip": "172.30.10.31", "config": {"role": "hmi", "summary": "hmi",
                                                                "engineering_access_enabled": True},
                    })
            else:
                self.assertIsNone(adapter.network_mode(runtime))

    def test_offsets_are_calculated_and_not_required_from_user(self) -> None:
        device = DeviceInput.model_validate(
            {
                "node_id": "plc-1",
                "device_type": "plc-s7",
                "name": "PLC principal",
                "ip": "172.30.10.10",
                "config": {
                    "db_size": 32,
                    "components": [
                        {"type": "valve", "name": "v1", "db": 1},
                        {"type": "valve", "name": "v2", "db": 1},
                        {
                            "type": "analog_sensor",
                            "name": "pressure",
                            "db": 1,
                            "min_value": 0,
                            "max_value": 10,
                        },
                    ],
                },
            }
        )

        runtime = PlcS7Device().build_runtime_device(device)
        components = runtime["config"]["components"]

        self.assertEqual((components[0]["byte_offset"], components[0]["bit_offset"]), (0, 0))
        self.assertEqual((components[1]["byte_offset"], components[1]["bit_offset"]), (0, 1))
        self.assertEqual(components[2]["byte_offset"], 2)

    def test_real_output_channel_gets_writable_aligned_s7_address(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-setpoint", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.16", "config": {"components": [
                {"type": "valve", "name": "Valve"},
                {"type": "io_channel", "name": "Flow setpoint", "direction": "output", "data_type": "REAL", "unit": "L/min"},
            ]},
        })
        runtime = PlcS7Device().build_runtime_device(device)
        point = next(item for item in PlcS7Device().build_io_map(runtime) if item["component_name"] == "Flow setpoint")
        self.assertEqual((point["address"], point["data_type"], point["access"], point["unit"]),
                         ("DB1.DBD2", "REAL", "read_write", "L/min"))

    def test_analog_sensor_exposes_only_explicit_engineering_range(self) -> None:
        for component, expected in (
            ({"type": "analog_sensor", "name": "Legacy sensor"}, False),
            ({"type": "analog_sensor", "name": "Bounded sensor", "min_value": -20, "max_value": 80}, True),
        ):
            with self.subTest(component=component):
                device = DeviceInput.model_validate({
                    "node_id": "plc-range", "device_type": "plc-s7", "name": "PLC",
                    "ip": "172.30.10.19", "config": {"components": [component]},
                })
                runtime = PlcS7Device().build_runtime_device(device)
                point = PlcS7Device().build_io_map(runtime)[0]
                self.assertEqual("min_value" in point, expected)
                self.assertEqual("max_value" in point, expected)

    def test_real_input_channel_accepts_optional_range_and_validates_declared_range(self) -> None:
        for config, valid in (
            ({"direction": "input", "data_type": "REAL"}, True),
            ({"direction": "input", "data_type": "REAL", "min_value": -5, "max_value": 5}, True),
            ({"direction": "input", "data_type": "REAL", "min_value": 5, "max_value": 5}, False),
        ):
            with self.subTest(config=config):
                payload = {"node_id": "plc-input-range", "device_type": "plc-s7", "name": "PLC",
                           "ip": "172.30.10.20", "config": {"components": [{"type": "io_channel", "name": "Analog", **config}]}}
                if valid:
                    DeviceInput.model_validate(payload)
                else:
                    with self.assertRaises(ValueError):
                        DeviceInput.model_validate(payload)

    def test_io_channels_allocate_independent_m_i_q_spaces_and_keep_input_read_only(self):
        device = DeviceInput.model_validate({
            "node_id": "plc-areas", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.17",
            "config": {"components": [
                {"type": "io_channel", "name": "DB output", "area": "DB", "direction": "output", "data_type": "BOOL"},
                {"type": "io_channel", "name": "Marker", "area": "M", "direction": "output", "data_type": "BOOL"},
                {"type": "io_channel", "name": "Input", "area": "I", "direction": "input", "data_type": "BOOL"},
                {"type": "io_channel", "name": "Output", "area": "Q", "direction": "output", "data_type": "REAL"},
            ]},
        })
        runtime = PlcS7Device().build_runtime_device(device)
        points = {point["component_name"]: point for point in PlcS7Device().build_io_map(runtime)}
        self.assertEqual(points["DB output"]["address"], "DB1.DBX0.0")
        self.assertEqual((points["Marker"]["address"], points["Marker"]["access"]), ("M0.0", "read_write"))
        self.assertEqual((points["Input"]["address"], points["Input"]["access"]), ("I0.0", "read"))
        self.assertEqual((points["Output"]["address"], points["Output"]["access"]), ("QD0", "read_write"))

    def test_io_channels_accept_explicit_s7_offsets_and_continue_auto_allocation(self):
        device = DeviceInput.model_validate({
            "node_id": "plc-explicit-areas", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.27", "config": {"components": [
                {"type": "io_channel", "name": "Explicit marker", "area": "M", "direction": "output",
                 "data_type": "BOOL", "byte_offset": 4, "bit_offset": 3},
                {"type": "io_channel", "name": "Automatic marker", "area": "M", "direction": "output",
                 "data_type": "BOOL"},
                {"type": "io_channel", "name": "Explicit REAL", "area": "Q", "direction": "output",
                 "data_type": "REAL", "byte_offset": 6},
            ]},
        })
        runtime = PlcS7Device().build_runtime_device(device)
        points = {point["component_name"]: point for point in PlcS7Device().build_io_map(runtime)}
        self.assertEqual(points["Explicit marker"]["address"], "M4.3")
        self.assertEqual(points["Automatic marker"]["address"], "M5.0")
        self.assertEqual(points["Explicit REAL"]["address"], "QD6")

    def test_io_channel_explicit_offsets_reject_overlap_and_invalid_address_shapes(self):
        base = {"node_id": "plc-overlap", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.28"}
        invalid_configs = (
            {"components": [
                {"type": "valve", "name": "Existing valve"},
                {"type": "io_channel", "name": "Duplicate bit", "data_type": "BOOL", "byte_offset": 0, "bit_offset": 0},
            ]},
            {"components": [
                {"type": "io_channel", "name": "REAL", "data_type": "REAL", "byte_offset": 2},
                {"type": "io_channel", "name": "Overlapping BOOL", "data_type": "BOOL", "byte_offset": 3, "bit_offset": 0},
            ]},
            {"components": [{"type": "io_channel", "name": "Odd REAL", "data_type": "REAL", "byte_offset": 1}]},
            {"components": [{"type": "io_channel", "name": "Bit without byte", "bit_offset": 2}]},
            {"db_size": 8, "components": [{"type": "io_channel", "name": "Out of range", "byte_offset": 8}]},
        )
        for config in invalid_configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                DeviceInput.model_validate({**base, "config": config})

    def test_input_and_output_areas_require_matching_directions(self):
        for area, direction in (("I", "output"), ("Q", "input")):
            with self.subTest(area=area, direction=direction), self.assertRaises(ValueError):
                DeviceInput.model_validate({"node_id": "bad", "device_type": "plc-s7", "name": "PLC",
                    "ip": "172.30.10.18", "config": {"components": [
                        {"type": "io_channel", "name": "channel", "area": area, "direction": direction, "data_type": "BOOL"}
                    ]}})

    def test_motor_and_discrete_signals_share_boolean_db_offsets(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-2", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.12", "config": {"components": [
                {"type": "motor", "name": "pump"},
                {"type": "digital_input", "name": "limit"},
                {"type": "alarm", "name": "fault"},
            ]},
        })
        runtime = PlcS7Device().build_runtime_device(device)
        self.assertEqual([(item["byte_offset"], item["bit_offset"]) for item in runtime["config"]["components"]], [(0, 0), (0, 1), (0, 2)])
        io_map = PlcS7Device().build_io_map(runtime)
        self.assertEqual([item["access"] for item in io_map], ["read_write", "read", "read"])

    def test_pump_expands_to_writable_command_and_read_only_flow_signal(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-pump", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.13", "config": {"components": [
                {"type": "valve", "name": "v"},
                {"type": "pump", "name": "pump", "flow_min": 20, "flow_max": 100},
            ]},
        })
        runtime = PlcS7Device().build_runtime_device(device)
        command, flow = runtime["config"]["components"][1:]
        self.assertEqual((command["type"], command["byte_offset"], command["bit_offset"]), ("pump_command", 2, 0))
        self.assertEqual((flow["type"], flow["byte_offset"], flow["command_byte_offset"]), ("pump_flow", 4, 2))
        io_map = PlcS7Device().build_io_map(runtime)
        self.assertEqual([point["data_type"] for point in io_map], ["BOOL", "BOOL", "REAL"])
        self.assertEqual([point["access"] for point in io_map], ["read_write", "read_write", "read"])

    def test_container_receives_runtime_config_only_through_environment(self) -> None:
        device = DeviceInput.model_validate(
            {
                "node_id": "plc-1",
                "device_type": "plc-s7",
                "name": "PLC principal",
                "ip": "172.30.10.10",
                "config": {"components": [{"type": "valve", "name": "v1"}]},
            }
        )
        runtime = PlcS7Device().build_runtime_device(device)
        config = PlcS7Device().build_container_config("scenario-id", runtime)

        self.assertNotIn("ports", config)
        self.assertEqual(json.loads(config["environment"]["PLC_CONFIG"]), runtime["config"])
        self.assertEqual(config["labels"]["anvil.node_id"], "plc-1")

    def test_external_s7_access_is_normalized_to_private_protocol_ingress(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.10.10", "external_access": True, "external_port": 1102,
            "config": {"components": [{"type": "valve", "name": "v1"}]},
        })
        self.assertTrue(device.external_access)
        self.assertEqual(device.external_port, 1102)

    def test_ingress_published_listener_binds_all_interfaces_but_healthchecks_zone_ip(self) -> None:
        runtime = {
            "node_id": "__anvil_ingress_plc-1",
            "external_host_ip": "127.0.0.1",
            "external_port": 1102,
            "config": {"listen_ip": "172.30.10.12", "listen_port": 25000,
                       "target_ip": "172.30.10.10", "target_port": 102,
                       "target_node_id": "plc-1", "protocol": "s7"},
        }
        config = ProtocolIngressDevice().build_container_config("scenario-id", runtime)
        payload = json.loads(config["environment"]["ANVIL_INGRESS_CONFIG"])
        self.assertEqual(payload["bind_ip"], "0.0.0.0")
        self.assertEqual(payload["listen_ip"], "172.30.10.12")
        self.assertEqual(config["ports"], {"25000/tcp": ("127.0.0.1", 1102)})
        self.assertIn("172.30.10.12", config["healthcheck"]["test"][-1])

    def test_external_bind_ip_requires_explicit_access_and_specific_ipv4(self) -> None:
        base = {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.10",
                "config": {"components": []}}
        for override in (
            {"external_host_ip": "192.0.2.10"},
            {"external_access": True, "external_port": 102, "external_host_ip": "0.0.0.0"},
            {"external_access": True, "external_port": 102, "external_host_ip": "224.0.0.1"},
            {"external_access": True, "external_port": 102, "external_host_ip": "2001:db8::1"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                DeviceInput.model_validate({**base, **override})


if __name__ == "__main__":
    unittest.main()
