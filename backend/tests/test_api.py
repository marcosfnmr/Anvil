from __future__ import annotations

import sys
import hashlib
import json
import io
import zipfile
import subprocess
import tempfile
import unittest
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import yaml
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import inspect, select
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from devices import register_device, unregister_device  # noqa: E402
from devices.base import Device  # noqa: E402
from models.db import create_db_and_tables, get_session  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from dependencies import get_optional_docker_manager  # noqa: E402
from models.scada import ScenarioScadaSample  # noqa: E402
from models.audit import ScenarioAuditEvent, ScenarioAuditCheckpoint  # noqa: E402
from models.exercise import ExerciseRun  # noqa: E402
from models.scenario import ScenarioOperationLease, ScenarioRecord  # noqa: E402
from services.audit import _event_hash, prune_audit_events_once, record_audit_event, verify_audit_integrity  # noqa: E402
from services.evidence_export import EvidenceBundleTooLarge, _EvidenceBudget  # noqa: E402
from services.evidence_integrity import verify_evidence_bundle  # noqa: E402


class FakeDockerManager:
    def __init__(self) -> None:
        self.running: dict[str, list[dict[str, str]]] = {}
        self.packet_captures: dict[str, tuple[str, str]] = {}
        self.route_probes: list[dict] = []
        self.field_binding_probes: list[dict] = []
        self.field_binding_result: dict | None = None
        self.route_probe_result = {"status": "reachable", "detail": "S7 setup acknowledged"}

    def pin_runtime_images(self, devices: list[dict]) -> None:
        for index, device in enumerate(devices):
            if device.get("device_type") == "ethernet-switch":
                continue
            image_name = "anvil/plc-s7" if device.get("device_type") == "plc-s7" else "anvil/test-runtime"
            digest = f"sha256:{index + 1:064x}"
            device.update(image_ref=f"{image_name}:test", image_id=digest,
                          image_pin=f"anvil-pinned/{index + 1:012x}:{index + 1:064x}")

    def deploy(self, scenario_id: str, subnet: str, devices: list[dict]) -> dict:
        del subnet
        containers = [
            {
                "node_id": device["node_id"],
                "name": f"anvil-{device['node_id']}",
                "status": "running",
            }
            for device in devices
        ]
        self.running[scenario_id] = containers
        return {"scenario_id": scenario_id, "status": "running", "containers": containers,
                "deployment_duration_seconds": 1.234}

    def stop(self, scenario_id: str) -> dict:
        self.running.pop(scenario_id, None)
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def restart_scenario(self, scenario_id: str, runtime_devices: list[dict]) -> dict:
        expected = {item["node_id"] for item in runtime_devices
                    if item.get("device_type") != "ethernet-switch"}
        containers = self.running.get(scenario_id, [])
        if expected - {item["node_id"] for item in containers}:
            raise RuntimeError("missing runtime node")
        for container in containers:
            container["status"] = "running"
        return self.status(scenario_id)

    def delete_retention_volumes(self, scenario_id: str) -> None:
        del scenario_id

    def pause_scenario(self, scenario_id: str) -> dict:
        for container in self.running.get(scenario_id, []):
            container["status"] = "paused"
        return self.status(scenario_id)

    def resume_scenario(self, scenario_id: str) -> dict:
        for container in self.running.get(scenario_id, []):
            container["status"] = "running"
        return self.status(scenario_id)

    def node_lifecycle(self, scenario_id: str, node_id: str, action: str) -> dict:
        containers = self.running.get(scenario_id, [])
        for container in containers:
            if container["node_id"] == node_id:
                container["status"] = {"stop": "exited", "pause": "paused", "resume": "running"}.get(action, "running")
                break
        return self.status(scenario_id)

    def recreate_node(self, scenario_id: str, runtime_device: dict) -> dict:
        containers = self.running.setdefault(scenario_id, [])
        if any(item["node_id"] == runtime_device["node_id"] for item in containers):
            raise RuntimeError("node already exists")
        containers.append({"node_id": runtime_device["node_id"], "name": f"anvil-{runtime_device['node_id']}", "status": "running"})
        return self.status(scenario_id)

    def logs(self, scenario_id: str, *, tail: int = 100) -> dict:
        del tail
        return {"scenario_id": scenario_id, "containers": []}

    def status(self, scenario_id: str) -> dict:
        containers = self.running.get(scenario_id, [])
        statuses = {container["status"] for container in containers}
        overall = "stopped" if not containers else "paused" if statuses == {"paused"} else "running" if statuses == {"running"} else "partial"
        return {
            "scenario_id": scenario_id,
            "status": overall,
            "containers": containers,
        }

    def probe_network_route(self, scenario_id: str, **kwargs) -> dict:
        self.route_probes.append({"scenario_id": scenario_id, **kwargs})
        return self.route_probe_result

    def probe_field_bindings(self, scenario_id: str, *, source_node_id: str, bindings: list[dict]) -> dict:
        self.field_binding_probes.append({"scenario_id": scenario_id, "source_node_id": source_node_id,
                                          "bindings": bindings})
        if self.field_binding_result is not None:
            return self.field_binding_result
        return {"status": "reachable", "detail": f"{len(bindings)}/{len(bindings)} field bindings fresh",
                "bindings": [{"address": item["address"], "status": "good"} for item in bindings]}

    def start_packet_capture(self, scenario_id: str, node_id: str | None, capture_id: str, *, zone_id: str | None = None, peer_node_id: str | None = None) -> dict:
        self.packet_captures[capture_id] = (scenario_id, node_id or zone_id or peer_node_id)
        return {"capture_id": capture_id, "node_id": node_id, "zone_id": zone_id,
                "source_node_id": node_id if peer_node_id else None, "target_node_id": peer_node_id,
                "scope": "segment" if zone_id else "link" if peer_node_id else "device", "status": "capturing"}

    def export_packet_capture(self, scenario_id: str, capture_id: str) -> bytes:
        if self.packet_captures.pop(capture_id, None) is None:
            raise RuntimeError("capture missing")
        return b"\xd4\xc3\xb2\xa1" + bytes(20)


