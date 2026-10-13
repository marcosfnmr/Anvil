"""Configuration schema and operation models for Siemens S7 PLCs."""

from __future__ import annotations

import math
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator


class SimulationInput(BaseModel):
    mode: Literal["constant", "sine_wave"] = "constant"
    period_seconds: float | None = Field(default=None, gt=0)
    noise: float = Field(default=0.0, ge=0.0, le=1.0)
    seed: StrictInt | None = Field(default=None, ge=0, le=4294967295)

    @model_validator(mode="after")
    def require_period_for_sine_wave(self) -> "SimulationInput":
        if self.mode == "sine_wave" and self.period_seconds is None:
            raise ValueError("period_seconds is required for sine_wave simulation")
        return self


class BooleanPointInput(BaseModel):
    name: str = Field(min_length=1)
    db: int = Field(default=1, ge=1)
    initial_state: StrictBool = False


class ValveInput(BooleanPointInput):
    type: Literal["valve"]


class MotorInput(BooleanPointInput):
    type: Literal["motor"]


class DigitalInputInput(BooleanPointInput):
    type: Literal["digital_input"]


class AlarmInput(BooleanPointInput):
    type: Literal["alarm"]


class PumpInput(BaseModel):
    type: Literal["pump"]
    name: str = Field(min_length=1)
    db: int = Field(default=1, ge=1)
    initial_state: StrictBool = False
    flow_min: float = Field(default=0.0, ge=0, le=3.4e38, allow_inf_nan=False)
    flow_max: float = Field(default=100.0, ge=0, le=3.4e38, allow_inf_nan=False)
    unit: str = "L/min"

    @model_validator(mode="after")
    def valid_flow_range(self) -> "PumpInput":
        if self.flow_max <= self.flow_min:
            raise ValueError("flow_max must be greater than flow_min")
        return self


