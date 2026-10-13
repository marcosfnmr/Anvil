from __future__ import annotations

import sys
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import run_simulation


class StopAfterCycle:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.stopped = True


class ServerCycleTests(unittest.TestCase):
    def test_scan_cycle_controls_component_dt_and_loop_interval(self):
        observed = []
        component = SimpleNamespace(
            config=SimpleNamespace(db=1),
            simulate=lambda _buffer, dt: observed.append(dt),
        )
        stop = StopAfterCycle()

        run_simulation([component], {1: bytearray(4)}, stop, scan_cycle_seconds=.25)

        self.assertEqual(observed, [.25])
        self.assertEqual(stop.waits, [.25])

    def test_component_simulates_against_configured_non_db_buffer(self):
        observed=[]
        component=SimpleNamespace(config=SimpleNamespace(db=1,area="M",byte_offset=0,bit_offset=0),
                                  simulate=lambda buffer, _dt: observed.append(buffer))
        marker=bytearray(4); stop=StopAfterCycle()
        run_simulation([component],{1:bytearray(4)},stop,io_buffers={"M":marker})
        self.assertEqual(observed,[marker])

    def test_control_logic_runs_once_after_component_inputs_each_scan(self):
        observed = []
        component = SimpleNamespace(config=SimpleNamespace(db=1), simulate=lambda _buffer, _dt: observed.append("input"))
        logic = SimpleNamespace(apply=lambda: observed.append("logic"))
        stop = StopAfterCycle()

        run_simulation([component], {1: bytearray(4)}, stop, scan_cycle_seconds=.1, logic=logic)

        self.assertEqual(observed, ["input", "logic"])

    def test_field_bridge_publishes_real_output_channel_value(self):
        observed = []
        buffer = bytearray(4)
        struct.pack_into(">f", buffer, 0, 37.5)
        field = SimpleNamespace(
            config={"bindings": [{"direction": "output", "data_type": "REAL", "db": 1,
                                  "byte_offset": 0, "connection_id": "setpoint"}]},
            exchange=lambda outputs: observed.append(outputs) or {},
        )
        stop = StopAfterCycle()

        run_simulation([], {1: buffer}, stop, field=field, scan_cycle_seconds=.1)

        self.assertEqual(observed, [{"setpoint": 37.5}])

    def test_field_bridge_updates_and_reads_non_db_memory_area(self):
        observed = []
        marker = bytearray(4)
        field = SimpleNamespace(
            config={"bindings": [{"direction": "input", "data_type": "BOOL", "area": "M", "db": 1,
                                  "byte_offset": 0, "bit_offset": 2, "connection_id": "limit"},
                                 {"direction": "output", "data_type": "BOOL", "area": "M", "db": 1,
                                  "byte_offset": 0, "bit_offset": 2, "connection_id": "echo"}]},
            exchange=lambda outputs: observed.append(outputs) or {"limit": True},
        )
        stop = StopAfterCycle()
        run_simulation([], {1: bytearray(4)}, stop, field=field, scan_cycle_seconds=.1, io_buffers={"M": marker})
        self.assertEqual(observed, [{"echo": False}])
        self.assertTrue(marker[0] & (1 << 2))


if __name__ == "__main__":
    unittest.main()
