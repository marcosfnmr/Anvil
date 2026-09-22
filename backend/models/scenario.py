"""Public scenario schemas and the private SQLite record."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Annotated, Any, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator
from sqlmodel import Field, SQLModel


ScenarioState = Literal["stopped", "deploying", "running", "partial", "error"]


class SimulationInput(BaseModel):
    mode: Literal["constant", "sine_wave"] = "constant"
    period_seconds: float | None = PydanticField(default=None, gt=0)
    noise: float = PydanticField(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_period_for_sine_wave(self) -> "SimulationInput":
        if self.mode == "sine_wave" and self.period_seconds is None:
            raise ValueError("period_seconds is required for sine_wave simulation")
        return self


class ValveInput(BaseModel):
    type: Literal["valve"]
    name: str = PydanticField(min_length=1)
    db: int = PydanticField(default=1, ge=1)
    initial_state: bool = False


class AnalogSensorInput(BaseModel):
    type: Literal["analog_sensor"]
    name: str = PydanticField(min_length=1)
    db: int = PydanticField(default=1, ge=1)
    unit: str = ""
    min_value: float = 0.0
    max_value: float = 100.0
    simulation: SimulationInput = PydanticField(default_factory=SimulationInput)

    @model_validator(mode="after")
    def validate_range(self) -> "AnalogSensorInput":
        if self.max_value <= self.min_value:
            raise ValueError("max_value must be greater than min_value")
        return self


ComponentInput = Annotated[
    Union[ValveInput, AnalogSensorInput],
    PydanticField(discriminator="type"),
]


class PlcS7ConfigInput(BaseModel):
    rack: int = PydanticField(default=0, ge=0)
    slot: int = PydanticField(default=1, ge=0)
    db_size: int = PydanticField(default=32, gt=0)
    components: list[ComponentInput] = PydanticField(min_length=1)


class NetworkInput(BaseModel):
    subnet: str

    @field_validator("subnet")
    @classmethod
    def validate_subnet(cls, value: str) -> str:
        network = ip_network(value, strict=True)
        if not isinstance(network, IPv4Network):
            raise ValueError("only IPv4 scenario networks are supported")
        return str(network)


class DeviceInput(BaseModel):
    node_id: str = PydanticField(min_length=1)
    device_type: str = PydanticField(min_length=1)
    name: str = PydanticField(min_length=1)
    ip: str
    config: PlcS7ConfigInput

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        address = ip_address(value)
        if not isinstance(address, IPv4Address):
            raise ValueError("only IPv4 device addresses are supported")
        return str(address)


class ScenarioPayload(BaseModel):
    name: str = PydanticField(min_length=1)
    network: NetworkInput
    devices: list[DeviceInput] = PydanticField(min_length=1)

    @model_validator(mode="after")
    def validate_topology(self) -> "ScenarioPayload":
        network = ip_network(self.network.subnet)
        node_ids: set[str] = set()
        addresses: set[str] = set()

        for device in self.devices:
            if device.node_id in node_ids:
                raise ValueError(f"duplicate node_id '{device.node_id}'")
            if device.ip in addresses:
                raise ValueError(f"duplicate device IP '{device.ip}'")
            if ip_address(device.ip) not in network:
                raise ValueError(
                    f"device '{device.node_id}' IP {device.ip} is outside {network}"
                )
            node_ids.add(device.node_id)
            addresses.add(device.ip)
        return self


class ScenarioCreate(ScenarioPayload):
    pass


class ScenarioUpdate(ScenarioPayload):
    pass


class ScenarioRead(ScenarioPayload):
    id: UUID
    status: ScenarioState
    created_at: datetime


class ContainerStatus(BaseModel):
    node_id: str
    name: str
    status: str


class ScenarioStatusRead(BaseModel):
    scenario_id: UUID
    status: ScenarioState
    containers: list[ContainerStatus] = PydanticField(default_factory=list)


class ScenarioRecord(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(index=True)
    network_json: str
    devices_json: str
    runtime_devices_json: str
    status: str = Field(default="stopped", index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def to_read_model(self) -> ScenarioRead:
        return ScenarioRead.model_validate(
            {
                "id": self.id,
                "name": self.name,
                "network": json.loads(self.network_json),
                "devices": json.loads(self.devices_json),
                "status": self.status,
                "created_at": self.created_at,
            }
        )

    def runtime_devices(self) -> list[dict[str, Any]]:
        return json.loads(self.runtime_devices_json)
