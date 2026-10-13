"""Verify SQLite SCADA samples survive a scenario stop and redeploy."""

from __future__ import annotations

import argparse
import ipaddress
import time

import docker

from platform_smoke import request


def unused_subnet() -> str:
    client = docker.from_env()
    occupied = []
    try:
        for network in client.networks.list():
            for config in (network.attrs.get("IPAM", {}).get("Config") or []):
                if config.get("Subnet"):
                    try:
                        occupied.append(ipaddress.ip_network(config["Subnet"], strict=False))
                    except ValueError:
                        pass
    finally:
        client.close()
    for third_octet in range(240, 255):
        subnet = ipaddress.ip_network(f"172.30.{third_octet}.0/24")
        if not any(subnet.overlaps(value) for value in occupied):
            return str(subnet)
    raise RuntimeError("No free subnet was found in 172.30.240.0/20")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    parser.add_argument("--settle-seconds", type=float, default=6.0)
    args = parser.parse_args()
    scenario_id = None
    payload = {
        "name": "SCADA durable historian smoke",
        "network": {"subnet": unused_subnet()},
        "devices": [
            {"node_id": "scada", "device_type": "scada", "name": "SCADA",
             "ip": "172.30.250.10", "config": {
                 "target_node_ids": "plc", "poll_interval_ms": 250,
                 "history_capacity": 100, "alarms": [],
             }},
            {"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
             "ip": "172.30.250.11", "config": {
                 "components": [{"type": "analog_sensor", "name": "Level", "min_value": 0, "max_value": 100}],
             }},
        ],
    }
    payload["devices"][0]["ip"] = str(ipaddress.ip_network(payload["network"]["subnet"])[10])
    payload["devices"][1]["ip"] = str(ipaddress.ip_network(payload["network"]["subnet"])[11])
    try:
        scenario_id = request(args.base_url, "/api/scenarios", "POST", payload)["id"]
        path = f"/api/scenarios/{scenario_id}"
        assert request(args.base_url, f"{path}/deploy", "POST")["status"] == "running"
        time.sleep(max(1.0, args.settle_seconds))
        assert request(args.base_url, f"{path}/stop", "POST")["status"] == "stopped"

        archived = request(args.base_url, f"{path}/scada/scada/history?limit=100")
        assert archived["live"] is False, archived
        assert archived["samples"], "backend historian did not persist any SCADA samples"
        original_timestamps = {sample["timestamp"] for sample in archived["samples"]}

        assert request(args.base_url, f"{path}/deploy", "POST")["status"] == "running"
        restored = request(args.base_url, f"{path}/scada/scada/history?limit=100")
        restored_timestamps = {sample["timestamp"] for sample in restored["samples"]}
        assert original_timestamps.issubset(restored_timestamps), {
            "original": sorted(original_timestamps), "after_redeploy": sorted(restored_timestamps),
        }
        assert restored["live"] is True, restored
        print(f"PASS: {len(original_timestamps)} durable SCADA samples survived stop and redeploy")
        return 0
    finally:
        if scenario_id:
            try:
                status = request(args.base_url, f"/api/scenarios/{scenario_id}").get("status")
                if status != "stopped":
                    request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
