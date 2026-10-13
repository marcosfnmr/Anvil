"""Deterministic scan-cycle function blocks for small PLC control programs."""

from __future__ import annotations

import math
import hashlib
import json
import struct
import time
from typing import Any


class ControlLogic:
    """Execute configured REAL-to-BOOL on/off blocks with retained hysteresis."""

    def __init__(
        self,
        rules: list[dict[str, Any]],
        db_buffers: dict[int, bytearray],
        timers: list[dict[str, Any]] | None = None,
        boolean_rules: list[dict[str, Any]] | None = None,
        latches: list[dict[str, Any]] | None = None,
        counters: list[dict[str, Any]] | None = None,
        sequences: list[dict[str, Any]] | None = None,
        clock=time.monotonic,
        safe_off_inputs_provider=None,
        retained_state: dict[str, Any] | None = None,
    ) -> None:
        self.rules = rules
        self.timers = timers or []
        self.boolean_rules = boolean_rules or []
        self.latches = latches or []
        self.counters = counters or []
        self.counter_values: dict[str, int] = {}
        self.counter_previous: dict[str, bool] = {}
        self.sequences = sequences or []
        self.sequence_steps: dict[str, int] = {}
        self.sequence_previous: dict[str, bool] = {}
        self.db_buffers = db_buffers
        self.states: dict[str, bool] = {}
        self.latch_values: dict[str, bool] = {}
        self.timer_started: dict[str, float] = {}
        self.timer_previous: dict[str, bool] = {}
        self.clock = clock
        self.safe_off_inputs_provider = safe_off_inputs_provider
        self._state_fingerprint = self._fingerprint()
        if retained_state and retained_state.get("version") == 1 and retained_state.get("fingerprint") == self._state_fingerprint:
            self._restore_state(retained_state.get("state", {}))

    def _fingerprint(self) -> str:
        config = {"rules": self.rules, "timers": self.timers, "boolean_rules": self.boolean_rules,
                  "latches": self.latches, "counters": self.counters, "sequences": self.sequences}
        encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def export_state(self) -> dict[str, Any]:
        """Return versioned block state suitable for durable storage by the runtime."""
        now = self.clock()
        return {"version": 1, "fingerprint": self._state_fingerprint, "state": {
            "states": self.states,
            "latch_values": {latch["name"]: self.latch_values.get(
                latch["name"], bool(self.db_buffers[latch["output_db"]][latch["output_byte_offset"]] & (1 << latch["output_bit_offset"])))
                             for latch in self.latches},
            "counter_values": self.counter_values,
            "counter_previous": self.counter_previous,
            "sequence_steps": self.sequence_steps,
            "sequence_previous": self.sequence_previous,
            "timer_elapsed_ms": {name: max(0, (now - started) * 1000) for name, started in self.timer_started.items()},
            "timer_previous": self.timer_previous,
        }}

    def _restore_state(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        names = lambda blocks: {block["name"] for block in blocks}
        def valid_map(key: str, allowed: set[str], predicate) -> dict:
            value = state.get(key, {})
            if not isinstance(value, dict):
                return {}
            return {name: item for name, item in value.items() if name in allowed and predicate(item)}
        self.states = valid_map("states", names(self.rules), lambda value: type(value) is bool)
        self.latch_values = valid_map("latch_values", names(self.latches), lambda value: type(value) is bool)
        self.counter_values = valid_map("counter_values", names(self.counters), lambda value: type(value) is int and value >= 0)
        self.counter_previous = valid_map("counter_previous", names(self.counters), lambda value: type(value) is bool)
        self.sequence_steps = valid_map("sequence_steps", names(self.sequences),
                                        lambda value: type(value) is int and value >= 0)
        self.sequence_steps = {name: min(value, len(next(x for x in self.sequences if x["name"] == name)["steps"]) - 1)
                               for name, value in self.sequence_steps.items()}
        self.sequence_previous = valid_map("sequence_previous", names(self.sequences), lambda value: type(value) is bool)
        self.timer_previous = valid_map("timer_previous", names(self.timers), lambda value: type(value) is bool)
        elapsed = valid_map("timer_elapsed_ms", names(self.timers), lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0)
        now = self.clock()
        self.timer_started = {name: now - min(value, next(x for x in self.timers if x["name"] == name)["preset_ms"]) / 1000
                              for name, value in elapsed.items()}

    def apply(self) -> None:
        safe_off_inputs = self.safe_off_inputs_provider() if self.safe_off_inputs_provider else set()
        for rule in self.rules:
            if (rule["input_db"], rule["input_byte_offset"], None) in safe_off_inputs:
                self.states[rule["name"]] = False
                self._write_bool(rule["output_db"], rule["output_byte_offset"], rule["output_bit_offset"], False)
                continue
            input_buffer = self.db_buffers[rule["input_db"]]
            value = struct.unpack_from(">f", input_buffer, rule["input_byte_offset"])[0]
            if not math.isfinite(value):
                continue
            output_key = (rule["output_db"], rule["output_byte_offset"], rule["output_bit_offset"])
            output_buffer = self.db_buffers[rule["output_db"]]
            mask = 1 << rule["output_bit_offset"]
            current = bool(output_buffer[rule["output_byte_offset"]] & mask)
            state = self.states.setdefault(rule["name"], current)
            setpoint = rule["setpoint"]
            hysteresis = rule["hysteresis"]
            if rule["mode"] == "below":
                if value <= setpoint:
                    state = True
                elif value >= setpoint + hysteresis:
                    state = False
            else:
                if value >= setpoint:
                    state = True
                elif value <= setpoint - hysteresis:
                    state = False
            self.states[rule["name"]] = state
            output_buffer[rule["output_byte_offset"]] = (
                output_buffer[rule["output_byte_offset"]] | mask
                if state else output_buffer[rule["output_byte_offset"]] & ~mask
            )
        for rule in self.boolean_rules:
            if (rule["input_a_db"], rule["input_a_byte_offset"], rule["input_a_bit_offset"]) in safe_off_inputs or (
                rule["input_b_db"], rule["input_b_byte_offset"], rule["input_b_bit_offset"]
            ) in safe_off_inputs:
                self._write_bool(rule["output_db"], rule["output_byte_offset"], rule["output_bit_offset"], False)
                continue
            input_a = self.db_buffers[rule["input_a_db"]][rule["input_a_byte_offset"]]
            input_b = self.db_buffers[rule["input_b_db"]][rule["input_b_byte_offset"]]
            a = bool(input_a & (1 << rule["input_a_bit_offset"]))
            b = bool(input_b & (1 << rule["input_b_bit_offset"]))
            if rule.get("input_a_negated", False):
                a = not a
            if rule.get("input_b_negated", False):
                b = not b
            result = {"and": a and b, "or": a or b, "xor": a != b}[rule["operator"]]
            output_buffer = self.db_buffers[rule["output_db"]]
            offset = rule["output_byte_offset"]
            mask = 1 << rule["output_bit_offset"]
            output_buffer[offset] = output_buffer[offset] | mask if result else output_buffer[offset] & ~mask
        for latch in self.latches:
            if (latch["set_db"], latch["set_byte_offset"], latch["set_bit_offset"]) in safe_off_inputs or (
                latch["reset_db"], latch["reset_byte_offset"], latch["reset_bit_offset"]
            ) in safe_off_inputs:
                self.latch_values[latch["name"]] = False
                self._write_bool(latch["output_db"], latch["output_byte_offset"], latch["output_bit_offset"], False)
                continue
            set_buffer = self.db_buffers[latch["set_db"]]
            reset_buffer = self.db_buffers[latch["reset_db"]]
            set_active = bool(set_buffer[latch["set_byte_offset"]] & (1 << latch["set_bit_offset"]))
            reset_active = bool(reset_buffer[latch["reset_byte_offset"]] & (1 << latch["reset_bit_offset"]))
            output_buffer = self.db_buffers[latch["output_db"]]
            offset = latch["output_byte_offset"]
            mask = 1 << latch["output_bit_offset"]
            current = self.latch_values.get(latch["name"], bool(output_buffer[offset] & mask))
            state = False if reset_active else True if set_active else current
            self.latch_values[latch["name"]] = state
            output_buffer[offset] = output_buffer[offset] | mask if state else output_buffer[offset] & ~mask
        for counter in self.counters:
            count_buffer = self.db_buffers[counter["count_db"]]
            reset_buffer = self.db_buffers[counter["reset_db"]]
            count_active = bool(count_buffer[counter["count_byte_offset"]] & (1 << counter["count_bit_offset"]))
            reset_active = bool(reset_buffer[counter["reset_byte_offset"]] & (1 << counter["reset_bit_offset"]))
            inputs_stale = (counter["count_db"], counter["count_byte_offset"], counter["count_bit_offset"]) in safe_off_inputs or (
                counter["reset_db"], counter["reset_byte_offset"], counter["reset_bit_offset"]
            ) in safe_off_inputs
            previous = self.counter_previous.get(counter["name"], False)
            count = self.counter_values.get(counter["name"], 0)
            if inputs_stale:
                count = 0
            elif reset_active:
                count = 0
            elif count_active and not previous:
                count = min(counter["preset"], count + 1)
            self.counter_previous[counter["name"]] = count_active
            self.counter_values[counter["name"]] = count
            output_buffer = self.db_buffers[counter["output_db"]]
            offset = counter["output_byte_offset"]
            mask = 1 << counter["output_bit_offset"]
            done = not inputs_stale and count >= counter["preset"]
            output_buffer[offset] = output_buffer[offset] | mask if done else output_buffer[offset] & ~mask
        for sequence in self.sequences:
            advance_active = bool(self.db_buffers[sequence["advance_db"]][sequence["advance_byte_offset"]]
                                  & (1 << sequence["advance_bit_offset"]))
            reset_active = bool(self.db_buffers[sequence["reset_db"]][sequence["reset_byte_offset"]]
                                & (1 << sequence["reset_bit_offset"]))
            inputs_stale = (sequence["advance_db"], sequence["advance_byte_offset"], sequence["advance_bit_offset"]) in safe_off_inputs or (
                sequence["reset_db"], sequence["reset_byte_offset"], sequence["reset_bit_offset"]
            ) in safe_off_inputs
            previous = self.sequence_previous.get(sequence["name"], False)
            step_index = self.sequence_steps.get(sequence["name"], 0)
            if inputs_stale:
                step_index = 0
            elif reset_active:
                step_index = 0
            elif advance_active and not previous:
                step_index = min(step_index + 1, len(sequence["steps"]) - 1)
            self.sequence_previous[sequence["name"]] = advance_active
            self.sequence_steps[sequence["name"]] = step_index
            for index, step in enumerate(sequence["steps"]):
                self._write_bool(step["db"], step["byte_offset"], step["bit_offset"],
                                 not inputs_stale and index == step_index)
        now = self.clock()
        for timer in self.timers:
            if (timer["input_db"], timer["input_byte_offset"], timer["input_bit_offset"]) in safe_off_inputs:
                self.timer_started.pop(timer["name"], None)
                self.timer_previous[timer["name"]] = False
                self._write_bool(timer["output_db"], timer["output_byte_offset"], timer["output_bit_offset"], False)
                continue
            input_buffer = self.db_buffers[timer["input_db"]]
            input_mask = 1 << timer["input_bit_offset"]
            active = bool(input_buffer[timer["input_byte_offset"]] & input_mask)
            if timer.get("mode", "ton") == "tof":
                previous = self.timer_previous.get(timer["name"], False)
                if active:
                    self.timer_started.pop(timer["name"], None)
                    done = True
                else:
                    if previous:
                        self.timer_started[timer["name"]] = now
                    started = self.timer_started.get(timer["name"])
                    done = started is not None and (now - started) * 1000 < timer["preset_ms"]
                    if started is not None and not done:
                        self.timer_started.pop(timer["name"], None)
                self.timer_previous[timer["name"]] = active
            else:
                if active:
                    started = self.timer_started.setdefault(timer["name"], now)
                    done = (now - started) * 1000 >= timer["preset_ms"]
                else:
                    self.timer_started.pop(timer["name"], None)
                    done = False
            output_buffer = self.db_buffers[timer["output_db"]]
            output_mask = 1 << timer["output_bit_offset"]
            output_offset = timer["output_byte_offset"]
            output_buffer[output_offset] = (
                output_buffer[output_offset] | output_mask
                if done else output_buffer[output_offset] & ~output_mask
            )

    def _write_bool(self, db: int, byte_offset: int, bit_offset: int, value: bool) -> None:
        output_buffer = self.db_buffers[db]
        mask = 1 << bit_offset
        output_buffer[byte_offset] = output_buffer[byte_offset] | mask if value else output_buffer[byte_offset] & ~mask
