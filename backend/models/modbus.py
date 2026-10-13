"""Modbus TCP device configuration schemas."""

from __future__ import annotations

import math
from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, Field, StrictInt, model_validator, field_validator


class ModbusCoilInput(BaseModel):
    type: Literal["coil"]
    name: str = Field(min_length=1)
    address: StrictInt | None = Field(default=None, ge=0, le=65535)
    initial_value: bool = False

    @field_validator("initial_value", mode="before")
    @classmethod
    def require_boolean(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("coil initial_value must be a boolean")
        return value


class ModbusDiscreteInputInput(BaseModel):
    type: Literal["discrete_input"]
    name: str = Field(min_length=1)
    address: StrictInt | None = Field(default=None, ge=0, le=65535)
    initial_value: bool = False

    @field_validator("initial_value", mode="before")
    @classmethod
    def require_boolean(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("discrete input initial_value must be a boolean")
        return value


class ModbusRegisterInput(BaseModel):
    type: Literal["holding_register", "input_register"]
    name: str = Field(min_length=1)
    address: StrictInt | None = Field(default=None, ge=0, le=65535)
    initial_value: int = Field(default=0, ge=0, le=65535)
    unit: str = ""
    min_value: StrictInt | None = Field(default=None, ge=0, le=65535)
    max_value: StrictInt | None = Field(default=None, ge=0, le=65535)

    @field_validator("initial_value", mode="before")
    @classmethod
    def require_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("register initial_value must be an integer")
        return value

    @model_validator(mode="after")
    def validate_engineering_range(self) -> "ModbusRegisterInput":
        if (self.min_value is None) != (self.max_value is None):
            raise ValueError("Modbus register range requires both min_value and max_value")
        if self.min_value is not None and self.max_value is not None:
            if self.max_value <= self.min_value:
                raise ValueError("Modbus register max_value must exceed min_value")
            if not self.min_value <= self.initial_value <= self.max_value:
                raise ValueError("Modbus register initial_value must be inside its configured range")
        return self


ModbusPointInput = Annotated[
    Union[ModbusCoilInput, ModbusDiscreteInputInput, ModbusRegisterInput], Field(discriminator="type")
]


class ModbusTcpConfigInput(BaseModel):
    transport: Literal["tcp", "rtu"] = "tcp"
    unit_id: StrictInt = Field(default=1, ge=0, le=255)
    port: StrictInt = Field(default=502, ge=1, le=65535)
    baudrate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200] = 19200
    bytesize: Literal[7, 8] = 8
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    response_delay_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_ms: StrictInt = Field(default=0, ge=0, le=10000)
    response_jitter_distribution: Literal["deterministic", "uniform", "normal"] = "deterministic"
    response_jitter_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    fault_mode: Literal["none", "device_failure", "disconnect"] = "none"
    fault_probability: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    fault_seed: StrictInt = Field(default=0, ge=0, le=2147483647)
    points: list[ModbusPointInput] = Field(min_length=1)

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

    component_config_model: ClassVar = ModbusPointInput

    @model_validator(mode="after")
    def unique_explicit_addresses(self) -> "ModbusTcpConfigInput":
        if self.transport == "rtu":
            if not 1 <= self.unit_id <= 247:
                raise ValueError("Modbus RTU unit_id must be from 1 to 247")
            if (self.bytesize, self.parity, self.stopbits) != (8, "N", 1):
                raise ValueError("virtual Modbus RTU currently supports the 8N1 line format only")
            if (self.response_delay_ms or self.response_jitter_ms or
                    self.response_jitter_distribution != "deterministic" or self.response_jitter_seed != 0 or
                    self.fault_mode != "none" or self.fault_probability != 1.0 or self.fault_seed != 0):
                raise ValueError("response delay and injected TCP faults are supported only for Modbus TCP")
        used: set[tuple[str, int]] = set()
        for point in self.points:
            if point.address is None:
                continue
            key = (point.type, point.address)
            if key in used:
                raise ValueError(f"duplicate Modbus address {point.type} {point.address}")
            used.add(key)
        return self
