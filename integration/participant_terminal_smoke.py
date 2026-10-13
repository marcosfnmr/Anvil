"""Exercise the participant WebSocket terminal against a local Docker Engine."""

from __future__ import annotations

import ipaddress
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_DATABASE_PATH = Path("/tmp") / f"anvil-terminal-smoke-{os.getpid()}.db"
os.environ["ANVIL_DATABASE_URL"] = f"sqlite:///{_DATABASE_PATH}"
os.environ.setdefault("ANVIL_BOOTSTRAP_TOKEN", "terminal-smoke-bootstrap-token-20261008")
os.environ.setdefault("ANVIL_PLC_IMAGE", "anvil/plc-s7:boolean-logic-srs")
os.environ.setdefault("ANVIL_SCADA_IMAGE", "anvil/scada:hmi-alarm-srs")
os.environ.setdefault("ANVIL_ALLOWED_IMAGES", os.environ["ANVIL_SCADA_IMAGE"])

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PROXY = Path(__file__).resolve().parents[1] / "docker_proxy"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROXY))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402
from docker import DockerClient, from_env  # noqa: E402

from main import create_app  # noqa: E402
from models.db import create_db_and_tables, engine, get_session  # noqa: E402
from models.audit import ScenarioAuditEvent  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from services.docker_manager import DockerManager  # noqa: E402


def choose_subnet() -> str:
    client = DockerClient(base_url=os.environ.get("DOCKER_SOCKET", "unix:///var/run/docker.sock"))
    occupied: list[ipaddress.IPv4Network] = []
    for network in client.networks.list():
        try:
            occupied.extend(ipaddress.ip_network(item["Subnet"]) for item in network.attrs["IPAM"]["Config"] or [])
        except (KeyError, TypeError, ValueError):
            continue
    for third_octet in range(180, 250):
        candidate = ipaddress.ip_network(f"172.30.{third_octet}.0/24")
        if not any(candidate.overlaps(item) for item in occupied):
            return str(candidate)
    raise RuntimeError("No free 172.30.x.0/24 subnet is available for the isolated smoke")