class ScenarioApiTests(unittest.TestCase):
    def test_curated_templates_are_listed_and_instantiable(self) -> None:
        response = self.client.get("/api/templates")
        self.assertEqual(response.status_code, 200, response.text)
        templates = response.json()
        self.assertGreaterEqual(len(templates), 5)
        self.assertTrue(all(item.get("category") and item.get("summary") for item in templates))
        created = {}
        for template in templates:
            result = self.client.post(f"/api/templates/{template['id']}/instantiate")
            self.assertEqual(result.status_code, 201, f"{template['id']}: {result.text}")
            self.assertEqual(result.json()["status"], "stopped")
            created[template["id"]] = result.json()

        loop = created["closed-loop-water-process"]
        self.assertEqual(len(loop["field_objects"]), 5)
        self.assertGreaterEqual(len(loop["process_connections"]), 4)
        self.assertTrue(any(device["device_type"] == "scada" for device in loop["devices"]))
        self.assertTrue(any(device["device_type"] == "modbus-tcp" for device in loop["devices"]))
        factory = created["field-to-plc-factory"]
        self.assertGreaterEqual(len(factory["field_objects"]), 3)
        self.assertTrue(factory["signal_connections"])

    def test_template_catalog_seed_files_stay_in_sync_with_examples(self) -> None:
        backend_dir = Path(__file__).resolve().parents[1]
        examples_dir = backend_dir.parent / "examples"
        for packaged, source in (
            ("demo-planta-agua.json", "demo-planta-agua-mapa.json"),
            ("demo-bombeo-conectado.json", "demo-bombeo-conectado.json"),
            ("dos-plc-s7.json", "s7-peer-links.json"),
        ):
            self.assertEqual(
                json.loads((backend_dir / "templates" / packaged).read_text(encoding="utf-8")),
                json.loads((examples_dir / source).read_text(encoding="utf-8")),
            )

    def test_unknown_template_returns_not_found(self) -> None:
        self.assertEqual(self.client.post("/api/templates/missing/instantiate").status_code, 404)

    def test_scenario_revisions_are_deduplicated_and_restorable(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        revisions_url = f"/api/scenarios/{scenario_id}/revisions"
        initial = self.client.get(revisions_url)
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual([item["version"] for item in initial.json()], [1])
        original_revision_id = initial.json()[0]["id"]

        changed = self.client.get(f"/api/scenarios/{scenario_id}").json()
        changed["name"] = "linea de prueba modificada"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=changed)
        self.assertEqual(updated.status_code, 200, updated.text)
        same = self.client.put(f"/api/scenarios/{scenario_id}", json=changed)
        self.assertEqual(same.status_code, 200, same.text)
        revisions = self.client.get(revisions_url).json()
        self.assertEqual([item["version"] for item in revisions], [2, 1])
        detail = self.client.get(f"{revisions_url}/{original_revision_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["snapshot"]["name"], "linea de prueba")

        restored = self.client.post(f"{revisions_url}/{original_revision_id}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["name"], "linea de prueba")
        final_revisions = self.client.get(revisions_url).json()
        self.assertEqual([item["version"] for item in final_revisions], [3, 2, 1])
        self.assertEqual(final_revisions[0]["content_hash"], final_revisions[2]["content_hash"])
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertIn("scenario.revision_restored", {item["event_type"] for item in events})

    def test_scenario_revisions_cannot_be_restored_while_running_or_cross_scenario(self) -> None:
        first = self.client.post("/api/scenarios", json=self.payload()).json()
        second_payload = self.payload()
        second_payload["name"] = "otra línea"
        second = self.client.post("/api/scenarios", json=second_payload).json()
        revision_id = self.client.get(f"/api/scenarios/{first['id']}/revisions").json()[0]["id"]
        wrong_scenario = self.client.post(f"/api/scenarios/{second['id']}/revisions/{revision_id}/restore")
        self.assertEqual(wrong_scenario.status_code, 404)
        self.assertEqual(self.client.post(f"/api/scenarios/{first['id']}/deploy").status_code, 200)
        running_restore = self.client.post(f"/api/scenarios/{first['id']}/revisions/{revision_id}/restore")
        self.assertEqual(running_restore.status_code, 409)

    def test_s7_endpoint_calls_real_service_and_not_itself(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"] = [{"type": "valve", "name": "V1"}]
        created = self.client.post("/api/scenarios", json=payload).json()
        scenario_id = created["id"]
        self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b'{"value": true, "data_type": "BOOL", "raw_hex": "01"}')
        self.manager.client = Mock()
        self.manager.client.containers.list.return_value = [container]
        result = self.client.get(f"/api/scenarios/{scenario_id}/plcs/plc-1/values")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["values"][0]["value"])
        container.exec_run.assert_called_once()

    def test_duplicate_signal_names_write_the_requested_address(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"] = [{"type": "valve", "name": "same"}, {"type": "motor", "name": "same"}]
        scenario_id = self.client.post("/api/scenarios", json=payload).json()["id"]
        self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        with patch("routes.plc_operations.write_plc_point") as write:
            write.return_value = {"scenario_id": scenario_id, "node_id": "plc-1", "address": "DB1.DBX0.1", "component_name": "same", "data_type": "BOOL", "value": True, "raw_hex": "02"}
            result = self.client.post(f"/api/scenarios/{scenario_id}/plcs/plc-1/write", json={"address": "DB1.DBX0.1", "value": True})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(write.call_args.args[3]["bit_offset"], 1)
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        operation = next(event for event in events if event["event_type"] == "operator.point_written")
        self.assertEqual(operation["details"], {
            "source": "s7-console", "node_id": "plc-1", "address": "DB1.DBX0.1", "value": True,
        })

    def test_deploy_stop_and_delete_reject_an_active_operation_lease(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        with Session(self.engine) as session:
            session.add(ScenarioOperationLease(
                scenario_id=scenario_id, owner_id="other-worker", operation="deploy",
                expires_at=time.time() + 120,
            ))
            session.commit()

        deploy = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        stop = self.client.post(f"/api/scenarios/{scenario_id}/stop")
        delete = self.client.delete(f"/api/scenarios/{scenario_id}")
        pause = self.client.post(f"/api/scenarios/{scenario_id}/pause")
        resume = self.client.post(f"/api/scenarios/{scenario_id}/resume")
        node = self.client.post(
            f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "restart"},
        )
        self.assertEqual(deploy.status_code, 409)
        self.assertEqual(stop.status_code, 409)
        self.assertEqual(delete.status_code, 409)
        self.assertEqual(pause.status_code, 409)
        self.assertEqual(resume.status_code, 409)
        self.assertEqual(node.status_code, 409)
        self.assertNotIn(scenario_id, self.manager.running)
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").status_code, 200)

    def test_audit_events_persist_lifecycle_and_export_as_jsonl(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        with patch("routes.scenarios.flush_protocol_traces_best_effort", return_value=0) as collect:
            self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/stop").status_code, 200)
        collect.assert_called_once_with(self.engine, manager=self.manager, scenario_id=scenario_id)

        response = self.client.get(f"/api/scenarios/{scenario_id}/audit-events?limit=2")
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(len(page["events"]), 2)
        self.assertIsNotNone(page["next_before_id"])
        earlier = self.client.get(f"/api/scenarios/{scenario_id}/audit-events?before_id={page['next_before_id']}")
        self.assertEqual(earlier.status_code, 200, earlier.text)
        self.assertTrue(any(event["event_type"] == "scenario.created" for event in earlier.json()["events"]))

        exported = self.client.get(f"/api/scenarios/{scenario_id}/audit-events/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("application/x-ndjson", exported.headers["content-type"])
        records = [json.loads(line) for line in exported.text.splitlines()]
        self.assertGreaterEqual(len(records), 4)
        self.assertEqual([item["id"] for item in records], sorted(item["id"] for item in records))
        self.assertTrue(all(len(item["event_hash"]) == 64 for item in records))
        self.assertTrue(self.client.get(f"/api/scenarios/{scenario_id}/audit-events/integrity").json()["valid"])

    def test_audit_integrity_endpoint_detects_event_content_tampering(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        with Session(self.engine) as session:
            event = session.execute(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id
            ).order_by(ScenarioAuditEvent.id)).scalars().first()
            self.assertIsNotNone(event)
            event.details_json = '{"tampered":true}'
            session.add(event)
            session.commit()
            damaged_id = event.id
        response = self.client.get(f"/api/scenarios/{scenario_id}/audit-events/integrity")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "scenario_id": scenario_id,
            "valid": False,
            "checked_events": 1,
            "first_invalid_event_id": damaged_id,
            "anchored_after_event_id": None,
        })

    def test_audit_retention_keeps_running_exercise_evidence_and_chain_anchor(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        jan = datetime(2026, 1, 1, tzinfo=timezone.utc)
        aug = datetime(2026, 8, 15, tzinfo=timezone.utc)
        sep = datetime(2026, 9, 30, tzinfo=timezone.utc)
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            first = session.execute(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id
            ).order_by(ScenarioAuditEvent.id)).scalars().first()
            first.timestamp = jan
            first.event_hash = _event_hash(first, first.previous_hash)
            session.add(first)
            session.commit()
            pruned_id = first.id
            record_audit_event(session, scenario_id, "exercise.objective_evidence", {"event": "kept"}, timestamp=aug)
            record_audit_event(session, scenario_id, "operator.action", {"event": "recent"}, timestamp=sep)
            session.add(ExerciseRun(
                scenario_id=scenario_id,
                definition_json='{"name":"active","events":[],"objectives":[],"success_rule":"all","passing_score":100}',
                status="running",
                started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ))
            session.commit()

        self.assertEqual(prune_audit_events_once(self.engine, 30, now=now), 1)
        with Session(self.engine) as session:
            retained = session.execute(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id
            ).order_by(ScenarioAuditEvent.id)).scalars().all()
            self.assertNotIn("scenario.created", [event.event_type for event in retained])
            self.assertIn("exercise.objective_evidence", [event.event_type for event in retained])
            self.assertIn("audit.retention_applied", [event.event_type for event in retained])
            checkpoint = session.get(ScenarioAuditCheckpoint, scenario_id)
            self.assertEqual(checkpoint.pruned_through_event_id, pruned_id)
            integrity = verify_audit_integrity(session, scenario_id)
            self.assertTrue(integrity.valid)
            self.assertEqual(integrity.anchored_after_event_id, pruned_id)
        self.assertEqual(prune_audit_events_once(self.engine, 30, now=now), 0)

    def test_evidence_bundle_packages_audit_scada_csv_and_selected_pcap(self) -> None:
        payload = self.payload()
        payload["devices"].append({
            "node_id": "scada", "device_type": "scada", "name": "SCADA history",
            "ip": "172.30.10.12",
            "config": {"target_node_ids": "plc-1", "poll_interval_ms": 250,
                       "history_capacity": 100, "alarms": []},
        })
        scenario_id = self.client.post("/api/scenarios", json=payload).json()["id"]
        with Session(self.engine) as session:
            session.add(ScenarioScadaSample(
                scenario_id=scenario_id, scada_node_id="scada", sampled_at="2026-10-08T10:00:00+00:00",
                target_node_id="plc-1", address="DB1.DBD0", name="Nivel", data_type="REAL",
                unit="%", value_json="42.5", quality="good",
            ))
            session.commit()
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        started = self.client.post(f"/api/scenarios/{scenario_id}/captures", json={"node_id": "plc-1"})
        self.assertEqual(started.status_code, 202, started.text)
        capture_id = started.json()["capture_id"]

        response = self.client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip?capture_id={capture_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {"manifest.json", "audit/events.jsonl", "logs/containers.json", "scada/scada.csv", f"captures/{capture_id}.pcap"})
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["scenario_id"], scenario_id)
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(response.headers["x-anvil-manifest-sha256"],
                             hashlib.sha256(archive.read("manifest.json")).hexdigest())
            self.assertTrue(manifest["audit_integrity"]["valid"])
            self.assertEqual(manifest["integrity"]["algorithm"], "sha256")
            self.assertEqual({item["path"] for item in manifest["integrity"]["files"]}, names - {"manifest.json"})
            self.assertTrue(verify_evidence_bundle(response.content)["valid"])
            self.assertTrue(archive.read(f"captures/{capture_id}.pcap").startswith(b"\xd4\xc3\xb2\xa1"))
            samples = archive.read("scada/scada.csv").decode("utf-8-sig")
            self.assertIn("DB1.DBD0", samples)
            self.assertIn("42.5", samples)
            events = [json.loads(line) for line in archive.read("audit/events.jsonl").decode().splitlines()]
            self.assertEqual(manifest["audit_integrity"]["checked_events"], len(events))
            self.assertIn("network.packet_capture_exported", {item["event_type"] for item in events})

        tampered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(response.content)) as original, zipfile.ZipFile(tampered, "w") as altered:
            for name in original.namelist():
                data = original.read(name)
                if name == f"captures/{capture_id}.pcap":
                    data += b"tampered"
                altered.writestr(name, data)
        verification = verify_evidence_bundle(tampered.getvalue())
        self.assertFalse(verification["valid"])
        self.assertEqual(verification["error"], "file_integrity_mismatch")
        self.assertEqual(verification["path"], f"captures/{capture_id}.pcap")

    def test_evidence_bundle_budget_is_enforced_and_api_returns_payload_too_large(self) -> None:
        budget = _EvidenceBudget(limit=5)
        budget.consume(3)
        with self.assertRaises(EvidenceBundleTooLarge):
            budget.consume(3)

        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        with patch("routes.scenarios.build_evidence_bundle",
                   side_effect=EvidenceBundleTooLarge("test bundle limit exceeded")):
            response = self.client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip")
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["detail"], "test bundle limit exceeded")

    def test_invalid_configuration_and_reserved_ips_return_validation_errors(self) -> None:
        for config in (None, [], "invalid"):
            payload = self.payload()
            payload["devices"][0]["config"] = config
            self.assertEqual(self.client.post("/api/scenarios", json=payload).status_code, 422)
        for ip in ("172.30.10.0", "172.30.10.1", "172.30.10.255"):
            payload = self.payload()
            payload["devices"][0]["ip"] = ip
            self.assertEqual(self.client.post("/api/scenarios", json=payload).status_code, 422)

    def test_duplicate_external_service_ports_are_rejected_during_scenario_validation(self) -> None:
        payload = self.payload()
        first = payload["devices"][0]
        first.update(external_access=True, external_port=1102)
        second = json.loads(json.dumps(first))
        second.update(node_id="plc-2", name="PLC secundario", ip="172.30.10.11")
        payload["devices"].append(second)

        response = self.client.post("/api/scenarios", json=payload)

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("external host port 1102", response.text)

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.manager = FakeDockerManager()
        app = create_app(initialize_database=False)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: self.manager
        app.dependency_overrides[get_optional_docker_manager] = lambda: self.manager
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    def test_existing_node_operation_journal_gets_restart_checkpoint_column(self) -> None:
        legacy_engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        try:
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE scenario_node_operations ("
                    "id VARCHAR PRIMARY KEY, scenario_id VARCHAR, node_id VARCHAR, action VARCHAR, "
                    "source VARCHAR, idempotency_key VARCHAR, status VARCHAR, attempts INTEGER, "
                    "requested_at DATETIME, updated_at DATETIME, result_json TEXT, last_error VARCHAR)"
                )
            create_db_and_tables(legacy_engine)
            columns = {column["name"] for column in inspect(legacy_engine).get_columns("scenario_node_operations")}
            self.assertIn("precondition_json", columns)
        finally:
            legacy_engine.dispose()

    def test_existing_protocol_trace_cursor_gets_durable_queue_columns(self) -> None:
        legacy_engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        try:
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE scenario_protocol_trace_cursors ("
                    "scenario_id VARCHAR(36) NOT NULL, node_id VARCHAR(128) NOT NULL, "
                    "last_timestamp VARCHAR(40) NOT NULL DEFAULT '', same_timestamp_hashes_json TEXT NOT NULL DEFAULT '[]', "
                    "PRIMARY KEY (scenario_id, node_id))"
                )
            create_db_and_tables(legacy_engine)
            columns = {column["name"] for column in inspect(legacy_engine).get_columns(
                "scenario_protocol_trace_cursors")}
            self.assertIn("dropped_events_seen", columns)
            self.assertIn("last_queue_id", columns)
        finally:
            legacy_engine.dispose()

    @staticmethod
    def payload() -> dict:
        return {
            "name": "linea de prueba",
            "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {
                    "node_id": "plc-1",
                    "device_type": "plc-s7",
                    "name": "PLC principal",
                    "ip": "172.30.10.10",
                    "config": {
                        "components": [
                            {"type": "valve", "name": "entrada", "db": 1},
                            {
                                "type": "analog_sensor",
                                "name": "presion",
                                "db": 1,
                                "max_value": 10,
                                "simulation": {
                                    "mode": "sine_wave",
                                    "period_seconds": 30,
                                },
                            },
                        ]
                    },
                }
            ],
        }

    def test_crud_does_not_expose_offsets(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario = created.json()
        scenario_id = scenario["id"]
        self.assertNotIn("byte_offset", str(scenario))

        listed = self.client.get("/api/scenarios")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        updated_payload = self.payload()
        updated_payload["name"] = "linea actualizada"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=updated_payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "linea actualizada")

        deleted = self.client.delete(f"/api/scenarios/{scenario_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").status_code, 404)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(ScenarioOperationLease, scenario_id))
        archived_audit = self.client.get(f"/api/scenarios/{scenario_id}/audit-events")
        self.assertEqual(archived_audit.status_code, 200, archived_audit.text)
        self.assertIn("scenario.deleted", {event["event_type"] for event in archived_audit.json()["events"]})
        self.assertTrue(self.client.get(f"/api/scenarios/{scenario_id}/audit-events/integrity").json()["valid"])

    def test_deploy_status_and_stop_lifecycle(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]

        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)
        self.assertEqual(deployed.json()["status"], "running")
        self.assertEqual(len(deployed.json()["containers"]), 1)
        self.assertEqual(deployed.json()["deployment_duration_seconds"], 1.234)
        from models.scenario import ScenarioRecord
        with Session(self.engine) as database:
            stored = database.get(ScenarioRecord, scenario_id)
            plan = stored.runtime_devices()
        self.assertRegex(plan[0]["image_id"], r"^sha256:[a-f0-9]{64}$")
        self.assertRegex(plan[0]["image_pin"], r"^anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}$")
        bundle = self.client.get(f"/api/scenarios/{scenario_id}/deployment-bundle")
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
        plc_service = next(service for service in compose["services"].values()
                           if service.get("labels", {}).get("anvil.bundle_node_id") == "plc-1")
        self.assertEqual(plc_service["image"], plan[0]["image_pin"])

        duplicate = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(duplicate.status_code, 409)

        current = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(current.json()["status"], "running")

        stopped = self.client.post(f"/api/scenarios/{scenario_id}/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["status"], "stopped")

    def test_deploy_revalidates_stored_topology_before_touching_docker(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        with Session(self.engine) as database:
            stored = database.get(ScenarioRecord, scenario_id)
            devices = json.loads(stored.devices_json)
            devices[0]["ip"] = "172.30.11.10"  # Outside the configured subnet.
            stored.devices_json = json.dumps(devices)
            database.add(stored)
            database.commit()

        response = self.client.post(f"/api/scenarios/{scenario_id}/deploy")

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("no supera la validación", response.json()["detail"]["message"])
        self.assertTrue(response.json()["detail"]["errors"])
        self.assertNotIn(scenario_id, self.manager.running)
        with Session(self.engine) as database:
            stored = database.get(ScenarioRecord, scenario_id)
            self.assertEqual(stored.status, "stopped")

    def test_budget_rejected_deploy_can_be_cleaned_and_retried(self) -> None:
        from services.docker_manager import DockerManagerError

        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.manager.deploy = Mock(side_effect=DockerManagerError(
            "Scenario CPU limit total 15000000000 nanocpus exceeds configured budget 12000000000 nanocpus"
        ))
        rejected = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("CPU limit total", rejected.json()["detail"])

        failed_state = self.client.get(f"/api/scenarios/{scenario_id}")
        self.assertEqual(failed_state.status_code, 200)
        self.assertEqual(failed_state.json()["status"], "error")
        observed = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(observed.status_code, 200, observed.text)
        self.assertEqual(observed.json()["status"], "error")
        self.assertEqual([item["status"] for item in observed.json()["containers"]], ["missing"])
        self.assertEqual(self.manager.running, {})

        stopped = self.client.post(f"/api/scenarios/{scenario_id}/stop")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["status"], "stopped")

        self.manager.deploy = FakeDockerManager.deploy.__get__(self.manager, FakeDockerManager)
        retried = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["status"], "running")

    def test_node_runtime_audit_records_only_observed_state_transitions(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)

        first = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(first.status_code, 200, first.text)
        self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.manager.running[scenario_id][0]["status"] = "exited"
        failed = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(failed.status_code, 200, failed.text)
        self.manager.running[scenario_id][0]["status"] = "running"
        recovered = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.manager.running[scenario_id][0].update(health_status="unhealthy", health_error="probe failed")
        degraded_health = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(degraded_health.status_code, 200, degraded_health.text)
        self.client.get(f"/api/scenarios/{scenario_id}/status")

        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        transitions = [event for event in events if event["event_type"] == "scenario.node_state_changed"]
        self.assertEqual([event["details"]["status"] for event in transitions], ["running", "running", "exited", "running"])
        self.assertEqual(transitions[0]["details"]["previous_status"], "running")
        self.assertEqual(transitions[1]["details"]["previous_status"], "exited")
        self.assertEqual(transitions[2]["details"]["previous_status"], "running")
        self.assertIsNone(transitions[3]["details"]["previous_status"])
        self.assertEqual(transitions[0]["details"]["health_error"], "probe failed")
        self.assertEqual(transitions[0]["details"]["health_status"], "unhealthy")

    def test_status_reports_a_missing_planned_node_while_other_nodes_keep_running(self) -> None:
        payload = self.payload()
        second = json.loads(json.dumps(payload["devices"][0]))
        second.update(node_id="plc-2", name="PLC respaldo", ip="172.30.10.11")
        payload["devices"].append(second)
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)
        self.manager.running[scenario_id] = [
            item for item in self.manager.running[scenario_id] if item["node_id"] == "plc-2"
        ]

        response = self.client.get(f"/api/scenarios/{scenario_id}/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "partial")
        states = {item["node_id"]: item["status"] for item in response.json()["containers"]}
        self.assertEqual(states, {"plc-2": "running", "plc-1": "missing"})
        missing = next(item for item in response.json()["containers"] if item["node_id"] == "plc-1")
        self.assertEqual(missing["resource_metrics_status"], "missing")
        self.assertIsNone(missing["resource_metrics_sampled_at"])
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").json()["status"], "partial")

    def test_status_exposes_resource_metric_quality_and_sample_time(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        sampled_at = datetime.now(timezone.utc).isoformat()
        self.manager.running[scenario_id][0].update(
            cpu_percent=2.5, memory_usage_bytes=1024, memory_limit_bytes=4096, pids=3,
            resource_metrics_status="good", resource_metrics_sampled_at=sampled_at,
        )

        response = self.client.get(f"/api/scenarios/{scenario_id}/status")

        self.assertEqual(response.status_code, 200, response.text)
        container = response.json()["containers"][0]
        self.assertEqual(container["resource_metrics_status"], "good")
        self.assertEqual(container["cpu_percent"], 2.5)
        self.assertEqual(container["memory_usage_bytes"], 1024)
        self.assertEqual(container["pids"], 3)
        self.assertIsNotNone(container["resource_metrics_sampled_at"])

    def test_node_lifecycle_is_scoped_to_deployed_scenario_and_audited(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        with patch("routes.scenarios.flush_protocol_traces_best_effort", return_value=0) as collect:
            response = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "restart"})
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs["scenario_id"] == scenario_id
                            and call.kwargs["target_node_id"] == "plc-1" for call in collect.call_args_list))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["containers"][0]["status"], "running")
        paused_node = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "pause"})
        self.assertEqual(paused_node.status_code, 200, paused_node.text)
        self.assertEqual(paused_node.json()["status"], "paused")
        resumed_node = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "resume"})
        self.assertEqual(resumed_node.status_code, 200, resumed_node.text)
        self.assertEqual(resumed_node.json()["status"], "running")
        self.assertTrue(any(event["event_type"] == "scenario.node_restart" for event in self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]))
        stopped = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "stop"})
        self.assertEqual(stopped.status_code, 200)
        invalid = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "delete"})
        self.assertEqual(invalid.status_code, 422)
        journal = self.client.get(f"/api/scenarios/{scenario_id}/node-operations")
        self.assertEqual(journal.status_code, 200, journal.text)
        self.assertEqual([item["action"] for item in journal.json()], ["stop", "resume", "pause", "restart"])
        self.assertTrue(all(item["status"] == "completed" for item in journal.json()))

    def test_scenario_restart_restarts_all_nodes_flushes_protocol_audit_and_records_event(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        with patch("routes.scenarios.flush_protocol_traces_best_effort", return_value=0) as collect:
            response = self.client.post(f"/api/scenarios/{scenario_id}/restart")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "running")
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs["scenario_id"] == scenario_id for call in collect.call_args_list))
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertTrue(any(event["event_type"] == "scenario.restarted" for event in events))

    def test_scenario_restart_requires_active_runtime(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        response = self.client.post(f"/api/scenarios/{scenario_id}/restart")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Restart requires a running or partial scenario", response.text)

    def test_scenario_restart_failure_persists_observed_partial_status(self) -> None:
        from services.docker_manager import DockerManagerError

        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        actual = {"scenario_id": scenario_id, "status": "partial", "containers": [
            {"node_id": "plc-1", "name": "anvil-plc-1", "status": "running"},
        ]}
        with patch.object(self.manager, "restart_scenario", side_effect=DockerManagerError("healthcheck failed")), \
             patch.object(self.manager, "status", return_value=actual):
            response = self.client.post(f"/api/scenarios/{scenario_id}/restart")
            observed = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("healthcheck failed", response.text)
        self.assertEqual(observed.json()["status"], "partial")
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertTrue(any(event["event_type"] == "scenario.restart_failed" for event in events))

    def test_missing_node_can_be_recreated_from_persisted_runtime_plan_and_audited(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        self.manager.running[scenario_id].clear()

        with patch("routes.scenarios.flush_protocol_traces_best_effort", return_value=0) as collect:
            response = self.client.post(f"/api/scenarios/{scenario_id}/nodes/plc-1/lifecycle", json={"action": "recreate"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs["scenario_id"] == scenario_id
                            and call.kwargs["target_node_id"] == "plc-1" for call in collect.call_args_list))
        self.assertEqual(response.json()["status"], "running")
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertTrue(any(event["event_type"] == "scenario.node_recreate" for event in events))
        journal = self.client.get(f"/api/scenarios/{scenario_id}/node-operations").json()
        self.assertEqual(journal[0]["action"], "recreate")
        self.assertEqual(journal[0]["status"], "completed")

    def test_scenario_pause_resume_updates_status_and_audit(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        paused = self.client.post(f"/api/scenarios/{scenario_id}/pause")
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["status"], "paused")
        self.assertEqual(paused.json()["containers"][0]["status"], "paused")
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}/status").json()["status"], "paused")
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/pause").status_code, 409)
        resumed = self.client.post(f"/api/scenarios/{scenario_id}/resume")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "running")
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/resume").status_code, 409)
        event_types = {event["event_type"] for event in self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]}
        self.assertTrue({"scenario.paused", "scenario.resumed"}.issubset(event_types))

    def test_packet_capture_start_and_pcap_export_are_audited(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)

        started = self.client.post(f"/api/scenarios/{scenario_id}/captures", json={"node_id": "plc-1"})

        self.assertEqual(started.status_code, 202, started.text)
        capture_id = started.json()["capture_id"]
        exported = self.client.get(f"/api/scenarios/{scenario_id}/captures/{capture_id}/pcap")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.headers["content-type"], "application/vnd.tcpdump.pcap")
        self.assertTrue(exported.content.startswith(b"\xd4\xc3\xb2\xa1"))
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertTrue(any(item["event_type"] == "network.packet_capture_started" for item in events))
        self.assertTrue(any(item["event_type"] == "network.packet_capture_exported" for item in events))

    def test_packet_capture_supports_zone_scope_and_rejects_ambiguous_target(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/captures", json={}).status_code, 422)
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/captures",
                                          json={"node_id": "plc-1", "zone_id": "default"}).status_code, 422)
        started = self.client.post(f"/api/scenarios/{scenario_id}/captures", json={"zone_id": "default"})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["scope"], "segment")
        self.assertEqual(started.json()["zone_id"], "default")

    def test_packet_capture_supports_a_distinct_device_pair(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]
        self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        endpoint = f"/api/scenarios/{scenario_id}/captures"
        self.assertEqual(self.client.post(endpoint, json={"link_source_node_id": "plc-1"}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, json={"link_source_node_id": "plc-1", "link_target_node_id": "plc-1"}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, json={"node_id": "plc-1", "link_source_node_id": "plc-1", "link_target_node_id": "plc-2"}).status_code, 422)
        started = self.client.post(endpoint, json={"link_source_node_id": "plc-1", "link_target_node_id": "plc-2"})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["scope"], "link")
        self.assertEqual(started.json()["source_node_id"], "plc-1")
        self.assertEqual(started.json()["target_node_id"], "plc-2")

    def test_rejects_device_outside_scenario_network(self) -> None:
        payload = self.payload()
        payload["devices"][0]["ip"] = "172.31.10.10"

        response = self.client.post("/api/scenarios", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertIn("outside", response.text)

    def test_persists_canvas_topology_and_defaults_for_legacy_payload(self) -> None:
        payload = self.payload()
        payload["canvas"] = {
            "data": {
                "1": {
                    "id": "1",
                    "name": "plc-s7",
                    "data": payload["devices"][0],
                    "pos_x": 340,
                    "pos_y": 210,
                    "inputs": {},
                    "outputs": {},
                }
            }
        }
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").json()["canvas"], payload["canvas"])

        legacy = self.payload()
        created_legacy = self.client.post("/api/scenarios", json=legacy)
        self.assertEqual(created_legacy.status_code, 201, created_legacy.text)
        self.assertEqual(created_legacy.json()["canvas"], {})

    def test_scenario_export_import_and_clone_preserve_canvas(self) -> None:
        payload = self.payload()
        payload["canvas"] = {"data": {"1": {"id": "1", "pos_x": 410, "pos_y": 250}}}
        created = self.client.post("/api/scenarios", json=payload)
        scenario_id = created.json()["id"]

        exported = self.client.get(f"/api/scenarios/{scenario_id}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()["schema_version"], 2)
        self.assertEqual(exported.json()["scenario"]["canvas"], payload["canvas"])

        imported = self.client.post("/api/scenarios/import", json=exported.json())
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertNotEqual(imported.json()["id"], scenario_id)
        self.assertEqual(imported.json()["canvas"], payload["canvas"])

        cloned = self.client.post(f"/api/scenarios/{scenario_id}/clone", json={})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        self.assertNotEqual(cloned.json()["id"], scenario_id)
        self.assertEqual(cloned.json()["canvas"], payload["canvas"])
        self.assertEqual(cloned.json()["devices"], created.json()["devices"])

    def test_device_profile_exports_resolved_s7_addresses_and_imports_validated_config(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        exported = self.client.get(f"/api/scenarios/{scenario_id}/devices/plc-1/profile")
        self.assertEqual(exported.status_code, 200, exported.text)
        profile = exported.json()
        self.assertEqual(profile["format"], "anvil-device-profile")
        self.assertEqual(profile["schema_version"], 1)
        self.assertEqual(profile["protocol"], "S7comm")
        self.assertIn("DB1.DBX0.0", {point["address"] for point in profile["resolved_mapping"]})
        self.assertIn("DB1.DBD2", {point["address"] for point in profile["resolved_mapping"]})

        imported = self.client.post(f"/api/scenarios/{scenario_id}/devices/import", json={
            "profile": profile, "node_id": "plc-imported", "ip": "172.30.10.11", "zone_id": "default",
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        imported_device = next(item for item in imported.json()["devices"] if item["node_id"] == "plc-imported")
        self.assertEqual(imported_device["config"], profile["config"])
        imported_profile = self.client.get(f"/api/scenarios/{scenario_id}/devices/plc-imported/profile")
        self.assertEqual(imported_profile.status_code, 200, imported_profile.text)
        self.assertEqual(imported_profile.json()["resolved_mapping"], profile["resolved_mapping"])

    def test_device_profile_import_rejects_unknown_version_and_mismatched_mapping_is_ignored(self) -> None:
        scenario = self.client.post("/api/scenarios", json=self.payload()).json()
        profile = self.client.get(f"/api/scenarios/{scenario['id']}/devices/plc-1/profile").json()
        invalid = {**profile, "schema_version": 2}
        rejected = self.client.post(f"/api/scenarios/{scenario['id']}/devices/import", json={
            "profile": invalid, "node_id": "plc-copy", "ip": "172.30.10.11",
        })
        self.assertEqual(rejected.status_code, 422, rejected.text)

        forged_mapping = {**profile, "resolved_mapping": [{"address": "DB999.DBX999.0", "access": "read_write"}]}
        imported = self.client.post(f"/api/scenarios/{scenario['id']}/devices/import", json={
            "profile": forged_mapping, "node_id": "plc-copy", "ip": "172.30.10.11",
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        actual = self.client.get(f"/api/scenarios/{scenario['id']}/devices/plc-copy/profile").json()
        self.assertNotEqual(actual["resolved_mapping"], forged_mapping["resolved_mapping"])

    def test_modbus_device_profile_preserves_resolved_addresses_and_access(self) -> None:
        payload = self.payload()
        payload["devices"] = [{
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "Modbus",
            "ip": "172.30.10.10", "config": {"unit_id": 7, "port": 1502, "points": [
                {"type": "coil", "name": "Run", "initial_value": False},
                {"type": "discrete_input", "name": "Ready", "initial_value": True},
                {"type": "holding_register", "name": "Setpoint", "initial_value": 42},
                {"type": "input_register", "name": "Pressure", "initial_value": 19},
            ]},
        }]
        scenario = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(scenario.status_code, 201, scenario.text)
        scenario_id = scenario.json()["id"]
        profile = self.client.get(f"/api/scenarios/{scenario_id}/devices/mb-1/profile").json()
        mapping = {item["component_name"]: item for item in profile["resolved_mapping"]}
        self.assertEqual((mapping["Run"]["address"], mapping["Run"]["access"]), ("coil:0", "read_write"))
        self.assertEqual((mapping["Ready"]["address"], mapping["Ready"]["access"]), ("discrete_input:0", "read"))
        self.assertEqual((mapping["Setpoint"]["address"], mapping["Setpoint"]["access"]), ("holding_register:0", "read_write"))
        imported = self.client.post(f"/api/scenarios/{scenario_id}/devices/import", json={
            "profile": profile, "node_id": "mb-copy", "ip": "172.30.10.11",
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        copied = self.client.get(f"/api/scenarios/{scenario_id}/devices/mb-copy/profile").json()
        self.assertEqual(copied["resolved_mapping"], profile["resolved_mapping"])

    def test_device_profile_import_requires_a_stopped_scenario(self) -> None:
        scenario = self.client.post("/api/scenarios", json=self.payload()).json()
        scenario_id = scenario["id"]
        profile = self.client.get(f"/api/scenarios/{scenario_id}/devices/plc-1/profile").json()
        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)
        imported = self.client.post(f"/api/scenarios/{scenario_id}/devices/import", json={
            "profile": profile, "node_id": "plc-copy", "ip": "172.30.10.11",
        })
        self.assertEqual(imported.status_code, 409, imported.text)

    def test_scenario_import_rejects_unknown_fields_and_invalid_versions(self) -> None:
        payload = self.payload()
        envelope = {"format": "anvil-scenario", "schema_version": 2, "scenario": payload}

        unknown_envelope_field = {**envelope, "future_option": True}
        response = self.client.post("/api/scenarios/import", json=unknown_envelope_field)
        self.assertEqual(response.status_code, 422, response.text)

        unknown_scenario_field = {**envelope, "scenario": {**payload, "mystery": "must not disappear"}}
        response = self.client.post("/api/scenarios/import", json=unknown_scenario_field)
        self.assertEqual(response.status_code, 422, response.text)

        boolean_version = {**envelope, "schema_version": True}
        response = self.client.post("/api/scenarios/import", json=boolean_version)
        self.assertEqual(response.status_code, 422, response.text)

        future_version = {**envelope, "schema_version": 3}
        response = self.client.post("/api/scenarios/import", json=future_version)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("Unsupported Anvil scenario schema version 3", response.json()["detail"])

    def test_scenario_schema_v1_migrates_network_topology_to_v2(self) -> None:
        legacy_topology = self.payload()
        legacy_topology.pop("network_connections", None)
        envelope = {"format": "anvil-scenario", "schema_version": 1, "scenario": legacy_topology}
        response = self.client.post("/api/scenarios/import", json=envelope)
        self.assertEqual(response.status_code, 201, response.text)
        imported = self.client.get(f"/api/scenarios/{response.json()['id']}")
        self.assertEqual(imported.json()["network_connections"], [])
        self.assertNotIn("network_connections", envelope["scenario"], "migration must not rewrite its source document")

    def test_legacy_scenario_import_keeps_defaults_but_rejects_unknown_fields(self) -> None:
        legacy_payload = self.payload()
        response = self.client.post("/api/scenarios/import", json=legacy_payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["network_connections"], [])

        malformed_legacy_payload = {**legacy_payload, "unrecognized": 1}
        response = self.client.post("/api/scenarios/import", json=malformed_legacy_payload)
        self.assertEqual(response.status_code, 422, response.text)

    def test_network_connections_persist_across_export_clone_and_legacy_update(self) -> None:
        payload = self.payload()
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.11",
            "config": {"role": "hmi", "summary": "Panel"},
        })
        payload["network_connections"] = [{"connection_id": "ethernet-1", "source_node_id": "plc-1", "target_node_id": "hmi-1", "label": "Ethernet · lógico"}]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        expected = payload["network_connections"]
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").json()["network_connections"], expected)
        exported = self.client.get(f"/api/scenarios/{scenario_id}/export").json()
        self.assertEqual(exported["scenario"]["network_connections"], expected)
        imported = self.client.post("/api/scenarios/import", json=exported)
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(imported.json()["network_connections"], expected)
        cloned = self.client.post(f"/api/scenarios/{scenario_id}/clone", json={})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        self.assertEqual(cloned.json()["network_connections"], expected)
        legacy_payload = self.payload()
        legacy_payload["devices"].append(payload["devices"][1])
        legacy_payload["name"] = "Actualización de cliente anterior"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=legacy_payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["network_connections"], expected)
        revisions = self.client.get(f"/api/scenarios/{scenario_id}/revisions")
        self.assertEqual(revisions.status_code, 200, revisions.text)
        revision = self.client.get(f"/api/scenarios/{scenario_id}/revisions/{revisions.json()[0]['id']}")
        self.assertEqual(revision.json()["snapshot"]["network_connections"], expected)

    def test_generic_tcp_route_builds_and_exposes_only_its_configured_endpoint(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "TCP policy test"},
        })
        payload["network_routes"] = [{"route_id": "generic-tcp", "source_node_id": "hmi-1",
            "target_node_id": "plc-1", "protocol": "tcp", "tcp_port": 102, "latency_ms": 250}]
        for invalid_latency in (-1, 10001, True, 1.5, "250"):
            invalid_payload = json.loads(json.dumps(payload))
            invalid_payload["network_routes"][0]["latency_ms"] = invalid_latency
            rejected = self.client.post("/api/scenarios", json=invalid_payload)
            self.assertEqual(rejected.status_code, 422, (invalid_latency, rejected.text))
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        endpoint = self.client.get(f"/api/scenarios/{scenario_id}/network-routes")
        self.assertEqual(endpoint.status_code, 200, endpoint.text)
        route = endpoint.json()[0]
        self.assertTrue(route["enabled"])
        self.assertEqual(route["latency_ms"], 250)
        self.assertEqual(route["target_port"], 102)
        self.assertEqual(route["listen_host"], "172.30.11.2")
        self.assertTrue(1024 <= route["listen_port"] <= 65535)
        record = self.client.get(f"/api/scenarios/{scenario_id}/deployment-bundle")
        self.assertEqual(record.status_code, 200, record.text)
        with zipfile.ZipFile(io.BytesIO(record.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            services = compose["services"].values()
            gateway = next(service for service in services if service.get("labels", {}).get("anvil.role") == "tcp-gateway")
            self.assertEqual(gateway["labels"]["anvil.route_protocol"], "tcp")
            self.assertEqual(gateway["labels"]["anvil.route_target_port"], "102")
            self.assertEqual(gateway["labels"]["anvil.route_latency_ms"], "250")
            gateway_env = gateway["environment"]
            gateway_config = json.loads(gateway_env["ANVIL_TCP_GATEWAY_CONFIG"])
            self.assertEqual(gateway_config["latency_ms"], 250)
            self.assertIsNone(gateway_config["inspect_protocol"])
            self.assertNotIn("ports", gateway)
            imported_bundle = json.loads(archive.read("scenario.json"))["scenario"]
            self.assertEqual(imported_bundle["network_routes"][0]["tcp_port"], 102)
        payload["network_routes"][0]["enabled"] = False
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        blocked_endpoint = self.client.get(f"/api/scenarios/{scenario_id}/network-routes").json()[0]
        self.assertFalse(blocked_endpoint["enabled"])
        self.assertIsNone(blocked_endpoint["listen_host"])
        blocked_bundle = self.client.get(f"/api/scenarios/{scenario_id}/deployment-bundle")
        with zipfile.ZipFile(io.BytesIO(blocked_bundle.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
        self.assertFalse(any(service.get("labels", {}).get("anvil.role") == "tcp-gateway"
                             for service in compose["services"].values()))
        diagnostics = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics")
        self.assertEqual(diagnostics.status_code, 200, diagnostics.text)
        warning = next(item for item in diagnostics.json()["warnings"]
                       if item["code"] == "network_route_blocked")
        self.assertEqual(warning["node_id"], "hmi-1")
        self.assertIn("proxy no se incluirá", warning["message"])

    def test_tcp_route_to_modbus_service_is_marked_for_protocol_inspection(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0] = {
            "node_id": "modbus-1", "device_type": "modbus-tcp", "name": "Servidor Modbus",
            "ip": "172.30.10.10", "zone_id": "control",
            "config": {"port": 502, "unit_id": 7, "points": [
                {"type": "holding_register", "name": "presion", "address": 4, "initial_value": 123},
            ]},
        }
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "Modbus route test"},
        })
        payload["network_routes"] = [{
            "route_id": "hmi-modbus", "source_node_id": "hmi-1", "target_node_id": "modbus-1",
            "protocol": "tcp", "tcp_port": 502,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        with Session(self.engine) as session:
            from models.scenario import ScenarioRecord
            record = session.get(ScenarioRecord, created.json()["id"])
            gateway = next(item for item in record.runtime_devices()
                           if item["device_type"] == "tcp-gateway")
            self.assertEqual(gateway["config"]["inspect_protocol"], "modbus-tcp")
        bundle = self.client.get(f"/api/scenarios/{created.json()['id']}/deployment-bundle")
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            gateway = next(service for service in compose["services"].values()
                           if service.get("labels", {}).get("anvil.role") == "tcp-gateway")
            gateway_config = json.loads(gateway["environment"]["ANVIL_TCP_GATEWAY_CONFIG"])
            self.assertEqual(gateway_config["inspect_protocol"], "modbus-tcp")
            self.assertNotIn("ports", gateway)

    def test_diagnostics_warn_when_an_s7_route_is_blocked(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "S7 route warning", "target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{"route_id": "hmi-plc", "source_node_id": "hmi-1",
            "target_node_id": "plc-1", "protocol": "s7", "enabled": False}]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        warning = next(item for item in response.json()["warnings"]
                       if item["code"] == "network_route_blocked")
        self.assertEqual(warning["node_id"], "hmi-1")
        self.assertIn("ruta S7", warning["message"])
        self.assertIn("no podrá comunicarse", warning["message"])
        unreachable = next(item for item in response.json()["warnings"]
                           if item["code"] == "device_target_unreachable")
        self.assertEqual(unreachable["node_id"], "hmi-1")
        self.assertIn("habilita la ruta S7", unreachable["message"])

    def test_generic_tcp_route_does_not_satisfy_hmi_protocol_target_reachability(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "HMI cross-zone", "target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{"route_id": "generic-port-102", "source_node_id": "hmi-1",
            "target_node_id": "plc-1", "protocol": "tcp", "tcp_port": 102}]
        response = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        report = self.client.get(f"/api/scenarios/{response.json()['id']}/diagnostics")
        warning = next(item for item in report.json()["warnings"]
                       if item["code"] == "device_target_unreachable")
        self.assertIn("TCP genérica no configura el cliente", warning["message"])
        self.assertIn("sustituye la ruta TCP genérica por una ruta S7", warning["message"])

    def test_diagnostics_explains_blocked_cross_zone_field_signal(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "field", "name": "Field", "purdue_level": "L0", "subnet": "172.30.11.0/24"},
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"][0]["config"]["components"].append({
            "type": "io_channel", "channel_id": "field-level", "name": "Field level",
            "direction": "input", "data_type": "REAL", "unit": "L",
        })
        payload["field_objects"] = [{"object_id": "field-level", "type": "analog_sensor", "name": "Field level",
                                     "unit": "L", "min_value": 0, "max_value": 100, "zone_id": "field"}]
        payload["signal_connections"] = [{
            "connection_id": "field-level-link",
            "source": {"kind": "field", "object_id": "field-level", "port": "value"},
            "target": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBD0", "channel_id": "field-level"},
            "route_enabled": False,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        report = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")
        self.assertEqual(report.status_code, 200, report.text)
        warning = next(item for item in report.json()["warnings"]
                       if item["code"] == "field_signal_route_blocked")
        self.assertEqual(warning["node_id"], "plc-1")
        self.assertIn("habilita la ruta al runtime de campo", warning["message"])

    def test_deployed_diagnostics_probes_enabled_cross_zone_field_http_route(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "field", "name": "Field", "purdue_level": "L0", "subnet": "172.30.11.0/24"},
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"][0]["config"]["components"].append({
            "type": "io_channel", "channel_id": "field-level", "name": "Field level",
            "direction": "input", "data_type": "REAL", "unit": "%",
        })
        payload["field_objects"] = [{"object_id": "field-level", "type": "analog_sensor", "name": "Field level",
                                     "unit": "%", "min_value": 0, "max_value": 100, "zone_id": "field"}]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        io_map = self.client.get(f"/api/scenarios/{scenario_id}/io-map").json()["points"]
        point = next(item for item in io_map if item["component_name"] == "Field level")
        payload["signal_connections"] = [{
            "connection_id": "field-level-link",
            "source": {"kind": "field", "object_id": "field-level", "port": "value"},
            "target": {"kind": "device", "node_id": "plc-1", "address": point["address"], "channel_id": "field-level"},
            "route_enabled": True,
        }]
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)

        report = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics").json()

        probe = next(item for item in self.manager.route_probes if item["protocol"] == "field-http")
        self.assertEqual(probe["source_node_id"], "plc-1")
        self.assertEqual(probe["target_config"]["objects"][0]["object_id"], "field-level")
        self.assertEqual(report["runtime"]["routes"][0]["protocol"], "field-http")
        self.assertEqual(report["runtime"]["routes"][0]["status"], "reachable")
        field_binding_route = next(item for item in report["runtime"]["routes"] if item["protocol"] == "field-binding")
        self.assertEqual(field_binding_route["bindings"][0]["status"], "good")
        self.assertEqual(len(self.manager.field_binding_probes), 1)
        self.assertTrue(report["runtime"]["protocol_transactions_verified"])

        self.manager.field_binding_result = {
            "status": "unreachable", "detail": "1/1 field bindings fresh; not ready: DB1.DBD8",
            "bindings": [{"address": "DB1.DBD8", "status": "stale", "age_ms": 3001}],
        }
        failed_report = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics").json()
        warning = next(item for item in failed_report["warnings"] if item["code"] == "runtime_route_unreachable")
        self.assertEqual(warning["node_id"], "plc-1")
        self.assertIn("DB1.DBD8", warning["message"])
        self.assertFalse(failed_report["runtime"]["protocol_transactions_verified"])

    def test_deployment_bundle_contains_hardened_compose_and_importable_scenario(self) -> None:
        payload = self.payload()
        payload["network"] = {
            "subnet": "172.30.12.0/24",
            "zones": [
                {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
                {"zone_id": "operations", "name": "Operaciones", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
            ],
        }
        payload["devices"][0]["zone_id"] = "control"
        payload["devices"][0]["hostname"] = "main-control-plc"
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI de prueba",
            "ip": "172.30.11.11", "zone_id": "operations",
            "config": {"role": "hmi", "summary": "panel de prueba"},
        })
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/scenarios/{created.json()['id']}/deployment-bundle")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("application/zip", response.headers["content-type"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {
                "docker-compose.yml", "scenario.json", "README.txt", "images.txt",
                "IMAGE_LOCK.txt", "export-images.sh", "check-images.sh",
                ".env", "network-addresses.json", "readdress.py",
            })
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            exported = json.loads(archive.read("scenario.json"))
            image_manifest = set(archive.read("images.txt").decode().splitlines())
            export_script = archive.read("export-images.sh").decode()
            check_script = archive.read("check-images.sh").decode()
        service = compose["services"]["plc-1"]
        self.assertEqual(service["pull_policy"], "never")
        self.assertTrue(compose["networks"]["zone_control"]["internal"])
        self.assertTrue(compose["networks"]["zone_operations"]["internal"])
        self.assertEqual(compose["networks"]["zone_control"]["driver_opts"], {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
        })
        self.assertEqual(service["networks"]["zone_control"]["ipv4_address"],
                         "${ANVIL_BUNDLE_IP_CONTROL_PLC_1_2CAC3131:-172.30.10.10}")
        self.assertEqual(service["hostname"], "main-control-plc")
        self.assertEqual(service["networks"]["zone_control"]["aliases"], ["main-control-plc"])
        self.assertNotIn("container_name", service)
        self.assertNotIn("anvil.managed", service["labels"])
        self.assertNotIn("anvil.scenario_id", service["labels"])
        self.assertEqual(service["labels"]["anvil.bundle"], "true")
        self.assertEqual(service["labels"]["anvil.bundle_node_id"], "plc-1")
        self.assertEqual(service["user"], "65534:65534")
        self.assertTrue(service["read_only"])
        self.assertIn("command", compose["services"]["hmi-1"])
        self.assertEqual(exported["format"], "anvil-scenario")
        self.assertEqual(exported["scenario"]["name"], created.json()["name"])
        self.assertEqual(exported["scenario"]["devices"][0]["hostname"], "main-control-plc")
        compose_images = {service["image"] for service in compose["services"].values()}
        self.assertEqual(image_manifest, compose_images)
        for image in compose_images:
            self.assertIn(image, export_script)
        self.assertIn("docker image inspect", check_script)
        self.assertIn("actual_id", check_script)
        self.assertIn("anvil-images.lock", export_script)
        for script in (export_script, check_script):
            checked = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)

        # Exercise the handoff scripts without Docker: the target must accept
        # the exported image ID and reject the same tag if it resolves later.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "export-images.sh").write_text(export_script)
            (directory / "check-images.sh").write_text(check_script)
            mock_bin = directory / "bin"
            mock_bin.mkdir()
            docker_cli = mock_bin / "docker"
            docker_cli.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then\n"
                "  printf '%s\\n' \"${MOCK_IMAGE_ID:-sha256:expected}\"; exit 0\n"
                "fi\n"
                "if [ \"$1\" = save ]; then\n"
                "  shift; [ \"$1\" = -o ] || exit 2; : > \"$2\"; exit 0\n"
                "fi\n"
                "exit 2\n"
            )
            docker_cli.chmod(0o755)
            environment = {**os.environ, "PATH": f"{mock_bin}:{os.environ['PATH']}"}
            exported_images = subprocess.run(
                ["sh", str(directory / "export-images.sh")], cwd=directory,
                env=environment, text=True, capture_output=True,
            )
            self.assertEqual(exported_images.returncode, 0, exported_images.stderr)
            self.assertTrue((directory / "anvil-images.tar").is_file())
            self.assertEqual(
                subprocess.run(["sh", str(directory / "check-images.sh")], cwd=directory,
                               env=environment, text=True, capture_output=True).returncode,
                0,
            )
            mismatch = subprocess.run(
                ["sh", str(directory / "check-images.sh")], cwd=directory,
                env={**environment, "MOCK_IMAGE_ID": "sha256:changed"},
                text=True, capture_output=True,
            )
            self.assertEqual(mismatch.returncode, 1)
            self.assertIn("Image ID mismatch", mismatch.stderr)

    def test_deployment_bundle_exports_explicit_cross_zone_scada_route(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "scada-1", "device_type": "scada", "name": "SCADA", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{
            "route_id": "scada-plc", "source_node_id": "scada-1", "target_node_id": "plc-1",
            "protocol": "s7", "enabled": True,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/scenarios/{created.json()['id']}/deployment-bundle")
        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            exported = json.loads(archive.read("scenario.json"))["scenario"]
        route = next(service for service in compose["services"].values()
                     if service.get("labels", {}).get("anvil.role") == "tcp-gateway")
        self.assertEqual(set(route["networks"]), {"zone_control", "zone_operations"})
        self.assertEqual(exported["network_routes"][0]["latency_ms"], 0)
        expected_route = {**payload["network_routes"][0], "latency_ms": 0}
        self.assertEqual(exported["network_routes"], [expected_route])

    def test_egress_allowlist_survives_api_export_and_import(self) -> None:
        payload = self.payload()
        payload["network"]["egress_rules"] = [{
            "rule_id": "engineering-docs", "source_node_id": "station-1",
            "hostname": "docs.example.net", "tcp_port": 443, "enabled": True,
        }]
        payload["network"]["subnet"] = "172.30.10.0/23"
        payload["devices"].append({
            "node_id": "station-1", "device_type": "engineering-station", "name": "Engineering",
            "ip": "172.30.10.11", "config": {"role": "engineering", "summary": "station",
                                                   "engineering_access_enabled": True},
        })
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        exported = self.client.get(f"/api/scenarios/{scenario_id}/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()["scenario"]["network"]["egress_rules"], payload["network"]["egress_rules"])
        imported = self.client.post("/api/scenarios/import", json=exported.json())
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(imported.json()["network"]["egress_rules"], payload["network"]["egress_rules"])

    def test_topology_diagnostics_reports_actionable_incomplete_setup_warnings(self) -> None:
        payload = self.payload()
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI sin destinos",
            "ip": "172.30.10.11", "config": {"role": "hmi", "summary": "panel de prueba"},
        })
        payload["field_objects"] = [
            {"object_id": "v-1", "type": "valve", "name": "Válvula sin mando"},
            {"object_id": "t-1", "type": "tank", "name": "Tanque sin tuberías"},
            {"object_id": "split-1", "type": "flow_splitter", "name": "Distribuidor incompleto"},
            {"object_id": "mix-1", "type": "flow_mixer", "name": "Colector incompleto"},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["error_count"], 0)
        warning_codes = {item["code"] for item in report["warnings"]}
        self.assertTrue({
            "hmi_without_targets", "actuator_without_command", "tank_process_incomplete",
            "flow_splitter_incomplete", "flow_mixer_incomplete",
        }.issubset(warning_codes))

    def test_topology_diagnostics_warns_when_independent_actuator_has_no_plc_logic(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"].append({
            "type": "io_channel", "name": "EV-1 mando", "channel_id": "valve-command",
            "direction": "output", "data_type": "BOOL",
        })
        payload["field_objects"] = [{"object_id": "valve-1", "type": "valve", "name": "EV-1"}]
        payload["signal_connections"] = [{
            "connection_id": "plc-valve", "source": {"kind": "device", "node_id": "plc-1",
                "address": "DB1.DBX1.0", "channel_id": "valve-command"},
            "target": {"kind": "field", "object_id": "valve-1", "port": "command"},
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertTrue(report["valid"])
        warning = next(item for item in report["warnings"]
                       if item["code"] == "plc_output_without_control_logic")
        self.assertEqual(warning["node_id"], "plc-1")
        self.assertIn("solo responderá a mandos manuales", warning["message"])

    def test_topology_diagnostics_accepts_independent_actuator_driven_by_sequence(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"].extend([
            {"type": "io_channel", "name": "Inicio secuencia", "channel_id": "sequence-start",
             "direction": "input", "data_type": "BOOL"},
            {"type": "io_channel", "name": "Reinicio secuencia", "channel_id": "sequence-reset",
             "direction": "input", "data_type": "BOOL"},
            {"type": "io_channel", "name": "EV-1 mando", "channel_id": "valve-command",
             "direction": "output", "data_type": "BOOL"},
        ])
        payload["devices"][0]["config"]["sequences"] = [{
            "name": "Secuencia línea", "advance_component": "Inicio secuencia",
            "reset_component": "Reinicio secuencia", "steps": ["entrada", "EV-1 mando"],
        }]
        payload["field_objects"] = [{"object_id": "valve-1", "type": "valve", "name": "EV-1"}]
        payload["signal_connections"] = [{
            "connection_id": "plc-valve", "source": {"kind": "device", "node_id": "plc-1",
                "address": "DB1.DBX1.0", "channel_id": "valve-command"},
            "target": {"kind": "field", "object_id": "valve-1", "port": "command"},
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        warning_codes = {item["code"] for item in response.json()["warnings"]}
        self.assertNotIn("plc_output_without_control_logic", warning_codes)

    def test_topology_diagnostics_checks_each_legacy_actuator_output(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"].append({
            "type": "motor", "name": "motor sin lógica", "db": 2,
        })
        payload["devices"][0]["config"]["control_rules"] = [{
            "name": "control válvula", "input_component": "presion",
            "output_component": "entrada", "mode": "below", "setpoint": 5,
            "hysteresis": 0.5,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        warnings = [item for item in response.json()["warnings"]
                    if item["code"] == "plc_without_control_logic"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("motor sin lógica", warnings[0]["message"])

    def test_topology_diagnostics_accepts_connected_physical_process_endpoints(self) -> None:
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "tank-1", "type": "tank", "name": "Tanque"},
            {"object_id": "valve-1", "type": "valve", "name": "Válvula"},
        ]
        payload["process_connections"] = [
            {"connection_id": "tank-valve", "source": {"object_id": "tank-1", "port": "outlet"},
             "target": {"object_id": "valve-1", "port": "process_in"}},
            {"connection_id": "valve-tank", "source": {"object_id": "valve-1", "port": "process_out"},
             "target": {"object_id": "tank-1", "port": "inlet"}},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        warning_codes = {item["code"] for item in response.json()["warnings"]}
        self.assertNotIn("field_process_unconnected", warning_codes)
        self.assertNotIn("field_process_path_incomplete", warning_codes)

    def test_topology_diagnostics_detects_linked_but_disconnected_process_fragments(self) -> None:
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Bomba"},
            {"object_id": "feed", "type": "pipe", "name": "Tramo de impulsión"},
            {"object_id": "return", "type": "pipe", "name": "Tramo de retorno"},
            {"object_id": "tank", "type": "tank", "name": "Depósito"},
        ]
        payload["process_connections"] = [
            {"connection_id": "pump-feed", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "feed", "port": "process_in"}},
            {"connection_id": "return-tank", "source": {"object_id": "return", "port": "process_out"},
             "target": {"object_id": "tank", "port": "inlet"}},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertTrue(report["valid"])  # Incomplete engineering is advisory at preflight.
        path_warnings = [item for item in report["warnings"] if item["code"] == "field_process_path_incomplete"]
        self.assertEqual({item["node_id"] for item in path_warnings}, {"pump", "feed", "return", "tank"})
        self.assertTrue(all("camino continuo" in item["message"] or "llega" in item["message"] or
                            "recibe caudal" in item["message"] for item in path_warnings))

    def test_topology_diagnostics_accepts_complete_flow_splitter_and_mixer(self) -> None:
        payload = self.payload()
        payload["field_objects"] = [
            {"object_id": "pump", "type": "pump", "name": "Pump"},
            {"object_id": "splitter", "type": "flow_splitter", "name": "Distributor"},
            {"object_id": "mixer", "type": "flow_mixer", "name": "Collector"},
            {"object_id": "tank", "type": "tank", "name": "Receiver"},
        ]
        payload["process_connections"] = [
            {"connection_id": "feed", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "splitter", "port": "process_in"}},
            {"connection_id": "branch-a", "source": {"object_id": "splitter", "port": "process_out_a"},
             "target": {"object_id": "mixer", "port": "process_in_a"}},
            {"connection_id": "branch-b", "source": {"object_id": "splitter", "port": "process_out_b"},
             "target": {"object_id": "mixer", "port": "process_in_b"}},
            {"connection_id": "out", "source": {"object_id": "mixer", "port": "process_out"},
             "target": {"object_id": "tank", "port": "inlet"}},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        warning_codes = {item["code"] for item in response.json()["warnings"]}
        self.assertNotIn("flow_splitter_incomplete", warning_codes)
        self.assertNotIn("flow_mixer_incomplete", warning_codes)

    def test_diagnostics_includes_runtime_health_for_deployed_scenario(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        self.manager.running[scenario_id][0].update(
            status="unhealthy", health_status="unhealthy", health_error="S7 listener failed"
        )

        response = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics")

        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertTrue(report["runtime"]["checked"])
        self.assertFalse(report["runtime"]["protocol_transactions_verified"])
        warning = next(item for item in report["warnings"] if item["code"] == "runtime_node_unhealthy")
        self.assertEqual(warning["node_id"], "plc-1")
        self.assertIn("S7 listener failed", warning["message"])

    def test_diagnostics_reports_missing_runtime_node(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        self.manager.running[scenario_id].clear()

        report = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics").json()

        self.assertEqual(report["runtime"]["nodes"], [{"node_id": "plc-1", "status": "missing", "health_status": None}])
        self.assertIn("runtime_node_missing", {item["code"] for item in report["warnings"]})

    def test_deployed_diagnostics_probes_s7_route_from_declared_source(self) -> None:
        payload = self.payload()
        payload["network"] = {"subnet": "172.30.12.0/24", "zones": [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]}
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI",
            "ip": "172.30.11.10", "zone_id": "operations",
            "config": {"role": "hmi", "summary": "Route probe", "target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{
            "route_id": "hmi-plc", "source_node_id": "hmi-1",
            "target_node_id": "plc-1", "protocol": "s7", "enabled": True,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)

        report = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics").json()

        self.assertEqual(len(self.manager.route_probes), 1)
        self.assertEqual(self.manager.route_probes[0]["source_node_id"], "hmi-1")
        self.assertEqual(self.manager.route_probes[0]["protocol"], "s7")
        self.assertEqual(report["runtime"]["routes"][0]["status"], "reachable")
        self.assertTrue(report["runtime"]["protocol_transactions_verified"])

        self.manager.route_probe_result = {"status": "unreachable", "detail": "COTP connection refused"}
        failed_report = self.client.get(f"/api/scenarios/{scenario_id}/diagnostics").json()
        warning = next(item for item in failed_report["warnings"] if item["code"] == "runtime_route_unreachable")
        self.assertEqual(warning["node_id"], "hmi-1")
        self.assertIn("COTP connection refused", warning["message"])
        self.assertFalse(failed_report["runtime"]["protocol_transactions_verified"])

    def test_external_access_is_saved_as_an_explicit_opt_in(self) -> None:
        payload = self.payload()
        device = payload["devices"][0]
        device["external_access"] = True
        device["external_port"] = 1102
        response = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["devices"][0]["external_access"])

        default_payload = self.payload()
        default = self.client.post("/api/scenarios", json=default_payload)
        self.assertFalse(default.json()["devices"][0]["external_access"])

    def test_external_access_bundle_includes_restricted_protocol_ingress(self) -> None:
        payload = self.payload()
        payload["devices"][0].update(external_access=True, external_port=1102,
                                     external_host_ip="192.0.2.25")
        payload["devices"].append({
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "Modbus",
            "ip": "172.30.10.11", "external_access": True, "external_port": 2502,
            "external_host_ip": "192.0.2.26",
            "config": {"unit_id": 1, "port": 1502,
                       "points": [{"type": "coil", "name": "pump"}]},
        })
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        bundle = self.client.get(f"/api/scenarios/{created.json()['id']}/deployment-bundle")
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
        services = compose["services"]
        relays = [service for service in services.values()
                  if service["labels"].get("anvil.role") == "protocol-ingress"]
        self.assertEqual(len(relays), 2)
        self.assertEqual(len([service for service in services.values() if service.get("ports")]), 2)

        payload["devices"][0]["external_host_ip"] = "0.0.0.0"
        rejected = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(rejected.status_code, 422)

    def test_s7_catalog_components_are_validated_and_normalized(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"] = [
            {"type": "motor", "name": "bomba"},
            {"type": "digital_input", "name": "presostato"},
            {"type": "alarm", "name": "alarma"},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        points = self.client.get(f"/api/scenarios/{created.json()['id']}/io-map").json()["points"]
        self.assertEqual([point["component_type"] for point in points], ["motor", "digital_input", "alarm"])
        self.assertEqual([point["address"] for point in points], ["DB1.DBX0.0", "DB1.DBX0.1", "DB1.DBX0.2"])
        self.assertEqual([point["access"] for point in points], ["read_write", "read", "read"])

    def test_s7_io_map_persists_explicit_offsets_and_rejects_collisions(self) -> None:
        payload = self.payload()
        payload["devices"][0]["config"]["components"] = [
            {"type": "io_channel", "name": "Marker", "area": "M", "direction": "output",
             "data_type": "BOOL", "byte_offset": 4, "bit_offset": 3},
            {"type": "io_channel", "name": "Process output", "area": "Q", "direction": "output",
             "data_type": "REAL", "byte_offset": 6},
        ]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        points = self.client.get(f"/api/scenarios/{scenario_id}/io-map").json()["points"]
        by_name = {point["component_name"]: point for point in points}
        self.assertEqual(by_name["Marker"]["address"], "M4.3")
        self.assertEqual(by_name["Process output"]["address"], "QD6")

        payload["network"]["subnet"] = "172.30.88.0/24"
        payload["devices"][0]["node_id"] = "plc-collision"
        payload["devices"][0]["ip"] = "172.30.88.10"
        payload["devices"][0]["config"]["components"] = [
            {"type": "valve", "name": "Existing output"},
            {"type": "io_channel", "name": "Duplicate output", "data_type": "BOOL",
             "byte_offset": 0, "bit_offset": 0},
        ]
        rejected = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(rejected.status_code, 422, rejected.text)

    def test_unknown_device_type_is_rejected(self) -> None:
        payload = self.payload()
        payload["devices"][0]["device_type"] = "unknown-device"
        self.assertEqual(self.client.post("/api/scenarios", json=payload).status_code, 422)

    def test_device_catalog_and_virtual_scenario_configs(self) -> None:
        catalog = self.client.get("/api/scenarios/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertTrue({"hmi", "gateway", "firewall", "attacker-station", "engineering-station"}.issubset({item["type"] for item in catalog.json()}))
        by_type = {item["type"]: item for item in catalog.json()}
        self.assertEqual(by_type["plc-s7"]["protocols"], ["s7comm"])
        self.assertIn("scan-logic", by_type["plc-s7"]["capabilities"])
        self.assertEqual(by_type["modbus-tcp"]["protocols"], ["modbus-tcp", "modbus-rtu-virtual"])
        self.assertIn("coils", by_type["modbus-tcp"]["capabilities"])
        self.assertEqual(by_type["scada"]["protocols"], ["s7comm-client", "modbus-tcp-client"])
        self.assertEqual(by_type["hmi"]["protocols"], ["s7comm-client", "modbus-tcp-client"])
        self.assertIn("authorized-write", by_type["hmi"]["capabilities"])
        payload = {
            "name": "rango auxiliar", "network": {"subnet": "172.30.40.0/24"},
            "devices": [{
                "node_id": "diag", "device_type": "attacker-station", "name": "Estación", "ip": "172.30.40.10",
                "config": {"role": "attacker", "summary": "aislada"},
            }],
        }
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        runtime = __import__("models.scenario", fromlist=["ScenarioRecord"]).ScenarioRecord
        with Session(self.engine) as session:
            record = session.get(runtime, created.json()["id"])
            self.assertEqual(record.runtime_devices()[0]["network_mode"], "none")

    def test_scenario_service_inventory_uses_effective_protocol_ports(self) -> None:
        payload = self.payload()
        payload["devices"][0].update(external_access=True, external_port=1102)
        payload["devices"].extend([
            {
                "node_id": "modbus-tcp", "device_type": "modbus-tcp", "name": "Modbus TCP",
                "ip": "172.30.10.11", "config": {
                    "transport": "tcp", "port": 1502,
                    "points": [{"type": "coil", "name": "Marcha", "initial_value": False}],
                },
            },
            {
                "node_id": "modbus-rtu", "device_type": "modbus-tcp", "name": "Modbus RTU",
                "ip": "172.30.10.12", "config": {
                    "transport": "rtu", "port": 1503,
                    "points": [{"type": "coil", "name": "Marcha RTU", "initial_value": False}],
                },
            },
            {
                "node_id": "scada-1", "device_type": "scada", "name": "SCADA",
                "ip": "172.30.10.13", "config": {"target_node_ids": "plc-1"},
            },
        ])
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)

        response = self.client.get(f"/api/scenarios/{created.json()['id']}/services")

        self.assertEqual(response.status_code, 200, response.text)
        services = {item["node_id"]: item for item in response.json()}
        self.assertEqual(services["plc-1"]["protocol"], "s7comm")
        self.assertEqual(services["plc-1"]["transport"], "tcp")
        self.assertEqual(services["plc-1"]["port"], 102)
        self.assertEqual(services["plc-1"]["host_bind_ip"], "127.0.0.1")
        self.assertEqual(services["plc-1"]["host_port"], 1102)
        self.assertEqual(services["modbus-tcp"]["protocol"], "modbus-tcp")
        self.assertEqual(services["modbus-tcp"]["transport"], "tcp")
        self.assertEqual(services["modbus-tcp"]["port"], 1502)
        self.assertEqual(services["modbus-rtu"]["protocol"], "modbus-rtu")
        self.assertEqual(services["modbus-rtu"]["transport"], "tcp-raw")
        self.assertEqual(services["modbus-rtu"]["port"], 1503)
        self.assertEqual(services["scada-1"]["port"], 8766)
        self.assertEqual(services["scada-1"]["protocol"], "http")
        self.assertEqual(services["scada-1"]["scope"], "internal")

    def test_documented_device_extension_is_visible_and_creatable_through_api(self) -> None:
        class ExtensionConfig(BaseModel):
            station_id: int

        class ExtensionDevice(Device):
            label = "Extension test device"
            description = "Registered extension for API contract test"
            icon = "XT"
            config_model = ExtensionConfig
            component_types = frozenset()

            def build_runtime_device(self, device):
                return {"node_id": device.node_id, "device_type": "extension-test",
                        "name": device.name, "ip": device.ip,
                        "config": device.config.model_dump()}

            def build_container_config(self, scenario_id, runtime_device):
                return {"name": f"{scenario_id}-{runtime_device['node_id']}"}

        register_device("extension-test")(ExtensionDevice)
        try:
            catalog = self.client.get("/api/scenarios/catalog")
            self.assertEqual(catalog.status_code, 200)
            extension = next(item for item in catalog.json() if item["type"] == "extension-test")
            self.assertEqual(extension, {
                "type": "extension-test", "label": "Extension test device",
                "description": "Registered extension for API contract test", "icon": "XT",
                "protocols": [], "capabilities": [],
            })
            created = self.client.post("/api/scenarios", json={
                "name": "Extension contract", "network": {"subnet": "172.30.40.0/24"},
                "devices": [{"node_id": "extension-1", "device_type": "extension-test",
                             "name": "Extension", "ip": "172.30.40.10",
                             "config": {"station_id": 17}}],
            })
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["devices"][0]["config"], {"station_id": 17})
        finally:
            unregister_device("extension-test")

    def test_existing_scenario_table_gets_canvas_column_on_startup(self) -> None:
        SQLModel.metadata.drop_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE scenarios (id VARCHAR NOT NULL PRIMARY KEY, name VARCHAR NOT NULL, "
                "network_json VARCHAR NOT NULL, devices_json VARCHAR NOT NULL, runtime_devices_json VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE scenario_audit_events (id INTEGER PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
                "timestamp DATETIME NOT NULL, event_type VARCHAR NOT NULL, actor VARCHAR NOT NULL, details_json TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO scenarios (id, name, network_json, devices_json, runtime_devices_json, status, created_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001', 'legacy', '{\"subnet\":\"172.30.10.0/24\"}', '{\"devices\":[]} ', '[]', 'stopped', '2026-01-01')"
            )
            connection.exec_driver_sql(
                "INSERT INTO scenario_audit_events (id,scenario_id,timestamp,event_type,actor,details_json) VALUES "
                "(1,'00000000-0000-0000-0000-000000000001','2026-01-01 00:00:00','legacy.created','system','{}'), "
                "(2,'00000000-0000-0000-0000-000000000001','2026-01-01 00:00:01','legacy.updated','system','{}')"
            )
        from models.db import create_db_and_tables

        create_db_and_tables(self.engine)
        with self.engine.connect() as connection:
            self.assertTrue(self.engine.dialect.has_table(connection, "scenario_revisions"))
            self.assertTrue(self.engine.dialect.has_table(connection, "scenario_scada_alarm_pending"))
        with Session(self.engine) as session:
            from models.scenario import ScenarioRecord

            record = session.get(ScenarioRecord, "00000000-0000-0000-0000-000000000001")
            self.assertEqual(record.canvas_json, None)
            self.assertEqual(json.loads(record.canvas_json or "{}"), {})
            from services.audit import verify_audit_integrity

            integrity = verify_audit_integrity(session, "00000000-0000-0000-0000-000000000001")
            self.assertTrue(integrity.valid)
            self.assertEqual(integrity.checked_events, 2)

    def test_protocol_endpoint_returns_db_and_process_image_areas(self):
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.manager.logs = lambda _scenario, tail=100: {
            "scenario_id": scenario_id,
            "containers": [{"node_id":"plc-1","lines":[
                "2026-10-07T12:00:00 INFO S7 READ source=172.30.10.20 DB1 bytes[0:1] data=01 result=ok code=0",
                "2026-10-07T12:00:01 INFO S7 READ source=172.30.10.20 M bytes[1:2] data=04 result=ok code=0",
                "2026-10-07T12:00:02 INFO S7 WRITE source=172.30.10.20 Q bytes[0:1] data=08 result=ok code=0",
                "2026-10-07T12:00:03 INFO S7 READ source=172.30.10.20 I bytes[0:1] data=01 result=ok code=0",
            ]}],
        }
        response = self.client.get(f"/api/scenarios/{scenario_id}/protocol")
        self.assertEqual(response.status_code, 200, response.text)
        operations = response.json()["operations"]
        self.assertEqual([(item["area"], item["db"], item["address"]) for item in operations], [
            ("DB", 1, "DB1.BYTE0"), ("M", None, "M.BYTE1"),
            ("Q", None, "Q.BYTE0"), ("I", None, "I.BYTE0"),
        ])


if __name__ == "__main__":
    unittest.main()
