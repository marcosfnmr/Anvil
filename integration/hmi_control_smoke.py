"""Exercise HMI reads and permission-checked writes against real S7/Modbus containers."""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from ipaddress import ip_network

import docker

BASE = os.environ.get("ANVIL_BASE_URL", "http://127.0.0.1:8005").rstrip("/")

def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list()
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(150, 220):
        candidate = ip_network(f"10.246.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free HMI protocol smoke subnet in 10.246.150.0/24–10.246.219.0/24")


def call(path, method="GET", payload=None, expected_error=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        timeout = float(os.environ.get("ANVIL_HTTP_TIMEOUT_SECONDS", "120"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        if expected_error == error.code:
            return error.code, json.loads(body)
        raise RuntimeError(f"{method} {path}: {error.code} {body}") from error


def main():
    docker_client = docker.from_env()
    subnet = free_subnet(docker_client)
    base_ip = subnet.network_address
    payload = {
        "name": "HMI operations " + uuid.uuid4().hex[:8],
        "network": {"subnet": str(subnet)},
        "devices": [
            {"node_id": "hmi", "device_type": "hmi", "name": "Operator HMI", "ip": str(base_ip + 10),
             "config": {"role": "hmi", "summary": "Operator panel", "target_node_ids": "plc,mb",
                        "alarm_source_node_ids": "scada", "alarms": [
                            {"target_node_id": "plc", "address": "DB1.DBX0.0",
                             "mode": "state", "expected_state": True, "delay_ms": 1500},
                        ]}},
            {"node_id": "plc", "device_type": "plc-s7", "name": "S7 PLC", "ip": str(base_ip + 11),
             "config": {"components": [
                 {"type": "valve", "name": "Inlet valve"},
                 {"type": "digital_input", "name": "High level alarm"},
                 {"type": "io_channel", "name": "Flow setpoint", "direction": "output", "data_type": "REAL", "unit": "L/min"},
             ]}},
            {"node_id": "mb", "device_type": "modbus-tcp", "name": "Modbus controller", "ip": str(base_ip + 12),
             "config": {"unit_id": 1, "port": 502, "points": [
                 {"type": "coil", "name": "Pump start"},
                 {"type": "holding_register", "name": "Level setpoint"},
                 {"type": "input_register", "name": "Measured level", "initial_value": 42},
             ]}},
            {"node_id": "scada", "device_type": "scada", "name": "Alarm SCADA", "ip": str(base_ip + 13),
             "config": {"target_node_ids": "plc", "poll_interval_ms": 250, "alarms": [
                 {"target_node_id": "plc", "address": "DB1.DBD2", "above": -1}]}},
        ],
    }
    scenario = call("/api/scenarios", "POST", payload)
    root = f"/api/scenarios/{scenario['id']}"
    try:
        call(root + "/deploy", "POST", {})
        initial = call(root + "/hmi/hmi/values")
        values = {(item["node_id"], item["address"]): item for item in initial["values"]}
        assert values[("plc", "DB1.DBX0.0")]["access"] == "read_write"
        assert values[("plc", "DB1.DBX0.1")]["access"] == "read"
        real_outputs = [item for item in values.values() if item["node_id"] == "plc" and item["data_type"] == "REAL" and item["access"] == "read_write"]
        assert len(real_outputs) == 1
        setpoint_address = real_outputs[0]["address"]
        assert values[("mb", "coil:0")]["access"] == "read_write"
        assert values[("mb", "holding_register:0")]["access"] == "read_write"
        assert values[("mb", "input_register:0")]["access"] == "read"
        state_alarm = next(item for item in initial["alarms"] if item.get("hmi_owned"))
        assert state_alarm["mode"] == "state" and not state_alarm["active"], state_alarm
        def write_input(enabled):
            # Toggle the BOOL through the HMI; its alarm reads the result over live Snap7.
            call(root + "/hmi/hmi/write", "POST", {
                "target_node_id": "plc", "address": "DB1.DBX0.0", "value": enabled,
            })

        write_input(True)
        deadline = time.monotonic() + 10
        activation_started = time.monotonic()
        alarm_snapshot = initial
        saw_pending = False
        while time.monotonic() < deadline:
            alarm_snapshot = call(root + "/hmi/hmi/values")
            state_alarm = next(item for item in alarm_snapshot["alarms"] if item.get("hmi_owned"))
            if state_alarm["pending"]:
                saw_pending = True
                assert not state_alarm["active"], state_alarm
            if state_alarm["active"]:
                break
            time.sleep(0.25)
        assert state_alarm["active"], alarm_snapshot
        assert saw_pending, "HMI alarm did not expose its pending delay state"
        assert time.monotonic() - activation_started >= 1.5, "HMI alarm activated before its configured delay"
        state_ack = call(root + "/hmi/hmi/alarms/acknowledge", "POST", {
            "target_node_id": "plc", "address": "DB1.DBX0.0",
        })
        assert state_ack["acknowledged"] and state_ack["hmi_owned"]
        write_input(False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            alarm_snapshot = call(root + "/hmi/hmi/values")
            state_alarm = next(item for item in alarm_snapshot["alarms"] if item.get("hmi_owned"))
            if not state_alarm["active"]:
                break
            time.sleep(0.25)
        assert not state_alarm["active"], state_alarm
        assert [item["transition"] for item in alarm_snapshot["alarm_history"]] == ["activated", "cleared"]

        alarm = next(item for item in alarm_snapshot["alarms"] if item.get("scada_node_id") == "scada")
        deadline = time.monotonic() + 10
        while not alarm["active"] and time.monotonic() < deadline:
            time.sleep(0.25)
            alarm_snapshot = call(root + "/hmi/hmi/values")
            alarm = next(item for item in alarm_snapshot["alarms"] if item.get("scada_node_id") == "scada")
        assert alarm["active"], alarm_snapshot
        assert alarm["scada_node_id"] == "scada" and not alarm["acknowledged"]
        call(root + "/hmi/hmi/alarms/acknowledge", "POST", {
            "scada_node_id": "scada", "target_node_id": alarm["target_node_id"], "address": alarm["address"],
        })
        call(root + "/nodes/scada/lifecycle", "POST", {"action": "restart"})
        alarm_after_ack = next(item for item in call(root + "/hmi/hmi/values")["alarms"]
                               if item.get("scada_node_id") == "scada")
        assert alarm_after_ack["active"] and alarm_after_ack["acknowledged"], alarm_after_ack
        deadline = time.monotonic() + 15
        while alarm_after_ack.get("stale") and time.monotonic() < deadline:
            time.sleep(0.25)
            alarm_after_ack = next(item for item in call(root + "/hmi/hmi/values")["alarms"]
                                   if item.get("scada_node_id") == "scada")
        assert alarm_after_ack["acknowledged"] is True, alarm_after_ack
        assert alarm_after_ack.get("stale") is not True, alarm_after_ack

        call(root + "/hmi/hmi/write", "POST", {"target_node_id": "plc", "address": "DB1.DBX0.0", "value": True})
        call(root + "/hmi/hmi/write", "POST", {"target_node_id": "plc", "address": setpoint_address, "value": 37.5})
        call(root + "/hmi/hmi/write", "POST", {"target_node_id": "mb", "address": "coil:0", "value": True})
        call(root + "/hmi/hmi/write", "POST", {"target_node_id": "mb", "address": "holding_register:0", "value": 789})
        denied = call(root + "/hmi/hmi/write", "POST", {"target_node_id": "mb", "address": "input_register:0", "value": 99}, expected_error=403)
        unselected = call(root + "/hmi/hmi/write", "POST", {"target_node_id": "unselected", "address": "coil:0", "value": True}, expected_error=403)
        assert denied[0] == unselected[0] == 403

        final = call(root + "/hmi/hmi/values")
        values = {(item["node_id"], item["address"]): item["value"] for item in final["values"]}
        assert values[("plc", "DB1.DBX0.0")] is True
        assert values[("plc", setpoint_address)] == 37.5
        assert values[("mb", "coil:0")] is True
        assert values[("mb", "holding_register:0")] == 789
        assert values[("mb", "input_register:0")] == 42
        events = call(root + "/audit-events")
        assert any(item["event_type"] == "hmi.alarm_acknowledged" for item in events["events"])
        assert any(item["event_type"] == "hmi.owned_alarm_activated" for item in events["events"])
        assert any(item["event_type"] == "hmi.owned_alarm_cleared" for item in events["events"])
        print("HMI live S7/Modbus reads/writes, BOOL alarm activation/ack/clear and SCADA alarm acknowledgement OK")
    finally:
        try:
            call(root + "/stop", "POST", {})
            call(root, "DELETE")
        finally:
            docker_client.close()


if __name__ == "__main__":
    main()
