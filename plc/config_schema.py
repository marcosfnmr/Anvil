"""Pydantic models for the PLC_CONFIG environment variable."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, IPvAnyAddress, StrictBool, StrictInt, field_validator, model_validator


class SimulationConfig(BaseModel):
    """Runtime behavior of an analog sensor."""

    mode: Literal["constant", "sine_wave"]
    period_seconds: float | None = Field(default=None, gt=0)
    noise: float = Field(default=0.0, ge=0.0, le=1.0)
    seed: StrictInt | None = Field(default=None, ge=0, le=4294967295)

    @model_validator(mode="after")
    def require_period_for_sine_wave(self) -> "SimulationConfig":
        if self.mode == "sine_wave" and self.period_seconds is None:
            raise ValueError("period_seconds is required when mode is 'sine_wave'")
        return self


class BooleanComponentConfig(BaseModel):
    """A boolean valve stored as a bit in an S7 data block."""

    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    bit_offset: int = Field(ge=0, le=7)
    initial_state: bool = False


class ValveComponentConfig(BooleanComponentConfig):
    type: Literal["valve"]


class MotorComponentConfig(BooleanComponentConfig):
    type: Literal["motor"]


class DigitalInputComponentConfig(BooleanComponentConfig):
    type: Literal["digital_input"]


class AlarmComponentConfig(BooleanComponentConfig):
    type: Literal["alarm"]


class PumpCommandComponentConfig(BooleanComponentConfig):
    type: Literal["pump_command"]
    flow_byte_offset: int = Field(ge=0)
    flow_min: float
    flow_max: float
    unit: str = "L/min"


class PumpFlowComponentConfig(BaseModel):
    type: Literal["pump_flow"]
    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    command_byte_offset: int = Field(ge=0)
    command_bit_offset: int = Field(ge=0, le=7)
    flow_min: float
    flow_max: float
    unit: str = "L/min"


class AnalogSensorComponentConfig(BaseModel):
    """An IEEE-754 big-endian REAL stored in an S7 data block."""

    type: Literal["analog_sensor"]
    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    unit: str = ""
    min_value: float
    max_value: float
    simulation: SimulationConfig

    @model_validator(mode="after")
    def validate_value_range(self) -> "AnalogSensorComponentConfig":
        if self.max_value <= self.min_value:
            raise ValueError("max_value must be greater than min_value")
        return self


class RemoteInputComponentConfig(BaseModel):
    type: Literal["remote_input"]
    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    bit_offset: int = Field(default=0, ge=0, le=7)
    data_type: Literal["BOOL", "REAL"]
    source_node_id: str
    source_address: str
    source_ip: IPvAnyAddress
    source_port: int = Field(default=102, ge=1, le=65535)
    source_rack: int = Field(ge=0)
    source_slot: int = Field(ge=0)
    source_db: int = Field(ge=1)
    source_byte_offset: int = Field(ge=0)
    source_bit_offset: int = Field(default=0, ge=0, le=7)
    period_ms: StrictInt = Field(default=200, ge=100, le=60000)
    timeout_ms: StrictInt = Field(default=1000, ge=100, le=10000)
    stale_after_ms: StrictInt = Field(default=1000, ge=100, le=120000)
    stale_behavior: Literal["hold", "force_off"] = "hold"
    unit: str = ""

    @model_validator(mode="after")
    def valid_stale_interval(self) -> "RemoteInputComponentConfig":
        if self.stale_after_ms < self.period_ms:
            raise ValueError("stale_after_ms must be at least period_ms")
        return self


class IoChannelConfig(BaseModel):
    type: Literal["io_channel"]
    name: str
    db: int = Field(ge=1)
    area: Literal["DB", "M", "I", "Q"] = "DB"
    byte_offset: int = Field(ge=0)
    bit_offset: int = Field(default=0, ge=0, le=7)
    data_type: Literal["BOOL", "REAL"]
    direction: Literal["input", "output"]
    unit: str = ""
    min_value: float = Field(default=0, allow_inf_nan=False)
    max_value: float = Field(default=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_output_range(self) -> "IoChannelConfig":
        if self.area == "I" and self.direction != "input":
            raise ValueError("S7 input area I only supports input channels")
        if self.area == "Q" and self.direction != "output":
            raise ValueError("S7 output area Q only supports output channels")
        if self.direction == "output" and self.data_type == "REAL" and self.max_value <= self.min_value:
            raise ValueError("REAL output channel maximum must exceed its minimum")
        return self


class ControlLogicConfig(BaseModel):
    """Resolved PLC-memory addresses for a bounded on/off function block."""
    name: str = Field(min_length=1)
    input_db: int = Field(ge=1)
    input_byte_offset: int = Field(ge=0)
    output_db: int = Field(ge=1)
    output_byte_offset: int = Field(ge=0)
    output_bit_offset: int = Field(ge=0, le=7)
    mode: Literal["below", "above"]
    setpoint: float = Field(ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    hysteresis: float = Field(gt=0, le=3.4e38, allow_inf_nan=False)

    @field_validator("setpoint", "hysteresis", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("PLC logic thresholds must be finite numbers")
        return value


class ControlTimerConfig(BaseModel):
    """Resolved IEC-style on/off-delay timer addresses and preset."""
    name: str = Field(min_length=1)
    input_db: int = Field(ge=1)
    input_byte_offset: int = Field(ge=0)
    input_bit_offset: int = Field(ge=0, le=7)
    output_db: int = Field(ge=1)
    output_byte_offset: int = Field(ge=0)
    output_bit_offset: int = Field(ge=0, le=7)
    mode: Literal["ton", "tof"] = "ton"
    preset_ms: StrictInt = Field(ge=10, le=3_600_000)


class BooleanLogicConfig(BaseModel):
    """Resolved BOOL addresses and contact polarity for one logic rung."""
    name: str = Field(min_length=1)
    input_a_db: int = Field(ge=1)
    input_a_byte_offset: int = Field(ge=0)
    input_a_bit_offset: int = Field(ge=0, le=7)
    input_b_db: int = Field(ge=1)
    input_b_byte_offset: int = Field(ge=0)
    input_b_bit_offset: int = Field(ge=0, le=7)
    output_db: int = Field(ge=1)
    output_byte_offset: int = Field(ge=0)
    output_bit_offset: int = Field(ge=0, le=7)
    operator: Literal["and", "or", "xor"]
    input_a_negated: StrictBool = False
    input_b_negated: StrictBool = False

    @model_validator(mode="after")
    def distinct_inputs(self) -> "BooleanLogicConfig":
        if (self.input_a_db, self.input_a_byte_offset, self.input_a_bit_offset) == (
            self.input_b_db, self.input_b_byte_offset, self.input_b_bit_offset
        ):
            raise ValueError("boolean logic requires two distinct input addresses")
        return self


class SetResetLatchConfig(BaseModel):
    """Resolved input/output addresses for a reset-dominant SR latch."""
    name: str = Field(min_length=1)
    set_db: int = Field(ge=1)
    set_byte_offset: int = Field(ge=0)
    set_bit_offset: int = Field(ge=0, le=7)
    reset_db: int = Field(ge=1)
    reset_byte_offset: int = Field(ge=0)
    reset_bit_offset: int = Field(ge=0, le=7)
    output_db: int = Field(ge=1)
    output_byte_offset: int = Field(ge=0)
    output_bit_offset: int = Field(ge=0, le=7)

    @model_validator(mode="after")
    def distinct_inputs(self) -> "SetResetLatchConfig":
        if (self.set_db, self.set_byte_offset, self.set_bit_offset) == (
            self.reset_db, self.reset_byte_offset, self.reset_bit_offset
        ):
            raise ValueError("SR latch requires distinct set and reset inputs")
        return self


class UpCounterConfig(BaseModel):
    """Resolved addresses for a rising-edge counter with RESET priority."""
    name: str = Field(min_length=1)
    count_db: int = Field(ge=1)
    count_byte_offset: int = Field(ge=0)
    count_bit_offset: int = Field(ge=0, le=7)
    reset_db: int = Field(ge=1)
    reset_byte_offset: int = Field(ge=0)
    reset_bit_offset: int = Field(ge=0, le=7)
    output_db: int = Field(ge=1)
    output_byte_offset: int = Field(ge=0)
    output_bit_offset: int = Field(ge=0, le=7)
    preset: StrictInt = Field(ge=1, le=65535)

    @model_validator(mode="after")
    def distinct_inputs(self) -> "UpCounterConfig":
        if (self.count_db, self.count_byte_offset, self.count_bit_offset) == (
            self.reset_db, self.reset_byte_offset, self.reset_bit_offset
        ):
            raise ValueError("up counter requires distinct count and reset inputs")
        return self


class SequenceOutputConfig(BaseModel):
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    bit_offset: int = Field(ge=0, le=7)


class SequenceConfig(BaseModel):
    """One-hot sequence advanced on rising edges; reset returns to step zero."""
    name: str = Field(min_length=1)
    advance_db: int = Field(ge=1)
    advance_byte_offset: int = Field(ge=0)
    advance_bit_offset: int = Field(ge=0, le=7)
    reset_db: int = Field(ge=1)
    reset_byte_offset: int = Field(ge=0)
    reset_bit_offset: int = Field(ge=0, le=7)
    steps: list[SequenceOutputConfig] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_sequence_addresses(self) -> "SequenceConfig":
        if (self.advance_db, self.advance_byte_offset, self.advance_bit_offset) == (
            self.reset_db, self.reset_byte_offset, self.reset_bit_offset
        ):
            raise ValueError("sequence requires distinct advance and reset inputs")
        addresses = [(step.db, step.byte_offset, step.bit_offset) for step in self.steps]
        if len(addresses) != len(set(addresses)):
            raise ValueError("sequence step outputs must be unique")
        return self


ComponentConfig = Annotated[
    Union[ValveComponentConfig, MotorComponentConfig, DigitalInputComponentConfig, AlarmComponentConfig, PumpCommandComponentConfig, PumpFlowComponentConfig, AnalogSensorComponentConfig, RemoteInputComponentConfig, IoChannelConfig],
    Field(discriminator="type"),
]


class PLCConfig(BaseModel):
    """Complete configuration supplied to the PLC container in PLC_CONFIG."""

    plc_id: str = Field(min_length=1)
    rack: int = Field(ge=0)
    slot: int = Field(ge=0)
    db_size: int = Field(gt=0)
    scan_cycle_ms: StrictInt = Field(default=100, ge=10, le=5000)
    retain_logic_state: StrictBool = False
    response_delay_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_distribution: Literal["deterministic", "uniform", "normal"] = "deterministic"
    response_jitter_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    fault_mode: Literal["none", "timeout"] = "none"
    fault_probability: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    fault_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    components: list[ComponentConfig] = Field(default_factory=list)
    logic: list[ControlLogicConfig] = Field(default_factory=list, max_length=32)
    boolean_logic: list[BooleanLogicConfig] = Field(default_factory=list, max_length=32)
    timers: list[ControlTimerConfig] = Field(default_factory=list, max_length=32)
    latches: list[SetResetLatchConfig] = Field(default_factory=list, max_length=32)
    counters: list[UpCounterConfig] = Field(default_factory=list, max_length=32)
    sequences: list[SequenceConfig] = Field(default_factory=list, max_length=16)
    field_bridge: dict | None = None

    @field_validator("fault_probability", mode="before")
    @classmethod
    def strict_fault_probability(cls, value: object) -> object:
        try:
            finite = math.isfinite(value) if type(value) in (int, float) else False
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("fault_probability must be a finite number from 0 to 1")
        return value

    @model_validator(mode="after")
    def validate_component_offsets(self) -> "PLCConfig":
        for component in self.components:
            required_bytes = 1 if isinstance(component, BooleanComponentConfig) or isinstance(component, (RemoteInputComponentConfig, IoChannelConfig)) and component.data_type == "BOOL" else 4
            if component.byte_offset + required_bytes > self.db_size:
                raise ValueError(
                    f"component '{component.name}' exceeds configured memory area size "
                    f"({self.db_size} bytes)"
                )
            if isinstance(component, PumpFlowComponentConfig) and component.command_byte_offset >= self.db_size:
                raise ValueError("pump command offset exceeds db_size")
        rule_names = [rule.name for rule in [*self.logic, *self.boolean_logic, *self.timers, *self.latches, *self.counters, *self.sequences]]
        outputs = [(rule.output_db, rule.output_byte_offset, rule.output_bit_offset) for rule in self.logic]
        outputs.extend((rule.output_db, rule.output_byte_offset, rule.output_bit_offset) for rule in self.boolean_logic)
        outputs.extend((timer.output_db, timer.output_byte_offset, timer.output_bit_offset) for timer in self.timers)
        outputs.extend((latch.output_db, latch.output_byte_offset, latch.output_bit_offset) for latch in self.latches)
        outputs.extend((counter.output_db, counter.output_byte_offset, counter.output_bit_offset) for counter in self.counters)
        sequence_outputs = [(step.db, step.byte_offset, step.bit_offset) for sequence in self.sequences for step in sequence.steps]
        outputs.extend(sequence_outputs)
        if len(rule_names) != len(set(rule_names)):
            raise ValueError("PLC logic rule names must be unique")
        if len(outputs) != len(set(outputs)):
            raise ValueError("a BOOL output cannot be driven by more than one logic rule")
        db_numbers = {component.db for component in self.components}
        real_inputs = {
            (component.db, component.byte_offset)
            for component in self.components
            if isinstance(component, (AnalogSensorComponentConfig, PumpFlowComponentConfig))
            or isinstance(component, RemoteInputComponentConfig) and component.data_type == "REAL"
            or isinstance(component, IoChannelConfig) and component.area == "DB" and component.direction == "input" and component.data_type == "REAL"
        }
        bool_outputs = {
            (component.db, component.byte_offset, getattr(component, "bit_offset", 0))
            for component in self.components
            if isinstance(component, (ValveComponentConfig, MotorComponentConfig, PumpCommandComponentConfig))
            or isinstance(component, IoChannelConfig) and component.area == "DB" and component.direction == "output" and component.data_type == "BOOL"
        }
        bool_inputs = {
            (component.db, component.byte_offset, getattr(component, "bit_offset", 0))
            for component in self.components
            if isinstance(component, DigitalInputComponentConfig)
            or isinstance(component, RemoteInputComponentConfig) and component.data_type == "BOOL"
            or isinstance(component, IoChannelConfig) and component.area == "DB" and component.direction == "input" and component.data_type == "BOOL"
        }
        for rule in self.logic:
            if rule.input_db not in db_numbers or rule.output_db not in db_numbers:
                raise ValueError(f"logic rule '{rule.name}' references an unregistered DB")
            if rule.input_byte_offset + 4 > self.db_size or rule.output_byte_offset >= self.db_size:
                raise ValueError(f"logic rule '{rule.name}' exceeds db_size")
            if (rule.input_db, rule.input_byte_offset) not in real_inputs:
                raise ValueError(f"logic rule '{rule.name}' input is not a mapped REAL input")
            if (rule.output_db, rule.output_byte_offset, rule.output_bit_offset) not in bool_outputs:
                raise ValueError(f"logic rule '{rule.name}' output is not a writable BOOL output")
        for timer in self.timers:
            if timer.input_db not in db_numbers or timer.output_db not in db_numbers:
                raise ValueError(f"timer '{timer.name}' references an unregistered DB")
            if timer.input_byte_offset >= self.db_size or timer.output_byte_offset >= self.db_size:
                raise ValueError(f"timer '{timer.name}' exceeds db_size")
            if (timer.input_db, timer.input_byte_offset, timer.input_bit_offset) not in bool_inputs:
                raise ValueError(f"timer '{timer.name}' input is not a mapped BOOL input")
            if (timer.output_db, timer.output_byte_offset, timer.output_bit_offset) not in bool_outputs:
                raise ValueError(f"timer '{timer.name}' output is not a writable BOOL output")
        for rule in self.boolean_logic:
            if rule.input_a_db not in db_numbers or rule.input_b_db not in db_numbers or rule.output_db not in db_numbers:
                raise ValueError(f"boolean rule '{rule.name}' references an unregistered DB")
            if any(offset >= self.db_size for offset in (rule.input_a_byte_offset, rule.input_b_byte_offset, rule.output_byte_offset)):
                raise ValueError(f"boolean rule '{rule.name}' exceeds db_size")
            if (rule.input_a_db, rule.input_a_byte_offset, rule.input_a_bit_offset) not in bool_inputs:
                raise ValueError(f"boolean rule '{rule.name}' input A is not a mapped BOOL input")
            if (rule.input_b_db, rule.input_b_byte_offset, rule.input_b_bit_offset) not in bool_inputs:
                raise ValueError(f"boolean rule '{rule.name}' input B is not a mapped BOOL input")
            if (rule.output_db, rule.output_byte_offset, rule.output_bit_offset) not in bool_outputs:
                raise ValueError(f"boolean rule '{rule.name}' output is not a writable BOOL output")
        for latch in self.latches:
            if latch.set_db not in db_numbers or latch.reset_db not in db_numbers or latch.output_db not in db_numbers:
                raise ValueError(f"SR latch '{latch.name}' references an unregistered DB")
            if any(offset >= self.db_size for offset in (latch.set_byte_offset, latch.reset_byte_offset, latch.output_byte_offset)):
                raise ValueError(f"SR latch '{latch.name}' exceeds db_size")
            if (latch.set_db, latch.set_byte_offset, latch.set_bit_offset) not in bool_inputs:
                raise ValueError(f"SR latch '{latch.name}' set input is not a mapped BOOL input")
            if (latch.reset_db, latch.reset_byte_offset, latch.reset_bit_offset) not in bool_inputs:
                raise ValueError(f"SR latch '{latch.name}' reset input is not a mapped BOOL input")
            if (latch.output_db, latch.output_byte_offset, latch.output_bit_offset) not in bool_outputs:
                raise ValueError(f"SR latch '{latch.name}' output is not a writable BOOL output")
        for counter in self.counters:
            if counter.count_db not in db_numbers or counter.reset_db not in db_numbers or counter.output_db not in db_numbers:
                raise ValueError(f"up counter '{counter.name}' references an unregistered DB")
            if any(offset >= self.db_size for offset in (counter.count_byte_offset, counter.reset_byte_offset, counter.output_byte_offset)):
                raise ValueError(f"up counter '{counter.name}' exceeds db_size")
            if (counter.count_db, counter.count_byte_offset, counter.count_bit_offset) not in bool_inputs:
                raise ValueError(f"up counter '{counter.name}' count input is not a mapped BOOL input")
            if (counter.reset_db, counter.reset_byte_offset, counter.reset_bit_offset) not in bool_inputs:
                raise ValueError(f"up counter '{counter.name}' reset input is not a mapped BOOL input")
            if (counter.output_db, counter.output_byte_offset, counter.output_bit_offset) not in bool_outputs:
                raise ValueError(f"up counter '{counter.name}' output is not a writable BOOL output")
        for sequence in self.sequences:
            input_addresses = (
                (sequence.advance_db, sequence.advance_byte_offset, sequence.advance_bit_offset),
                (sequence.reset_db, sequence.reset_byte_offset, sequence.reset_bit_offset),
            )
            if any(address[0] not in db_numbers for address in input_addresses):
                raise ValueError(f"sequence '{sequence.name}' references an unregistered DB")
            if any(address[1] >= self.db_size for address in input_addresses):
                raise ValueError(f"sequence '{sequence.name}' exceeds db_size")
            if any(address not in bool_inputs for address in input_addresses):
                raise ValueError(f"sequence '{sequence.name}' inputs must be mapped BOOL inputs")
            for step in sequence.steps:
                if step.db not in db_numbers or step.byte_offset >= self.db_size:
                    raise ValueError(f"sequence '{sequence.name}' output exceeds configured memory")
                if (step.db, step.byte_offset, step.bit_offset) not in bool_outputs:
                    raise ValueError(f"sequence '{sequence.name}' step output is not a writable BOOL output")
        return self
