from __future__ import annotations

import copy
import io
import json
import sys
import unittest
import zipfile
from unittest.mock import patch
import yaml
from pathlib import Path
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.scenario import ScenarioCreate, ScenarioRecord
from models.field import AnalogField, FlowSplitterField, VariableSpeedDriveField, TankField, ProcessConnection, field_ports, process_ports, validate_process_connections
from models.db import create_db_and_tables
from sqlmodel import Session
import test_api as api_tests


class FieldModelTests(unittest.TestCase):
    @staticmethod
    def payload():
        payload = api_tests.ScenarioApiTests.payload()
        payload["field_objects"] = [
            {"object_id": "valve-1", "type": "valve", "name": "Válvula independiente", "response_seconds": 2, "position": {"x": 20, "y": 30}},
            {"object_id": "sensor-1", "type": "analog_sensor", "name": "Presión", "min_value": 0, "max_value": 10, "unit": "bar"},
        ]
        payload["signal_connections"] = [
            {"connection_id": "order", "source": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBX0.0"}, "target": {"kind": "field", "object_id": "valve-1", "port": "command"}},
            {"connection_id": "measure", "source": {"kind": "field", "object_id": "sensor-1", "port": "value"}, "target": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBD2"}},
        ]
        return payload

    def test_valid_connections_and_legacy_defaults(self):
        self.assertEqual(len(ScenarioCreate.model_validate(self.payload()).signal_connections), 2)
        legacy = ScenarioCreate.model_validate(api_tests.ScenarioApiTests.payload())
        self.assertEqual(legacy.field_objects, [])
        self.assertEqual(legacy.devices[0].hostname, "plc-1")

    def test_sensor_noise_seed_is_strict_and_bounded(self):
        sensor = AnalogField.model_validate({
            "object_id": "sensor-seeded", "type": "analog_sensor", "name": "Sensor",
            "simulation": {"mode": "constant", "noise": 0.2, "seed": 4294967295},
        })
        self.assertEqual(sensor.simulation.seed, 4294967295)
        for seed in (-1, 4294967296, 1.5, True):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                AnalogField.model_validate({
                    "object_id": "sensor-seeded", "type": "analog_sensor", "name": "Sensor",
                    "simulation": {"mode": "constant", "noise": 0.2, "seed": seed},
                })

    def test_variable_speed_drive_has_bounded_real_ports_and_physical_flow(self):
        drive = VariableSpeedDriveField.model_validate({
            "object_id": "vfd-1", "type": "variable_speed_drive", "name": "Variador",
            "initial_speed_pct": 35, "max_flow_lpm": 80,
        })
        self.assertEqual({port["port"]: port["data_type"] for port in field_ports(drive)}, {
            "command": "REAL", "speed": "REAL", "flow": "REAL", "fault": "BOOL",
        })
        invalid_speed = drive.model_dump()
        invalid_speed["initial_speed_pct"] = 101
        with self.assertRaises(ValueError):
            VariableSpeedDriveField.model_validate(invalid_speed)
        tank = TankField.model_validate({"object_id": "tank-1", "type": "tank", "name": "Depósito"})
        link = ProcessConnection.model_validate({
            "connection_id": "vfd-tank",
            "source": {"object_id": "vfd-1", "port": "flow"},
            "target": {"object_id": "tank-1", "port": "inlet"},
        })
        validate_process_connections([drive, tank], [link])

    def test_variable_speed_drive_can_be_commanded_by_s7_real_output(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"][0]["config"]["components"] = [{
            "type": "io_channel", "channel_id": "speed-command", "name": "Consigna VFD",
            "db": 1, "direction": "output", "data_type": "REAL", "min_value": 0, "max_value": 100,
        }]
        payload["field_objects"] = [{
            "object_id": "vfd-1", "type": "variable_speed_drive", "name": "Variador",
            "max_flow_lpm": 80,
        }]
        payload["signal_connections"] = [{
            "connection_id": "speed-link",
            "source": {"kind": "device", "node_id": "plc-1", "channel_id": "speed-command", "address": "DB1.DBD0"},
            "target": {"kind": "field", "object_id": "vfd-1", "port": "command"},
        }]
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual(scenario.signal_connections[0].target.port, "command")

    def test_network_connections_are_same_zone_descriptive_links(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"].append({
            "node_id": "plc-2", "device_type": "plc-s7", "name": "PLC secundario",
            "ip": "172.30.10.11", "config": {"components": []},
        })
        payload["network_connections"] = [{"connection_id": "ethernet-1", "source_node_id": "plc-1", "target_node_id": "plc-2"}]
        self.assertEqual(ScenarioCreate.model_validate(payload).network_connections[0].label, "Ethernet")
        for invalid, message in [
            ([{"connection_id": "loop", "source_node_id": "plc-1", "target_node_id": "plc-1"}], "itself"),
            ([{"connection_id": "missing", "source_node_id": "plc-1", "target_node_id": "missing"}], "unknown device"),
            ([{"connection_id": "a", "source_node_id": "plc-1", "target_node_id": "plc-2"}, {"connection_id": "b", "source_node_id": "plc-2", "target_node_id": "plc-1"}], "duplicate network connection"),
        ]:
            candidate = copy.deepcopy(payload)
            candidate["network_connections"] = invalid
            with self.assertRaisesRegex(ValueError, message):
                ScenarioCreate.model_validate(candidate)
        cross_zone = copy.deepcopy(payload)
        cross_zone["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        cross_zone["devices"][0].update(zone_id="control", ip="172.30.10.10")
        cross_zone["devices"][1].update(zone_id="operations", ip="172.30.11.10")
        with self.assertRaisesRegex(ValueError, "crosses Purdue zones"):
            ScenarioCreate.model_validate(cross_zone)

        isolated_host = copy.deepcopy(payload)
        isolated_host["devices"].append({
            "node_id": "engineering", "device_type": "engineering-station", "name": "Engineering",
            "ip": "172.30.10.12", "config": {"role": "engineering", "summary": "Engineering station"},
        })
        isolated_host["network_connections"] = [{
            "connection_id": "isolated-link", "source_node_id": "plc-1", "target_node_id": "engineering",
        }]
        with self.assertRaisesRegex(ValueError, "both devices attached"):
            ScenarioCreate.model_validate(isolated_host)
        isolated_host["devices"][-1]["config"]["engineering_access_enabled"] = True
        self.assertEqual(len(ScenarioCreate.model_validate(isolated_host).network_connections), 1)

    def test_generic_tcp_routes_require_a_valid_port_and_cross_zones(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "Panel"},
        })
        payload["network_routes"] = [{"route_id": "generic", "source_node_id": "hmi-1",
            "target_node_id": "plc-1", "protocol": "tcp", "tcp_port": 25000}]
        route = ScenarioCreate.model_validate(payload).network_routes[0]
        self.assertEqual(route.target_port, 25000)
        invalid = copy.deepcopy(payload)
        del invalid["network_routes"][0]["tcp_port"]
        with self.assertRaisesRegex(ValueError, "tcp_port is required"):
            ScenarioCreate.model_validate(invalid)
        invalid = copy.deepcopy(payload)
        invalid["network_routes"][0]["tcp_port"] = 65536
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)
        invalid["network_routes"][0]["tcp_port"] = True
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)
        invalid = copy.deepcopy(payload)
        invalid["devices"][1]["zone_id"] = "control"
        invalid["devices"][1]["ip"] = "172.30.10.11"
        with self.assertRaisesRegex(ValueError, "must connect different zones"):
            ScenarioCreate.model_validate(invalid)
        isolated = copy.deepcopy(payload)
        isolated["devices"].append({"node_id": "attacker", "device_type": "attacker-station",
            "name": "Aislada", "ip": "172.30.11.11", "zone_id": "operations",
            "config": {"role": "attacker", "summary": "Sin interfaz de red"}})
        isolated["network_routes"][0]["source_node_id"] = "attacker"
        with self.assertRaisesRegex(ValueError, "requires endpoints attached"):
            ScenarioCreate.model_validate(isolated)

    def test_hostnames_are_validated_unique_per_zone_and_defaulted_from_node_ids(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"].append({
            "node_id": "hmi one", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.11",
            "config": {"role": "hmi", "summary": "Operator"},
        })
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual([device.hostname for device in scenario.devices], ["plc-1", "hmi-one"])
        duplicate = copy.deepcopy(payload)
        duplicate["devices"][1]["hostname"] = "plc-1"
        with self.assertRaisesRegex(ValueError, "duplicate hostname"):
            ScenarioCreate.model_validate(duplicate)
        invalid = copy.deepcopy(payload)
        invalid["devices"][1]["hostname"] = "bad.host.name"
        with self.assertRaisesRegex(ValueError, "DNS label"):
            ScenarioCreate.model_validate(invalid)

    def test_physical_process_links_validate_flow_units_and_sensor_mode(self):
        payload=self.payload()
        payload['field_objects']=[
            {'object_id':'pump','type':'pump','name':'Pump','unit':'L/min'},
            {'object_id':'tank','type':'tank','name':'Tank','capacity_liters':60,'initial_level_liters':30,'outlet_flow_lpm':20},
            {'object_id':'valve','type':'valve','name':'Drain','flow_rate_lpm':30},
            {'object_id':'level','type':'analog_sensor','name':'Level','unit':'%','min_value':0,'max_value':100,'simulation':{'mode':'process'}},
        ]
        payload['signal_connections']=[]
        payload['process_connections']=[
            {'connection_id':'fill','source':{'object_id':'pump','port':'flow'},'target':{'object_id':'tank','port':'inlet'}},
            {'connection_id':'drain','source':{'object_id':'tank','port':'outlet'},'target':{'object_id':'valve','port':'process_in'}},
            {'connection_id':'level','source':{'object_id':'tank','port':'level'},'target':{'object_id':'level','port':'measurement'}},
        ]
        model=ScenarioCreate.model_validate(payload)
        self.assertEqual(len(model.process_connections),3)
        invalid=copy.deepcopy(payload)
        invalid['field_objects'][3]['simulation']={'mode':'constant'}
        with self.assertRaisesRegex(ValueError,'must use process simulation'):
            ScenarioCreate.model_validate(invalid)
        invalid=copy.deepcopy(payload)
        invalid['process_connections'][1]['target']['port']='measurement'
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)

    def test_pipe_is_a_configurable_flow_limiter_in_the_process_topology(self):
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Pump"},
            {"object_id": "pipe", "type": "pipe", "name": "Feed line", "max_flow_lpm": 35},
            {"object_id": "tank", "type": "tank", "name": "Tank"},
        ]
        payload["signal_connections"] = []
        payload["process_connections"] = [
            {"connection_id": "pump-pipe", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "pipe", "port": "process_in"}},
            {"connection_id": "pipe-tank", "source": {"object_id": "pipe", "port": "process_out"},
             "target": {"object_id": "tank", "port": "inlet"}},
        ]
        model = ScenarioCreate.model_validate(payload)
        self.assertEqual(model.field_objects[1].type, "pipe")
        self.assertEqual(model.field_objects[1].max_flow_lpm, 35)
        self.assertEqual(model.field_objects[1].pressure_drop_kpa, 0)

        invalid = copy.deepcopy(payload)
        invalid["field_objects"][1]["max_flow_lpm"] = 0
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        invalid["field_objects"][1]["pressure_drop_kpa"] = -0.1
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)

    def test_process_connection_cycles_remain_valid_for_runtime_compatibility(self):
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pipe-a", "type": "pipe", "name": "Pipe A"},
            {"object_id": "pipe-b", "type": "pipe", "name": "Pipe B"},
        ]
        payload["signal_connections"] = []
        payload["process_connections"] = [
            {"connection_id": "a-b", "source": {"object_id": "pipe-a", "port": "process_out"},
             "target": {"object_id": "pipe-b", "port": "process_in"}},
            {"connection_id": "b-a", "source": {"object_id": "pipe-b", "port": "process_out"},
             "target": {"object_id": "pipe-a", "port": "process_in"}},
        ]
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual(len(scenario.process_connections), 2)

    def test_pump_can_feed_parallel_branches_but_intermediate_pipe_cannot_split(self):
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Pump"},
            {"object_id": "pipe-a", "type": "pipe", "name": "Pipe A"},
            {"object_id": "pipe-b", "type": "pipe", "name": "Pipe B"},
            {"object_id": "tank-a", "type": "tank", "name": "Tank A"},
            {"object_id": "tank-b", "type": "tank", "name": "Tank B"},
        ]
        payload["signal_connections"] = []
        payload["process_connections"] = [
            {"connection_id": "pump-a", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "pipe-a", "port": "process_in"}},
            {"connection_id": "pump-b", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "pipe-b", "port": "process_in"}},
            {"connection_id": "a-tank", "source": {"object_id": "pipe-a", "port": "process_out"},
             "target": {"object_id": "tank-a", "port": "inlet"}},
            {"connection_id": "b-tank", "source": {"object_id": "pipe-b", "port": "process_out"},
             "target": {"object_id": "tank-b", "port": "inlet"}},
        ]
        self.assertEqual(len(ScenarioCreate.model_validate(payload).process_connections), 4)

        invalid = copy.deepcopy(payload)
        invalid["process_connections"][1]["source"] = {"object_id": "pipe-a", "port": "process_out"}
        with self.assertRaisesRegex(ValueError, "only pump or drive flow outputs"):
            ScenarioCreate.model_validate(invalid)

    def test_process_manifold_models_validate_ports_and_split_ratio(self):
        splitter = FlowSplitterField.model_validate({
            "object_id": "splitter", "type": "flow_splitter", "name": "Distributor",
            "split_ratio_pct": 35,
        })
        self.assertEqual(splitter.split_ratio_pct, 35)
        self.assertEqual([port["port"] for port in process_ports(splitter)], [
            "process_in", "process_out_a", "process_out_b", "pressure",
        ])
        invalid = splitter.model_dump()
        invalid["split_ratio_pct"] = 101
        with self.assertRaises(ValueError):
            FlowSplitterField.model_validate(invalid)

        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Pump"},
            {"object_id": "splitter", "type": "flow_splitter", "name": "Distributor"},
            {"object_id": "pipe-a", "type": "pipe", "name": "Branch A"},
            {"object_id": "pipe-b", "type": "pipe", "name": "Branch B"},
            {"object_id": "mixer", "type": "flow_mixer", "name": "Collector"},
            {"object_id": "tank", "type": "tank", "name": "Tank"},
        ]
        payload["signal_connections"] = []
        payload["process_connections"] = [
            {"connection_id": "feed", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "splitter", "port": "process_in"}},
            {"connection_id": "branch-a", "source": {"object_id": "splitter", "port": "process_out_a"},
             "target": {"object_id": "pipe-a", "port": "process_in"}},
            {"connection_id": "branch-b", "source": {"object_id": "splitter", "port": "process_out_b"},
             "target": {"object_id": "pipe-b", "port": "process_in"}},
            {"connection_id": "merge-a", "source": {"object_id": "pipe-a", "port": "process_out"},
             "target": {"object_id": "mixer", "port": "process_in_a"}},
            {"connection_id": "merge-b", "source": {"object_id": "pipe-b", "port": "process_out"},
             "target": {"object_id": "mixer", "port": "process_in_b"}},
            {"connection_id": "out", "source": {"object_id": "mixer", "port": "process_out"},
             "target": {"object_id": "tank", "port": "inlet"}},
        ]
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual(len(scenario.process_connections), 6)

    def test_tank_pressure_connects_only_to_a_matching_process_sensor(self):
        payload = self.payload()
        payload['field_objects'] = [
            {'object_id': 'tank', 'type': 'tank', 'name': 'Tank', 'height_m': 3},
            {'object_id': 'pressure', 'type': 'analog_sensor', 'name': 'Pressure', 'unit': 'kPa',
             'min_value': 0, 'max_value': 30, 'simulation': {'mode': 'process'}},
        ]
        payload['signal_connections'] = []
        payload['process_connections'] = [{
            'connection_id': 'head-pressure',
            'source': {'object_id': 'tank', 'port': 'pressure'},
            'target': {'object_id': 'pressure', 'port': 'measurement'},
        }]
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual(scenario.field_objects[0].height_m, 3)
        mismatch = copy.deepcopy(payload)
        mismatch['field_objects'][1]['unit'] = 'bar'
        with self.assertRaisesRegex(ValueError, 'units must match'):
            ScenarioCreate.model_validate(mismatch)

    def test_pipe_pressure_output_connects_to_process_sensor(self):
        payload = self.payload()
        payload['field_objects'] = [
            {'object_id': 'pump', 'type': 'pump', 'name': 'Pump', 'max_head_kpa': 120},
            {'object_id': 'pipe', 'type': 'pipe', 'name': 'Pipe', 'pressure_drop_kpa': 20},
            {'object_id': 'pressure', 'type': 'analog_sensor', 'name': 'Line pressure',
             'min_value': 0, 'max_value': 120, 'unit': 'kPa',
             'simulation': {'mode': 'process'}},
        ]
        payload['signal_connections'] = []
        payload['process_connections'] = [
            {'connection_id': 'pump-pipe',
             'source': {'object_id': 'pump', 'port': 'flow'},
             'target': {'object_id': 'pipe', 'port': 'process_in'}},
            {'connection_id': 'pipe-pressure',
             'source': {'object_id': 'pipe', 'port': 'pressure'},
             'target': {'object_id': 'pressure', 'port': 'measurement'}},
        ]
        scenario = ScenarioCreate.model_validate(payload)
        self.assertEqual(scenario.field_objects[1].pressure_drop_kpa, 20)
        mismatch = copy.deepcopy(payload)
        mismatch['field_objects'][2]['unit'] = 'bar'
        with self.assertRaisesRegex(ValueError, 'units must match'):
            ScenarioCreate.model_validate(mismatch)

    def test_tank_leak_disturbance_is_optional_and_rejects_negative_flow(self):
        payload = self.payload()
        payload['field_objects'] = [
            {'object_id': 'tank', 'type': 'tank', 'name': 'Tank', 'leak_flow_lpm': 12.5},
        ]
        payload['signal_connections'] = []
        payload['process_connections'] = []
        model = ScenarioCreate.model_validate(payload)
        self.assertEqual(model.field_objects[0].leak_flow_lpm, 12.5)
        payload['field_objects'][0]['leak_flow_lpm'] = -0.1
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(payload)

    def test_tank_demand_defaults_to_zero_and_must_be_non_negative(self):
        payload = self.payload()
        payload['field_objects'] = [{'object_id': 'tank', 'type': 'tank', 'name': 'Tank'}]
        payload['signal_connections'] = []
        payload['process_connections'] = []
        self.assertEqual(ScenarioCreate.model_validate(payload).field_objects[0].demand_flow_lpm, 0)
        payload['field_objects'][0]['demand_flow_lpm'] = 5.5
        self.assertEqual(ScenarioCreate.model_validate(payload).field_objects[0].demand_flow_lpm, 5.5)
        payload['field_objects'][0]['demand_flow_lpm'] = -0.1
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(payload)

    def test_tank_demand_control_requires_a_non_negative_finite_number(self):
        from models.field import FieldControl
        self.assertEqual(FieldControl.model_validate({'demand_flow_lpm': 3}).demand_flow_lpm, 3)
        for value in (-1, True, float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FieldControl.model_validate({'demand_flow_lpm': value})

    def test_purdue_zones_validate_subnets_assignments_and_signal_boundaries(self):
        payload = self.payload()
        payload["network"]["zones"] = [
            {"zone_id": "level-0", "name": "Proceso", "purdue_level": "L0", "subnet": "172.30.10.0/24"},
            {"zone_id": "level-1", "name": "Control", "purdue_level": "L1", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0]["zone_id"] = "level-1"
        payload["devices"][0]["ip"] = "172.30.11.10"
        payload["field_objects"][0]["zone_id"] = "level-1"
        payload["field_objects"][1]["zone_id"] = "level-1"
        self.assertEqual(ScenarioCreate.model_validate(payload).devices[0].zone_id, "level-1")

        cross_zone = copy.deepcopy(payload)
        cross_zone["field_objects"][0]["zone_id"] = "level-0"
        with self.assertRaisesRegex(ValueError, "span multiple Purdue zones"):
            ScenarioCreate.model_validate(cross_zone)

        invalid = copy.deepcopy(payload)
        invalid["network"]["zones"][1]["subnet"] = "172.30.10.128/25"
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        invalid["devices"][0]["ip"] = "172.30.10.10"
        with self.assertRaisesRegex(ValueError, "outside"):
            ScenarioCreate.model_validate(invalid)

    def test_cross_zone_field_signal_requires_and_materializes_explicit_gateway(self):
        payload = self.payload()
        payload["network"]["zones"] = [
            {"zone_id": "field", "name": "Campo", "purdue_level": "L0", "subnet": "172.30.10.0/24"},
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0].update(zone_id="control", ip="172.30.11.10")
        payload["devices"].append({
            "node_id": "plc-2", "device_type": "plc-s7", "name": "PLC 2", "ip": "172.30.11.11",
            "zone_id": "control", "config": {"components": [
                {"type": "io_channel", "channel_id": "measurement-2", "name": "Measurement 2", "direction": "input", "data_type": "REAL", "unit": "%"},
            ]},
        })
        for item in payload["field_objects"]:
            item["zone_id"] = "field"
        payload["field_objects"].append({"object_id": "sensor-2", "type": "analog_sensor", "name": "Sensor 2", "zone_id": "field"})
        with self.assertRaisesRegex(ValueError, "declare its explicit field gateway route"):
            ScenarioCreate.model_validate(payload)

        blocked = copy.deepcopy(payload)
        for connection in blocked["signal_connections"]:
            connection["route_enabled"] = False
        blocked_scenario = ScenarioCreate.model_validate(blocked)
        from services.scenario_service import ScenarioService
        blocked_runtime = ScenarioService._build_runtime_devices(blocked_scenario)
        self.assertFalse(any(item["device_type"] == "tcp-gateway" for item in blocked_runtime))
        blocked_plc = next(item for item in blocked_runtime if item["node_id"] == "plc-1")
        blocked_field = next(item for item in blocked_runtime if item["device_type"] == "field-runtime")
        self.assertEqual(blocked_plc["config"]["field_bridge"]["url"], f"http://{blocked_field['ip']}:8765/exchange")

        for connection in payload["signal_connections"]:
            connection["route_enabled"] = True
        payload["signal_connections"].append({
            "connection_id": "measure-2", "source": {"kind": "field", "object_id": "sensor-2", "port": "value"},
            "target": {"kind": "device", "node_id": "plc-2", "address": "DB1.DBD0", "channel_id": "measurement-2"},
            "route_enabled": True,
        })
        scenario = ScenarioCreate.model_validate(payload)
        runtime = ScenarioService._build_runtime_devices(scenario)
        field_runtime = next(item for item in runtime if item["device_type"] == "field-runtime")
        self.assertEqual(field_runtime["config"]["contract_version"], 1)
        gateways = [item for item in runtime if item["device_type"] == "tcp-gateway"]
        gateway = gateways[0]
        plc = next(item for item in runtime if item["node_id"] == "plc-1")
        self.assertEqual(field_runtime["zone_id"], "field")
        self.assertEqual(gateway["config"]["protocol"], "field-http")
        self.assertEqual(gateway["config"]["target_node_id"], "__anvil_field__")
        self.assertEqual(gateway["config"]["target_port"], 8765)
        self.assertEqual(gateway["config"]["target_zone_id"], "field")
        self.assertEqual(len(gateways), 2)
        self.assertNotEqual(gateways[0]["ip"], gateways[1]["ip"])
        self.assertNotEqual(gateways[0]["config"]["target_interface_ip"], gateways[1]["config"]["target_interface_ip"])
        self.assertIn(f"http://{gateway['ip']}:{gateway['config']['listen_port']}/exchange", plc["config"]["field_bridge"]["url"])
        from services.compose_export import build_compose_bundle
        archive = zipfile.ZipFile(io.BytesIO(build_compose_bundle("fieldroute0000001", scenario, runtime)))
        compose = yaml.safe_load(archive.read("docker-compose.yml"))
        gateway_service = next(service for service in compose["services"].values()
                               if service["labels"]["anvil.node_id"] == gateway["node_id"])
        self.assertEqual(set(gateway_service["networks"]), {"zone_control", "zone_field"})
        self.assertTrue(all(compose["networks"][name]["internal"] for name in gateway_service["networks"]))
        mixed = copy.deepcopy(payload)
        mixed["signal_connections"][0]["route_enabled"] = False
        with self.assertRaisesRegex(ValueError, "must share the same gateway route state"):
            ScenarioCreate.model_validate(mixed)

    def test_hmi_and_s7_links_cannot_cross_purdue_zones_without_a_route(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L3", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0]["zone_id"] = "control"
        payload["devices"].append({
            "node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations",
            "config": {"role": "hmi", "summary": "Operator panel", "target_node_ids": "plc-1"},
        })
        with self.assertRaisesRegex(ValueError, "different zones"):
            ScenarioCreate.model_validate(payload)

    def test_scada_cross_zone_route_builds_a_single_protocol_gateway(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "scada-1", "device_type": "scada", "name": "SCADA", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"target_node_ids": "plc-1,plc-2"},
        })
        payload["devices"].append({
            "node_id": "plc-2", "device_type": "plc-s7", "name": "PLC 2", "ip": "172.30.10.11",
            "zone_id": "control", "config": {"components": [{"type": "valve", "name": "V2"}]},
        })
        payload["network_routes"] = [{
            "route_id": "scada-plc", "source_node_id": "scada-1", "target_node_id": "plc-1",
            "protocol": "s7", "enabled": True,
        }, {
            "route_id": "scada-plc-2", "source_node_id": "scada-1", "target_node_id": "plc-2",
            "protocol": "s7", "enabled": True,
        }]
        scenario = ScenarioCreate.model_validate(payload)
        from services.scenario_service import ScenarioService
        runtime = ScenarioService._build_runtime_devices(scenario)
        gateway = next(item for item in runtime if item["device_type"] == "tcp-gateway")
        scada = next(item for item in runtime if item["node_id"] == "scada-1")
        target = scada["config"]["targets"][0]
        self.assertEqual(target["host"], gateway["ip"])
        self.assertEqual(target["port"], scenario.network_routes[0].listen_port)
        self.assertEqual(gateway["config"]["target_ip"], "172.30.10.10")
        self.assertEqual(gateway["config"]["target_port"], 102)
        second_gateway = next(item for item in runtime if item["node_id"] == "__anvil_route_scada-plc-2")
        self.assertNotEqual(gateway["config"]["target_interface_ip"], second_gateway["config"]["target_interface_ip"])
        self.assertNotEqual(gateway["ip"], second_gateway["ip"])
        disabled = copy.deepcopy(payload)
        for route in disabled["network_routes"]:
            route["enabled"] = False
        disabled_runtime = ScenarioService._build_runtime_devices(ScenarioCreate.model_validate(disabled))
        self.assertFalse(any(item["device_type"] == "tcp-gateway" for item in disabled_runtime))
        invalid = copy.deepcopy(payload)
        invalid["network_routes"][0]["protocol"] = "modbus-tcp"
        with self.assertRaisesRegex(ValueError, "does not match its target"):
            ScenarioCreate.model_validate(invalid)

    def test_hmi_cross_zone_route_builds_gateway_for_hmi_originated_operations(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "Operator HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "Operator", "target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{
            "route_id": "hmi-to-plc", "source_node_id": "hmi-1", "target_node_id": "plc-1",
            "protocol": "s7", "enabled": True,
        }]
        scenario = ScenarioCreate.model_validate(payload)
        from services.scenario_service import ScenarioService
        runtime = ScenarioService._build_runtime_devices(scenario)
        hmi = next(item for item in runtime if item["node_id"] == "hmi-1")
        gateway = next(item for item in runtime if item["device_type"] == "tcp-gateway")
        self.assertEqual(hmi["config"]["network_routes"]["plc-1"]["host"], gateway["ip"])
        self.assertEqual(hmi["config"]["network_routes"]["plc-1"]["port"], scenario.network_routes[0].listen_port)
        self.assertEqual(gateway["config"]["target_zone_id"], "control")
        self.assertEqual(gateway["config"]["target_port"], 102)

    def test_field_only_draft_and_empty_rejected(self):
        payload = self.payload()
        payload["devices"] = []
        payload["signal_connections"] = []
        self.assertEqual(len(ScenarioCreate.model_validate(payload).field_objects), 2)
        payload["field_objects"] = []
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(payload)

    def test_duplicate_identifiers_targets_and_unknown_endpoints(self):
        cases = []
        payload = self.payload()
        payload["field_objects"].append(copy.deepcopy(payload["field_objects"][0]))
        cases.append(payload)
        payload = self.payload()
        payload["signal_connections"].append(copy.deepcopy(payload["signal_connections"][0]))
        cases.append(payload)
        payload = self.payload()
        extra = copy.deepcopy(payload["signal_connections"][0])
        extra["connection_id"] = "other"
        payload["signal_connections"].append(extra)
        cases.append(payload)
        for key, value in [("object_id", "missing"), ("port", "missing"), ("port", "state")]:
            payload = self.payload()
            payload["signal_connections"][0]["target"][key] = value
            cases.append(payload)
        payload = self.payload()
        payload["signal_connections"][0]["source"]["address"] = "DB1.DBX9.0"
        cases.append(payload)
        payload = self.payload()
        payload["field_objects"][0]["object_id"] = "plc-1"
        cases.append(payload)
        for index, payload in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                ScenarioCreate.model_validate(payload)

    def test_type_direction_and_readonly_command_validation(self):
        for source in (
            {"kind": "field", "object_id": "sensor-1", "port": "value"},
            {"kind": "device", "node_id": "plc-1", "address": "DB1.DBD2"},
        ):
            payload = self.payload()
            payload["signal_connections"][0]["source"] = source
            with self.assertRaises(ValueError):
                ScenarioCreate.model_validate(payload)
        payload = self.payload()
        payload["signal_connections"][1]["target"]["address"] = "DB1.DBX0.0"
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(payload)

    def test_plc_program_output_can_drive_field_actuator_without_manual_write_permission(self):
        payload = self.payload()
        payload["devices"][0]["config"] = {
            "components": [
                {"type": "io_channel", "channel_id": "sensor", "name": "Nivel", "direction": "input", "data_type": "REAL"},
                {"type": "io_channel", "channel_id": "pump", "name": "Bomba", "direction": "output", "data_type": "BOOL"},
            ],
            "control_rules": [{"name": "Regla", "input_component": "Nivel", "output_component": "Bomba", "mode": "below", "setpoint": 10, "hysteresis": 1}],
        }
        payload["field_objects"] = [{"object_id": "pump-field", "type": "pump", "name": "Bomba"}]
        payload["signal_connections"] = [{
            "connection_id": "pump-command",
            "source": {"kind": "device", "node_id": "plc-1", "channel_id": "pump", "address": "DB1.DBX1.0"},
            "target": {"kind": "field", "object_id": "pump-field", "port": "command"},
        }]
        self.assertEqual(len(ScenarioCreate.model_validate(payload).signal_connections), 1)

    def test_proportional_valve_accepts_bounded_s7_real_command_and_process_links(self):
        payload = self.payload()
        payload["devices"][0]["config"] = {"components": [
            {"type": "io_channel", "channel_id": "valve-command", "name": "Valve command", "direction": "output", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
        ]}
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Feed pump", "flow_max": 60},
            {"object_id": "valve", "type": "proportional_valve", "name": "Control valve", "flow_rate_lpm": 60},
            {"object_id": "tank", "type": "tank", "name": "Receiver", "capacity_liters": 100, "initial_level_liters": 0},
        ]
        payload["signal_connections"] = [{
            "connection_id": "valve-setpoint",
            "source": {"kind": "device", "node_id": "plc-1", "channel_id": "valve-command", "address": "DB1.DBD0"},
            "target": {"kind": "field", "object_id": "valve", "port": "command"},
        }]
        payload["process_connections"] = [
            {"connection_id": "pump-valve", "source": {"object_id": "pump", "port": "flow"}, "target": {"object_id": "valve", "port": "process_in"}},
            {"connection_id": "valve-tank", "source": {"object_id": "valve", "port": "process_out"}, "target": {"object_id": "tank", "port": "inlet"}},
        ]
        model = ScenarioCreate.model_validate(payload)
        self.assertEqual(model.field_objects[1].type, "proportional_valve")
        self.assertEqual(model.devices[0].config.components[0].max_value, 100)

        invalid = copy.deepcopy(payload)
        invalid["devices"][0]["config"]["components"][0]["max_value"] = 0
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        invalid["devices"][0]["config"]["components"][0]["max_value"] = 101
        with self.assertRaisesRegex(ValueError, "source signal range exceeds"):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        invalid["devices"][0]["config"]["components"][0]["unit"] = "L/min"
        with self.assertRaisesRegex(ValueError, "incompatible signal units"):
            ScenarioCreate.model_validate(invalid)

    def test_modbus_holding_register_can_drive_proportional_valve_with_explicit_scaling(self):
        payload = self.payload()
        payload["devices"].append({
            "node_id": "modbus-1", "device_type": "modbus-tcp", "name": "Modbus",
            "ip": "172.30.10.12", "config": {"points": [
                {"type": "holding_register", "name": "Valve opening", "initial_value": 0},
            ]},
        })
        payload["field_objects"] = [
            {"object_id": "valve", "type": "proportional_valve", "name": "Modbus valve"},
        ]
        payload["signal_connections"] = [{
            "connection_id": "modbus-setpoint",
            "source": {"kind": "device", "node_id": "modbus-1", "address": "holding_register:0"},
            "target": {"kind": "field", "object_id": "valve", "port": "command"},
            "conversion": {"input_min": 0, "input_max": 1000, "output_min": 0, "output_max": 100},
        }]
        model = ScenarioCreate.model_validate(payload)
        self.assertEqual(model.signal_connections[0].conversion.output_max, 100)

        invalid = copy.deepcopy(payload)
        invalid["signal_connections"][0]["conversion"]["input_max"] = 65536
        with self.assertRaisesRegex(ValueError, "UINT16 conversion input range"):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        invalid["signal_connections"][0]["conversion"]["output_max"] = 101
        with self.assertRaisesRegex(ValueError, "conversion output range exceeds"):
            ScenarioCreate.model_validate(invalid)

        invalid = copy.deepcopy(payload)
        del invalid["signal_connections"][0]["conversion"]
        with self.assertRaisesRegex(ValueError, "explicit conversion is required"):
            ScenarioCreate.model_validate(invalid)

    def test_invalid_values_and_unknown_properties(self):
        for field, key, value in ((0, "initial_state", "false"), (0, "response_seconds", -1), (0, "ip", "172.30.10.10"), (1, "max_value", float("inf")), (1, "max_value", -1), (0, "position", {"x": float("nan"), "y": 0})):
            payload = self.payload()
            payload["field_objects"][field][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                ScenarioCreate.model_validate(payload)

    def test_modbus_coil_command_and_incompatible_register(self):
        payload = self.payload()
        payload["devices"].append({"node_id": "modbus-1", "device_type": "modbus-tcp", "name": "Modbus", "ip": "172.30.10.11", "config": {"points": [{"type": "coil", "name": "Order"}, {"type": "input_register", "name": "Measure"}]}})
        payload["signal_connections"][0]["source"] = {"kind": "device", "node_id": "modbus-1", "address": "coil:0"}
        ScenarioCreate.model_validate(payload)
        payload["signal_connections"][1]["target"] = {"kind": "device", "node_id": "modbus-1", "address": "input_register:0"}
        with self.assertRaisesRegex(ValueError, "incompatible signal types"):
            ScenarioCreate.model_validate(payload)

    def test_boolean_field_input_compiles_to_modbus_discrete_input_binding(self):
        payload = self.payload()
        payload["devices"] = [{
            "node_id": "modbus-1", "device_type": "modbus-tcp", "name": "Modbus",
            "ip": "172.30.10.11", "config": {"points": [
                {"type": "discrete_input", "name": "presostato", "address": 0},
            ]},
        }]
        payload["field_objects"] = [{"object_id": "switch", "type": "digital_input", "name": "Presostato", "initial_state": True}]
        payload["signal_connections"] = [{
            "connection_id": "switch-to-modbus",
            "source": {"kind": "field", "object_id": "switch", "port": "state"},
            "target": {"kind": "device", "node_id": "modbus-1", "address": "discrete_input:0"},
        }]
        scenario = ScenarioCreate.model_validate(payload)
        from services.scenario_service import ScenarioService
        runtime = ScenarioService._build_runtime_devices(scenario)
        modbus = next(item for item in runtime if item["node_id"] == "modbus-1")
        binding = modbus["config"]["field_bridge"]["bindings"][0]
        self.assertEqual((binding["address"], binding["direction"], binding["data_type"]),
                         ("discrete_input:0", "input", "BOOL"))

    def test_different_units_are_not_implicitly_converted(self):
        payload = self.payload()
        payload["devices"][0]["config"]["components"][1]["unit"] = "psi"
        with self.assertRaisesRegex(ValueError, "incompatible signal units"):
            ScenarioCreate.model_validate(payload)

    def test_channel_identity_resolves_addresses_after_reordering(self):
        payload=self.payload()
        payload['signal_connections']=[]
        payload['devices'][0]['config']['components']=[
            {'type':'io_channel','channel_id':'command','name':'Command','direction':'output','data_type':'BOOL'},
            {'type':'io_channel','channel_id':'measure','name':'Measure','direction':'input','data_type':'REAL'},
        ]
        payload['signal_connections']=[{'connection_id':'measure','source':{'kind':'field','object_id':'sensor-1','port':'value'},'target':{'kind':'device','node_id':'plc-1','address':'DB1.DBD0','channel_id':'measure'}}]
        model=ScenarioCreate.model_validate(payload)
        self.assertEqual(model.signal_connections[0].target.address,'DB1.DBD2')
        payload['devices'][0]['config']['components'].reverse()
        model=ScenarioCreate.model_validate(payload)
        self.assertEqual(model.signal_connections[0].target.address,'DB1.DBD0')
        payload['devices'][0]['config']['components']=[payload['devices'][0]['config']['components'][1]]
        with self.assertRaisesRegex(ValueError,'no longer exists'):
            ScenarioCreate.model_validate(payload)


class FieldApiTests(unittest.TestCase):
    setUp = api_tests.ScenarioApiTests.setUp
    tearDown = api_tests.ScenarioApiTests.tearDown

    def create(self):
        response = self.client.post("/api/scenarios", json=FieldModelTests.payload())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_crud_export_import_and_clone_preserve_objects(self):
        created = self.create()
        scenario_id = created["id"]
        exported = self.client.get(f"/api/scenarios/{scenario_id}/export").json()
        self.assertEqual(exported["format"], "anvil-scenario")
        self.assertEqual(exported["schema_version"], 2)
        exported_scenario = exported["scenario"]
        imported = self.client.post("/api/scenarios/import", json=exported)
        clone = self.client.post(f"/api/scenarios/{scenario_id}/clone", json={})
        for response in (imported, clone):
            self.assertEqual(response.status_code, 201, response.text)
            for key in ("field_objects", "signal_connections", "process_connections"):
                self.assertEqual(response.json()[key], created[key])
        exported_scenario["name"] = "Updated"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=exported_scenario)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["field_objects"], created["field_objects"])

    def test_legacy_client_save_preserves_fields_and_revalidates_links(self):
        created = self.create()
        payload = api_tests.ScenarioApiTests.payload()
        response = self.client.put(f'/api/scenarios/{created["id"]}', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["field_objects"], created["field_objects"])
        payload["devices"][0]["config"]["components"] = [{"type": "valve", "name": "only-valve"}]
        response = self.client.put(f'/api/scenarios/{created["id"]}', json=payload)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(f'/api/scenarios/{created["id"]}').json()["devices"], created["devices"])

    def test_legacy_client_save_preserves_explicit_egress_allowlist(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"].append({
            "node_id": "engineering", "device_type": "engineering-station", "name": "Engineering",
            "ip": "172.30.10.12", "config": {
                "role": "engineering", "summary": "Engineering station", "engineering_access_enabled": True,
            },
        })
        payload["network"]["egress_rules"] = [{
            "rule_id": "docs", "source_node_id": "engineering", "hostname": "docs.example.net", "tcp_port": 443,
            "enabled": True,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(created.json()["network"]["egress_rules"], payload["network"]["egress_rules"])

        legacy = api_tests.ScenarioApiTests.payload()
        legacy["devices"].append(payload["devices"][-1])
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=legacy)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["network"]["egress_rules"], payload["network"]["egress_rules"])

    def test_import_accepts_legacy_payload_and_rejects_unknown_export_version(self):
        created = self.create()
        exported = self.client.get(f"/api/scenarios/{created['id']}/export").json()
        legacy = exported["scenario"]
        self.assertEqual(self.client.post("/api/scenarios/import", json=legacy).status_code, 201)
        exported["schema_version"] = 99
        response = self.client.post("/api/scenarios/import", json=exported)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Unsupported Anvil scenario schema version", response.text)

    def test_zones_survive_clone_export_and_legacy_client_update(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"}
        ]
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"][0]["hostname"] = "main-plc"
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        cloned = self.client.post(f"/api/scenarios/{scenario_id}/clone", json={})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        self.assertEqual(cloned.json()["network"]["zones"], payload["network"]["zones"])
        exported = self.client.get(f"/api/scenarios/{scenario_id}/export").json()
        imported = self.client.post("/api/scenarios/import", json=exported)
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(imported.json()["network"]["zones"], payload["network"]["zones"])

        legacy = api_tests.ScenarioApiTests.payload()
        legacy["devices"][0]["ip"] = "172.30.10.10"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=legacy)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["network"]["zones"], payload["network"]["zones"])
        self.assertEqual(updated.json()["devices"][0]["zone_id"], "control")
        self.assertEqual(updated.json()["devices"][0]["hostname"], "main-plc")

    def test_explicit_deletion(self):
        created = self.create()
        payload = api_tests.ScenarioApiTests.payload()
        payload.update(field_objects=[], signal_connections=[])
        response = self.client.put(f'/api/scenarios/{created["id"]}', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["field_objects"], [])

    def test_deploy_includes_dedicated_field_runtime(self):
        created = self.create()
        response = self.client.post(f'/api/scenarios/{created["id"]}/deploy')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["containers"]), 2)
        self.assertEqual(response.json()["containers"][0]["node_id"], "__anvil_field__")
        self.assertEqual(self.client.get(f'/api/scenarios/{created["id"]}').json()["status"], "running")

    def test_live_tank_demand_control_is_forwarded_and_audited(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload.update({
            "field_objects": [{"object_id": "tank", "type": "tank", "name": "Tank"}],
            "signal_connections": [], "process_connections": [],
        })
        created = self.client.post("/api/scenarios", json=payload).json()
        scenario_id = created["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        with patch("routes.scenarios.field_operation", return_value={"objects": []}) as operation:
            response = self.client.post(f"/api/scenarios/{scenario_id}/field/tank/control", json={
                "demand_flow_lpm": 12, "leak_flow_lpm": 4,
            })
        self.assertEqual(response.status_code, 200, response.text)
        operation.assert_called_once_with(
            self.manager, scenario_id, "control", {
                "object_id": "tank", "demand_flow_lpm": 12.0, "leak_flow_lpm": 4.0,
            },
        )
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        event = next(item for item in events if item["event_type"] == "operator.field_control")
        self.assertEqual(event["details"], {
            "object_id": "tank", "demand_flow_lpm": 12.0, "leak_flow_lpm": 4.0,
        })

    def test_empty_plc_and_automatic_channels(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"][0]["config"]["components"] = []
        response = self.client.post('/api/scenarios', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.get(f'/api/scenarios/{response.json()["id"]}/io-map').json()['points'], [])

    def test_field_runtime_does_not_appear_in_catalog(self):
        catalog=self.client.get('/api/scenarios/catalog').json()
        self.assertNotIn('field-runtime', [item['type'] for item in catalog])

    def test_preview_keeps_original_and_addresses(self):
        created = self.client.post("/api/scenarios", json=api_tests.ScenarioApiTests.payload()).json()
        scenario_id = created["id"]
        url = f"/api/scenarios/{scenario_id}"
        response = self.client.get(url + "/field-migration-preview")
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertFalse(preview["applied"])
        self.assertIn("POST /api/scenarios/{id}/field-migration-copy", preview["warnings"][1])
        self.assertNotIn("pending", preview["warnings"][1].lower())
        self.assertEqual(len(preview["field_objects"]), 2)
        self.assertEqual(preview["signal_connections"][0]["source"]["address"], "DB1.DBX0.0")
        self.assertEqual(preview["signal_connections"][1]["target"]["address"], "DB1.DBD2")
        self.assertEqual(self.client.get(url).json(), created)
        self.assertEqual(self.client.get(url + "/field-migration-preview").json(), preview)

    def test_migration_creates_independent_copy_and_preserves_s7_addresses_and_logic(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"][0]["config"]["control_rules"] = [{
            "name": "control presión", "input_component": "presion",
            "output_component": "entrada", "mode": "below", "setpoint": 5, "hysteresis": 1,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        source = created.json()
        source_id = source["id"]
        source_points = self.client.get(f"/api/scenarios/{source_id}/io-map").json()["points"]

        migrated = self.client.post(f"/api/scenarios/{source_id}/field-migration-copy", json={})
        self.assertEqual(migrated.status_code, 201, migrated.text)
        copy = migrated.json()
        self.assertNotEqual(copy["id"], source_id)
        self.assertEqual(copy["status"], "stopped")
        self.assertEqual(copy["devices"][0]["config"]["control_rules"], payload["devices"][0]["config"]["control_rules"])
        self.assertEqual(
            [component["type"] for component in copy["devices"][0]["config"]["components"]],
            ["io_channel", "io_channel"],
        )
        copy_points = self.client.get(f"/api/scenarios/{copy['id']}/io-map").json()["points"]
        self.assertEqual(
            [(item["component_name"], item["address"], item["data_type"], item["access"])
             for item in copy_points],
            [(item["component_name"], item["address"], item["data_type"], item["access"])
             for item in source_points],
        )
        self.assertEqual({item["type"] for item in copy["field_objects"]}, {"valve", "analog_sensor"})
        self.assertEqual(len(copy["signal_connections"]), 2)
        self.assertEqual(self.client.get(f"/api/scenarios/{source_id}").json(), source)

    def test_migration_preserves_both_pump_addresses_and_flow_parameters(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"][0]["config"]["components"] = [{
            "type": "pump", "name": "Bomba", "flow_min": 12, "flow_max": 80,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        source_id = created.json()["id"]
        source_points = self.client.get(f"/api/scenarios/{source_id}/io-map").json()["points"]
        migrated = self.client.post(f"/api/scenarios/{source_id}/field-migration-copy", json={})
        self.assertEqual(migrated.status_code, 201, migrated.text)
        copy = migrated.json()
        channels = copy["devices"][0]["config"]["components"]
        self.assertEqual([(item["name"], item["direction"], item["data_type"])
                          for item in channels],
                         [("Bomba", "output", "BOOL"), ("Bomba", "input", "REAL")])
        copy_points = self.client.get(f"/api/scenarios/{copy['id']}/io-map").json()["points"]
        self.assertEqual([item["address"] for item in copy_points], [item["address"] for item in source_points])
        pump = next(item for item in copy["field_objects"] if item["type"] == "pump")
        self.assertEqual((pump["flow_min"], pump["flow_max"]), (12, 80))

    def test_schema_migration_is_additive_and_idempotent(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE scenario_audit_events")
            connection.exec_driver_sql("ALTER TABLE scenarios DROP COLUMN field_objects_json")
            connection.exec_driver_sql("ALTER TABLE scenarios DROP COLUMN signal_connections_json")
            connection.exec_driver_sql("ALTER TABLE scenarios DROP COLUMN process_connections_json")
            connection.exec_driver_sql("ALTER TABLE scenarios DROP COLUMN network_connections_json")
            connection.exec_driver_sql("INSERT INTO scenarios (id,name,network_json,devices_json,runtime_devices_json,status,created_at) VALUES ('legacy','Legacy','{}','[]','[]','stopped','2026-01-01')")
        create_db_and_tables(self.engine)
        create_db_and_tables(self.engine)
        with Session(self.engine) as session:
            record = session.get(ScenarioRecord, "legacy")
            self.assertEqual(record.name, "Legacy")
            self.assertEqual(json.loads(record.field_objects_json), [])
        self.assertEqual(json.loads(record.signal_connections_json), [])
        self.assertEqual(json.loads(record.process_connections_json), [])
        self.assertEqual(json.loads(record.network_connections_json), [])
        self.assertIn("scenario_audit_events", inspect(self.engine).get_table_names())

    def test_pump_preview_preserves_flow_parameters_and_both_addresses(self):
        payload = api_tests.ScenarioApiTests.payload()
        payload["devices"][0]["config"]["components"] = [{"type": "pump", "name": "Bomba", "flow_min": 40, "flow_max": 80}]
        created = self.client.post("/api/scenarios", json=payload).json()
        preview = self.client.get(f'/api/scenarios/{created["id"]}/field-migration-preview').json()
        self.assertEqual(preview["field_objects"][0]["flow_min"], 40)
        self.assertEqual(preview["signal_connections"][1]["source"]["port"], "flow")
        self.assertEqual(preview["signal_connections"][1]["target"]["address"], "DB1.DBD2")
        payload["field_objects"] = preview["field_objects"]
        payload["signal_connections"] = preview["signal_connections"]
        ScenarioCreate.model_validate(payload)

    def test_preview_recovers_positions_and_does_not_extract_connected_points_twice(self):
        payload = api_tests.ScenarioApiTests.payload()
        key = json.dumps(["plc-1", "valve", "entrada", 0], separators=(",", ":"))
        payload["canvas"] = {"field_positions": {key: {"x": 123, "y": 456}}}
        created = self.client.post("/api/scenarios", json=payload).json()
        url = f'/api/scenarios/{created["id"]}'
        preview = self.client.get(url + "/field-migration-preview").json()
        self.assertEqual(preview["field_objects"][0]["position"], {"x": 123, "y": 456})
        payload["field_objects"] = preview["field_objects"]
        payload["signal_connections"] = preview["signal_connections"]
        self.assertEqual(self.client.put(url, json=payload).status_code, 200)
        second = self.client.get(url + "/field-migration-preview").json()
        self.assertEqual(len({item["object_id"] for item in second["field_objects"]}), 2)
        self.assertEqual(len({item["connection_id"] for item in second["signal_connections"]}), 2)


if __name__ == "__main__":
    unittest.main()
