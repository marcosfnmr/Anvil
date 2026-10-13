"""End-to-end smoke test for Anvil's S7 console and protocol trace."""

from __future__ import annotations

import argparse
import http.cookies
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from websockets.sync.client import connect


_AUTH_TOKENS: dict[str, str] = {}


def _origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _read_response(base_url: str, path: str, method: str, payload: dict | None, headers: dict[str, str]) -> tuple[int, dict | None, str | None]:
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = {"Content-Type": "application/json"} if body else {}
    request_headers.update(headers)
    with urlopen(Request(f"{base_url}{path}", data=body, headers=request_headers, method=method)) as response:
        raw = response.read()
        decoded = json.loads(raw) if raw else None
        return response.status, decoded, response.headers.get("Set-Cookie")


def _authenticate(base_url: str) -> str | None:
    _, auth, _ = _read_response(base_url, "/api/auth/status", "GET", None, {})
    if not auth or not auth.get("auth_required"):
        return None

    email = os.environ.get("ANVIL_TEST_EMAIL", "")
    password = os.environ.get("ANVIL_TEST_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            "This Anvil requires authentication; set ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD for the integration smoke"
        )
    if auth.get("setup_required"):
        bootstrap_token = os.environ.get("ANVIL_BOOTSTRAP_TOKEN", "")
        if len(bootstrap_token) < 32:
            raise RuntimeError(
                "Initial Anvil setup requires ANVIL_BOOTSTRAP_TOKEN with at least 32 characters"
            )
        path = "/api/auth/bootstrap"
        payload = {"email": email, "password": password, "token": bootstrap_token}
    else:
        path = "/api/auth/login"
        payload = {"email": email, "password": password}

    _, _, set_cookie = _read_response(
        base_url, path, "POST", payload, {"Origin": _origin(base_url)}
    )
    cookie = http.cookies.SimpleCookie()
    cookie.load(set_cookie or "")
    session = cookie.get("anvil_session")
    if session is None or not session.value:
        raise RuntimeError("Anvil authentication succeeded without issuing a session cookie")
    return session.value

def request(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict | None:
    token = _AUTH_TOKENS.get(base_url)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        _, decoded, _ = _read_response(base_url, path, method, payload, headers)
        return decoded
    except HTTPError as error:
        if error.code == 401:
            if token is not None:
                _AUTH_TOKENS.pop(base_url, None)
            token = _authenticate(base_url)
            if token is not None:
                _AUTH_TOKENS[base_url] = token
                _, decoded, _ = _read_response(
                    base_url, path, method, payload, {"Authorization": f"Bearer {token}"}
                )
                return decoded
        raise RuntimeError(
            f"{method} {path} failed ({error.code}): {error.read().decode()}"
        ) from error


def request_bytes(base_url: str, path: str) -> bytes:
    """Fetch a protected binary artifact using the same smoke-test auth session."""
    if base_url not in _AUTH_TOKENS:
        request(base_url, "/api/scenarios")
    token = _AUTH_TOKENS.get(base_url)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urlopen(Request(f"{base_url}{path}", headers=headers, method="GET")) as response:
            if response.status != 200:
                raise RuntimeError(f"GET {path} returned HTTP {response.status}")
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"GET {path} failed ({error.code}): {error.read().decode(errors='replace')}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8000")
    args = parser.parse_args()
    scenario_id: str | None = None
    payload = {
        "name": "Phase 6 smoke",
        "network": {"subnet": "172.30.160.0/24"},
        "devices": [
            {
                "node_id": "plc-smoke",
                "device_type": "plc-s7",
                "name": "PLC smoke",
                "ip": "172.30.160.10",
                "config": {
                    "components": [
                        {"type": "valve", "name": "Válvula", "db": 1},
                        {
                            "type": "analog_sensor",
                            "name": "Presión",
                            "db": 1,
                            "unit": "bar",
                            "min_value": 0,
                            "max_value": 10,
                        },
                    ]
                },
            }
        ],
    }
    try:
        ready = request(args.base_url, "/api/ready")
        if ready != {"status": "ready", "database": "ok", "docker": "ok"}:
            raise AssertionError(f"unexpected readiness response: {ready}")
        created = request(args.base_url, "/api/scenarios", "POST", payload)
        assert created is not None
        scenario_id = created["id"]
        io_map = request(args.base_url, f"/api/scenarios/{scenario_id}/io-map")
        addresses = [point["address"] for point in io_map["points"]]
        if addresses != ["DB1.DBX0.0", "DB1.DBD2"]:
            raise AssertionError(f"unexpected S7 map: {addresses}")

        deployed = request(args.base_url, f"/api/scenarios/{scenario_id}/deploy", "POST")
        if deployed["status"] != "running":
            raise AssertionError(f"scenario did not start: {deployed}")

        base_path = f"/api/scenarios/{scenario_id}"
        with connect(f"{args.ws_url}{base_path}/logs") as websocket:
            deadline = time.monotonic() + 20
            saw_startup = False
            while time.monotonic() < deadline and not saw_startup:
                event = json.loads(websocket.recv(timeout=5))
                lines = [
                    line
                    for container in event.get("containers", [])
                    for line in container.get("lines", [])
                ]
                saw_startup = any("listening on port 102" in line for line in lines)
            if not saw_startup:
                raise AssertionError("PLC startup was not observed in live logs")

            values = request(args.base_url, f"{base_path}/plcs/plc-smoke/values")
            by_address = {item["address"]: item for item in values["values"]}
            if by_address["DB1.DBX0.0"]["value"] is not False:
                raise AssertionError(f"unexpected initial valve value: {by_address}")
            if abs(by_address["DB1.DBD2"]["value"] - 5.0) > 0.01:
                raise AssertionError(f"unexpected initial sensor value: {by_address}")

            written = request(
                args.base_url,
                f"{base_path}/plcs/plc-smoke/write",
                "POST",
                {"address": "DB1.DBX0.0", "value": True},
            )
            if written["value"] is not True:
                raise AssertionError(f"valve write did not take effect: {written}")
            reread = request(args.base_url, f"{base_path}/plcs/plc-smoke/values")
            valve = next(item for item in reread["values"] if item["address"] == "DB1.DBX0.0")
            if valve["value"] is not True:
                raise AssertionError(f"valve state did not persist: {valve}")

            protocol = request(args.base_url, f"{base_path}/protocol")
            operations = {item["operation"] for item in protocol["operations"]}
            if not {"read", "write"}.issubset(operations):
                raise AssertionError(f"protocol trace is missing reads/writes: {protocol}")

            print(
                "Phase 6 smoke passed: "
                f"addresses={addresses}, live_values=ok, valve_write=ok, "
                f"protocol_operations={len(protocol['operations'])}"
            )
        return 0
    finally:
        if scenario_id is not None:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")


if __name__ == "__main__":
    raise SystemExit(main())
