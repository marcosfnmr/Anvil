"""Verify HMI -> S7 -> field process -> sensor feedback in a deployed scenario."""
import json
import time
import urllib.request
import uuid
import docker

BASE = "http://127.0.0.1:8005"
NETWORK = f"10.{90 + int(time.time()) % 60}.0.0/24"
PLC_IP = NETWORK.replace(".0.0/24", ".0.10")


def call(path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{method} {path}: {error.code} {error.read().decode()}") from error


def wait_for_field(root, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        objects = {item["object_id"]: item for item in call(root + "/field/values")["objects"]}
        if predicate(objects):
            return objects
        time.sleep(.2)
    raise AssertionError("Timed out waiting for the field actuators to reach their commanded state")


def main():
    name = "SRS process loop " + uuid.uuid4().hex[:8]
    payload = {
        "name": name,
        "network": {"subnet": NETWORK},
        "devices": [
            {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": PLC_IP,
                     "config": {"scan_cycle_ms": 250, "components": [
                         {"type": "io_channel", "name": "Pump command", "direction": "output", "data_type": "BOOL"},
                         {"type": "io_channel", "name": "Valve command", "direction": "output", "data_type": "BOOL"},
                         {"type": "io_channel", "name": "Tank level", "direction": "input", "data_type": "REAL", "unit": "%"},
                     ]}},
            {"node_id": "hmi", "device_type": "hmi", "name": "Operator panel",
             "ip": PLC_IP.rsplit(".", 1)[0] + ".11",
             "config": {"role": "hmi", "summary": "Process operation", "target_node_ids": "plc"}},
        ],
        "field_objects": [
            {"object_id": "pump", "type": "pump", "name": "Feed pump", "response_seconds": 0,
             "flow_min": 50, "flow_max": 70, "unit": "L/min"},
            {"object_id": "tank", "type": "tank", "name": "Tank", "capacity_liters": 100,
             "initial_level_liters": 50, "outlet_flow_lpm": 30},
            {"object_id": "valve", "type": "valve", "name": "Drain valve", "response_seconds": 0,
             "flow_rate_lpm": 30},
            {"object_id": "level", "type": "analog_sensor", "name": "Level transmitter", "unit": "%",
             "min_value": 0, "max_value": 100, "simulation": {"mode": "process"}},
        ],
        "signal_connections": [
            {"connection_id": "pump-cmd", "source": {"kind": "device", "node_id": "plc", "address": "DB1.DBX0.0"},
             "target": {"kind": "field", "object_id": "pump", "port": "command"}},
            {"connection_id": "valve-cmd", "source": {"kind": "device", "node_id": "plc", "address": "DB1.DBX1.0"},
             "target": {"kind": "field", "object_id": "valve", "port": "command"}},
            {"connection_id": "level-ai", "source": {"kind": "field", "object_id": "level", "port": "value"},
             "target": {"kind": "device", "node_id": "plc", "address": "DB1.DBD2"}},
        ],
        "process_connections": [
            {"connection_id": "pump-tank", "source": {"object_id": "pump", "port": "flow"},
             "target": {"object_id": "tank", "port": "inlet"}},
            {"connection_id": "tank-valve", "source": {"object_id": "tank", "port": "outlet"},
             "target": {"object_id": "valve", "port": "process_in"}},
            {"connection_id": "tank-level", "source": {"object_id": "tank", "port": "level"},
             "target": {"object_id": "level", "port": "measurement"}},
        ],
    }
    scenario = call("/api/scenarios", "POST", payload)
    root = f"/api/scenarios/{scenario['id']}"
    docker_client = docker.from_env()
    try:
        call(root + "/deploy", "POST", {})
        containers = docker_client.containers.list(filters={"label": f"anvil.scenario_id={scenario['id']}"})
        plc = next(item for item in containers if item.labels.get("anvil.node_id") == "plc")
        raw_config = next(item.split("=", 1)[1] for item in plc.attrs["Config"]["Env"] if item.startswith("PLC_CONFIG="))
        assert json.loads(raw_config)["scan_cycle_ms"] == 250
        assert "anvil/plc-s7:srs-v2" in plc.image.tags
        hmi = root + "/hmi/hmi"
        initial = wait_for_field(root, lambda objects: "tank" in objects)["tank"]["value"]
        call(hmi + "/write", "POST", {"target_node_id": "plc", "address": "DB1.DBX0.0", "value": True})
        call(hmi + "/write", "POST", {"target_node_id": "plc", "address": "DB1.DBX1.0", "value": False})
        filled_objects = wait_for_field(root, lambda objects: objects.get("pump", {}).get("state") is True and objects.get("valve", {}).get("state") is False)
        time.sleep(1.0)
        filled_objects = {item["object_id"]: item for item in call(root + "/field/values")["objects"]}
        filled = filled_objects["tank"]["value"]
        call(hmi + "/write", "POST", {"target_node_id": "plc", "address": "DB1.DBX0.0", "value": False})
        call(hmi + "/write", "POST", {"target_node_id": "plc", "address": "DB1.DBX1.0", "value": True})
        opened_objects = wait_for_field(root, lambda objects: objects.get("pump", {}).get("state") is False and objects.get("valve", {}).get("state") is True)
        opened_level = opened_objects["tank"]["value"]
        time.sleep(1.0)
        final_objects = {item["object_id"]: item for item in call(root + "/field/values")["objects"]}
        drained = final_objects["tank"]["value"]
        assert filled > initial + .25, (initial, filled)
        assert drained < opened_level - .1, (opened_level, drained)
        print(f"HMI/S7 process loop OK: {initial:.2f}% -> {filled:.2f}% (pump on, valve closed) -> {drained:.2f}% (pump off, valve open)")
        hmi_values = call(hmi + "/values")["values"]
        level_value = next(item for item in hmi_values if item["address"] == "DB1.DBD2")
        assert level_value["access"] == "read"
        assert abs(level_value["value"] - drained) < 2, (level_value["value"], drained)
        live = call(root + "/field/values")
        objects = {item["object_id"]: item for item in live["objects"]}
        assert 0 <= objects["level"]["value"] <= 100
        assert abs(objects["tank"]["value"] - objects["level"]["value"]) < 1e-6
        print(f"Field runtime confirms tank={objects['tank']['value']:.2f}% and sensor={objects['level']['value']:.2f}%")
    finally:
        call(root + "/stop", "POST", {})
        call(root, "DELETE")
        docker_client.close()


if __name__ == "__main__":
    main()
