"""Exercise the scenario-wide restart API against a Docker-backed Anvil API."""

from __future__ import annotations

import argparse
from ipaddress import ip_network
import os
from pathlib import Path
import sys
import tempfile

import docker

from platform_smoke import request


def free_subnet(client) -> str:
    used = [ip_network(entry["Subnet"]) for network in client.networks.list()
            for entry in ((network.attrs.get("IPAM") or {}).get("Config") or []) if entry.get("Subnet")]
    for third in range(20, 240):
        candidate = ip_network(f"10.246.{third}.0/24")
        if all(not candidate.overlaps(item) for item in used):
            return str(candidate)
    raise RuntimeError("No free subnet in the scenario-restart smoke range")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    parser.add_argument("--local", action="store_true",
                        help="Use an isolated in-process API and temporary SQLite database")
    args = parser.parse_args()
    client = docker.from_env()
    api_client = None
    cleanup = None
    scenario_id: str | None = None
    deployed = False
    try:
        if args.local:
            database_file = Path(tempfile.gettempdir()) / f"anvil-scenario-restart-{os.getpid()}.db"
            os.environ["ANVIL_DATABASE_URL"] = f"sqlite:///{database_file}"
            os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:ci")
            os.environ.setdefault("ANVIL_VIRTUAL_IMAGE", os.environ["ANVIL_PLC_IMAGE"])
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            from fastapi.testclient import TestClient
            from sqlmodel import Session
            from main import create_app
            from models.db import create_db_and_tables, engine, get_session
            from routes.scenarios import get_docker_manager
            from services.docker_manager import DockerManager

            create_db_and_tables(engine)
            manager = DockerManager(client)
            app = create_app(initialize_database=False, auth_enabled=False, auth_engine=engine)

            def session_override():
                with Session(engine) as session:
                    yield session

            app.dependency_overrides[get_session] = session_override
            app.dependency_overrides[get_docker_manager] = lambda: manager
            api_client = TestClient(app)
            api_base = ""
            cleanup = lambda: (engine.dispose(), *[
                Path(str(database_file) + suffix).unlink(missing_ok=True)
                for suffix in ("", "-wal", "-shm")
            ])
        else:
            api_base = args.base_url

        def api(path: str, method: str = "GET", payload=None):
            if api_client is not None:
                response = api_client.request(method, path, json=payload)
                if response.is_error:
                    raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text}")
                return response.json() if response.content else None
            return request(api_base, path, method, payload)

        network = ip_network(free_subnet(client))
        scenario = api("/api/scenarios", "POST", {
            "name": "Scenario-wide restart smoke",
            "network": {"subnet": str(network)},
            "devices": [{
                "node_id": "restart-plc", "device_type": "plc-s7", "name": "Restart PLC",
                "ip": str(network.network_address + 10), "config": {"components": []},
            }],
        })
        scenario_id = scenario["id"]
        runtime = api(f"/api/scenarios/{scenario_id}/deploy", "POST")
        if runtime["status"] != "running":
            raise AssertionError(f"Initial deployment did not reach running: {runtime}")
        deployed = True
        before = client.containers.list(filters={
            "label": [f"anvil.scenario_id={scenario_id}", "anvil.node_id=restart-plc"],
        })
        if len(before) != 1:
            raise AssertionError(f"Expected one PLC container before restart; found {len(before)}")
        before[0].reload()
        started_at = before[0].attrs["State"]["StartedAt"]

        restarted = api(f"/api/scenarios/{scenario_id}/restart", "POST")
        if restarted["status"] != "running":
            raise AssertionError(f"Scenario restart did not return to running: {restarted}")
        after = client.containers.get(before[0].id)
        after.reload()
        if after.status != "running":
            raise AssertionError(f"PLC status after restart is {after.status}")
        if after.attrs["State"]["StartedAt"] == started_at:
            raise AssertionError("Docker container start time did not change after scenario restart")
        events = api(f"/api/scenarios/{scenario_id}/audit-events")
        if not any(event["event_type"] == "scenario.restarted" for event in events["events"]):
            raise AssertionError("Scenario restart was not added to the audit trail")
        print("PASS: Docker-backed scenario restart returned PLC health to running and recorded the audit event")
        return 0
    finally:
        if scenario_id:
            if deployed:
                try:
                    api(f"/api/scenarios/{scenario_id}/stop", "POST")
                except Exception:
                    pass
            try:
                api(f"/api/scenarios/{scenario_id}", "DELETE")
            except Exception:
                pass
        if api_client is not None:
            api_client.close()
        if cleanup:
            cleanup()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
