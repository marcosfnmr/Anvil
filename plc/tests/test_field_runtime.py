import sys
import unittest
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from field_server import FIELD_RUNTIME_CONTRACT_VERSION, FieldEngine, create_handler, field_runtime_capabilities


class FieldEngineTests(unittest.TestCase):
    def test_legacy_runtime_configuration_without_version_defaults_to_v1(self):
        engine = FieldEngine({"objects": []})
        self.assertEqual(engine.snapshot()["objects"], [])

    def test_runtime_contract_is_explicit_and_rejects_unknown_versions(self):
        self.assertEqual(field_runtime_capabilities(), {
            "contract": "anvil-field-runtime",
            "contract_version": FIELD_RUNTIME_CONTRACT_VERSION,
            "endpoints": ["/snapshot", "/exchange", "/control"],
        })
        with self.assertRaisesRegex(ValueError, "unsupported field runtime contract version"):
            FieldEngine({"contract_version": FIELD_RUNTIME_CONTRACT_VERSION + 1, "objects": []})

    def test_capabilities_endpoint_reports_runtime_contract_over_http(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.engine))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/capabilities", timeout=2
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), field_runtime_capabilities())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def setUp(self):
        self.now=0
        self.config={"objects":[{"object_id":"v","type":"valve","response_seconds":2}, {"object_id":"s","type":"analog_sensor","min_value":0,"max_value":100}],"connections":[
            {"connection_id":"cmd","source":{"kind":"device","node_id":"plc","address":"DB1.DBX0.0"},"target":{"kind":"field","object_id":"v","port":"command"}},
            {"connection_id":"feedback","source":{"kind":"field","object_id":"v","port":"state"},"target":{"kind":"device","node_id":"plc","address":"DB1.DBX1.0"}},
            {"connection_id":"sensor","source":{"kind":"field","object_id":"s","port":"value"},"target":{"kind":"device","node_id":"plc","address":"DB1.DBD2"}},
        ]}
        self.engine=FieldEngine(self.config,clock=lambda:self.now)

    def test_order_is_not_feedback_and_response_is_delayed(self):
        values=self.engine.exchange({'node_id':'plc','outputs':{'cmd':True}})
        self.assertFalse(values['inputs']['feedback'])
        self.assertTrue(self.engine.states['v']['command'])
        self.now=2
        self.assertTrue(self.engine.exchange({'node_id':'plc','outputs':{'cmd':True}})['inputs']['feedback'])

    def test_fault_prevents_motion_and_restore_recovers(self):
        self.engine.control({'object_id':'v','fault':True})
        self.engine.exchange({'node_id':'plc','outputs':{'cmd':True}})
        self.now=3
        self.assertFalse(self.engine.snapshot()['objects'][0]['state'])
        self.engine.control({'object_id':'v','fault':False})
        self.assertTrue(self.engine.states['v']['state'])

    def test_sensor_override_and_release(self):
        self.assertEqual(self.engine.states['s']['value'],50)

    def test_seeded_sensor_noise_repeats_and_uses_independent_sensor_streams(self):
        def build(seed):
            now = [0.0]
            config = {"objects": [
                {"object_id": "pressure-a", "type": "analog_sensor", "min_value": 0,
                 "max_value": 100, "simulation": {"mode": "constant", "noise": 0.2, "seed": seed}},
                {"object_id": "pressure-b", "type": "analog_sensor", "min_value": 0,
                 "max_value": 100, "simulation": {"mode": "constant", "noise": 0.2, "seed": seed}},
            ], "connections": []}
            return FieldEngine(config, clock=lambda: now[0]), now

        first, first_clock = build(1234)
        second, second_clock = build(1234)
        first_values, second_values = [], []
        for _ in range(4):
            first_values.append(tuple(first.states[oid]["value"] for oid in ("pressure-a", "pressure-b")))
            second_values.append(tuple(second.states[oid]["value"] for oid in ("pressure-a", "pressure-b")))
            first_clock[0] += 1
            second_clock[0] += 1
            first.step()
            second.step()

        self.assertEqual(first_values, second_values)
        self.assertTrue(all(values[0] != values[1] for values in first_values))
        other_seed, _ = build(4321)
        self.assertNotEqual(first_values[0][0], other_seed.states["pressure-a"]["value"])

    def test_sensor_fault_freezes_last_process_reading_and_marks_link(self):
        config = {"objects": [
            {"object_id": "tank", "type": "tank", "capacity_liters": 100,
             "initial_level_liters": 50, "height_m": 2, "outlet_flow_lpm": 0},
            {"object_id": "sensor", "type": "analog_sensor", "unit": "%",
             "min_value": 0, "max_value": 100, "simulation": {"mode": "process"}},
        ], "connections": [{"connection_id": "level", "source": {"kind": "field",
            "object_id": "sensor", "port": "value"}, "target": {"kind": "device",
            "node_id": "plc", "address": "DB1.DBD0"}}], "process_connections": [{
            "connection_id": "measure", "source": {"object_id": "tank", "port": "level"},
            "target": {"object_id": "sensor", "port": "measurement"}}]}
        engine = FieldEngine(config, clock=lambda: self.now)
        original = engine.states["sensor"]["value"]
        engine.control({"object_id": "tank", "demand_flow_lpm": 60})
        self.now = 10
        engine.step()
        changed = engine.states["sensor"]["value"]
        self.assertNotEqual(changed, original)
        engine.control({"object_id": "sensor", "fault": True})
        self.now = 20
        engine.step()
        self.assertEqual(engine.states["sensor"]["value"], changed)
        self.assertTrue(engine.states["sensor"]["fault"])
        self.assertEqual(engine.snapshot()["connections"][0]["quality"], "fault")
        engine.control({"object_id": "sensor", "fault": False})
        self.now = 21
        engine.step()
        self.assertNotEqual(engine.states["sensor"]["value"], changed)
        self.engine.control({'object_id':'s','override':72})
        self.assertEqual(self.engine.exchange({'node_id':'plc','outputs':{}})['inputs']['sensor'],72)
        self.engine.control({'object_id':'s','override':None})
        self.assertEqual(self.engine.states['s']['value'],50)

    def test_invalid_controls_do_not_change_values(self):
        for value in (101, -1, True, float('inf')):
            with self.assertRaises(ValueError): self.engine.control({'object_id':'s','override':value})
        with self.assertRaises(ValueError): self.engine.control({'object_id':'v','override':True})
        with self.assertRaisesRegex(ValueError, 'leak_flow_lpm requires a tank'):
            self.engine.control({'object_id':'s','override':72,'leak_flow_lpm':1})
        self.assertIsNone(self.engine.states['s']['override'])
        with self.assertRaises(ValueError): self.engine.exchange({'node_id':'plc','outputs':{'cmd':1}})
        self.assertFalse(self.engine.states['v']['command'])

    def test_explicit_modbus_conversion_clips_and_encodes_uint16(self):
        self.config['connections'][2]['conversion']={'input_min':0,'input_max':100,'output_min':0,'output_max':65535}
        values=self.engine.exchange({'node_id':'plc','outputs':{}})['inputs']
        self.assertEqual(values['sensor'],32768)
        self.assertIs(type(values['sensor']),int)

    def test_disconnection_marks_link_stale_and_recovery_marks_good(self):
        self.assertEqual(self.engine.snapshot()['connections'][0]['quality'],'waiting')
        self.engine.exchange({'node_id':'plc','outputs':{}})
        self.now=3
        self.assertEqual(self.engine.snapshot()['connections'][0]['quality'],'stale')
        self.engine.exchange({'node_id':'plc','outputs':{}})
        self.assertEqual(self.engine.snapshot()['connections'][0]['quality'],'good')

    def test_other_node_cannot_drive_commands(self):
        self.engine.exchange({'node_id':'other','outputs':{'cmd':True}})
        self.assertFalse(self.engine.states['v']['command'])

    def test_pump_valve_tank_and_process_sensor_form_a_deterministic_loop(self):
        config={
            'objects':[
                {'object_id':'pump','type':'pump','initial_state':False,'response_seconds':0,'flow_min':0,'flow_max':60,'unit':'L/min'},
                {'object_id':'tank','type':'tank','capacity_liters':60,'initial_level_liters':30,'outlet_flow_lpm':30},
                {'object_id':'valve','type':'valve','initial_state':False,'response_seconds':0,'flow_rate_lpm':60},
                {'object_id':'level','type':'analog_sensor','min_value':0,'max_value':10,'unit':'%','simulation':{'mode':'process'}},
            ],
            'connections':[
                {'connection_id':'pump-command','source':{'kind':'device','node_id':'plc','address':'DB1.DBX0.0'},'target':{'kind':'field','object_id':'pump','port':'command'}},
                {'connection_id':'valve-command','source':{'kind':'device','node_id':'plc','address':'DB1.DBX1.0'},'target':{'kind':'field','object_id':'valve','port':'command'}},
            ],
            'process_connections':[
                {'connection_id':'pump-to-tank','source':{'object_id':'pump','port':'flow'},'target':{'object_id':'tank','port':'inlet'}},
                {'connection_id':'tank-to-valve','source':{'object_id':'tank','port':'outlet'},'target':{'object_id':'valve','port':'process_in'}},
                {'connection_id':'tank-level','source':{'object_id':'tank','port':'level'},'target':{'object_id':'level','port':'measurement'}},
            ],
        }
        engine=FieldEngine(config,clock=lambda:self.now)
        self.assertEqual(engine.states['level']['value'],5)
        engine.exchange({'node_id':'plc','outputs':{'pump-command':True,'valve-command':True}})
        self.now=60
        engine.snapshot()
        self.assertEqual(engine.states['tank']['volume_liters'],30)
        engine.exchange({'node_id':'plc','outputs':{'pump-command':True,'valve-command':False}})
        self.now=120
        snapshot=engine.snapshot()
        self.assertEqual(snapshot['objects'][1]['volume_liters'],60)
        self.assertEqual(snapshot['objects'][3]['value'],10)
        self.assertEqual(snapshot['process_connections'][0]['quality'],'good')

    def test_pipe_limits_flow_between_pump_and_tank(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 80, 'unit': 'L/min'},
                {'object_id': 'pipe', 'type': 'pipe', 'max_flow_lpm': 25},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-pipe', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'pipe', 'port': 'process_in'}},
                {'connection_id': 'pipe-tank', 'source': {'object_id': 'pipe', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        self.now = 60
        snapshot = engine.snapshot()
        objects = {item['object_id']: item for item in snapshot['objects']}
        self.assertEqual(objects['pump']['flow'], 40)
        self.assertEqual(objects['pipe']['process_in'], 40)
        self.assertEqual(objects['pipe']['flow'], 25)
        self.assertEqual(objects['tank']['inflow_lpm'], 25)
        self.assertEqual(objects['tank']['volume_liters'], 25)

    def test_closed_valve_limits_upstream_pump_flow_to_zero(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 80},
                {'object_id': 'valve', 'type': 'valve', 'initial_state': False,
                 'response_seconds': 0, 'flow_rate_lpm': 30},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-valve', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'valve', 'port': 'process_in'}},
                {'connection_id': 'valve-tank', 'source': {'object_id': 'valve', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertEqual(objects['pump']['flow'], 0)
        self.assertEqual(objects['valve']['process_in'], 0)
        self.assertEqual(objects['valve']['flow'], 0)
        self.assertEqual(objects['tank']['inflow_lpm'], 0)

    def test_proportional_valve_capacity_scales_upstream_flow_and_is_conserved(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 60},
                {'object_id': 'valve', 'type': 'proportional_valve',
                 'initial_opening_pct': 40, 'response_seconds': 0, 'flow_rate_lpm': 60},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-valve', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'valve', 'port': 'process_in'}},
                {'connection_id': 'valve-tank', 'source': {'object_id': 'valve', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['pump']['flow'], 24)
        self.assertAlmostEqual(objects['valve']['process_in'], 24)
        self.assertAlmostEqual(objects['valve']['flow'], 24)
        self.assertAlmostEqual(objects['tank']['inflow_lpm'], 24)

    def test_parallel_closed_valve_branch_does_not_consume_pump_flow(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 100},
                {'object_id': 'open-valve', 'type': 'valve', 'initial_state': True,
                 'response_seconds': 0, 'flow_rate_lpm': 50},
                {'object_id': 'closed-valve', 'type': 'valve', 'initial_state': False,
                 'response_seconds': 0, 'flow_rate_lpm': 50},
                {'object_id': 'tank-a', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
                {'object_id': 'tank-b', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-open', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'open-valve', 'port': 'process_in'}},
                {'connection_id': 'pump-closed', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'closed-valve', 'port': 'process_in'}},
                {'connection_id': 'open-tank', 'source': {'object_id': 'open-valve', 'port': 'process_out'},
                 'target': {'object_id': 'tank-a', 'port': 'inlet'}},
                {'connection_id': 'closed-tank', 'source': {'object_id': 'closed-valve', 'port': 'process_out'},
                 'target': {'object_id': 'tank-b', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['pump']['flow'], 50)
        self.assertAlmostEqual(objects['open-valve']['flow'], 50)
        self.assertEqual(objects['closed-valve']['process_in'], 0)
        self.assertEqual(objects['tank-a']['inflow_lpm'], 50)
        self.assertEqual(objects['tank-b']['inflow_lpm'], 0)

    def test_pipe_pressure_drop_tracks_square_of_flow_and_reaches_pressure_sensor(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 80,
                 'max_head_kpa': 100, 'unit': 'L/min'},
                {'object_id': 'pipe', 'type': 'pipe', 'max_flow_lpm': 40,
                 'pressure_drop_kpa': 16},
                {'object_id': 'pressure', 'type': 'analog_sensor', 'min_value': 0,
                 'max_value': 100, 'unit': 'kPa', 'simulation': {'mode': 'process'}},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-pipe', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'pipe', 'port': 'process_in'}},
                {'connection_id': 'pipe-sensor', 'source': {'object_id': 'pipe', 'port': 'pressure'},
                 'target': {'object_id': 'pressure', 'port': 'measurement'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertEqual(objects['pipe']['flow'], 40)
        self.assertEqual(objects['pipe']['pressure_in_kpa'], 100)
        self.assertEqual(objects['pipe']['pressure_drop_kpa'], 16)
        self.assertEqual(objects['pipe']['pressure_kpa'], 84)
        self.assertEqual(objects['pressure']['value'], 84)

    def test_restrictive_pipe_pressure_loss_reduces_source_and_tank_flow(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 80,
                 'max_head_kpa': 100, 'unit': 'L/min'},
                {'object_id': 'pipe', 'type': 'pipe', 'max_flow_lpm': 40,
                 'pressure_drop_kpa': 400},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-pipe', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'pipe', 'port': 'process_in'}},
                {'connection_id': 'pipe-tank', 'source': {'object_id': 'pipe', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['pump']['flow'], 20)
        self.assertAlmostEqual(objects['pipe']['flow'], 20)
        self.assertAlmostEqual(objects['pipe']['pressure_drop_kpa'], 100)
        self.assertEqual(objects['pipe']['pressure_kpa'], 0)
        self.assertEqual(objects['tank']['inflow_lpm'], 20)

    def test_pump_splits_flow_conservatively_across_parallel_pipe_branches(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 100,
                 'max_head_kpa': 100, 'unit': 'L/min'},
                {'object_id': 'pipe-a', 'type': 'pipe', 'max_flow_lpm': 60,
                 'pressure_drop_kpa': 100},
                {'object_id': 'pipe-b', 'type': 'pipe', 'max_flow_lpm': 60,
                 'pressure_drop_kpa': 400},
                {'object_id': 'tank-a', 'type': 'tank', 'capacity_liters': 1000,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
                {'object_id': 'tank-b', 'type': 'tank', 'capacity_liters': 1000,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-a', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'pipe-a', 'port': 'process_in'}},
                {'connection_id': 'pump-b', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'pipe-b', 'port': 'process_in'}},
                {'connection_id': 'a-tank', 'source': {'object_id': 'pipe-a', 'port': 'process_out'},
                 'target': {'object_id': 'tank-a', 'port': 'inlet'}},
                {'connection_id': 'b-tank', 'source': {'object_id': 'pipe-b', 'port': 'process_out'},
                 'target': {'object_id': 'tank-b', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['pipe-a']['flow'] + objects['pipe-b']['flow'], 50)
        self.assertAlmostEqual(objects['pump']['flow'], 50)
        self.assertAlmostEqual(objects['pipe-a']['flow'], 100 / 3)
        self.assertAlmostEqual(objects['pipe-b']['flow'], 50 / 3)
        self.assertAlmostEqual(objects['tank-a']['inflow_lpm'] + objects['tank-b']['inflow_lpm'], 50)
        self.assertAlmostEqual(objects['pipe-a']['pressure_drop_kpa'], objects['pipe-b']['pressure_drop_kpa'])

    def test_process_splitter_reroutes_capacity_limited_flow_and_mixer_conserves_it(self):
        config = {
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 100, 'unit': 'L/min'},
                {'object_id': 'splitter', 'type': 'flow_splitter', 'split_ratio_pct': 30},
                {'object_id': 'pipe-a', 'type': 'pipe', 'max_flow_lpm': 10},
                {'object_id': 'pipe-b', 'type': 'pipe', 'max_flow_lpm': 100},
                {'object_id': 'mixer', 'type': 'flow_mixer'},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 1000,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'pump-splitter', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'splitter', 'port': 'process_in'}},
                {'connection_id': 'split-a', 'source': {'object_id': 'splitter', 'port': 'process_out_a'},
                 'target': {'object_id': 'pipe-a', 'port': 'process_in'}},
                {'connection_id': 'split-b', 'source': {'object_id': 'splitter', 'port': 'process_out_b'},
                 'target': {'object_id': 'pipe-b', 'port': 'process_in'}},
                {'connection_id': 'a-mix', 'source': {'object_id': 'pipe-a', 'port': 'process_out'},
                 'target': {'object_id': 'mixer', 'port': 'process_in_a'}},
                {'connection_id': 'b-mix', 'source': {'object_id': 'pipe-b', 'port': 'process_out'},
                 'target': {'object_id': 'mixer', 'port': 'process_in_b'}},
                {'connection_id': 'mix-tank', 'source': {'object_id': 'mixer', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }
        engine = FieldEngine(config, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['pump']['flow'], 50)
        self.assertAlmostEqual(objects['splitter']['process_out_a'], 10)
        self.assertAlmostEqual(objects['splitter']['process_out_b'], 40)
        self.assertAlmostEqual(objects['pipe-a']['flow'], 10)
        self.assertAlmostEqual(objects['mixer']['flow'], 50)
        self.assertAlmostEqual(objects['tank']['inflow_lpm'], 50)

        restricted = {**config, 'objects': [dict(item) for item in config['objects']]}
        restricted['objects'][3]['max_flow_lpm'] = 20
        limited_engine = FieldEngine(restricted, clock=lambda: self.now)
        limited = {item['object_id']: item for item in limited_engine.snapshot()['objects']}
        self.assertAlmostEqual(limited['pump']['flow'], 30)
        self.assertAlmostEqual(limited['splitter']['process_out_a'], 10)
        self.assertAlmostEqual(limited['splitter']['process_out_b'], 20)
        self.assertAlmostEqual(limited['mixer']['flow'], 30)
        self.assertAlmostEqual(limited['tank']['inflow_lpm'], 30)

    def test_nested_splitters_conserve_flow_with_unordered_connections(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'initial_state': True,
                 'response_seconds': 0, 'flow_min': 0, 'flow_max': 100,
                 'max_head_kpa': 100, 'unit': 'L/min'},
                {'object_id': 'root', 'type': 'flow_splitter', 'split_ratio_pct': 40},
                {'object_id': 'branch', 'type': 'flow_splitter', 'split_ratio_pct': 50},
                {'object_id': 'pipe-a', 'type': 'pipe', 'max_flow_lpm': 8},
                {'object_id': 'pipe-b', 'type': 'pipe', 'max_flow_lpm': 10},
                {'object_id': 'pipe-c', 'type': 'pipe', 'max_flow_lpm': 100},
                {'object_id': 'branch-mixer', 'type': 'flow_mixer'},
                {'object_id': 'main-mixer', 'type': 'flow_mixer'},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 1000,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [],
            # Sinks and downstream edges deliberately precede their sources.
            'process_connections': [
                {'connection_id': 'main-tank', 'source': {'object_id': 'main-mixer', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
                {'connection_id': 'pipe-c-mix', 'source': {'object_id': 'pipe-c', 'port': 'process_out'},
                 'target': {'object_id': 'branch-mixer', 'port': 'process_in_b'}},
                {'connection_id': 'branch-main', 'source': {'object_id': 'branch-mixer', 'port': 'process_out'},
                 'target': {'object_id': 'main-mixer', 'port': 'process_in_b'}},
                {'connection_id': 'pipe-a-main', 'source': {'object_id': 'pipe-a', 'port': 'process_out'},
                 'target': {'object_id': 'main-mixer', 'port': 'process_in_a'}},
                {'connection_id': 'pipe-b-mix', 'source': {'object_id': 'pipe-b', 'port': 'process_out'},
                 'target': {'object_id': 'branch-mixer', 'port': 'process_in_a'}},
                {'connection_id': 'branch-c', 'source': {'object_id': 'branch', 'port': 'process_out_b'},
                 'target': {'object_id': 'pipe-c', 'port': 'process_in'}},
                {'connection_id': 'branch-b', 'source': {'object_id': 'branch', 'port': 'process_out_a'},
                 'target': {'object_id': 'pipe-b', 'port': 'process_in'}},
                {'connection_id': 'root-branch', 'source': {'object_id': 'root', 'port': 'process_out_b'},
                 'target': {'object_id': 'branch', 'port': 'process_in'}},
                {'connection_id': 'root-a', 'source': {'object_id': 'root', 'port': 'process_out_a'},
                 'target': {'object_id': 'pipe-a', 'port': 'process_in'}},
                {'connection_id': 'pump-root', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'root', 'port': 'process_in'}},
            ],
        }, clock=lambda: self.now)
        objects = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertAlmostEqual(objects['root']['process_out_a'], 8)
        self.assertAlmostEqual(objects['root']['process_out_b'], 42)
        self.assertAlmostEqual(objects['branch']['process_out_a'], 10)
        self.assertAlmostEqual(objects['branch']['process_out_b'], 32)
        self.assertAlmostEqual(objects['branch-mixer']['flow'], 42)
        self.assertAlmostEqual(objects['main-mixer']['flow'], 50)
        self.assertAlmostEqual(objects['tank']['inflow_lpm'], 50)
        self.assertAlmostEqual(objects['pump']['flow'], 50)

    def test_runtime_detects_process_cycles_and_uses_compatibility_propagation(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                 'initial_level_liters': 100, 'outlet_flow_lpm': 10},
                {'object_id': 'valve', 'type': 'valve', 'initial_state': True,
                 'response_seconds': 0, 'flow_rate_lpm': 10},
            ],
            'connections': [],
            'process_connections': [
                {'connection_id': 'tank-valve', 'source': {'object_id': 'tank', 'port': 'outlet'},
                 'target': {'object_id': 'valve', 'port': 'process_in'}},
                {'connection_id': 'valve-tank', 'source': {'object_id': 'valve', 'port': 'process_out'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        self.assertFalse(engine._process_graph_acyclic)
        self.assertEqual(len(engine._ordered_process_connections), 2)
        self.assertAlmostEqual(engine.states['tank']['inflow_lpm'], 10)
        self.assertAlmostEqual(engine.states['tank']['outflow_lpm'], 10)
        self.assertAlmostEqual(engine.states['tank']['volume_liters'], 100)

    def test_variable_speed_drive_maps_real_setpoint_to_process_flow_and_fault(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'vfd', 'type': 'variable_speed_drive', 'initial_speed_pct': 0,
                 'response_seconds': 1, 'max_flow_lpm': 80},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 1000,
                 'initial_level_liters': 0, 'outlet_flow_lpm': 0},
            ],
            'connections': [
                {'connection_id': 'setpoint', 'source': {'kind': 'device', 'node_id': 'plc', 'address': 'DB1.DBD0'},
                 'target': {'kind': 'field', 'object_id': 'vfd', 'port': 'command'}},
            ],
            'process_connections': [
                {'connection_id': 'drive-to-tank', 'source': {'object_id': 'vfd', 'port': 'flow'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
            ],
        }, clock=lambda: self.now)
        engine.exchange({'node_id': 'plc', 'outputs': {'setpoint': 50.0}})
        self.now = 2
        state = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertEqual(state['vfd']['speed_pct'], 50.0)
        self.assertEqual(state['vfd']['flow'], 40.0)
        self.assertAlmostEqual(state['tank']['volume_liters'], 4 / 3)
        engine.control({'object_id': 'vfd', 'fault': True})
        self.now = 62
        state = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertEqual(state['vfd']['flow'], 0.0)
        self.assertAlmostEqual(state['tank']['volume_liters'], 4 / 3)
        engine.control({'object_id': 'vfd', 'fault': False})
        self.now = 122
        state = {item['object_id']: item for item in engine.snapshot()['objects']}
        self.assertEqual(state['vfd']['flow'], 40.0)
        self.assertAlmostEqual(state['tank']['volume_liters'], 4 / 3 + 40)

    def test_modbus_uint16_holding_register_scales_to_vfd_speed(self):
        engine = FieldEngine({
            'objects': [{'object_id': 'vfd', 'type': 'variable_speed_drive', 'max_flow_lpm': 80}],
            'connections': [{
                'connection_id': 'modbus-setpoint',
                'source': {'kind': 'device', 'node_id': 'modbus', 'address': 'holding_register:0'},
                'target': {'kind': 'field', 'object_id': 'vfd', 'port': 'command'},
                'conversion': {'input_min': 0, 'input_max': 65535, 'output_min': 0, 'output_max': 100},
            }],
        }, clock=lambda: self.now)
        engine.exchange({'node_id': 'modbus', 'outputs': {'modbus-setpoint': 32768}})
        self.now = 1
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'vfd')
        self.assertAlmostEqual(state['speed_pct'], 32768 * 100 / 65535)
        self.assertAlmostEqual(state['flow'], 80 * 32768 / 65535)

    def test_tank_leak_is_a_deterministic_process_disturbance(self):
        engine = FieldEngine({
            'objects': [{'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                         'initial_level_liters': 20, 'outlet_flow_lpm': 0, 'leak_flow_lpm': 6}],
            'connections': [], 'process_connections': [],
        }, clock=lambda: self.now)
        self.now = 30
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['leak_lpm'], 6)
        self.assertEqual(state['outflow_lpm'], 6)
        self.assertEqual(state['volume_liters'], 17)
        self.now = 300
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['volume_liters'], 0)
        self.assertEqual(state['value'], 0)
        self.now = 301
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['leak_lpm'], 0)

    def test_tank_demand_can_change_while_running_and_reduces_volume(self):
        engine = FieldEngine({
            'objects': [{'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                         'initial_level_liters': 20, 'outlet_flow_lpm': 0,
                         'leak_flow_lpm': 2, 'demand_flow_lpm': 4}],
            'connections': [], 'process_connections': [],
        }, clock=lambda: self.now)
        self.now = 30
        engine.snapshot()
        self.assertAlmostEqual(engine.states['tank']['volume_liters'], 17)
        engine.control({'object_id': 'tank', 'demand_flow_lpm': 10})
        self.assertEqual(engine.states['tank']['demand_lpm'], 10)
        self.assertEqual(engine.states['tank']['effective_demand_lpm'], 10)
        self.now = 60
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['outflow_lpm'], 12)
        self.assertAlmostEqual(state['volume_liters'], 11)
        for value in (-1, True, float('inf')):
            with self.assertRaises(ValueError):
                engine.control({'object_id': 'tank', 'demand_flow_lpm': value})

    def test_tank_leak_can_be_injected_and_restored_while_running(self):
        engine = FieldEngine({
            'objects': [{'object_id': 'tank', 'type': 'tank', 'capacity_liters': 100,
                         'initial_level_liters': 20, 'outlet_flow_lpm': 0},
                        {'object_id': 'valve', 'type': 'valve'}],
            'connections': [], 'process_connections': [],
        }, clock=lambda: self.now)
        engine.control({'object_id': 'tank', 'leak_flow_lpm': 12})
        self.assertEqual(engine.states['tank']['leak_rate_lpm'], 12)
        self.now = 30
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['leak_lpm'], 12)
        self.assertEqual(state['leak_rate_lpm'], 12)
        self.assertAlmostEqual(state['volume_liters'], 14)
        self.now = 1000
        engine.snapshot()
        self.now = 1001
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['leak_lpm'], 0)
        self.assertEqual(state['leak_rate_lpm'], 12)
        self.assertEqual(state['volume_liters'], 0)
        engine.control({'object_id': 'tank', 'leak_flow_lpm': 0})
        self.assertEqual(engine.states['tank']['leak_rate_lpm'], 0)
        self.now = 1030
        state = next(item for item in engine.snapshot()['objects'] if item['object_id'] == 'tank')
        self.assertEqual(state['leak_lpm'], 0)
        self.assertEqual(state['leak_rate_lpm'], 0)
        self.assertEqual(state['volume_liters'], 0)
        for value in (-1, True, float('inf')):
            with self.assertRaises(ValueError):
                engine.control({'object_id': 'tank', 'leak_flow_lpm': value})
        with self.assertRaises(ValueError):
            engine.control({'object_id': 'valve', 'leak_flow_lpm': 1})

    def test_hydrostatic_pressure_tracks_tank_level_and_drives_pressure_sensor(self):
        engine = FieldEngine({
            'objects': [
                {'object_id': 'pump', 'type': 'pump', 'response_seconds': 0,
                 'flow_min': 0, 'flow_max': 60},
                {'object_id': 'tank', 'type': 'tank', 'capacity_liters': 1000,
                 'height_m': 2, 'initial_level_liters': 500, 'outlet_flow_lpm': 0},
                {'object_id': 'pressure', 'type': 'analog_sensor', 'min_value': 0,
                 'max_value': 30, 'unit': 'kPa', 'simulation': {'mode': 'process'}},
            ],
            'connections': [{
                'connection_id': 'pump-command',
                'source': {'kind': 'device', 'node_id': 'plc', 'address': 'DB1.DBX0.0'},
                'target': {'kind': 'field', 'object_id': 'pump', 'port': 'command'},
            }],
            'process_connections': [
                {'connection_id': 'pump-fill', 'source': {'object_id': 'pump', 'port': 'flow'},
                 'target': {'object_id': 'tank', 'port': 'inlet'}},
                {'connection_id': 'pressure-reading', 'source': {'object_id': 'tank', 'port': 'pressure'},
                 'target': {'object_id': 'pressure', 'port': 'measurement'}},
            ],
        }, clock=lambda: self.now)
        self.assertAlmostEqual(engine.states['tank']['pressure_kpa'], 9.80665)
        self.assertAlmostEqual(engine.states['pressure']['value'], 9.80665)
        engine.exchange({'node_id': 'plc', 'outputs': {'pump-command': True}})
        self.now = 60
        snapshot = engine.snapshot()
        tank = next(item for item in snapshot['objects'] if item['object_id'] == 'tank')
        sensor = next(item for item in snapshot['objects'] if item['object_id'] == 'pressure')
        expected = 9.80665 * 0.53 * 2
        self.assertAlmostEqual(tank['volume_liters'], 530)
        self.assertAlmostEqual(tank['pressure_kpa'], expected)
        self.assertAlmostEqual(sensor['value'], expected)

    def test_s7_real_command_drives_proportional_valve_and_changes_tank_level(self):
        config={
            'objects':[
                {'object_id':'pump','type':'pump','initial_state':False,'response_seconds':0,'flow_min':0,'flow_max':60,'unit':'L/min'},
                {'object_id':'valve','type':'proportional_valve','initial_opening_pct':0,'response_seconds':0,'flow_rate_lpm':60},
                {'object_id':'tank','type':'tank','capacity_liters':100,'initial_level_liters':0,'outlet_flow_lpm':0},
            ],
            'connections':[
                {'connection_id':'start','source':{'kind':'device','node_id':'plc','address':'DB1.DBX0.0'},'target':{'kind':'field','object_id':'pump','port':'command'}},
                {'connection_id':'setpoint','source':{'kind':'device','node_id':'plc','address':'DB1.DBD2'},'target':{'kind':'field','object_id':'valve','port':'command'}},
            ],
            'process_connections':[
                {'connection_id':'pump-to-valve','source':{'object_id':'pump','port':'flow'},'target':{'object_id':'valve','port':'process_in'}},
                {'connection_id':'valve-to-tank','source':{'object_id':'valve','port':'process_out'},'target':{'object_id':'tank','port':'inlet'}},
            ],
        }
        engine=FieldEngine(config,clock=lambda:self.now)
        engine.exchange({'node_id':'plc','outputs':{'start':True,'setpoint':40.0}})
        self.assertEqual(engine.states['valve']['opening_pct'],40.0)
        self.assertEqual(engine.states['valve']['flow'],24.0)
        self.now=60
        snapshot=engine.snapshot()
        self.assertAlmostEqual(snapshot['objects'][2]['volume_liters'],24.0)
        self.assertEqual(snapshot['objects'][2]['value'],24.0)
        with self.assertRaisesRegex(ValueError,'finite percentage'):
            engine.exchange({'node_id':'plc','outputs':{'setpoint':100.1}})
        self.assertEqual(engine.states['valve']['command'],40.0)

    def test_proportional_valve_response_delay_and_fault(self):
        engine=FieldEngine({'objects':[{'object_id':'v','type':'proportional_valve','initial_opening_pct':10,'response_seconds':2,'flow_rate_lpm':50}],'connections':[]},clock=lambda:self.now)
        engine.exchange({'node_id':'plc','outputs':{}})
        # Apply a test-only controller binding so the valve receives its REAL setpoint.
        engine.connections=[{'connection_id':'cmd','source':{'kind':'device','node_id':'plc','address':'DB1.DBD0'},'target':{'kind':'field','object_id':'v','port':'command'}}]
        engine.exchange({'node_id':'plc','outputs':{'cmd':75.0}})
        self.assertEqual(engine.states['v']['opening_pct'],10)
        self.now=2
        self.assertEqual(engine.snapshot()['objects'][0]['opening_pct'],75.0)
        engine.control({'object_id':'v','fault':True})
        engine.exchange({'node_id':'plc','outputs':{'cmd':25.0}})
        self.now=8
        self.assertEqual(engine.snapshot()['objects'][0]['opening_pct'],75.0)

    def test_modbus_uint16_setpoint_scales_to_real_valve_command(self):
        config={
            'objects':[{'object_id':'v','type':'proportional_valve','flow_rate_lpm':40}],
            'connections':[{'connection_id':'cmd','source':{'kind':'device','node_id':'modbus','address':'holding_register:0'},'target':{'kind':'field','object_id':'v','port':'command'},'conversion':{'input_min':0,'input_max':1000,'output_min':0,'output_max':100}}],
        }
        engine=FieldEngine(config,clock=lambda:self.now)
        engine.exchange({'node_id':'modbus','outputs':{'cmd':625}})
        self.assertEqual(engine.states['v']['command'],62.5)
        self.assertEqual(engine.states['v']['opening_pct'],62.5)
        with self.assertRaisesRegex(ValueError,'UINT16 value'):
            engine.exchange({'node_id':'modbus','outputs':{'cmd':True}})
        with self.assertRaisesRegex(ValueError,'UINT16 value'):
            engine.exchange({'node_id':'modbus','outputs':{'cmd':65536}})
        self.assertEqual(engine.states['v']['command'],62.5)


if __name__=='__main__':unittest.main()
