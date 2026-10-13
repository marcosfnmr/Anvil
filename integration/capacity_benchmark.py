"""Measure Docker orchestration time for repeated 20–100-node scenarios.

The default profile launches actual Anvil S7 simulator containers with BOOL and
REAL points and waits on the Docker image's TCP/102 healthcheck. Lightweight
gateway and mixed S7/Modbus profiles are available for orchestration and
protocol-image comparisons. Runs above 30 nodes need explicit opt-in.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import docker


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    token = os.environ.get("ANVIL_BENCHMARK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(f"{base_url}{path}", data=body, headers=headers, method=method), timeout=600) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed ({error.code}): {detail}") from error


def used_networks(client) -> list[ipaddress.IPv4Network]:
    result = []
    for network in client.networks.list():
        for config in (network.attrs.get("IPAM") or {}).get("Config") or []:
            subnet = config.get("Subnet")
            if subnet:
                try:
                    parsed = ipaddress.ip_network(subnet, strict=False)
                except ValueError:
                    continue
                if isinstance(parsed, ipaddress.IPv4Network):
                    result.append(parsed)
    return result


def choose_subnets(client, samples: int) -> list[str]:
    used = used_networks(client)
    candidates = [ipaddress.ip_network(f"172.30.{octet}.0/24") for octet in range(240, 256)]
    candidates += [ipaddress.ip_network(f"172.31.{octet}.0/24") for octet in range(240, 256)]
    available = [str(candidate) for candidate in candidates if not any(candidate.overlaps(item) for item in used)]
    if len(available) < samples:
        raise RuntimeError(f"Need {samples} unused /24 subnets; found {len(available)} in benchmark pool")
    return available[:samples]


def scenario_payload(sample: int, count: int, subnet: str, profile: str) -> dict:
    network = ipaddress.ip_network(subnet)
    devices = []
    for index in range(count):
        address = network.network_address + 10 + index
        if address >= network.broadcast_address:
            raise ValueError("node count does not fit in the selected /24 subnet")
        device_type = (
            "plc-s7" if profile == "plc-s7"
            else "modbus-tcp" if profile == "modbus-tcp"
            else ("modbus-tcp" if index % 2 else "plc-s7") if profile == "mixed"
            else "gateway"
        )
        device = {
            "node_id": f"bench-{sample:02d}-{index:03d}",
            "device_type": device_type,
            "name": f"Benchmark {profile} {index + 1}",
            "ip": str(address),
        }
        if device_type == "plc-s7":
            device["config"] = {"scan_cycle_ms": 100, "components": [
                {"type": "valve", "name": f"valve-{index:03d}"},
                {"type": "analog_sensor", "name": f"sensor-{index:03d}", "min_value": 0, "max_value": 100},
            ]}
        elif device_type == "modbus-tcp":
            device["config"] = {"points": [
                {"type": "coil", "name": f"coil-{index:03d}"},
                {"type": "holding_register", "name": f"holding-{index:03d}"},
                {"type": "input_register", "name": f"input-{index:03d}"},
            ]}
        else:
            device["config"] = {"role": "gateway", "summary": "synthetic capacity benchmark node"}
        devices.append(device)
    return {"name": f"Anvil capacity benchmark {sample}", "network": {"subnet": subnet}, "devices": devices}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8014")
    parser.add_argument("--nodes", type=int, default=20)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--profile", choices=("plc-s7", "modbus-tcp", "mixed", "gateway"), default="plc-s7")
    parser.add_argument("--target-seconds", type=float, default=300.0)
    parser.add_argument(
        "--expected-docker-server-version",
        help="require this exact Docker ServerVersion before creating any scenario",
    )
    parser.add_argument(
        "--expected-docker-name-sha256",
        help="require SHA-256 of Docker daemon name (avoids printing it in shared logs)",
    )
    parser.add_argument(
        "--expected-api-health",
        help="require this exact value from GET /api/health before creating any scenario",
    )
    parser.add_argument("--allow-large-run", action="store_true",
                        help="allow runs above 30 nodes; use only on an isolated test Docker daemon")
    args = parser.parse_args()
    if not 20 <= args.nodes <= 100:
        parser.error("--nodes must be between 20 and 100")
    if args.nodes > 30 and not args.allow_large_run:
        parser.error("runs above 30 nodes require --allow-large-run and an isolated test Docker daemon")
    if args.nodes > 30 and not all((
        args.expected_docker_server_version,
        args.expected_docker_name_sha256,
        args.expected_api_health,
    )):
        parser.error(
            "runs above 30 nodes require expected Docker version, daemon identity hash, "
            "and API health identity for the isolated test environment"
        )
    if not 1 <= args.samples <= 10 or args.target_seconds <= 0:
        parser.error("--samples must be 1–10 and --target-seconds must be positive")

    client = docker.from_env()
    try:
        info = client.info()
        api_health = request(args.base_url, "/api/health")
    except Exception as error:
        print(f"Preflight failed before scenario creation: {error}", file=sys.stderr)
        return 2

    server_version = str(info.get("ServerVersion", ""))
    daemon_name = str(info.get("Name", ""))
    daemon_name_sha256 = hashlib.sha256(daemon_name.encode("utf-8")).hexdigest()
    if args.expected_docker_server_version and server_version != args.expected_docker_server_version:
        print("Preflight rejected: Docker ServerVersion did not match the expected isolated daemon.", file=sys.stderr)
        return 2
    if args.expected_docker_name_sha256 and daemon_name_sha256 != args.expected_docker_name_sha256:
        print("Preflight rejected: Docker daemon identity did not match the expected isolated daemon.", file=sys.stderr)
        return 2
    expected_health = None
    if args.expected_api_health:
        try:
            expected_health = json.loads(args.expected_api_health)
        except json.JSONDecodeError:
            print("Preflight rejected: --expected-api-health must be a JSON object.", file=sys.stderr)
            return 2
        if not isinstance(expected_health, dict) or api_health != expected_health:
            print("Preflight rejected: API health identity did not match the expected isolated backend.", file=sys.stderr)
            return 2
    if args.nodes > 30 and (
        not isinstance(api_health, dict)
        or not api_health.get("instance_id")
        or not isinstance(expected_health, dict)
        or not expected_health.get("instance_id")
    ):
        print("Preflight rejected: isolated benchmark API must expose a unique instance_id.", file=sys.stderr)
        return 2
    subnets = choose_subnets(client, args.samples)
    measurements = []
    for sample, subnet in enumerate(subnets, start=1):
        scenario_id = None
        try:
            created = request(args.base_url, "/api/scenarios", "POST",
                              scenario_payload(sample, args.nodes, subnet, args.profile))
            scenario_id = created["id"]
            started = time.monotonic()
            deployed = request(args.base_url, f"/api/scenarios/{scenario_id}/deploy", "POST")
            wall_seconds = round(time.monotonic() - started, 3)
            if deployed["status"] != "running" or len(deployed["containers"]) != args.nodes:
                raise RuntimeError(f"sample {sample} did not reach running with {args.nodes} nodes: {deployed}")
            reported = deployed.get("deployment_duration_seconds")
            if reported is None:
                raise RuntimeError("API response is missing deployment_duration_seconds")
            measurements.append({
                "sample": sample,
                "nodes": args.nodes,
                "subnet": subnet,
                "api_deployment_seconds": reported,
                "client_wall_seconds": wall_seconds,
                "within_target": reported <= args.target_seconds,
            })
        finally:
            if scenario_id is not None:
                try:
                    request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
                finally:
                    request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")

    times = [item["api_deployment_seconds"] for item in measurements]
    result = {
        "status": "passed" if all(item["within_target"] for item in measurements) else "target_exceeded",
        "scope": f"Docker orchestration with {args.profile} containers",
        "profile": args.profile,
        "nodes_per_scenario": args.nodes,
        "samples": measurements,
        "summary_seconds": {
            "min": min(times),
            "median": sorted(times)[len(times) // 2],
            "max": max(times),
            "target": args.target_seconds,
        },
        "host": {
            "os": platform.platform(),
            "docker_cpus": info.get("NCPU"),
            "docker_memory_bytes": info.get("MemTotal"),
            "docker_server_version": server_version,
            "docker_daemon_name_sha256": daemon_name_sha256,
            "api_health": api_health,
        },
    }
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
