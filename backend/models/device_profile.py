"""Portable, vendor-neutral device configuration and protocol address profile."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class DeviceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["anvil-device-profile"] = "anvil-device-profile"
    schema_version: StrictInt = Field(ge=1)
    device_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    protocol: str = Field(min_length=1, max_length=80)
    config: dict[str, Any]
    resolved_mapping: list[dict[str, Any]] = Field(default_factory=list, max_length=4096)
    fidelity_note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def supported_version(self) -> "DeviceProfile":
        if self.schema_version != 1:
            raise ValueError(f"Unsupported Anvil device profile schema version {self.schema_version}")
        return self


class DeviceProfileImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: DeviceProfile
    node_id: str = Field(min_length=1, max_length=128)
    ip: str
    zone_id: str = Field(default="default", min_length=1, max_length=32)
