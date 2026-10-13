"""Schemas for auxiliary cyber-range devices."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictFloat, field_validator, model_validator


class HmiAlarmRule(BaseModel):
    target_node_id: str = Field(min_length=1, max_length=128)
    address: str = Field(min_length=1, max_length=128)
    mode: Literal["threshold", "state"] = "threshold"
    above: StrictInt | StrictFloat | None = None
    below: StrictInt | StrictFloat | None = None
    expected_state: StrictBool | None = None
    delay_ms: StrictInt = Field(default=0, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "HmiAlarmRule":
        if any(value is not None and not math.isfinite(value) for value in (self.above, self.below)):
            raise ValueError("HMI alarm thresholds must be finite numbers")
        if self.mode == "threshold":
            if self.above is None and self.below is None:
                raise ValueError("an HMI threshold alarm needs an above or below threshold")
            if self.expected_state is not None:
                raise ValueError("threshold alarms cannot define an expected boolean state")
            if self.above is not None and self.below is not None and self.below >= self.above:
                raise ValueError("HMI alarm below threshold must be lower than above threshold")
        elif self.expected_state is None or self.above is not None or self.below is not None:
            raise ValueError("state alarms need expected_state and cannot define numeric thresholds")
        return self


class VirtualDeviceConfig(BaseModel):
    role: Literal["hmi", "gateway", "firewall", "attacker", "engineering", "switch"]
    summary: str = Field(min_length=1, max_length=240)
    target_node_ids: str = Field(default="", max_length=500)
    alarm_source_node_ids: str = Field(default="", max_length=500)
    participant_access_enabled: StrictBool = False
    engineering_access_enabled: StrictBool = False
    alarms: list[HmiAlarmRule] = Field(default_factory=list, max_length=100)
    firewall_allow_tcp_ports: list[StrictInt] = Field(default_factory=list, max_length=32)
    switch_port_capacity: StrictInt = Field(default=24, ge=2, le=256)

    @field_validator("firewall_allow_tcp_ports")
    @classmethod
    def validate_ports(cls, value: list[StrictInt]) -> list[StrictInt]:
        if len(set(value)) != len(value) or any(port < 1 or port > 65535 for port in value):
            raise ValueError("firewall ports must be unique integers from 1 to 65535")
        return value

    @model_validator(mode="after")
    def validate_alarm_rules(self) -> "VirtualDeviceConfig":
        keys = [(rule.target_node_id, rule.address) for rule in self.alarms]
        if len(keys) != len(set(keys)):
            raise ValueError("HMI alarm rules must be unique per target address")
        if any(rule.target_node_id not in {item.strip() for item in self.target_node_ids.split(",")} for rule in self.alarms):
            raise ValueError("HMI alarm rules must reference a selected target")
        if self.role != "hmi" and self.alarms:
            raise ValueError("only HMI devices can define HMI alarm rules")
        if self.participant_access_enabled and self.role != "attacker":
            raise ValueError("participant network access is only available to participant stations")
        if self.engineering_access_enabled and self.role != "engineering":
            raise ValueError("engineering network access is only available to engineering stations")
        if self.role != "switch" and self.switch_port_capacity != 24:
            raise ValueError("switch_port_capacity is only valid for ethernet switches")
        return self
