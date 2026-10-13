"""Verify field state transitions reach scenario audit through real Docker."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import docker
from sqlmodel import Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from models.audit import ScenarioAuditEvent  # noqa: E402
from models.auth import PlatformUser  # noqa: E402,F401 — registers the owner FK target.
from models.scenario import ScenarioRecord  # noqa: E402
from services.audit import verify_audit_integrity  # noqa: E402
from services.docker_manager import DockerManager  # noqa: E402
from services.field_operations import field_operation  # noqa: E402
from services.protocol_trace_collector import collect_protocol_traces_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/field:dev")
    args = parser.parse_args()

    scenario_id = str(uuid.uuid4())
    container_name = f"anvil-process-audit-{scenario_id[:8]}"
    client = docker.from_env()
    engine = create_engine(f"sqlite:///{ROOT / f'.process-audit-{scenario_id}.db'}")
    container = None
    try:
        SQLModel.metadata.create_all(engine)
        signal_connection = {
            "connection_id": "valve-command",
            "source": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBX0.0"},
            "target": {"kind": "field", "object_id": "valve-1", "port": "command"},
        }
        field_config = {
            "contract_version": 1,
            "objects": [
                {"object_id": "valve-1", "type": "valve", "name": "Valve",
                 "initial_state": False, "response_seconds": 0},
                {"object_id": "sensor-1", "type": "analog_sensor", "name": "Sensor",
                 "min_value": 0, "max_value": 100, "unit": "%",
                 "simulation": {"mode": "sine_wave", "period_seconds": 0.5}},
            ],
            "connections": [signal_connection],
            "process_connections": [],
        }
        runtime_device = {
            "node_id": "__anvil_field__", "device_type": "field-runtime",
            "config": {"objects": field_config["objects"], "connections": [signal_connection]},
        }
        container = client.containers.run(
            args.image,
            detach=True,
            name=container_name,
            network_mode="none",
            labels={
                "anvil.managed": "true",
                "anvil.scenario_id": scenario_id,
                "anvil.node_id": "__anvil_field__",
                "anvil.role": "field-runtime",
            },
            environment={"FIELD_CONFIG": json.dumps(field_config)},
        )
        with Session(engine) as session:
            session.add(ScenarioRecord(
                id=scenario_id, name="Process audit integration smoke",
                network_json='{"subnet":"172.31.0.0/24"}', devices_json="[]",
                runtime_devices_json=json.dumps([runtime_device]), status="running",
            ))
            session.commit()

        manager = DockerManager(client=client)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                ready = container.exec_run([
                    "python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/snapshot',timeout=1)",
                ])
                if ready.exit_code == 0:
                    break
                time.sleep(0.1)
            except Exception:
                container.reload()
                if container.status != "running":
                    raise RuntimeError(f"field runtime exited: {container.logs(tail=100).decode(errors='replace')}")
                time.sleep(0.1)
        else:
            raise TimeoutError("field runtime did not become ready")

        # First collection establishes a baseline. A sine-wave sensor changes
        # its reading while no process audit event should be emitted for it.
        self_check = field_operation(manager, scenario_id)
        sensor_before = next(item["value"] for item in self_check["objects"]
                             if item["object_id"] == "sensor-1")
        collect_protocol_traces_once(engine, manager)
        time.sleep(0.12)
        script = r'''import json,urllib.request
payload={"node_id":"plc-1","outputs":{"valve-command":True}}
request=urllib.request.Request("http://127.0.0.1:8765/exchange",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
with urllib.request.urlopen(request,timeout=2) as response: response.read()
'''
        result = container.exec_run(["python", "-c", script], demux=True)
        if result.exit_code:
            stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, b"")
            raise RuntimeError((stdout or stderr or b"field exchange failed").decode(errors="replace"))
        collect_protocol_traces_once(engine, manager)
        current_snapshot = field_operation(manager, scenario_id)
        sensor_after = next(item["value"] for item in current_snapshot["objects"]
                            if item["object_id"] == "sensor-1")
        if abs(sensor_after - sensor_before) < 1e-3:
            raise AssertionError(f"analog sensor did not change during the smoke: {sensor_before} -> {sensor_after}")

        with Session(engine) as session:
            events = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "process.field_state_changed",
            )).all()
            details = [json.loads(event.details_json) for event in events]
            if len(details) != 1 or details[0].get("object_id") != "valve-1":
                raise AssertionError(f"unexpected process audit events: {details}")
            if details[0].get("changes", {}).get("state") != {"from": False, "to": True}:
                raise AssertionError(f"valve state transition was not audited: {details[0]}")
            if details[0].get("control_sources") != [{
                "node_id": "plc-1", "address": "DB1.DBX0.0", "connection_id": "valve-command",
            }]:
                raise AssertionError(f"signal context missing from audit: {details[0]}")
            if not verify_audit_integrity(session, scenario_id).valid:
                raise AssertionError("scenario audit hash chain is invalid")

        time.sleep(0.12)
        collect_protocol_traces_once(engine, manager)
        with Session(engine) as session:
            sensor_events = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "process.field_state_changed",
                ScenarioAuditEvent.details_json.contains("sensor-1"),
            )).all()
            if sensor_events:
                raise AssertionError("continuous analog sensor samples entered process audit")

        print("PASS: isolated Docker field state change persisted with signal context; analog samples stayed out of process audit")
        return 0
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
        client.close()
        engine.dispose()
        database_path = ROOT / f".process-audit-{scenario_id}.db"
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(f"{database_path}{suffix}").unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
