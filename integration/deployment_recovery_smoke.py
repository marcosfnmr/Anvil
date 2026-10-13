"""Exercise recovery of a Docker deployment interrupted before status commit."""

from __future__ import annotations

import ipaddress
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


_database_file = Path(tempfile.gettempdir()) / f"anvil-deployment-recovery-{os.getpid()}.db"
os.environ["ANVIL_DATABASE_URL"] = f"sqlite:///{_database_file}"
os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:ci")
os.environ.setdefault("ANVIL_VIRTUAL_IMAGE", os.environ["ANVIL_PLC_IMAGE"])
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import docker
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from main import create_app
from models.db import create_db_and_tables, engine, get_session
from models.scenario import ScenarioOperationLease, ScenarioRecord
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager
from services.reconciliation import _recover_expired_deployments


def choose_subnet(client: docker.DockerClient) -> ipaddress.IPv4Network:
    occupied: list[ipaddress.IPv4Network] = []
    for network in client.networks.list():
        for config in (network.attrs.get("IPAM", {}).get("Config") or []):
            try:
                occupied.append(ipaddress.ip_network(config["Subnet"]))
            except (KeyError, ValueError):
                continue
    for octet in range(160, 250):
        candidate = ipaddress.ip_network(f"172.29.{octet}.0/24")
        if not any(candidate.overlaps(item) for item in occupied):
            return candidate
    raise RuntimeError("No free subnet is available in 172.29.160.0/24–172.29.249.0/24")


def main() -> int:
    docker_client = docker.from_env()
    manager = DockerManager(docker_client)
    subnet = choose_subnet(docker_client)
    create_db_and_tables(engine)
    app = create_app(initialize_database=False, auth_enabled=False, auth_engine=engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_docker_manager] = lambda: manager
    scenario_id: str | None = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/scenarios", json={
                "name": "Deployment recovery integration smoke",
                "network": {"subnet": str(subnet)},
                "devices": [{
                    "node_id": "plc-recovery", "device_type": "plc-s7",
                    "name": "PLC de recuperación", "ip": str(subnet.network_address + 10),
                    "config": {"components": []},
                }],
            })
            if created.status_code != 201:
                raise RuntimeError(f"Scenario creation failed ({created.status_code}): {created.text}")
            scenario_id = created.json()["id"]
            deployed = client.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise RuntimeError(f"Scenario deployment failed ({deployed.status_code}): {deployed.text}")

            # Reproduce a process interruption after Docker resources exist but
            # before the route commits its final `running` state.
            with Session(engine) as session:
                record = session.get(ScenarioRecord, scenario_id)
                assert record is not None
                record.status = "deploying"
                session.add(record)
                session.exec(delete(ScenarioOperationLease).where(
                    ScenarioOperationLease.scenario_id == scenario_id,
                ))
                session.commit()

            _recover_expired_deployments(engine)
            with Session(engine) as session:
                recovered = session.get(ScenarioRecord, scenario_id)
            containers = docker_client.containers.list(
                all=True, filters={"label": f"anvil.scenario_id={scenario_id}"},
            )
            networks = docker_client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"},
            )
            if recovered is None or recovered.status != "stopped":
                raise AssertionError(f"stale deployment was not marked stopped: {getattr(recovered, 'status', None)}")
            if containers or networks:
                raise AssertionError("recovery left scenario containers or networks behind")
            print("PASS: expired deployment lease recovered a real Docker scenario; managed container and network were removed")
            return 0
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception:
                pass
            try:
                with Session(engine) as session:
                    session.exec(delete(ScenarioOperationLease).where(
                        ScenarioOperationLease.scenario_id == scenario_id,
                    ))
                    record = session.get(ScenarioRecord, scenario_id)
                    if record is not None:
                        session.delete(record)
                    session.commit()
            except Exception:
                pass
        docker_client.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(_database_file) + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
