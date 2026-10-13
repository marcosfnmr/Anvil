"""Exercise peer S7 traffic, held stale values and recovery on real Docker PLCs."""
import argparse
import json
import time
from pathlib import Path

import docker
from platform_smoke import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    client = docker.from_env()
    scenario_id = None
    sender = None
    payload = json.loads((Path(__file__).resolve().parents[1] / "examples/s7-peer-links.json").read_text())
    try:
        scenario_id = request(args.base_url, "/api/scenarios", "POST", payload)["id"]
        base = f"/api/scenarios/{scenario_id}"
        assert request(args.base_url, base + "/deploy", "POST")["status"] == "running"
        containers = client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        sender = next(item for item in containers if item.labels["anvil.node_id"] == "sender")
        for container in containers:
            assert list(container.attrs["NetworkSettings"]["Networks"]) == [f"anvil-{scenario_id[:12]}"]
            assert not container.attrs["HostConfig"]["PortBindings"]
            expected = next(device["ip"] for device in payload["devices"] if device["node_id"] == container.labels["anvil.node_id"])
            assert container.attrs["NetworkSettings"]["Networks"][f"anvil-{scenario_id[:12]}"]["IPAddress"] == expected

        def values():
            return {item["address"]: item for item in request(args.base_url, base + "/plcs/receiver/values")["values"]}

        def until(predicate, timeout=15):
            deadline = time.monotonic() + timeout
            latest = None
            while time.monotonic() < deadline:
                latest = values()
                if predicate(latest):
                    return latest
                time.sleep(0.2)
            raise AssertionError(f"Peer state not reached: {latest}")

        snapshot = until(lambda v: all(v[address]["communication"]["quality"] == "good"
                                       for address in ("DB1.DBX0.0", "DB1.DBD2"))
                         and v["DB1.DBX6.0"]["value"] is True)
        assert snapshot["DB1.DBX0.0"]["value"] is False
        assert snapshot["DB1.DBD2"]["value"] == 30.0
        assert snapshot["DB1.DBX6.0"]["value"] is True
        request(args.base_url, base + "/plcs/sender/write", "POST", {"address": "DB1.DBX0.0", "value": True})
        until(lambda v: v["DB1.DBX0.0"]["value"] is True)
        # Reject remote input writes through the supported API.
        try:
            request(args.base_url, base + "/plcs/receiver/write", "POST", {"address": "DB1.DBX0.0", "value": False})
        except RuntimeError as error:
            assert "403" in str(error), error
        else:
            raise AssertionError("Remote input was writable")
        protocol = request(args.base_url, base + "/protocol")
        assert any(item["node_id"] == "sender" and item["source"] == "172.30.243.11" and item["operation"] == "read"
                   for item in protocol["operations"]), protocol
        print("S7 peers OK: real receiver-IP traffic, BOOL/REAL copied, remote writes rejected, no host ports")
        sender.stop(timeout=5)
        request(args.base_url, base + "/status")
        snapshot = until(lambda v: all(v[address]["communication"]["quality"] == "stale"
                                       and v[address]["communication"]["error"]
                                       for address in ("DB1.DBX0.0", "DB1.DBD2")), timeout=8)
        assert snapshot["DB1.DBX0.0"]["value"] is True
        assert snapshot["DB1.DBD2"]["value"] == 30.0
        assert snapshot["DB1.DBX6.0"]["value"] is False
        print("Stopped peer OK: stale diagnostic, telemetry retained, force_off control output de-energized")
        sender.start()
        snapshot = until(lambda v: v["DB1.DBX0.0"]["value"] is False and v["DB1.DBX6.0"]["value"] is True
                         and all(v[address]["communication"]["quality"] == "good"
                                 for address in ("DB1.DBX0.0", "DB1.DBD2")))
        print("Recovery OK: automatic reconnect restores good quality and control behavior")
    finally:
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
        client.close()


if __name__ == "__main__":
    main()
