"""Scenario persistence and topology normalization."""

from __future__ import annotations

import json

from sqlmodel import Session, select

from devices import DEVICE_REGISTRY
from models.scenario import ScenarioCreate, ScenarioRead, ScenarioRecord, ScenarioUpdate


class ScenarioNotFoundError(LookupError):
    pass


class ScenarioConflictError(RuntimeError):
    pass


class ScenarioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, payload: ScenarioCreate) -> ScenarioRead:
        runtime_devices = self._build_runtime_devices(payload)
        record = ScenarioRecord(
            name=payload.name,
            network_json=json.dumps(payload.network.model_dump(mode="json")),
            devices_json=json.dumps(
                [device.model_dump(mode="json") for device in payload.devices]
            ),
            runtime_devices_json=json.dumps(runtime_devices),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record.to_read_model()

    def list(self) -> list[ScenarioRead]:
        records = self.session.exec(
            select(ScenarioRecord).order_by(ScenarioRecord.created_at.desc())
        ).all()
        return [record.to_read_model() for record in records]

    def get_record(self, scenario_id: str) -> ScenarioRecord:
        record = self.session.get(ScenarioRecord, scenario_id)
        if record is None:
            raise ScenarioNotFoundError(f"Scenario '{scenario_id}' was not found")
        return record

    def get(self, scenario_id: str) -> ScenarioRead:
        return self.get_record(scenario_id).to_read_model()

    def update(self, scenario_id: str, payload: ScenarioUpdate) -> ScenarioRead:
        record = self.get_record(scenario_id)
        if record.status != "stopped":
            raise ScenarioConflictError("Stop the scenario before updating its topology")

        runtime_devices = self._build_runtime_devices(payload)
        record.name = payload.name
        record.network_json = json.dumps(payload.network.model_dump(mode="json"))
        record.devices_json = json.dumps(
            [device.model_dump(mode="json") for device in payload.devices]
        )
        record.runtime_devices_json = json.dumps(runtime_devices)
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record.to_read_model()

    def delete(self, scenario_id: str) -> None:
        record = self.get_record(scenario_id)
        if record.status != "stopped":
            raise ScenarioConflictError("Stop the scenario before deleting it")
        self.session.delete(record)
        self.session.commit()

    def set_status(self, scenario_id: str, status: str) -> None:
        record = self.get_record(scenario_id)
        record.status = status
        self.session.add(record)
        self.session.commit()

    @staticmethod
    def _build_runtime_devices(payload: ScenarioCreate | ScenarioUpdate) -> list[dict]:
        runtime_devices: list[dict] = []
        for device in payload.devices:
            device_class = DEVICE_REGISTRY.get(device.device_type)
            if device_class is None:
                raise ValueError(f"Unsupported device type '{device.device_type}'")
            runtime_devices.append(device_class().build_runtime_device(device))
        return runtime_devices