class AnalogSensorInput(BaseModel):
    type: Literal["analog_sensor"]
    name: str = Field(min_length=1)
    db: int = Field(default=1, ge=1)
    unit: str = ""
    min_value: float = Field(default=0.0, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    max_value: float = Field(default=100.0, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    simulation: SimulationInput = Field(default_factory=SimulationInput)

    @model_validator(mode="after")
    def validate_range(self) -> "AnalogSensorInput":
        if self.max_value <= self.min_value:
            raise ValueError("max_value must be greater than min_value")
        return self


class RemoteInput(BaseModel):
    type: Literal["remote_input"]
    name: str = Field(min_length=1)
    db: int = Field(default=1, ge=1)
    data_type: Literal["BOOL", "REAL"] = "BOOL"
    source_node_id: str = Field(min_length=1)
    source_address: str = Field(pattern=r"^DB[1-9]\d*\.(DBX\d+\.[0-7]|DBD\d+)$")
    source_name: str | None = None
    period_ms: StrictInt = Field(default=200, ge=100, le=60000)
    timeout_ms: StrictInt = Field(default=1000, ge=100, le=10000)
    stale_after_ms: StrictInt = Field(default=1000, ge=100, le=120000)
    stale_behavior: Literal["hold", "force_off"] = "hold"
    unit: str = ""

    @model_validator(mode="after")
    def valid_stale_interval(self) -> "RemoteInput":
        if self.stale_after_ms < self.period_ms:
            raise ValueError("stale_after_ms must be at least period_ms")
        return self


class IoChannelInput(BaseModel):
    type: Literal["io_channel"]
    name: str = Field(min_length=1)
    db: int = Field(default=1, ge=1)
    area: Literal["DB", "M", "I", "Q"] = "DB"
    byte_offset: StrictInt | None = Field(default=None, ge=0)
    bit_offset: StrictInt | None = Field(default=None, ge=0, le=7)
    direction: Literal["input", "output"] = "input"
    data_type: Literal["BOOL", "REAL"] = "BOOL"
    unit: str = ""
    channel_id: str | None = Field(default=None, min_length=1)
    min_value: float = Field(default=0, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    max_value: float = Field(default=100, ge=-3.4e38, le=3.4e38, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_output_range(self):
        if self.area == "I" and self.direction != "input":
            raise ValueError("S7 input area I only supports input channels")
        if self.area == "Q" and self.direction != "output":
            raise ValueError("S7 output area Q only supports output channels")
        if self.data_type == "REAL" and {"min_value", "max_value"}.issubset(self.model_fields_set) and self.max_value <= self.min_value:
            raise ValueError("REAL channel maximum must exceed its minimum")
        if self.byte_offset is None and self.bit_offset not in (None, 0):
            raise ValueError("bit_offset requires an explicit byte_offset")
        if self.data_type == "REAL" and self.bit_offset not in (None, 0):
            raise ValueError("REAL channels cannot have a bit offset")
        if self.data_type == "REAL" and self.byte_offset is not None and self.byte_offset % 2:
            raise ValueError("REAL channels require an even byte offset")
        return self

class OnOffControlInput(BaseModel):
    """Hysteretic on/off function block over a REAL input and BOOL output."""
    name: str = Field(min_length=1, max_length=80)
    input_component: str = Field(min_length=1, max_length=80)
    output_component: str = Field(min_length=1, max_length=80)
    mode: Literal["below", "above"] = "below"
    setpoint: float = Field(ge=-3.4e38, le=3.4e38, allow_inf_nan=False)
    hysteresis: float = Field(gt=0, le=3.4e38, allow_inf_nan=False)

    @field_validator("setpoint", "hysteresis", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("control thresholds must be finite numbers")
        return value


class OnDelayTimerInput(BaseModel):
    """PLC TON/TOF block with a BOOL input/output and time preset."""
    name: str = Field(min_length=1, max_length=80)
    input_component: str = Field(min_length=1, max_length=80)
    output_component: str = Field(min_length=1, max_length=80)
    mode: Literal["ton", "tof"] = "ton"
    preset_ms: StrictInt = Field(ge=10, le=3_600_000)


class BooleanLogicInput(BaseModel):
    """Two-contact combinational BOOL rung with an explicit output coil."""
    name: str = Field(min_length=1, max_length=80)
    input_a_component: str = Field(min_length=1, max_length=80)
    input_b_component: str = Field(min_length=1, max_length=80)
    output_component: str = Field(min_length=1, max_length=80)
    operator: Literal["and", "or", "xor"] = "and"
    input_a_negated: StrictBool = False
    input_b_negated: StrictBool = False

    @model_validator(mode="after")
    def require_distinct_inputs(self) -> "BooleanLogicInput":
        if self.input_a_component == self.input_b_component:
            raise ValueError("boolean logic requires two distinct input components")
        return self


class SetResetLatchInput(BaseModel):
    """Reset-dominant SR memory block with BOOL set/reset inputs."""
    name: str = Field(min_length=1, max_length=80)
    set_component: str = Field(min_length=1, max_length=80)
    reset_component: str = Field(min_length=1, max_length=80)
    output_component: str = Field(min_length=1, max_length=80)


class UpCounterInput(BaseModel):
    """Rising-edge BOOL counter with reset and a DONE output."""
    name: str = Field(min_length=1, max_length=80)
    count_component: str = Field(min_length=1, max_length=80)
    reset_component: str = Field(min_length=1, max_length=80)
    output_component: str = Field(min_length=1, max_length=80)
    preset: StrictInt = Field(ge=1, le=65535)


class SequenceInput(BaseModel):
    """One-hot BOOL sequence advanced by a rising edge, with RESET to step one."""
    name: str = Field(min_length=1, max_length=80)
    advance_component: str = Field(min_length=1, max_length=80)
    reset_component: str = Field(min_length=1, max_length=80)
    steps: list[str] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_sequence_shape(self) -> "SequenceInput":
        if self.advance_component == self.reset_component:
            raise ValueError("sequence requires distinct advance and reset inputs")
        if len(self.steps) != len(set(self.steps)):
            raise ValueError("sequence step outputs must be unique")
        return self


ComponentInput = Annotated[
    ValveInput | MotorInput | DigitalInputInput | AlarmInput | PumpInput | AnalogSensorInput | RemoteInput | IoChannelInput,
    Field(discriminator="type"),
]
ModbusPointInput = Annotated["ModbusCoilInput | ModbusRegisterInput", Field(discriminator="type")]


class PlcS7ConfigInput(BaseModel):
    rack: int = Field(default=0, ge=0)
    slot: int = Field(default=1, ge=0)
    db_size: int = Field(default=32, gt=0)
    scan_cycle_ms: StrictInt = Field(default=100, ge=10, le=5000)
    retain_logic_state: StrictBool = False
    response_delay_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_distribution: Literal["deterministic", "uniform", "normal"] = "deterministic"
    response_jitter_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    fault_mode: Literal["none", "timeout"] = "none"
    fault_probability: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    fault_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    components: list[ComponentInput] = Field(default_factory=list)
    control_rules: list[OnOffControlInput] = Field(default_factory=list, max_length=32)
    boolean_rules: list[BooleanLogicInput] = Field(default_factory=list, max_length=32)
    timers: list[OnDelayTimerInput] = Field(default_factory=list, max_length=32)
    latches: list[SetResetLatchInput] = Field(default_factory=list, max_length=32)
    counters: list[UpCounterInput] = Field(default_factory=list, max_length=32)
    sequences: list[SequenceInput] = Field(default_factory=list, max_length=16)

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

    component_config_model: ClassVar = ComponentInput

    @model_validator(mode="after")
    def validate_components_fit_db(self) -> "PlcS7ConfigInput":
        identifiers = [c.channel_id for c in self.components if isinstance(c, IoChannelInput) and c.channel_id is not None]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate channel_id")
        cursors: dict[tuple[str, int], tuple[int, int]] = {}
        occupied: dict[tuple[str, int], list[tuple[int, int, str]]] = {}

        def reserve(key: tuple[str, int], start_bit: int, end_bit: int, name: str) -> None:
            for old_start, old_end, old_name in occupied.setdefault(key, []):
                if start_bit < old_end and old_start < end_bit:
                    raise ValueError(f"component '{name}' overlaps S7 memory used by '{old_name}'")
            occupied[key].append((start_bit, end_bit, name))

        for component in self.components:
            key = (component.area if isinstance(component, IoChannelInput) else "DB", component.db if not isinstance(component, IoChannelInput) or component.area == "DB" else 0)
            byte_offset, bit_offset = cursors.get(key, (0, 0))
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
                byte_length = 4 if component.data_type == "REAL" else 1
                required_end = byte_offset + byte_length
                start_bit = byte_offset * 8 + (bit_offset if component.data_type == "BOOL" else 0)
                end_bit = start_bit + (32 if component.data_type == "REAL" else 1)
                reserve(key, start_bit, end_bit, component.name)
                cursors[key] = (max(cursors.get(key, (0, 0))[0], required_end), 0)
            elif isinstance(component, PumpInput):
                if bit_offset:
                    byte_offset += 1
                    bit_offset = 0
                if byte_offset % 2:
                    byte_offset += 1
                required_end = byte_offset + 6
                reserve(key, byte_offset * 8, byte_offset * 8 + 1, f"{component.name} command")
                reserve(key, (byte_offset + 2) * 8, (byte_offset + 6) * 8, f"{component.name} flow")
                cursors[key] = (required_end, 0)
            elif isinstance(component, AnalogSensorInput) or isinstance(component, RemoteInput) and component.data_type == "REAL":
                if bit_offset:
                    byte_offset += 1
                if byte_offset % 2:
                    byte_offset += 1
                required_end = byte_offset + 4
                reserve(key, byte_offset * 8, required_end * 8, component.name)
                cursors[key] = (required_end, 0)
            else:
                required_end = byte_offset + 1
                reserve(key, byte_offset * 8 + bit_offset, byte_offset * 8 + bit_offset + 1, component.name)
                bit_offset += 1
                if bit_offset == 8:
                    cursors[key] = (byte_offset + 1, 0)
                else:
                    cursors[key] = (byte_offset, bit_offset)
            if required_end > self.db_size:
                raise ValueError(
                    f"component '{component.name}' exceeds configured memory area size ({self.db_size} bytes)"
                )
        return self

    @model_validator(mode="after")
    def limit_remote_inputs(self) -> "PlcS7ConfigInput":
        if sum(isinstance(item, RemoteInput) for item in self.components) > 32:
            raise ValueError("at most 32 remote inputs are allowed per PLC")
        return self

    @model_validator(mode="after")
    def validate_control_rules(self) -> "PlcS7ConfigInput":
        names = [item.name for item in [*self.control_rules, *self.boolean_rules, *self.timers, *self.latches, *self.counters, *self.sequences]]
        if len(names) != len(set(names)):
            raise ValueError("PLC logic block names must be unique")
        outputs: set[str] = set()
        real_inputs = [item.name for item in self.components if
                       isinstance(item, AnalogSensorInput)
                       or isinstance(item, RemoteInput) and item.data_type == "REAL"
                       or isinstance(item, IoChannelInput) and item.area == "DB" and item.direction == "input" and item.data_type == "REAL"
                       or isinstance(item, PumpInput)]
        boolean_outputs = [item.name for item in self.components if
                           isinstance(item, (ValveInput, MotorInput, PumpInput))
                           or isinstance(item, IoChannelInput) and item.area == "DB" and item.direction == "output" and item.data_type == "BOOL"]
        for rule in self.control_rules:
            if real_inputs.count(rule.input_component) != 1:
                raise ValueError(f"control rule '{rule.name}' must reference one unique REAL input component")
            if boolean_outputs.count(rule.output_component) != 1:
                raise ValueError(f"control rule '{rule.name}' must reference one unique BOOL output component")
            if rule.output_component in outputs:
                raise ValueError("a BOOL output cannot be driven by more than one control rule")
            if rule.input_component == rule.output_component and rule.input_component not in real_inputs:
                raise ValueError("control input and output must be separate components")
            outputs.add(rule.output_component)
        bool_inputs = [item.name for item in self.components if
                       isinstance(item, DigitalInputInput)
                       or isinstance(item, RemoteInput) and item.data_type == "BOOL"
                       or isinstance(item, IoChannelInput) and item.area == "DB" and item.direction == "input" and item.data_type == "BOOL"]
        for timer in self.timers:
            if bool_inputs.count(timer.input_component) != 1:
                raise ValueError(f"timer '{timer.name}' must reference one unique BOOL input component")
            if boolean_outputs.count(timer.output_component) != 1:
                raise ValueError(f"timer '{timer.name}' must reference one unique BOOL output component")
            if timer.output_component in outputs:
                raise ValueError("a BOOL output cannot be driven by more than one control block")
            outputs.add(timer.output_component)
        for rule in self.boolean_rules:
            for input_name in (rule.input_a_component, rule.input_b_component):
                if bool_inputs.count(input_name) != 1:
                    raise ValueError(f"boolean rule '{rule.name}' must reference two unique BOOL input components")
            if boolean_outputs.count(rule.output_component) != 1:
                raise ValueError(f"boolean rule '{rule.name}' must reference one unique BOOL output component")
            if rule.output_component in outputs:
                raise ValueError("a BOOL output cannot be driven by more than one control block")
            outputs.add(rule.output_component)
        for latch in self.latches:
            if latch.set_component == latch.reset_component:
                raise ValueError(f"latch '{latch.name}' requires distinct set and reset inputs")
            for input_name in (latch.set_component, latch.reset_component):
                if bool_inputs.count(input_name) != 1:
                    raise ValueError(f"latch '{latch.name}' must reference unique BOOL input components")
            if boolean_outputs.count(latch.output_component) != 1:
                raise ValueError(f"latch '{latch.name}' must reference one unique BOOL output component")
            if latch.output_component in outputs:
                raise ValueError("a BOOL output cannot be driven by more than one control block")
            outputs.add(latch.output_component)
        for counter in self.counters:
            if counter.count_component == counter.reset_component:
                raise ValueError(f"counter '{counter.name}' requires distinct count and reset inputs")
            for input_name in (counter.count_component, counter.reset_component):
                if bool_inputs.count(input_name) != 1:
                    raise ValueError(f"counter '{counter.name}' must reference unique BOOL input components")
            if boolean_outputs.count(counter.output_component) != 1:
                raise ValueError(f"counter '{counter.name}' must reference one unique BOOL output component")
            if counter.output_component in outputs:
                raise ValueError("a BOOL output cannot be driven by more than one control block")
            outputs.add(counter.output_component)
        for sequence in self.sequences:
            if sequence.advance_component == sequence.reset_component:
                raise ValueError(f"sequence '{sequence.name}' requires distinct advance and reset inputs")
            for input_name in (sequence.advance_component, sequence.reset_component):
                if bool_inputs.count(input_name) != 1:
                    raise ValueError(f"sequence '{sequence.name}' must reference unique BOOL input components")
            for output_name in sequence.steps:
                if boolean_outputs.count(output_name) != 1:
                    raise ValueError(f"sequence '{sequence.name}' must reference mapped BOOL output components")
                if output_name in outputs:
                    raise ValueError("a BOOL output cannot be driven by more than one control block")
                outputs.add(output_name)
        return self

class PlcWriteRequest(BaseModel):
    address: str
    value: StrictBool


class HmiWriteRequest(BaseModel):
    target_node_id: str = Field(min_length=1, max_length=128)
    address: str = Field(min_length=1, max_length=128)
    value: StrictBool | StrictInt | StrictFloat


class PlcValueRead(BaseModel):
    node_id: str
    address: str
    component_name: str
    data_type: str
    value: bool | float
    raw_hex: str
    unit: str | None = None
    communication: dict | None = None
    quality: str = "good"
    protocol_quality: str | None = None
    range_diagnostic: dict | None = None


class DeviceValuesRead(BaseModel):
    scenario_id: UUID
    node_id: str
    values: list[PlcValueRead]


class PlcWriteRead(PlcValueRead):
    scenario_id: UUID


class ModbusWriteRequest(BaseModel):
    address: str
    value: StrictBool | StrictInt


class ModbusValueRead(BaseModel):
    node_id: str
    address: str
    component_name: str
    data_type: str
    value: bool | int
    raw_hex: str
    unit: str | None = None
    communication: dict | None = None
    quality: str = "good"
    protocol_quality: str | None = None
    range_diagnostic: dict | None = None
    min_value: int | None = None
    max_value: int | None = None


class ModbusDeviceValuesRead(BaseModel):
    scenario_id: UUID
    node_id: str
    values: list[ModbusValueRead]


class ModbusWriteRead(ModbusValueRead):
    scenario_id: UUID


class ProtocolOperationRead(BaseModel):
    timestamp: str
    operation: str
    source: str
    node_id: str
    area: Literal["DB", "M", "I", "Q"] = "DB"
    db: int | None = None
    address: str | None = None
    byte_count: int = 0
    raw_hex: str = ""
    result: str


class ScenarioProtocolRead(BaseModel):
    scenario_id: UUID
    operations: list[ProtocolOperationRead]


class SignalChangeRead(BaseModel):
    timestamp: str
    node_id: str
    address: str
    operation: str
    previous: bool | float | int | None
    value: bool | float | int
    raw_hex: str


class ScenarioSignalHistoryRead(BaseModel):
    scenario_id: UUID
    changes: list[SignalChangeRead]
