"""Verify API status and single-node recovery against a local Docker Engine."""

from __future__ import annotations

import ipaddress
import json
import os
import time
from pathlib import Path

_DATABASE_PATH = Path("/tmp") / f"anvil-node-recovery-{os.getpid()}.db"
os.environ["ANVIL_DATABASE_URL"] = f"sqlite:///{_DATABASE_PATH}"
os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:s7-peer-route-test")
os.environ.setdefault("ANVIL_VIRTUAL_IMAGE", os.environ["ANVIL_PLC_IMAGE"])

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import docker
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from main import create_app
from models.db import create_db_and_tables, engine, get_session
from models.scenario import ScenarioNodeOperation
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager
from services.reconciliation import recover_pending_node_operations


def choose_subnet(client: docker.DockerClient) -> ipaddress.IPv4Network:
    occupied: list[ipaddress.IPv4Network] = []
    for network in client.networks.list():
        for config in (network.attrs.get("IPAM", {}).get("Config") or []):
            try:
                occupied.append(ipaddress.ip_network(config["Subnet"]))
            except (KeyError, ValueError):
                continue
    for third_octet in range(160, 250):
        candidate = ipaddress.ip_network(f"172.29.{third_octet}.0/24")
        if not any(candidate.overlaps(item) for item in occupied):
            return candidate
    raise RuntimeError("No free subnet is available in 172.29.160.0/24–172.29.249.0/24")


def restart_signature(container: docker.models.containers.Container) -> tuple[int, str]:
    container.reload()
    attrs = container.attrs
    state = attrs.get("State") or {}
    return int(attrs.get("RestartCount", 0)), str(state.get("StartedAt", ""))


