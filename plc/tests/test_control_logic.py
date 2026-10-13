from __future__ import annotations

import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_logic import ControlLogic
from config_schema import PLCConfig


class ControlLogicTests(unittest.TestCase):
    def test_exported_state_restores_latches_timers_counters_and_sequence(self):
        buffers = {1: bytearray(8)}
        latch = {"name": "sr", "set_db": 1, "set_byte_offset": 0, "set_bit_offset": 0,
                 "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                 "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0}
        counter = {"name": "ctu", "count_db": 1, "count_byte_offset": 0, "count_bit_offset": 0,
                   "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                   "output_db": 1, "output_byte_offset": 2, "output_bit_offset": 0, "preset": 3}
        sequence = {"name": "seq", "advance_db": 1, "advance_byte_offset": 0, "advance_bit_offset": 0,
                    "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                    "steps": [{"db": 1, "byte_offset": 3, "bit_offset": 0},
                              {"db": 1, "byte_offset": 3, "bit_offset": 1}]}
        timer = {"name": "ton", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                 "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 0, "preset_ms": 1000}
        clock = [10.0]
        original = ControlLogic([], buffers, [timer], latches=[latch], counters=[counter], sequences=[sequence], clock=lambda: clock[0])
        buffers[1][0] = 1
        original.apply()
        clock[0] += .4
        snapshot = original.export_state()
        buffers[1][1] = 0  # Component initialization on process start applies initial_state.
        restarted = ControlLogic([], buffers, [timer], latches=[latch], counters=[counter], sequences=[sequence], clock=lambda: 50.0, retained_state=snapshot)
        restarted.apply()
        self.assertTrue(buffers[1][1] & 1)
        self.assertEqual(restarted.counter_values["ctu"], 1)
        self.assertEqual(restarted.sequence_steps["seq"], 1)
        self.assertTrue(restarted.counter_previous["ctu"])
        self.assertAlmostEqual(restarted.timer_started["ton"], 49.6)

    def test_tof_holds_output_after_falling_edge_and_retains_elapsed_delay(self):
        buffers = {1: bytearray(4)}
        timer = {"name": "tof", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                 "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0,
                 "mode": "tof", "preset_ms": 1000}
        now = [0.0]
        logic = ControlLogic([], buffers, [timer], clock=lambda: now[0])
        buffers[1][0] = 1
        logic.apply()
        self.assertTrue(buffers[1][1] & 1)
        buffers[1][0] = 0
        now[0] = 2.0
        logic.apply()
        self.assertTrue(buffers[1][1] & 1)
        now[0] = 2.4
        snapshot = logic.export_state()
        restarted_now = [50.0]
        restarted = ControlLogic([], buffers, [timer], clock=lambda: restarted_now[0], retained_state=snapshot)
        restarted.apply()
        self.assertTrue(buffers[1][1] & 1)
        self.assertAlmostEqual(restarted.timer_started["tof"], 49.6)
        restarted_now[0] = 50.7
        restarted.apply()
        self.assertFalse(buffers[1][1] & 1)

    def test_tof_reassertion_cancels_off_delay(self):
        buffers = {1: bytearray(4)}
        timer = {"name": "tof", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                 "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0,
                 "mode": "tof", "preset_ms": 1000}
        now = [0.0]
        logic = ControlLogic([], buffers, [timer], clock=lambda: now[0])
        buffers[1][0] = 1
        logic.apply()
        buffers[1][0] = 0
        now[0] = 1.0
        logic.apply()
        now[0] = 1.5
        logic.apply()
        buffers[1][0] = 1
        logic.apply()
        buffers[1][0] = 0
        logic.apply()
        now[0] = 2.4
        logic.apply()
        self.assertTrue(buffers[1][1] & 1)
        now[0] = 3.1
        logic.apply()
        self.assertFalse(buffers[1][1] & 1)

    def test_state_restore_ignores_configuration_mismatch_and_invalid_values(self):
        buffers = {1: bytearray(4)}
        rule = {"name": "alarm", "input_db": 1, "input_byte_offset": 0,
                "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0,
                "mode": "above", "setpoint": 10.0, "hysteresis": 1.0}
        logic = ControlLogic([rule], buffers, retained_state={"version": 1, "fingerprint": "wrong", "state": {"states": {"alarm": True}}})
        self.assertEqual(logic.states, {})
        snapshot = logic.export_state()
        snapshot["state"]["states"] = {"alarm": "yes"}
        restored = ControlLogic([rule], buffers, retained_state=snapshot)
        self.assertEqual(restored.states, {})

    def test_stale_remote_input_can_force_controlled_output_off_and_recovers(self):
        buffers = {1: bytearray(8)}
        struct.pack_into(">f", buffers[1], 0, 30.0)
        unsafe = set()
        rule = {"name": "pressure alarm", "input_db": 1, "input_byte_offset": 0,
                "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 0,
                "mode": "above", "setpoint": 25.0, "hysteresis": 2.0}
        logic = ControlLogic([rule], buffers, safe_off_inputs_provider=lambda: unsafe)
        logic.apply()
        self.assertTrue(buffers[1][4] & 1)
        unsafe.add((1, 0, None))
        logic.apply()
        self.assertFalse(buffers[1][4] & 1)
        unsafe.clear()
        logic.apply()
        self.assertTrue(buffers[1][4] & 1)

    def test_stale_remote_bool_forces_sr_counter_sequence_and_timer_outputs_safe(self):
        buffers = {1: bytearray(8)}
        unsafe = set()
        latch = {"name": "sr", "set_db": 1, "set_byte_offset": 0, "set_bit_offset": 0,
                 "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                 "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0}
        counter = {"name": "ctu", "count_db": 1, "count_byte_offset": 0, "count_bit_offset": 0,
                   "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                   "output_db": 1, "output_byte_offset": 2, "output_bit_offset": 0, "preset": 1}
        sequence = {"name": "seq", "advance_db": 1, "advance_byte_offset": 0, "advance_bit_offset": 0,
                    "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                    "steps": [{"db": 1, "byte_offset": 3, "bit_offset": 0},
                              {"db": 1, "byte_offset": 3, "bit_offset": 1}]}
        timer = {"name": "ton", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                 "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 0, "preset_ms": 10}
        logic = ControlLogic([], buffers, timers=[timer], latches=[latch], counters=[counter],
                             sequences=[sequence], safe_off_inputs_provider=lambda: unsafe)
        buffers[1][0] = 1
        logic.apply()
        self.assertTrue(buffers[1][1] & 1)
        self.assertTrue(buffers[1][2] & 1)
        self.assertEqual(buffers[1][3] & 0b11, 0b10)
        unsafe.add((1, 0, 0))
        logic.apply()
        self.assertFalse(buffers[1][1] & 1)
        self.assertFalse(buffers[1][2] & 1)
        self.assertEqual(buffers[1][3] & 0b11, 0)
        self.assertFalse(buffers[1][4] & 1)
        self.assertEqual(logic.counter_values["ctu"], 0)

    def test_sequence_advances_on_edges_holds_terminal_step_and_reset_wins(self):
        buffers = {1: bytearray(3)}
        sequence = {"name": "steps", "advance_db": 1, "advance_byte_offset": 0, "advance_bit_offset": 0,
                    "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                    "steps": [{"db": 1, "byte_offset": 1, "bit_offset": 0},
                              {"db": 1, "byte_offset": 1, "bit_offset": 1},
                              {"db": 1, "byte_offset": 1, "bit_offset": 2}]}
        logic = ControlLogic([], buffers, sequences=[sequence])
        logic.apply()
        self.assertEqual(buffers[1][1] & 0b111, 0b001)
        buffers[1][0] = 1
        logic.apply()
        self.assertEqual(buffers[1][1] & 0b111, 0b010)
        logic.apply()  # held high does not advance again
        self.assertEqual(buffers[1][1] & 0b111, 0b010)
        buffers[1][0] = 0
        logic.apply()
        buffers[1][0] = 1
        logic.apply()
        self.assertEqual(buffers[1][1] & 0b111, 0b100)
        buffers[1][0] = 0
        logic.apply()
        buffers[1][0] = 1
        logic.apply()
        self.assertEqual(buffers[1][1] & 0b111, 0b100)  # terminal step saturates
        buffers[1][0] = 0b11
        logic.apply()
        self.assertEqual(buffers[1][1] & 0b111, 0b001)  # RESET priority

    def test_up_counter_counts_rising_edges_saturates_and_reset_dominates(self):
        buffers = {1: bytearray(2)}
        counter = {"name": "ctu", "count_db": 1, "count_byte_offset": 0, "count_bit_offset": 0,
                   "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                   "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0, "preset": 2}
        logic = ControlLogic([], buffers, counters=[counter])
        buffers[1][0] = 1
        logic.apply()
        self.assertEqual(logic.counter_values["ctu"], 1)
        self.assertFalse(buffers[1][1] & 1)
        logic.apply()  # held high is not another edge
        self.assertEqual(logic.counter_values["ctu"], 1)
        buffers[1][0] = 0
        logic.apply()
        buffers[1][0] = 1
        logic.apply()
        self.assertEqual(logic.counter_values["ctu"], 2)
        self.assertTrue(buffers[1][1] & 1)
        logic.apply()
        self.assertEqual(logic.counter_values["ctu"], 2)
        buffers[1][0] = 0b11  # RESET wins over a coincident count edge
        logic.apply()
        self.assertEqual(logic.counter_values["ctu"], 0)
        self.assertFalse(buffers[1][1] & 1)

    def test_boolean_blocks_evaluate_and_or_xor_on_each_scan(self):
        buffers = {1: bytearray(4)}
        rules = [
            {"name": "and", "input_a_db": 1, "input_a_byte_offset": 0, "input_a_bit_offset": 0,
             "input_b_db": 1, "input_b_byte_offset": 0, "input_b_bit_offset": 1,
             "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0, "operator": "and"},
            {"name": "or", "input_a_db": 1, "input_a_byte_offset": 0, "input_a_bit_offset": 0,
             "input_b_db": 1, "input_b_byte_offset": 0, "input_b_bit_offset": 1,
             "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 1, "operator": "or"},
            {"name": "xor", "input_a_db": 1, "input_a_byte_offset": 0, "input_a_bit_offset": 0,
             "input_b_db": 1, "input_b_byte_offset": 0, "input_b_bit_offset": 1,
             "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 2, "operator": "xor"},
        ]
        logic = ControlLogic([], buffers, boolean_rules=rules)
        for inputs, expected in ((0, 0), (1, 6), (2, 6), (3, 3)):
            with self.subTest(inputs=inputs):
                buffers[1][0] = inputs
                buffers[1][1] = 0b10000000
                logic.apply()
                self.assertEqual(buffers[1][1], 0b10000000 | expected)

    def test_boolean_rung_supports_normally_closed_contacts(self):
        buffers = {1: bytearray(2)}
        rule = {"name": "NC interlock", "input_a_db": 1, "input_a_byte_offset": 0,
                "input_a_bit_offset": 0, "input_b_db": 1, "input_b_byte_offset": 0,
                "input_b_bit_offset": 1, "output_db": 1, "output_byte_offset": 1,
                "output_bit_offset": 0, "operator": "and", "input_a_negated": False,
                "input_b_negated": True}
        logic = ControlLogic([], buffers, boolean_rules=[rule])
        for inputs, expected in ((0b00, False), (0b01, True), (0b10, False), (0b11, False)):
            with self.subTest(inputs=inputs):
                buffers[1][0] = inputs
                logic.apply()
                self.assertEqual(bool(buffers[1][1] & 1), expected)

    def test_sr_latch_sets_holds_and_reset_dominates(self):
        buffers = {1: bytearray(3)}
        latch = {"name": "motor memory", "set_db": 1, "set_byte_offset": 0, "set_bit_offset": 0,
                 "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                 "output_db": 1, "output_byte_offset": 1, "output_bit_offset": 0}
        logic = ControlLogic([], buffers, latches=[latch])
        for inputs, expected in ((0, False), (1, True), (0, True), (2, False), (3, False), (1, True)):
            with self.subTest(inputs=inputs):
                buffers[1][0] = inputs
                logic.apply()
                self.assertEqual(bool(buffers[1][1] & 1), expected)

    def test_boolean_block_config_requires_two_mapped_inputs_and_unique_output(self):
        config = {"plc_id": "plc", "rack": 0, "slot": 1, "db_size": 8,
                  "components": [
                      {"type": "digital_input", "name": "a", "db": 1, "byte_offset": 0, "bit_offset": 0},
                      {"type": "digital_input", "name": "b", "db": 1, "byte_offset": 0, "bit_offset": 1},
                      {"type": "valve", "name": "v", "db": 1, "byte_offset": 0, "bit_offset": 2},
                  ],
                  "boolean_logic": [{"name": "interlock", "input_a_db": 1, "input_a_byte_offset": 0,
                                     "input_a_bit_offset": 0, "input_b_db": 1, "input_b_byte_offset": 0,
                                     "input_b_bit_offset": 1, "output_db": 1, "output_byte_offset": 0,
                                     "output_bit_offset": 2, "operator": "and"}]}
        self.assertEqual(PLCConfig.model_validate(config).boolean_logic[0].operator, "and")
        nc_config = dict(config, boolean_logic=[dict(config["boolean_logic"][0], input_b_negated=True)])
        self.assertTrue(PLCConfig.model_validate(nc_config).boolean_logic[0].input_b_negated)
        latch_config = dict(config, boolean_logic=[], latches=[{"name": "sr", "set_db": 1, "set_byte_offset": 0,
                                                "set_bit_offset": 0, "reset_db": 1,
                                                "reset_byte_offset": 0, "reset_bit_offset": 1,
                                                "output_db": 1, "output_byte_offset": 0,
                                                "output_bit_offset": 2}])
        self.assertEqual(len(PLCConfig.model_validate(latch_config).latches), 1)
        invalid_latch = dict(latch_config, latches=[dict(latch_config["latches"][0], reset_bit_offset=0)])
        with self.assertRaisesRegex(ValueError, "distinct set and reset inputs"):
            PLCConfig.model_validate(invalid_latch)
        invalid = dict(config, boolean_logic=[dict(config["boolean_logic"][0], input_a_negated="false")])
        with self.assertRaises(ValueError):
            PLCConfig.model_validate(invalid)
        invalid = dict(config, boolean_logic=[dict(config["boolean_logic"][0], input_b_bit_offset=0)])
        with self.assertRaisesRegex(ValueError, "distinct input addresses"):
            PLCConfig.model_validate(invalid)
        invalid = dict(config, boolean_logic=[dict(config["boolean_logic"][0], input_a_byte_offset=8)])
        with self.assertRaisesRegex(ValueError, "exceeds db_size"):
            PLCConfig.model_validate(invalid)
        counter_config = {
            "plc_id": "plc", "rack": 0, "slot": 1, "db_size": 8,
            "components": [
                {"type": "digital_input", "name": "count", "db": 1, "byte_offset": 0, "bit_offset": 0},
                {"type": "digital_input", "name": "reset", "db": 1, "byte_offset": 0, "bit_offset": 1},
                {"type": "valve", "name": "done", "db": 1, "byte_offset": 0, "bit_offset": 2},
            ],
            "counters": [{"name": "ctu", "count_db": 1, "count_byte_offset": 0, "count_bit_offset": 0,
                          "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                          "output_db": 1, "output_byte_offset": 0, "output_bit_offset": 2, "preset": 4}],
        }
        self.assertEqual(PLCConfig.model_validate(counter_config).counters[0].preset, 4)
        for change in ({"preset": 0}, {"preset": 65536}, {"preset": True}, {"output_bit_offset": 1}):
            with self.subTest(counter_change=change), self.assertRaises(ValueError):
                PLCConfig.model_validate({**counter_config, "counters": [{**counter_config["counters"][0], **change}]})
        sequence_config = {
            "plc_id": "plc", "rack": 0, "slot": 1, "db_size": 8,
            "components": [
                {"type": "digital_input", "name": "advance", "db": 1, "byte_offset": 0, "bit_offset": 0},
                {"type": "digital_input", "name": "reset", "db": 1, "byte_offset": 0, "bit_offset": 1},
                {"type": "valve", "name": "step1", "db": 1, "byte_offset": 0, "bit_offset": 2},
                {"type": "valve", "name": "step2", "db": 1, "byte_offset": 0, "bit_offset": 3},
            ],
            "sequences": [{"name": "seq", "advance_db": 1, "advance_byte_offset": 0, "advance_bit_offset": 0,
                           "reset_db": 1, "reset_byte_offset": 0, "reset_bit_offset": 1,
                           "steps": [{"db": 1, "byte_offset": 0, "bit_offset": 2},
                                     {"db": 1, "byte_offset": 0, "bit_offset": 3}]}],
        }
        self.assertEqual(len(PLCConfig.model_validate(sequence_config).sequences[0].steps), 2)
        for changes in ({"steps": sequence_config["sequences"][0]["steps"][:1]},
                        {"steps": [*sequence_config["sequences"][0]["steps"], sequence_config["sequences"][0]["steps"][0]]},
                        {"advance_bit_offset": 1}):
            with self.subTest(sequence_change=changes), self.assertRaises(ValueError):
                PLCConfig.model_validate({**sequence_config, "sequences": [{**sequence_config["sequences"][0], **changes}]})

    def test_on_delay_timer_requires_continuous_input_and_resets(self):
        buffers = {1: bytearray(4)}
        clock = [10.0]
        timer = {"name": "delay", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                 "output_db": 1, "output_byte_offset": 0, "output_bit_offset": 1, "preset_ms": 1000}
        control = ControlLogic([], buffers, [timer], clock=lambda: clock[0])
        buffers[1][0] = 0b10000001
        control.apply()
        self.assertFalse(buffers[1][0] & 0b10)
        clock[0] += 0.6
        control.apply()
        self.assertFalse(buffers[1][0] & 0b10)
        clock[0] += 0.5
        control.apply()
        self.assertTrue(buffers[1][0] & 0b10)
        self.assertTrue(buffers[1][0] & 0b10000000)
        buffers[1][0] &= ~1
        control.apply()
        self.assertFalse(buffers[1][0] & 0b10)
        buffers[1][0] |= 1
        clock[0] += 0.5
        control.apply()
        self.assertFalse(buffers[1][0] & 0b10)

    def test_below_setpoint_pump_block_retains_state_inside_hysteresis_band(self):
        buffers = {1: bytearray(8)}
        buffers[1][4] = 0b10000000
        rule = {"name": "pump", "input_db": 1, "input_byte_offset": 0,
                "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 0,
                "mode": "below", "setpoint": 30.0, "hysteresis": 5.0}
        control = ControlLogic([rule], buffers)
        for value, expected in ((20, True), (32, True), (35, False), (32, False), (29, True)):
            struct.pack_into(">f", buffers[1], 0, value)
            control.apply()
            self.assertEqual(bool(buffers[1][4] & 1), expected)
            self.assertTrue(buffers[1][4] & 0b10000000)

    def test_above_setpoint_output_and_nonfinite_input(self):
        buffers = {1: bytearray(8)}
        rule = {"name": "cool", "input_db": 1, "input_byte_offset": 0,
                "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 2,
                "mode": "above", "setpoint": 70.0, "hysteresis": 4.0}
        control = ControlLogic([rule], buffers)
        struct.pack_into(">f", buffers[1], 0, 75.0)
        control.apply()
        self.assertTrue(buffers[1][4] & 4)
        struct.pack_into(">f", buffers[1], 0, math.nan)
        control.apply()
        self.assertTrue(buffers[1][4] & 4)

    def test_config_rejects_unregistered_db_and_colliding_output(self):
        base = {"plc_id": "plc", "rack": 0, "slot": 1, "db_size": 8,
                "components": [{"type": "analog_sensor", "name": "level", "db": 1,
                                "byte_offset": 0, "min_value": 0, "max_value": 100,
                                "simulation": {"mode": "constant"}},
                               {"type": "valve", "name": "v", "db": 1,
                                "byte_offset": 4, "bit_offset": 0}],
                "logic": [{"name": "rule", "input_db": 2, "input_byte_offset": 0,
                           "output_db": 1, "output_byte_offset": 4, "output_bit_offset": 0,
                           "mode": "below", "setpoint": 10, "hysteresis": 2}]}
        with self.assertRaisesRegex(ValueError, "unregistered DB"):
            PLCConfig.model_validate(base)

    def test_config_validates_on_delay_timer_io_and_preset(self):
        base = {"plc_id": "plc", "rack": 0, "slot": 1, "db_size": 8,
                "components": [
                    {"type": "digital_input", "name": "permit", "db": 1, "byte_offset": 0, "bit_offset": 0},
                    {"type": "valve", "name": "valve", "db": 1, "byte_offset": 0, "bit_offset": 1},
                ],
                "timers": [{"name": "ton", "input_db": 1, "input_byte_offset": 0, "input_bit_offset": 0,
                            "output_db": 1, "output_byte_offset": 0, "output_bit_offset": 1, "preset_ms": 750}]}
        self.assertEqual(PLCConfig.model_validate(base).timers[0].preset_ms, 750)
        tof_config = {**base, "timers": [{**base["timers"][0], "mode": "tof"}]}
        self.assertEqual(PLCConfig.model_validate(tof_config).timers[0].mode, "tof")
        invalid_mode = {**base, "timers": [{**base["timers"][0], "mode": "tp"}]}
        with self.assertRaises(ValueError):
            PLCConfig.model_validate(invalid_mode)
        for change in (
            {"preset_ms": True},
            {"preset_ms": 3_600_001},
            {"input_bit_offset": 2},
        ):
            config = {**base, "timers": [{**base["timers"][0], **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                PLCConfig.model_validate(config)


if __name__ == "__main__":
    unittest.main()
