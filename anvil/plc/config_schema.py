"""Pydantic models for the PLC_CONFIG environment variable."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class SimulationConfig(BaseModel):
    """Runtime behavior of an analog sensor."""

    mode: Literal["constant", "sine_wave"]
    period_seconds: float | None = Field(default=None, gt=0)
    noise: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_period_for_sine_wave(self) -> "SimulationConfig":
        if self.mode == "sine_wave" and self.period_seconds is None:
            raise ValueError("period_seconds is required when mode is 'sine_wave'")
        return self


class ValveComponentConfig(BaseModel):
    """A boolean valve stored as a bit in an S7 data block."""

    type: Literal["valve"]
    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    bit_offset: int = Field(ge=0, le=7)
    initial_state: bool = False


class AnalogSensorComponentConfig(BaseModel):
    """An IEEE-754 big-endian REAL stored in an S7 data block."""

    type: Literal["analog_sensor"]
    name: str = Field(min_length=1)
    db: int = Field(ge=1)
    byte_offset: int = Field(ge=0)
    unit: str = Field(min_length=1)
    min_value: float
    max_value: float
    simulation: SimulationConfig

    @model_validator(mode="after")
    def validate_value_range(self) -> "AnalogSensorComponentConfig":
        if self.max_value <= self.min_value:
            raise ValueError("max_value must be greater than min_value")
        return self


ComponentConfig = Annotated[
    Union[ValveComponentConfig, AnalogSensorComponentConfig],
    Field(discriminator="type"),
]


class PLCConfig(BaseModel):
    """Complete configuration supplied to the PLC container in PLC_CONFIG."""

    plc_id: str = Field(min_length=1)
    rack: int = Field(ge=0)
    slot: int = Field(ge=0)
    db_size: int = Field(gt=0)
    components: list[ComponentConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_component_offsets(self) -> "PLCConfig":
        for component in self.components:
            required_bytes = 1 if component.type == "valve" else 4
            if component.byte_offset + required_bytes > self.db_size:
                raise ValueError(
                    f"component '{component.name}' exceeds db_size "
                    f"({self.db_size} bytes)"
                )
        return self