def main() -> int:
    docker_client = docker.from_env()
    subnet = choose_subnet(docker_client)
    manager = DockerManager(docker_client)
    create_db_and_tables(engine)
    app = create_app(initialize_database=True, auth_enabled=False, auth_engine=engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_docker_manager] = lambda: manager
    scenario_id: str | None = None
    original_ids: dict[str, str] = {}
    try:
        with TestClient(app) as client:
            created = client.post("/api/scenarios", json={
                "name": "Node recovery integration smoke",
                "network": {"subnet": str(subnet)},
                "devices": [
                    {"node_id": node_id, "device_type": "plc-s7", "name": name,
                     "ip": str(subnet.network_address + offset), "config": {"components": []}}
                    for node_id, name, offset in (
                        ("plc-primary", "PLC principal", 10),
                        ("plc-secondary", "PLC secundario", 11),
                    )
                ],
            })
            if created.status_code != 201:
                raise RuntimeError(f"Scenario creation failed ({created.status_code}): {created.text}")
            scenario_id = created.json()["id"]
            deployed = client.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise RuntimeError(f"Scenario deployment failed ({deployed.status_code}): {deployed.text}")

            def by_node() -> dict[str, docker.models.containers.Container]:
                return {
                    item.labels["anvil.node_id"]: item
                    for item in docker_client.containers.list(
                        filters={"label": f"anvil.scenario_id={scenario_id}"}
                    )
                }

            containers = by_node()
            if set(containers) != {"plc-primary", "plc-secondary"}:
                raise AssertionError(f"unexpected deployed nodes: {sorted(containers)}")
            original_ids = {node_id: item.id for node_id, item in containers.items()}
            if any(item.attrs.get("HostConfig", {}).get("PortBindings") for item in containers.values()):
                raise AssertionError("PLC ports must stay private during this smoke")

            # Simulate backend exit after durable intent, before Docker receives
            # the restart. Recovery must perform the requested action.
            primary_container = docker_client.containers.get(original_ids["plc-primary"])
            before = restart_signature(primary_container)
            checkpoint = manager.node_operation_checkpoint(scenario_id, "plc-primary")
            before_operation_id = "node-recovery-before-effect"
            with Session(engine) as session:
                session.add(ScenarioNodeOperation(
                    id=before_operation_id, scenario_id=scenario_id, node_id="plc-primary",
                    action="restart", source="smoke", precondition_json=json.dumps(checkpoint),
                ))
                session.commit()
            recovered_before = recover_pending_node_operations(engine, manager)
            after_before = restart_signature(primary_container)
            if recovered_before["recovered"] != 1 or after_before == before:
                raise AssertionError("pending restart intent was not applied after simulated backend exit")

            # Simulate backend exit after Docker applied restart but before its
            # result was committed. Recovery must not restart it a second time.
            checkpoint = manager.node_operation_checkpoint(scenario_id, "plc-primary")
            after_effect_id = "node-recovery-after-effect"
            with Session(engine) as session:
                session.add(ScenarioNodeOperation(
                    id=after_effect_id, scenario_id=scenario_id, node_id="plc-primary",
                    action="restart", source="smoke", precondition_json=json.dumps(checkpoint),
                ))
                session.commit()
            manager.node_lifecycle(scenario_id, "plc-primary", "restart")
            after_docker = restart_signature(primary_container)
            recovered_after = recover_pending_node_operations(engine, manager)
            after_recovery = restart_signature(primary_container)
            if recovered_after["recovered"] != 1 or after_recovery != after_docker:
                raise AssertionError("recovery repeated a restart that Docker had already completed")
            with Session(engine) as session:
                journal_entries = [session.get(ScenarioNodeOperation, operation_id) for operation_id in (
                    before_operation_id, after_effect_id,
                )]
            if any(item is None or item.status != "completed" for item in journal_entries):
                raise AssertionError("recovered node operations were not durably marked completed")

            restarted = client.post(
                f"/api/scenarios/{scenario_id}/nodes/plc-primary/lifecycle", json={"action": "restart"}
            )
            if restarted.status_code != 200 or restarted.json()["status"] != "running":
                raise AssertionError(f"single-node restart failed: {restarted.status_code} {restarted.text}")
            after_restart = by_node()
            if after_restart["plc-primary"].id != original_ids["plc-primary"]:
                raise AssertionError("restart unexpectedly replaced the primary container")
            if after_restart["plc-secondary"].id != original_ids["plc-secondary"]:
                raise AssertionError("restarting one PLC changed the secondary container")

            after_restart["plc-primary"].remove(force=True)
            status = client.get(f"/api/scenarios/{scenario_id}/status")
            if status.status_code != 200 or status.json()["status"] != "partial":
                raise AssertionError(f"missing PLC was not reported as partial: {status.status_code} {status.text}")
            states = {item["node_id"]: item["status"] for item in status.json()["containers"]}
            if states != {"plc-secondary": "running", "plc-primary": "missing"}:
                raise AssertionError(f"unexpected status after node loss: {states}")

            # The app's real 10-second reconciliation scheduler must discover
            # the loss, journal a recreate intent, and replay it on a later tick.
            recovery_deadline = time.monotonic() + 35
            after_recovery = by_node()
            recovered_status = client.get(f"/api/scenarios/{scenario_id}/status")
            while (
                time.monotonic() < recovery_deadline
                and ("plc-primary" not in after_recovery
                     or after_recovery["plc-primary"].id == original_ids["plc-primary"]
                     or recovered_status.status_code != 200
                     or recovered_status.json().get("status") != "running")
            ):
                time.sleep(0.5)
                after_recovery = by_node()
                recovered_status = client.get(f"/api/scenarios/{scenario_id}/status")
            if set(after_recovery) != set(original_ids):
                raise AssertionError(f"scheduler recovery left an incomplete topology: {sorted(after_recovery)}")
            if after_recovery["plc-primary"].id == original_ids["plc-primary"]:
                raise AssertionError("scheduler did not replace the missing container")
            if after_recovery["plc-secondary"].id != original_ids["plc-secondary"]:
                raise AssertionError("scheduler recovery changed the secondary container")
            if recovered_status.status_code != 200 or recovered_status.json()["status"] != "running":
                raise AssertionError(
                    f"scheduler did not restore running state: "
                    f"{recovered_status.status_code} {recovered_status.text}"
                )
            with Session(engine) as session:
                automatic_operations = session.exec(
                    select(ScenarioNodeOperation).where(
                        ScenarioNodeOperation.scenario_id == scenario_id,
                        ScenarioNodeOperation.node_id == "plc-primary",
                        ScenarioNodeOperation.action == "recreate",
                        ScenarioNodeOperation.source == "runtime-reconciler",
                    )
                ).all()
            if not automatic_operations or any(item.status != "completed" for item in automatic_operations):
                raise AssertionError("scheduler recovery was not recorded as completed in its journal")
            audit = client.get(f"/api/scenarios/{scenario_id}/audit-events")
            if audit.status_code != 200 or not any(
                event["event_type"] == "scenario.node_operation_recovered"
                and event.get("details", {}).get("source") == "runtime-reconciler"
                for event in audit.json().get("events", [])
            ):
                raise AssertionError("automatic node recovery was not recorded in scenario audit")

            # A persisted running scenario represents desired state even if
            # Docker loses every container. The reconciler must restore all
            # planned containers without publishing host ports.
            before_full_loss = {node_id: item.id for node_id, item in after_recovery.items()}
            for item in after_recovery.values():
                item.remove(force=True)
            managed_networks = docker_client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"}
            )
            owned_networks = [
                item for item in managed_networks
                if (item.attrs.get("Labels") or {}).get("anvil.managed") == "true"
                and (item.attrs.get("Labels") or {}).get("anvil.scenario_id") == scenario_id
            ]
            if len(owned_networks) != 1:
                raise AssertionError(f"expected one managed network before loss, got {len(owned_networks)}")
            owned_networks[0].remove()
            full_recovery_deadline = time.monotonic() + 40
            fully_recovered = by_node()
            full_status = client.get(f"/api/scenarios/{scenario_id}/status")
            while (
                time.monotonic() < full_recovery_deadline
                and (set(fully_recovered) != set(original_ids)
                     or any(fully_recovered.get(node_id) is None
                            or fully_recovered[node_id].id == before_full_loss[node_id]
                            for node_id in original_ids)
                     or full_status.status_code != 200
                     or full_status.json().get("status") != "running")
            ):
                time.sleep(0.5)
                fully_recovered = by_node()
                full_status = client.get(f"/api/scenarios/{scenario_id}/status")
            if set(fully_recovered) != set(original_ids):
                with Session(engine) as session:
                    operations_debug = session.exec(
                        select(ScenarioNodeOperation).where(
                            ScenarioNodeOperation.scenario_id == scenario_id,
                            ScenarioNodeOperation.source == "runtime-reconciler",
                        )
                    ).all()
                    from models.scenario import ScenarioRecord
                    record_debug = session.get(ScenarioRecord, scenario_id)
                    persisted_debug = record_debug.status if record_debug else "deleted"
                raise AssertionError(
                    f"full-loss recovery left an incomplete topology: {sorted(fully_recovered)}; "
                    f"api_status={full_status.text}; persisted={persisted_debug}; "
                    f"ops={[(o.node_id, o.status, o.last_error) for o in operations_debug]}"
                )
            if any(fully_recovered[node_id].id == before_full_loss[node_id] for node_id in original_ids):
                raise AssertionError("scheduler did not replace every container after full runtime loss")
            if full_status.status_code != 200 or full_status.json().get("status") != "running":
                raise AssertionError(f"scheduler did not restore fully lost runtime: {full_status.text}")
            if any(item.attrs.get("HostConfig", {}).get("PortBindings") for item in fully_recovered.values()):
                raise AssertionError("fully recovered PLCs unexpectedly publish host ports")
            restored_networks = docker_client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"}
            )
            if len(restored_networks) != 1:
                raise AssertionError(f"network recovery created an unexpected topology: {len(restored_networks)}")
            restored_labels = restored_networks[0].attrs.get("Labels") or {}
            if restored_labels.get("anvil.managed") != "true" or restored_labels.get("anvil.scenario_id") != scenario_id:
                raise AssertionError("restored network does not have the exact scenario ownership labels")
            with Session(engine) as session:
                full_operations = session.exec(
                    select(ScenarioNodeOperation).where(
                        ScenarioNodeOperation.scenario_id == scenario_id,
                        ScenarioNodeOperation.action == "recreate",
                        ScenarioNodeOperation.source == "runtime-reconciler",
                    )
                ).all()
            completed_nodes = {item.node_id for item in full_operations if item.status == "completed"}
            if not {"plc-primary", "plc-secondary"}.issubset(completed_nodes):
                raise AssertionError(f"full-loss recovery journal is incomplete: {completed_nodes}")
            network_recovery_audit = client.get(f"/api/scenarios/{scenario_id}/audit-events")
            if network_recovery_audit.status_code != 200 or not any(
                event["event_type"] == "scenario.networks_recovered"
                and event.get("details", {}).get("zone_ids") == ["default"]
                for event in network_recovery_audit.json().get("events", [])
            ):
                raise AssertionError("restored network was not recorded in scenario audit")

            print(json.dumps({
                "status": "passed", "scenario_status_after_loss": "partial",
                "single_node_restart": "passed", "scheduler_node_recovery": "passed",
                "journal_restart_before_effect": "replayed",
                "journal_restart_after_effect": "not_repeated",
                "unaffected_node": "preserved", "audit": "recorded", "host_ports": "none",
                "scheduler_full_runtime_recovery": "passed",
                "scheduler_network_recovery": "passed",
            }))
            return 0
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception:
                pass
            try:
                with TestClient(app) as cleanup_client:
                    cleanup_client.delete(f"/api/scenarios/{scenario_id}")
            except Exception:
                pass
        docker_client.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(_DATABASE_PATH) + suffix)
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