def main() -> int:
    create_db_and_tables(engine)
    app = create_app(initialize_database=False, auth_enabled=True, auth_engine=engine)

    # Exercise the same HTTP Docker gateway used by Compose, on an ephemeral local port.
    import proxy  # noqa: PLC0415
    from proxy import DockerGate, Handler  # noqa: PLC0415

    docker_socket_url = os.environ.get("DOCKER_SOCKET", "unix:///var/run/docker.sock")
    socket_url = urlsplit(docker_socket_url)
    if socket_url.scheme != "unix" or not socket_url.path:
        raise RuntimeError("Participant terminal smoke requires DOCKER_SOCKET to be a Unix socket URL")
    proxy.SOCKET_PATH = socket_url.path

    Handler.gate = DockerGate({os.environ["ANVIL_SCADA_IMAGE"]})
    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    from threading import Lock
    gateway.exec_containers = {}
    gateway.exec_containers_lock = Lock()
    gateway.exec_cleanup_timers = {}
    gateway.daemon_threads = True
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    os.environ["DOCKER_HOST"] = f"tcp://127.0.0.1:{gateway.server_port}"
    app.dependency_overrides[get_docker_manager] = DockerManager

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    scenario_id: str | None = None
    manager = DockerManager()
    try:
        with TestClient(app, headers={"Origin": "http://testserver"}) as admin:
            boot = admin.post("/api/auth/bootstrap", json={
                "email": "terminal-admin@example.test",
                "password": "terminal-admin-password-strong-1",
                "token": os.environ["ANVIL_BOOTSTRAP_TOKEN"],
            })
            if boot.status_code != 201:
                raise RuntimeError(f"Admin bootstrap failed ({boot.status_code}): {boot.text}")
            subnet = choose_subnet()
            network = ipaddress.ip_network(subnet)
            created = admin.post("/api/scenarios", json={
                "name": "Participant terminal smoke",
                "network": {"subnet": subnet},
                "devices": [{
                    "node_id": "attacker-smoke", "device_type": "attacker-station",
                    "name": "Participant station", "ip": str(network.network_address + 10),
                    "config": {"role": "attacker", "summary": "isolated smoke", "participant_access_enabled": True},
                }],
            })
            if created.status_code != 201:
                raise RuntimeError(f"Scenario creation failed ({created.status_code}): {created.text}")
            scenario_id = created.json()["id"]
            participant_create = admin.post("/api/users", json={
                "email": "terminal-participant@example.test",
                "password": "terminal-participant-password-strong-1", "role": "participant",
            })
            if participant_create.status_code != 201:
                raise RuntimeError(f"Participant creation failed ({participant_create.status_code}): {participant_create.text}")
            member = admin.post(f"/api/scenarios/{scenario_id}/members", json={
                "email": "terminal-participant@example.test", "role": "participant",
            })
            if member.status_code != 201:
                raise RuntimeError(f"Participant assignment failed ({member.status_code}): {member.text}")
            deployed = admin.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise RuntimeError(f"Scenario deployment failed ({deployed.status_code}): {deployed.text}")

            # Simulate an API worker disappearing without its WebSocket cleanup.
            # The gateway must forward terminal EOF and let Docker reap the exec.
            probe_exec, probe_stream = manager.open_participant_terminal(scenario_id, "attacker-smoke")
            probe_stream.close()
            deadline = time.monotonic() + 5
            probe_state = {}
            while time.monotonic() < deadline:
                probe_state = manager.client.api.exec_inspect(probe_exec)
                if not probe_state.get("Running"):
                    break
                time.sleep(0.05)
            if probe_state.get("Running"):
                raise RuntimeError("Docker exec stayed alive after the participant terminal socket was dropped")
            if gateway.exec_containers:
                raise RuntimeError(f"Docker gateway retained terminal exec ownership after disconnect: {gateway.exec_containers}")

            with TestClient(app, headers={"Origin": "http://testserver"}) as participant:
                login = participant.post("/api/auth/login", json={
                    "email": "terminal-participant@example.test",
                    "password": "terminal-participant-password-strong-1",
                })
                if login.status_code != 200:
                    raise RuntimeError(f"Participant login failed ({login.status_code}): {login.text}")
                with participant.websocket_connect(
                    f"/api/scenarios/{scenario_id}/participants/attacker-smoke/terminal",
                    headers={"Origin": "http://testserver"},
                ) as websocket:
                    welcome = websocket.receive_text()
                    if "UID 65534" not in welcome:
                        raise RuntimeError(f"Unexpected terminal greeting: {welcome!r}")
                    websocket.send_text("id -u\n")
                    output = bytearray()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and b"65534" not in output:
                        output.extend(websocket.receive_bytes())
                    if b"65534" not in output:
                        raise RuntimeError(f"Terminal did not return the non-root UID: {bytes(output)!r}")
                    websocket.send_text("exit\n")
                    deadline = time.monotonic() + 5
                    event_names: set[str] = set()
                    while time.monotonic() < deadline:
                        with Session(engine) as session:
                            events = session.exec(select(ScenarioAuditEvent).where(
                                ScenarioAuditEvent.scenario_id == scenario_id
                            )).all()
                            event_names = {event.event_type for event in events}
                        if "participant.terminal_closed" in event_names:
                            break
                        time.sleep(0.05)
            if not {"participant.terminal_opened", "participant.terminal_closed"}.issubset(event_names):
                raise RuntimeError(f"Terminal lifecycle audit events missing: {event_names}")
        print("Participant terminal smoke passed: opted-in role, UID 65534, audit lifecycle, abrupt disconnect cleanup and gateway tracking release.")
        return 0
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception as error:
                print(f"DockerManager cleanup needed a direct Docker SDK fallback: {error}", file=sys.stderr)
            docker_client = from_env()
            for container in docker_client.containers.list(all=True, filters={"label": f"anvil.scenario_id={scenario_id}"}):
                try:
                    container.remove(force=True)
                except Exception as error:
                    print(f"Unable to remove smoke container {container.id[:12]}: {error}", file=sys.stderr)
            for network in docker_client.networks.list(filters={"label": f"anvil.scenario_id={scenario_id}"}):
                try:
                    network.remove()
                except Exception as error:
                    print(f"Unable to remove smoke network {network.name}: {error}", file=sys.stderr)
            with Session(engine) as session:
                from services.scenario_service import ScenarioService
                try:
                    service = ScenarioService(session)
                    service.set_status(scenario_id, "stopped")
                    service.delete(scenario_id)
                except Exception as error:
                    print(f"Unable to remove smoke scenario record: {error}", file=sys.stderr)
        engine.dispose()
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join(timeout=2)
        try:
            _DATABASE_PATH.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
