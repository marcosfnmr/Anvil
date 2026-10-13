import json
import ast
import hashlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proxy import CAPTURE_IMAGE, DockerGate, Handler, RUNTIME_EXEC_POLICY


SCENARIO = "12345678-1234-4234-8234-123456789abc"


class RuntimeOperationPolicyTests(unittest.TestCase):
    def test_fixed_operation_scripts_match_backend_and_reject_arbitrary_exec(self):
        gate = DockerGate(images={"anvil/runtime:test"})
        for rule in RUNTIME_EXEC_POLICY:
            source = Path(__file__).resolve().parents[2] / "backend/services" / rule["source"]
            tree = ast.parse(source.read_text())
            script = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                          if isinstance(node, ast.Assign) and any(
                              isinstance(target, ast.Name) and target.id == rule["constant"]
                              for target in node.targets))
            self.assertEqual(hashlib.sha256(script.encode()).hexdigest(), rule["sha256"])
            container = {"Config": {"Image": "anvil/runtime:test", "User": "65534:65534", "Labels": {
                "anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                "anvil.node_id": "test", "anvil.device_type": rule["device_type"],
            }}}
            args = ["python", "-c", script] + ["snapshot"] * (rule["argc"] - 3)
            config = {"Cmd": args}
            self.assertTrue(gate._runtime_operations_exec_valid(container, config))
            self.assertFalse(gate._runtime_operations_exec_valid(container, {**config, "Env": ["PYTHONPATH=/tmp"]}))
            self.assertFalse(gate._runtime_operations_exec_valid(container, {**config, "User": "0"}))
            self.assertFalse(gate._runtime_operations_exec_valid(container, {**config, "AttachStdin": True}))
            self.assertFalse(gate._runtime_operations_exec_valid(container, {"Cmd": ["sh"]}))
            self.assertFalse(gate._runtime_operations_exec_valid(container, {"Cmd": ["python", "-c", "print('arbitrary')"]}))
            args[2] += "\nprint('changed')"
            self.assertFalse(gate._runtime_operations_exec_valid(container, {"Cmd": args}))


def container_config(image="anvil/plc-s7:test", **host_changes):
    host = {
        "Privileged": False,
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "CapAdd": [],
        "SecurityOpt": ["no-new-privileges:true"],
        "NetworkMode": "anvil-12345678",
        "Memory": 134217728,
        "NanoCpus": 500000000,
        "PidsLimit": 32,
    }
    protocol_type = ("plc-s7" if image == "anvil/plc-s7:test" else
                     "modbus-tcp" if image == "anvil/modbus-tcp:test" else None)
    if protocol_type:
        host["LogConfig"] = {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}
        node_id = "plc-1"
        volume_name = (f"anvil-protocol-audit-{SCENARIO}-"
                       f"{hashlib.sha256(node_id.encode()).hexdigest()[:16]}")
        host["Mounts"] = [{"Type": "volume", "Source": volume_name,
                           "Target": "/var/lib/anvil/protocol-audit", "ReadOnly": False}]
        host.setdefault("_audit_volume_name", volume_name)
    host.update(host_changes)
    env = (["PLC_CONFIG={}"] if protocol_type == "plc-s7" else
           ["MODBUS_CONFIG={\"transport\":\"tcp\"}"] if protocol_type == "modbus-tcp" else ["PLC_CONFIG={}"])
    if protocol_type:
        env.append("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN=" + "x" * 43)
    labels = {
        "anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc-1", "anvil.hostname": "plc-1",
        **({"anvil.device_type": protocol_type} if protocol_type else {}),
        **({"anvil.protocol_audit_queue": "true"} if protocol_type else {}),
        **({"anvil.image_reference": image} if protocol_type else {}),
    }
    # Test-only marker is not part of the Docker API HostConfig.
    host.pop("_audit_volume_name", None)
    return {"Image": image, "Env": env, "User": "65534:65534", "Labels": labels,
      "Hostname": "plc-1", "HostConfig": host,
      "NetworkingConfig": {"EndpointsConfig": {"anvil-12345678": {
          "IPAMConfig": {"IPv4Address": "10.20.0.3"}, "Aliases": ["plc-1"]}}}}


class DockerGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = DockerGate({"anvil/plc-s7:test", "anvil/modbus-tcp:test", "anvil/scada:test",
                                CAPTURE_IMAGE, "anvil/egress-proxy:test"},
                               plc_images={"anvil/plc-s7:test"}, modbus_images={"anvil/modbus-tcp:test"},
                               egress_images={"anvil/egress-proxy:test"}, scada_images={"anvil/scada:test"})

    def auth(self, method, path, query="", body=None, inspect=None):
        raw = json.dumps(body).encode() if body is not None else b""
        return self.gate.authorize(method, path, query, raw, inspect)

    def test_unlisted_docker_api_mutations_are_denied_by_default(self):
        denied = [
            ("GET", "/info"),
            ("GET", "/images/json"),
            ("POST", "/images/create"),
            ("DELETE", "/images/sha256:" + "a" * 64),
            ("POST", "/build"),
            ("POST", "/commit"),
            ("POST", "/volumes/create"),
            ("DELETE", "/volumes/anvil-data"),
            ("POST", "/plugins/pull"),
            ("POST", "/services/create"),
            ("POST", "/secrets/create"),
            ("POST", "/swarm/init"),
            ("POST", "/containers/abcdef012345/exec"),
            ("GET", "/containers/json"),
            ("POST", "/v1.45/networks/create"),
        ]
        for method, path in denied:
            with self.subTest(method=method, path=path):
                self.assertFalse(self.auth(method, path, body={})[0])

    def test_retention_volumes_are_created_and_deleted_only_when_owned(self):
        node = "plc-1"
        volume_name = f"anvil-retain-{SCENARIO}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
        labels = {"anvil.managed": "true", "anvil.purpose": "plc-retention",
                  "anvil.scenario_id": SCENARIO, "anvil.node_id": node, "anvil.volume_name": volume_name}
        self.assertTrue(self.auth("POST", "/volumes/create", body={"Name": volume_name, "Driver": "local", "Labels": labels})[0])
        self.assertFalse(self.auth("POST", "/volumes/create", body={"Name": "anvil-data", "Driver": "local", "Labels": labels})[0])
        inspect = lambda kind, _name: {"Name": volume_name, "Labels": labels} if kind == "volume" else None
        self.assertTrue(self.auth("GET", f"/volumes/{volume_name}", inspect=inspect)[0])
        self.assertFalse(self.auth("DELETE", "/volumes/anvil-data", inspect=inspect)[0])
        self.assertFalse(self.auth("DELETE", f"/volumes/{volume_name}", query="force=true", inspect=inspect)[0])

    def test_missing_managed_volume_can_return_docker_not_found(self):
        missing = lambda _kind, _name: None
        for purpose in ("retain", "scada-history", "protocol-audit"):
            name = f"anvil-{purpose}-{SCENARIO}-{'a' * 16}"
            self.assertTrue(self.auth("GET", f"/volumes/{name}", inspect=missing)[0])
            self.assertFalse(self.auth("DELETE", f"/volumes/{name}", inspect=missing)[0])
            foreign = lambda _kind, _name: {"Name": name, "Labels": {}}
            self.assertFalse(self.auth("GET", f"/volumes/{name}", inspect=foreign)[0])
        self.assertFalse(self.auth("GET", "/volumes/unmanaged", inspect=missing)[0])
        self.assertFalse(self.auth("GET", "/volumes/anvil-retain-invalid-aaaaaaaaaaaaaaaa", inspect=missing)[0])

    def test_only_retentive_s7_may_mount_its_scenario_owned_volume(self):
        node = "plc-1"
        volume_name = f"anvil-retain-{SCENARIO}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
        volume_labels = {"anvil.managed": "true", "anvil.purpose": "plc-retention",
                         "anvil.scenario_id": SCENARIO, "anvil.node_id": node, "anvil.volume_name": volume_name}
        config = container_config()
        config["Env"] = ["PLC_CONFIG={\"retain_logic_state\":true}",
                         "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN=" + "x" * 43]
        config["Labels"]["anvil.retain_logic_state"] = "true"
        config["HostConfig"]["Mounts"] = [{"Type": "volume", "Source": volume_name,
                                            "Target": "/var/lib/anvil/retention", "ReadOnly": False}]
        audit_volume_name = f"anvil-protocol-audit-{SCENARIO}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
        audit_volume_labels = {"anvil.managed": "true", "anvil.purpose": "protocol-audit",
                               "anvil.scenario_id": SCENARIO, "anvil.node_id": node,
                               "anvil.volume_name": audit_volume_name}
        config["HostConfig"]["Mounts"].append({"Type": "volume", "Source": audit_volume_name,
                                                "Target": "/var/lib/anvil/protocol-audit", "ReadOnly": False})
        inspect = lambda kind, name: ({"Name": name, "Labels": (volume_labels if name == volume_name
                                                                   else audit_volume_labels)} if kind == "volume"
                                       else {"Name": "anvil-12345678", "Driver": "bridge", "Internal": True,
                                             "Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                                        "anvil.zone_id": "default"}} if kind == "network" else None)
        self.assertTrue(self.auth("POST", "/containers/create", query="name=anvil-plc", body=config, inspect=inspect)[0])
        config["HostConfig"]["Mounts"][0]["Target"] = "/etc"
        self.assertFalse(self.auth("POST", "/containers/create", query="name=anvil-plc", body=config, inspect=inspect)[0])

    def test_only_scada_may_mount_its_exact_history_buffer_volume(self):
        node = "scada-1"
        volume_name = f"anvil-scada-history-{SCENARIO}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
        volume_labels = {"anvil.managed": "true", "anvil.purpose": "scada-history",
                         "anvil.scenario_id": SCENARIO, "anvil.node_id": node, "anvil.volume_name": volume_name}
        self.assertTrue(self.auth("POST", "/volumes/create", body={
            "Name": volume_name, "Driver": "local", "Labels": volume_labels,
        })[0])
        config = container_config(image="anvil/scada:test")
        config["Env"] = ["SCADA_CONFIG={\"durable_history_path\":\"/var/lib/anvil/scada-history/history.sqlite3\",\"durable_history_capacity\":100000}"]
        config["Env"].append("SCADA_HISTORY_ACK_TOKEN=" + "x" * 43)
        config["Labels"]["anvil.node_id"] = node
        config["Labels"]["anvil.hostname"] = node
        config["Hostname"] = node
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-12345678"]["Aliases"] = [node]
        config["Labels"]["anvil.scada_history_buffer"] = "true"
        config["HostConfig"]["Mounts"] = [{"Type": "volume", "Source": volume_name,
                                             "Target": "/var/lib/anvil/scada-history", "ReadOnly": False}]
        inspect = lambda kind, _name: ({"Name": volume_name, "Labels": volume_labels} if kind == "volume"
                                       else {"Name": "anvil-12345678", "Driver": "bridge", "Internal": True,
                                             "Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                                        "anvil.zone_id": "default"}} if kind == "network" else None)
        allowed, reason = self.auth("POST", "/containers/create", query="name=anvil-scada", body=config, inspect=inspect)
        self.assertTrue(allowed, reason)
        config["Env"][1] = "SCADA_HISTORY_ACK_TOKEN=invalid"
        self.assertFalse(self.auth("POST", "/containers/create", query="name=anvil-scada", body=config, inspect=inspect)[0])
        config["Env"][1] = "SCADA_HISTORY_ACK_TOKEN=" + "x" * 43
        config["HostConfig"]["Mounts"][0]["Target"] = "/etc"
        self.assertFalse(self.auth("POST", "/containers/create", query="name=anvil-scada", body=config, inspect=inspect)[0])

    def test_protocol_runtimes_require_bounded_json_logs(self):
        for device_type in ("plc-s7", "modbus-tcp"):
            with self.subTest(device_type=device_type):
                config = container_config(
                    "anvil/modbus-tcp:test" if device_type == "modbus-tcp" else "anvil/plc-s7:test"
                )
                config["Labels"]["anvil.device_type"] = device_type
                config["HostConfig"]["LogConfig"] = {
                    "Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"},
                }
                allowed, reason = self.auth(
                    "POST", "/containers/create", query=f"name=anvil-{device_type}",
                    body=config, inspect=self.inspect_owned,
                )
                self.assertTrue(allowed, reason)

                config["HostConfig"]["LogConfig"]["Config"]["max-file"] = "1000"
                self.assertFalse(self.auth(
                    "POST", "/containers/create", query=f"name=anvil-{device_type}",
                    body=config, inspect=self.inspect_owned,
                )[0])

                config["HostConfig"]["LogConfig"]["Config"]["max-file"] = "3"
                config["HostConfig"]["LogConfig"]["Config"]["max-size"] = "unbounded"
                self.assertFalse(self.auth(
                    "POST", "/containers/create", query=f"name=anvil-{device_type}",
                    body=config, inspect=self.inspect_owned,
                )[0])

        config["Labels"]["anvil.device_type"] = "hmi"
        config["HostConfig"]["LogConfig"] = {"Type": "json-file", "Config": {}}
        self.assertFalse(self.auth(
            "POST", "/containers/create", query="name=anvil-hmi",
            body=config, inspect=self.inspect_owned,
        )[0])

    def test_protocol_audit_exec_is_limited_to_fixed_pending_and_ack_commands(self):
        config = container_config()
        container_id = "a" * 64
        volume_name = config["HostConfig"]["Mounts"][0]["Source"]
        volume_labels = {
            "anvil.managed": "true", "anvil.purpose": "protocol-audit",
            "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc-1",
            "anvil.volume_name": volume_name,
        }
        inspected = {"Config": {"Labels": config["Labels"], "Env": config["Env"]},
                     "HostConfig": config["HostConfig"]}

        def inspect(kind, _name):
            if kind == "container":
                return inspected
            if kind == "volume":
                return {"Name": volume_name, "Labels": volume_labels}
            return None

        base = {"Container": container_id, "User": "65534:65534", "Privileged": False,
                "Tty": False, "AttachStdin": False, "AttachStdout": True,
                "AttachStderr": True, "Cmd": ["python", "protocol_audit.py", "pending", "1000"],
                "Env": None, "WorkingDir": "/app"}
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=base,
                                  inspect=inspect)[0])
        ack = dict(base, Cmd=["python", "protocol_audit.py", "ack", "42"],
                   Env=["ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN=" + "x" * 43])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=ack,
                                  inspect=inspect)[0])
        for invalid in (
            dict(base, Cmd=["/bin/sh", "-i"]),
            dict(base, User="0:0"),
            dict(base, Privileged=True),
            dict(base, WorkingDir="/"),
            dict(ack, Env=["ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN=" + "y" * 43]),
        ):
            self.assertFalse(self.auth("POST", f"/containers/{container_id}/exec", body=invalid,
                                       inspect=inspect)[0])

        exec_inspect = lambda kind, _name: (
            inspected if kind == "exec" else
            {"Name": volume_name, "Labels": volume_labels} if kind == "volume" else None
        )
        self.assertTrue(self.auth("GET", "/exec/exec-id/json", inspect=exec_inspect)[0])

    @staticmethod
    def inspect_owned(kind, _resource_id):
        if kind == "network":
            return {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                               "anvil.zone_id": "default"}, "Internal": True}
        if kind == "volume" and _resource_id.startswith("anvil-protocol-audit-"):
            node = "plc-1"
            expected = f"anvil-protocol-audit-{SCENARIO}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
            if _resource_id == expected:
                return {"Name": expected, "Labels": {
                    "anvil.managed": "true", "anvil.purpose": "protocol-audit",
                    "anvil.scenario_id": SCENARIO, "anvil.node_id": node,
                    "anvil.volume_name": expected,
                }}
        return None

    def test_only_a_pinned_policy_egress_container_can_join_default_bridge(self):
        image = "anvil/egress-proxy:test"
        container_id = "a" * 64
        rules = [{"hostname": "docs.example.net", "tcp_port": 443}]
        labels = {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "__anvil_egress_station",
            "anvil.hostname": "egress-station", "anvil.zone_id": "default", "anvil.role": "egress-gateway",
            "anvil.image_reference": image, "anvil.egress_source_node_id": "station-1",
            "anvil.egress_source_ip": "10.20.0.10", "anvil.egress_source_zone": "default",
            "anvil.egress_allowlist": json.dumps(rules, sort_keys=True, separators=(",", ":")),
        }
        config = container_config(image)
        config["Cmd"] = ["python", "-u", "egress_proxy.py"]
        config["Hostname"] = "egress-station"
        config["Env"] = ["ANVIL_EGRESS_CONFIG=" + json.dumps({
            "listen_ip": "10.20.0.11", "source_ip": "10.20.0.10", "rules": rules,
        }, separators=(",", ":"))]
        config["Labels"] = labels
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-12345678"] = {
            "IPAMConfig": {"IPv4Address": "10.20.0.11"}, "Aliases": ["egress-station"],
        }
        create_inspect = lambda kind, _resource: self.inspect_owned(kind, _resource)
        create_result = self.auth("POST", "/containers/create", "name=anvil-egress-station",
                                  body=config, inspect=create_inspect)
        self.assertTrue(create_result[0], create_result[1])
        config["Cmd"] = ["python", "-c", "import os; os.system('sh')"]
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-egress-station",
                                   body=config, inspect=create_inspect)[0])

        container = {"Config": {"Labels": labels}, "NetworkSettings": {"Networks": {"anvil-12345678": {}}}}
        internal_network = {
            "Id": "b" * 64, "Name": "anvil-12345678", "Driver": "bridge", "Internal": True,
            "Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.zone_id": "default"},
            "IPAM": {"Config": [{"Subnet": "10.20.0.0/24"}]},
        }
        bridge = {"Id": "c" * 64, "Name": "bridge", "Driver": "bridge", "Internal": False}

        def inspect(kind, resource_id):
            if kind == "network" and resource_id in {"bridge", bridge["Id"]}:
                return bridge
            if kind == "network" and resource_id == "anvil-12345678":
                return internal_network
            if kind == "container" and resource_id == container_id:
                return container
            return None

        connect = {"Container": container_id, "EndpointConfig": {
            "IPAMConfig": None, "Links": None, "Aliases": None, "DriverOpts": None, "MacAddress": None,
        }}
        connect_result = self.auth("POST", f"/networks/{'c' * 64}/connect", body=connect, inspect=inspect)
        self.assertTrue(connect_result[0], connect_result[1])
        connect["Container"] = "d" * 64
        self.assertFalse(self.auth("POST", f"/networks/{'c' * 64}/connect", body=connect, inspect=inspect)[0])

    def test_only_owned_networks_are_created_and_removed(self):
        config = {"Name": "anvil-12345678", "Driver": "bridge", "Internal": True,
                  "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                  "IPAM": {"Driver": "default", "Config": [{"Subnet": "10.20.0.0/24"}]},
                  "Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.zone_id": "default"}}
        self.assertTrue(self.auth("POST", "/v1.44/networks/create", body=config)[0])
        config["Options"] = {"com.docker.network.bridge.gateway_mode_ipv4": "nat"}
        self.assertFalse(self.auth("POST", "/networks/create", body=config)[0])
        config["Options"] = {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}
        config["Internal"] = False
        self.assertFalse(self.auth("POST", "/networks/create", body=config)[0])

        inspect = lambda kind, _id: {"Labels": config["Labels"], "Internal": True}
        self.assertTrue(self.auth("DELETE", "/networks/abcdef0123456789", inspect=inspect)[0])
        self.assertFalse(self.auth("DELETE", "/networks/abcdef0123456789", inspect=lambda *_: None)[0])

    def test_container_create_requires_allowlisted_image_and_full_hardening(self):
        self.assertTrue(self.auth("POST", "/v1.44/containers/create", "name=anvil-plc", body=container_config(), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config("nginx:latest"), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(Privileged=True), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(Binds=["/:/host"]), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(PortBindings={"102/tcp": [{"HostIp": "0.0.0.0", "HostPort": "102"}]}), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(IpcMode="host"), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(Runtime="malicious"), inspect=self.inspect_owned)[0])
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=container_config(), inspect=lambda *_: None)[0])

    def test_pinned_image_requires_matching_allowlisted_local_pin_and_image_id(self):
        image_id = "sha256:" + "a" * 64
        image_pin = "anvil-pinned/123456789abc:" + "a" * 64
        config = container_config()
        config["Image"] = image_id
        config["Labels"].update({"anvil.image_id": image_id,
                                 "anvil.image_reference": "anvil/plc-s7:test",
                                 "anvil.image_pin": image_pin})
        inspected_image = {"Id": image_id, "RepoTags": [image_pin]}

        def inspect(kind, resource_id):
            if kind == "image" and resource_id == image_id:
                return inspected_image
            return self.inspect_owned(kind, resource_id)

        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-plc",
                                  body=config, inspect=inspect)[0])
        inspected_image["Id"] = "sha256:" + "c" * 64
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc",
                                   body=config, inspect=inspect)[0])
        inspected_image["Id"] = image_id
        inspected_image["RepoTags"] = []
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc",
                                   body=config, inspect=inspect)[0])

    def test_image_inspection_is_limited_to_allowlisted_references_and_verified_pins(self):
        image_id = "sha256:" + "a" * 64
        image_pin = "anvil-pinned/123456789abc:" + "a" * 64
        image = {"Id": image_id, "RepoTags": ["anvil/plc-s7:test", image_pin]}
        inspect = lambda kind, resource: image if kind == "image" and resource in {image_id, image_pin, "anvil/plc-s7:test"} else None

        self.assertTrue(self.auth("GET", f"/images/{quote('anvil/plc-s7:test', safe='')}/json", inspect=inspect)[0])
        self.assertTrue(self.auth("GET", f"/images/{quote(image_pin, safe='')}/json", inspect=inspect)[0])
        self.assertFalse(self.auth("GET", f"/images/{quote('nginx:latest', safe='')}/json", inspect=inspect)[0])

    def test_only_matching_non_forced_tags_can_create_anvil_image_pins(self):
        image_id = "sha256:" + "a" * 64
        image = {"Id": image_id, "RepoTags": ["anvil/plc-s7:test"]}
        inspect = lambda kind, resource: image if kind == "image" and resource == image_id else None
        query = urlencode({"repo": "anvil-pinned/" + hashlib.sha256(b"anvil/plc-s7").hexdigest()[:12],
                           "tag": "a" * 64, "force": "0"})
        path = f"/images/{quote(image_id, safe='')}/tag"

        self.assertTrue(self.auth("POST", path, query, inspect=inspect)[0])
        self.assertFalse(self.auth("POST", path, query.replace("force=0", "force=1"), inspect=inspect)[0])
        self.assertFalse(self.auth("POST", path, query.replace("tag=" + "a" * 64, "tag=" + "b" * 64), inspect=inspect)[0])

    def test_protocol_bindings_are_rejected_on_devices_even_when_explicitly_requested(self):
        config = container_config(PortBindings={"102/tcp": [{"HostIp": "192.0.2.10", "HostPort": "1102"}]})
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])
        config["Labels"].update({"anvil.external_access": "true", "anvil.external_host_ip": "192.0.2.10",
                                 "anvil.external_port": "1102", "anvil.external_container_port": "102"})
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])
        config["Labels"]["anvil.external_container_port"] = "1502"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])
        config["Labels"]["anvil.external_container_port"] = "102"
        config["HostConfig"]["PortBindings"]["102/tcp"][0]["HostIp"] = "0.0.0.0"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])
        config["HostConfig"]["PortBindings"]["102/tcp"][0]["HostIp"] = "192.0.2.10"
        config["Labels"]["anvil.external_host_ip"] = "0.0.0.0"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])

    def test_external_binding_is_rejected_on_modbus_device(self):
        gate = DockerGate({"anvil/modbus-tcp:test"}, plc_images=set(), modbus_images={"anvil/modbus-tcp:test"})
        config = container_config("anvil/modbus-tcp:test", PortBindings={
            "1502/tcp": [{"HostIp": "127.0.0.1", "HostPort": "2502"}],
        })
        config["Env"] = ['MODBUS_CONFIG={"port":1502}']
        config["Labels"].update({"anvil.external_access": "true", "anvil.external_host_ip": "127.0.0.1",
                                 "anvil.external_port": "2502", "anvil.external_container_port": "1502"})
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-modbus", json.dumps(config).encode(), self.inspect_owned)[0])
        config["HostConfig"]["PortBindings"]["1502/tcp"][0]["HostPort"] = "2503"
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-modbus", json.dumps(config).encode(), self.inspect_owned)[0])
        config["HostConfig"]["PortBindings"]["1502/tcp"][0]["HostPort"] = "2502"
        config["Env"] = ["MODBUS_CONFIG=[]"]
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-modbus", json.dumps(config).encode(), self.inspect_owned)[0])

    def test_protocol_ingress_profile_allows_wildcard_container_bind_but_not_host_wildcard(self):
        image = "anvil/protocol-ingress:test"
        pinned_id = "sha256:" + "c" * 64
        image_pin = f"anvil-pinned/{hashlib.sha256(b'anvil/protocol-ingress').hexdigest()[:12]}:{'c' * 64}"
        gate = DockerGate({image, "anvil/plc-s7:test"}, plc_images={"anvil/plc-s7:test"}, modbus_images=set(),
                          ingress_images={image})
        target_id = "b" * 64
        bridge = {"Name": "bridge", "Driver": "bridge", "Internal": False,
                  "IPAM": {"Config": [{"Gateway": "172.17.0.1"}]}}
        zone_labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.zone_id": "default"}
        zone = {"Name": "anvil-12345678", "Driver": "bridge", "Internal": True,
                "Labels": zone_labels, "IPAM": {"Config": [{"Subnet": "10.20.0.0/24"}]},
                "Containers": {target_id: {"IPv4Address": "10.20.0.10/24"}}}
        target = {"Config": {"Labels": {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc-1",
            "anvil.zone_id": "default", "anvil.device_type": "plc-s7",
            "anvil.image_reference": "anvil/plc-s7:test",
        }, "Env": ["PLC_CONFIG={}"]}}

        def inspect(kind, resource_id):
            if kind == "image" and resource_id == pinned_id:
                return {"Id": pinned_id, "RepoTags": [image_pin]}
            if kind == "network" and resource_id == "bridge":
                return bridge
            if kind == "network" and resource_id == "anvil-12345678":
                return zone
            if kind == "container" and resource_id == target_id:
                return target
            return None

        ingress_config = {
            "listen_ip": "10.20.0.11", "listen_port": 25000, "target_ip": "10.20.0.10",
            "target_port": 102, "allowed_peer_ip": "172.17.0.1", "bind_ip": "0.0.0.0",
        }
        config = container_config(pinned_id, NetworkMode="bridge", PortBindings={
            "25000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1102"}],
        })
        config["Cmd"] = ["python", "-u", "ingress_proxy.py"]
        config["Env"] = ["ANVIL_INGRESS_CONFIG=" + json.dumps(ingress_config)]
        config["NetworkingConfig"] = {"EndpointsConfig": {}}
        config["Labels"].update({
            "anvil.role": "protocol-ingress", "anvil.zone_id": "default",
            "anvil.image_reference": image, "anvil.external_access": "true",
            "anvil.image_id": pinned_id, "anvil.image_pin": image_pin,
            "anvil.external_host_ip": "127.0.0.1", "anvil.external_port": "1102",
            "anvil.external_container_port": "25000", "anvil.ingress_target_node_id": "plc-1",
            "anvil.ingress_target_port": "102", "anvil.ingress_protocol": "s7",
            "anvil.ingress_zone_network": "anvil-12345678",
            "anvil.ingress_allowed_peer_ip": "172.17.0.1",
        })
        config["Labels"].pop("anvil.protocol_audit_queue", None)
        config["HostConfig"].pop("Mounts", None)
        accepted, detail = gate.authorize("POST", "/containers/create", "name=anvil-ingress",
                                          json.dumps(config).encode(), inspect)
        self.assertTrue(accepted, detail)

        config["Labels"]["anvil.image_reference"] = "anvil/plc-s7:test"
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-ingress",
                                         json.dumps(config).encode(), inspect)[0])
        config["Labels"]["anvil.image_reference"] = image
        config["Image"] = "anvil/plc-s7:test"
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-ingress",
                                         json.dumps(config).encode(), inspect)[0])
        config["Image"] = pinned_id

        ingress_config["bind_ip"] = "10.20.0.11"
        config["Env"] = ["ANVIL_INGRESS_CONFIG=" + json.dumps(ingress_config)]
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-ingress",
                                        json.dumps(config).encode(), inspect)[0])
        ingress_config["bind_ip"] = "0.0.0.0"
        config["Env"] = ["ANVIL_INGRESS_CONFIG=" + json.dumps(ingress_config)]
        config["HostConfig"]["PortBindings"]["25000/tcp"][0]["HostIp"] = "0.0.0.0"
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-ingress",
                                        json.dumps(config).encode(), inspect)[0])

        config["HostConfig"]["PortBindings"]["25000/tcp"][0]["HostIp"] = "127.0.0.1"
        ingress_id = "a" * 64
        relay = {"Config": {"Labels": config["Labels"], "Env": config["Env"]},
                 "NetworkSettings": {"Networks": {"bridge": {"NetworkID": "bridge-id"}}}}

        def inspect_with_relay(kind, resource_id):
            if kind == "container" and resource_id == ingress_id:
                return relay
            return inspect(kind, resource_id)

        endpoint = {"IPAMConfig": {"IPv4Address": "10.20.0.11"}}
        connected, detail = gate.authorize(
            "POST", "/networks/anvil-12345678/connect", "",
            json.dumps({"Container": ingress_id, "EndpointConfig": endpoint}).encode(),
            inspect_with_relay,
        )
        self.assertTrue(connected, detail)
        endpoint["IPAMConfig"]["IPv4Address"] = "10.20.0.12"
        self.assertFalse(gate.authorize(
            "POST", "/networks/anvil-12345678/connect", "",
            json.dumps({"Container": ingress_id, "EndpointConfig": endpoint}).encode(),
            inspect_with_relay,
        )[0])

    def test_auxiliary_allowlisted_image_cannot_publish_a_fake_protocol_port(self):
        gate = DockerGate({"anvil/auxiliary:test", "anvil/plc-s7:test"}, plc_images={"anvil/plc-s7:test"}, modbus_images=set())
        config = container_config("anvil/auxiliary:test", PortBindings={
            "102/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1102"}],
        })
        config["Labels"].update({"anvil.external_access": "true", "anvil.external_host_ip": "127.0.0.1",
                                 "anvil.external_port": "1102", "anvil.external_container_port": "102"})
        self.assertFalse(gate.authorize("POST", "/containers/create", "name=anvil-aux", json.dumps(config).encode(), self.inspect_owned)[0])

    def test_network_alias_is_limited_to_declared_hostname(self):
        config = container_config()
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-12345678"]["Aliases"] = ["spoofed-plc"]
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])

    def test_only_an_explicit_route_gateway_may_connect_to_two_owned_zone_networks(self):
        config = container_config()
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-operations"] = {
            "IPAMConfig": {"IPv4Address": "10.21.0.3"}, "Aliases": [],
        }
        networks = {
            "anvil-12345678": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                            "anvil.zone_id": "control"}, "Internal": True,
                                "IPAM": {"Config": [{"Subnet": "10.20.0.0/24"}]}},
            "anvil-operations": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                              "anvil.zone_id": "operations"}, "Internal": True,
                                  "IPAM": {"Config": [{"Subnet": "10.21.0.0/24"}]}},
        }
        inspect = lambda kind, name: networks.get(name) if kind == "network" else None
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=inspect)[0])
        config["Labels"].update({
            "anvil.role": "tcp-gateway", "anvil.device_type": "tcp-gateway",
            "anvil.route_target_ip": "10.21.0.10",
            "anvil.route_target_port": "102", "anvil.route_target_node_id": "plc-1",
            "anvil.route_source_node_id": "hmi-1", "anvil.route_source_ip": "10.20.0.10",
            "anvil.route_protocol": "s7", "anvil.route_source_zone": "control",
            "anvil.route_target_zone": "operations", "anvil.route_listen_port": "31024",
            "anvil.route_listen_ip": "10.20.0.3", "anvil.route_inspect_protocol": "none",
            "anvil.route_latency_ms": "0",
        })
        config["Labels"].pop("anvil.protocol_audit_queue", None)
        config["Labels"].update({"anvil.node_id": "__anvil_route_route", "anvil.hostname": "route1"})
        config["Hostname"] = "route1"
        control_token = "r" * 43
        gateway_config = {
            "route_id": "route", "listen_ip": "10.20.0.3", "listen_port": 31024,
            "source_ip": "10.20.0.10", "target_ip": "10.21.0.10", "target_port": 102,
            "latency_ms": 0, "inspect_protocol": None, "control_token": control_token,
        }
        config["Cmd"] = ["python", "-u", "tcp_gateway.py"]
        config["Env"] = ["ANVIL_TCP_GATEWAY_CONFIG=" + json.dumps(gateway_config),
                         "ANVIL_TCP_GATEWAY_CONTROL_TOKEN=" + control_token]
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-12345678"]["Aliases"] = ["route1"]

        def sync_gateway_config():
            gateway_config["target_ip"] = config["Labels"]["anvil.route_target_ip"]
            gateway_config["target_port"] = int(config["Labels"]["anvil.route_target_port"])
            gateway_config["source_ip"] = config["Labels"]["anvil.route_source_ip"]
            gateway_config["listen_port"] = int(config["Labels"]["anvil.route_listen_port"])
            gateway_config["listen_ip"] = config["Labels"]["anvil.route_listen_ip"]
            gateway_config["latency_ms"] = int(config["Labels"].get("anvil.route_latency_ms", "0"))
            inspect_label = config["Labels"].get("anvil.route_inspect_protocol", "none")
            gateway_config["inspect_protocol"] = None if inspect_label == "none" else inspect_label
            config["Env"][0] = "ANVIL_TCP_GATEWAY_CONFIG=" + json.dumps(gateway_config)

        config["HostConfig"].pop("Mounts", None)
        config["HostConfig"]["LogConfig"] = {"Type": "json-file", "Config": {}}
        accepted, detail = self.auth("POST", "/containers/create", "name=anvil-route", body=config, inspect=inspect)
        self.assertTrue(accepted, detail)
        for latency in ("-1", "10001", "invalid"):
            config["Labels"]["anvil.route_latency_ms"] = latency
            self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-route", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_latency_ms"] = "0"
        config["Labels"].update({"anvil.route_target_port": "25000", "anvil.route_protocol": "tcp"})
        sync_gateway_config()
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-generic-tcp-route", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_target_port"] = "65536"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-invalid-tcp-route", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_target_port"] = "102"
        config["Labels"]["anvil.route_source_ip"] = "192.0.2.10"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-invalid-source", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_source_ip"] = "10.20.0.10"
        config["Labels"]["anvil.route_target_ip"] = "192.0.2.10"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-invalid-target", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_target_ip"] = "10.21.0.10"
        config["Labels"]["anvil.route_target_port"] = "102"
        config["Labels"]["anvil.route_protocol"] = "s7"
        config["Labels"].update({
            "anvil.route_target_port": "8765", "anvil.route_target_node_id": "__anvil_field__",
            "anvil.route_protocol": "field-http",
        })
        sync_gateway_config()
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-field-route", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_target_port"] = "8766"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-field-route", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.route_target_port"] = "8765"
        config["NetworkingConfig"]["EndpointsConfig"]["anvil-other"] = config["NetworkingConfig"]["EndpointsConfig"].pop("anvil-operations")
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-route", body=config, inspect=inspect)[0])
        config = container_config()
        config["Hostname"] = "spoofed-plc"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-plc", body=config, inspect=self.inspect_owned)[0])

    def test_modbus_route_control_exec_is_fixed_authenticated_and_rule_bounded(self):
        token = "q" * 43
        container_id = "a" * 64
        labels = {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO,
            "anvil.node_id": "__anvil_route_modbus-route", "anvil.hostname": "route1",
            "anvil.role": "tcp-gateway", "anvil.route_source_ip": "10.20.0.10",
            "anvil.route_target_ip": "10.21.0.10", "anvil.route_target_port": "502",
            "anvil.route_listen_ip": "10.20.0.3", "anvil.route_listen_port": "24000",
            "anvil.route_latency_ms": "0", "anvil.route_inspect_protocol": "modbus-tcp",
        }
        gateway_config = {
            "route_id": "modbus-route", "listen_ip": "10.20.0.3", "listen_port": 24000,
            "source_ip": "10.20.0.10", "target_ip": "10.21.0.10", "target_port": 502,
            "latency_ms": 0, "inspect_protocol": "modbus-tcp", "control_token": token,
        }
        inspected = {"Config": {
            "Labels": labels, "Image": "anvil/plc-s7:test",
            "Cmd": ["python", "-u", "tcp_gateway.py"],
            "Env": ["ANVIL_TCP_GATEWAY_CONFIG=" + json.dumps(gateway_config),
                    "ANVIL_TCP_GATEWAY_CONTROL_TOKEN=" + token],
        }}
        rule = {"rule_id": "pressure", "unit_id": 1, "function_code": 3,
                "address": 10, "replacement_value": 65000}
        base = {"Container": container_id, "User": "65534:65534", "Privileged": False,
                "Tty": False, "AttachStdin": False, "AttachStdout": True,
                "AttachStderr": True, "Cmd": ["python", "tcp_route_control.py", "set", json.dumps([rule])],
                "Env": ["ANVIL_TCP_GATEWAY_CONTROL_TOKEN=" + token], "WorkingDir": "/app"}
        inspect = lambda kind, _name: inspected if kind in {"container", "exec"} else None
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=base, inspect=inspect)[0])
        self.assertTrue(self.auth("POST", f"/exec/{'b' * 64}/start", body={}, inspect=inspect)[0])
        multiwrite_rule = dict(rule, function_code=16, address=10, replacement_value=65000)
        multiwrite_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([multiwrite_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=multiwrite_exec, inspect=inspect)[0])
        coil_rule = dict(rule, function_code=15, address=2, replacement_value=1)
        coil_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([coil_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=coil_exec, inspect=inspect)[0])
        single_coil_rule = dict(rule, function_code=5, address=2, replacement_value=1)
        single_coil_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([single_coil_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=single_coil_exec, inspect=inspect)[0])
        mask_rule = dict(rule, function_code=22, address=10, replacement_value=0xA55A)
        mask_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([mask_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=mask_exec, inspect=inspect)[0])
        read_write_rule = dict(rule, function_code=23, address=10, replacement_value=0xA55A)
        read_write_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([read_write_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=read_write_exec, inspect=inspect)[0])
        combined_write_rule = dict(read_write_rule, rule_id="combined-write", phase="write")
        combined_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set",
                                         json.dumps([read_write_rule, combined_write_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=combined_exec, inspect=inspect)[0])
        read_coil_rule = dict(rule, function_code=1, address=2, replacement_value=0)
        read_coil_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([read_coil_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=read_coil_exec, inspect=inspect)[0])
        read_discrete_rule = dict(rule, function_code=2, address=3, replacement_value=1)
        read_discrete_exec = dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([read_discrete_rule])])
        self.assertTrue(self.auth("POST", f"/containers/{container_id}/exec", body=read_discrete_exec, inspect=inspect)[0])
        for invalid in (
            dict(base, Cmd=["/bin/sh", "-c", "id"]),
            dict(base, Env=["ANVIL_TCP_GATEWAY_CONTROL_TOKEN=" + "x" * 43]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(coil_rule, replacement_value=2)])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(single_coil_rule, replacement_value=2)])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(mask_rule, replacement_value=65536)])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(rule, function_code=5)])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(rule, phase="write")])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([dict(read_write_rule, phase="unknown")])]),
            dict(base, Cmd=["python", "tcp_route_control.py", "set", json.dumps([rule, rule])]),
        ):
            self.assertFalse(self.auth("POST", f"/containers/{container_id}/exec", body=invalid, inspect=inspect)[0])
        labels["anvil.route_inspect_protocol"] = "none"
        self.assertFalse(self.auth("POST", f"/containers/{container_id}/exec", body=base, inspect=inspect)[0])

    def test_resource_listing_requires_anvil_filters_and_unowned_ids_are_denied(self):
        self.assertFalse(self.auth("GET", "/containers/json")[0])
        self.assertTrue(self.auth("GET", "/containers/json", "filters=%7B%22label%22%3A%5B%22anvil.managed%3Dtrue%22%5D%7D")[0])
        self.assertFalse(self.auth("GET", "/containers/abcdef012345/json", inspect=lambda *_: None)[0])
        owned = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc"}}}
        self.assertTrue(self.auth("DELETE", "/containers/abcdef0123456789", inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("POST", "/images/create")[0])

    def test_container_stats_are_single_sample_and_owned_only(self):
        owned = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc"}}}
        self.assertTrue(self.auth("GET", "/containers/abcdef012345/stats", "stream=false&one-shot=true", inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("GET", "/containers/abcdef012345/stats", "stream=true&one-shot=true", inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("GET", "/containers/abcdef012345/stats", "stream=false&one-shot=true", inspect=lambda *_: None)[0])

    def test_pause_and_unpause_are_limited_to_owned_containers(self):
        owned = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc"}}}
        inspect = lambda *_: owned
        self.assertTrue(self.auth("POST", "/containers/abcdef012345/pause", inspect=inspect)[0])
        self.assertTrue(self.auth("POST", "/containers/abcdef012345/unpause", inspect=inspect)[0])
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/pause", inspect=lambda *_: None)[0])
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/unpause", inspect=lambda *_: None)[0])

    def test_exec_is_limited_to_owned_containers_and_non_root_user(self):
        owned = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                         "anvil.node_id": "attacker-1", "anvil.role": "attacker",
                                         "anvil.terminal_access_enabled": "true"}}}
        self.assertTrue(self.auth("POST", "/containers/abcdef012345/exec", body={
            "Cmd": ["/bin/sh", "-i"], "User": "65534:65534", "Privileged": False,
        }, inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body={
            "Cmd": ["/bin/sh", "-i"], "User": "0:0", "Privileged": False,
        }, inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body={
            "Cmd": ["/bin/sh", "-i"], "User": "65534:65534", "Privileged": True,
        }, inspect=lambda *_: owned)[0])
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body={
            "Cmd": ["/bin/sh", "-i"], "User": "65534:65534", "Privileged": False,
        }, inspect=lambda *_: None)[0])

    def test_exec_is_scoped_to_participant_and_engineering_station_roles(self):
        request = {"Cmd": ["/bin/sh", "-i"], "User": "65534:65534", "Privileged": False}
        for role in ("attacker", "engineering"):
            labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                      "anvil.node_id": "station-1", "anvil.role": role,
                      "anvil.terminal_access_enabled": "true"}
            inspected = {"Config": {"Labels": labels}}
            with self.subTest(role=role):
                self.assertTrue(self.auth("POST", "/containers/abcdef012345/exec", body=request,
                                           inspect=lambda *_: inspected)[0])
                self.assertTrue(self.auth("POST", "/exec/exec-id/start", body={},
                                           inspect=lambda *_: inspected)[0])
                self.assertTrue(self.auth("GET", "/exec/exec-id/json",
                                           inspect=lambda *_: inspected)[0])

        for role in ("plc-s7", "modbus-tcp", "scada", "hmi", "tcp-gateway", "packet-capture", None):
            labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                      "anvil.node_id": "device-1", "anvil.terminal_access_enabled": "true"}
            if role is not None:
                labels["anvil.role"] = role
            inspected = {"Config": {"Labels": labels}}
            with self.subTest(role=role):
                self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body=request,
                                            inspect=lambda *_: inspected)[0])
                self.assertFalse(self.auth("POST", "/exec/exec-id/start", body={},
                                            inspect=lambda *_: inspected)[0])
                self.assertFalse(self.auth("GET", "/exec/exec-id/json",
                                            inspect=lambda *_: inspected)[0])

        for role in ("attacker", "engineering"):
            for terminal_access in (None, "false"):
                labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                          "anvil.node_id": "station-1", "anvil.role": role}
                if terminal_access is not None:
                    labels["anvil.terminal_access_enabled"] = terminal_access
                inspected = {"Config": {"Labels": labels}}
                with self.subTest(role=role, terminal_access=terminal_access):
                    self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body=request,
                                                inspect=lambda *_: inspected)[0])
                    self.assertFalse(self.auth("POST", "/exec/exec-id/start", body={},
                                                inspect=lambda *_: inspected)[0])
                    self.assertFalse(self.auth("GET", "/exec/exec-id/json",
                                                inspect=lambda *_: inspected)[0])

    def test_exec_tunnel_sends_terminal_eof_and_keeps_result_inspectable(self):
        class FakeSocket:
            def __init__(self, incoming=()):
                self.incoming = list(incoming)
                self.sent = []
                self.closed = False

            def settimeout(self, _timeout):
                pass

            def connect(self, _path):
                pass

            def recv(self, _size):
                return self.incoming.pop(0) if self.incoming else b""

            def sendall(self, value):
                self.sent.append(value)

            def close(self):
                self.closed = True

        upstream = FakeSocket([b"HTTP/1.1 101 UPGRADED\r\n\r\n"])
        downstream = FakeSocket()
        handler = Handler.__new__(Handler)
        handler.command = "POST"
        handler.headers = {"Upgrade": "tcp"}
        handler.connection = downstream
        from threading import Lock
        handler.server = SimpleNamespace(exec_containers={"exec-id": "container-id"}, exec_containers_lock=Lock(),
                                         exec_cleanup_timers={})
        with patch("proxy.socket.socket", return_value=upstream), patch(
            "proxy.select.select", return_value=([downstream], [], [])
        ), patch.object(handler, "_schedule_exec_cleanup") as schedule_cleanup:
            handler._exec_tunnel(urlsplit("/v1.45/exec/exec-id/start"), b'{"Detach":false}')

        self.assertEqual(upstream.sent[-1], b"\x04")
        self.assertTrue(upstream.closed)
        self.assertEqual(handler.server.exec_containers, {"exec-id": "container-id"})
        schedule_cleanup.assert_called_once_with("exec-id")

    def test_exec_inspect_uses_the_owned_container_labels_without_locking_out_later_execs(self):
        handler = Handler.__new__(Handler)
        from threading import Lock
        handler.server = SimpleNamespace(exec_containers={"exec-id": "container-id"}, exec_containers_lock=Lock())
        owned = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                                         "anvil.node_id": "engineering-1", "anvil.role": "engineering",
                                         "anvil.terminal_access_enabled": "true"}}}
        handler._inspect = lambda kind, resource_id: owned if (kind, resource_id) == ("container", "container-id") else None
        self.assertEqual(self.auth("GET", "/exec/exec-id/json", inspect=lambda *_: {"Config": {"Labels": owned["Config"]["Labels"]}}), (True, ""))
        self.assertEqual(handler.server.exec_containers, {"exec-id": "container-id"})
        self.assertFalse(self.auth("GET", "/exec/other/json", inspect=lambda *_: None)[0])

    def test_expired_exec_tracking_is_removed_after_inactivity(self):
        from threading import Lock
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(exec_containers={"exec-id": "container-id"},
                                         exec_containers_lock=Lock(), exec_cleanup_timers={})
        handler._expire_exec_tracking("exec-id")
        self.assertEqual(handler.server.exec_containers, {})
        self.assertEqual(handler.server.exec_cleanup_timers, {})

    def test_packet_capture_profile_is_fixed_to_owned_device_namespace(self):
        capture_id = "12345678-1234-4234-8234-123456789abd"
        target_id = "a" * 64
        target = {"Config": {"Labels": {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.node_id": "plc-1",
        }}, "State": {"Status": "running"}}
        config = container_config(
            CAPTURE_IMAGE,
            NetworkMode=f"container:{target_id}",
            CapAdd=["NET_RAW", "SETGID", "SETUID"],
            Memory=67108864,
            NanoCpus=250000000,
            PidsLimit=8,
            Tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"},
        )
        config["User"] = "0:0"
        config["StopSignal"] = "SIGINT"
        config["Labels"].update({
            "anvil.node_id": f"capture-{capture_id}", "anvil.role": "packet-capture",
            "anvil.capture_id": capture_id, "anvil.capture_target_node_id": "plc-1",
            "anvil.capture_scope": "device", "anvil.capture_zone_id": "",
        })
        config["Entrypoint"] = ["/bin/sh", "-c"]
        config["Cmd"] = ["tcpdump -i any -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""]
        config["NetworkingConfig"] = {"EndpointsConfig": {}}
        inspect = lambda kind, _id: target if kind == "container" else None
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-capture-test", body=config, inspect=inspect)[0])

        config["Cmd"] = ["/bin/sh", "-c", "id"]
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-capture-test", body=config, inspect=inspect)[0])

    def test_segment_capture_sidecar_is_limited_to_the_owned_internal_bridge(self):
        capture_id = "12345678-1234-4234-8234-123456789abd"
        network_id = "b" * 64
        interface = f"br-{network_id[:12]}"
        network = {"Id": network_id, "Driver": "bridge", "Internal": True, "Options": {}, "Labels": {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO, "anvil.zone_id": "control",
        }}
        config = container_config(CAPTURE_IMAGE, NetworkMode="host",
                                  CapAdd=["NET_RAW", "SETGID", "SETUID"], Memory=67108864,
                                  NanoCpus=250000000, PidsLimit=8,
                                  Tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"})
        config["User"] = "0:0"
        config["StopSignal"] = "SIGINT"
        config["Labels"].update({
            "anvil.node_id": f"capture-{capture_id}", "anvil.role": "packet-capture",
            "anvil.capture_id": capture_id, "anvil.capture_target_node_id": "",
            "anvil.capture_scope": "segment", "anvil.capture_zone_id": "control",
            "anvil.capture_network_id": network_id, "anvil.capture_interface": interface,
            "anvil.zone_id": "control",
        })
        config["Entrypoint"] = ["/bin/sh", "-c"]
        config["Cmd"] = [f"tcpdump -i {interface} -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""]
        config["NetworkingConfig"] = {"EndpointsConfig": {}}
        inspect = lambda kind, _id: network if kind == "network" else None
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-zone-capture", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.capture_zone_id"] = "dmz"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-zone-capture", body=config, inspect=inspect)[0])

    def test_link_capture_is_peer_filtered_and_both_endpoints_must_share_network(self):
        capture_id = "12345678-1234-4234-8234-123456789abd"
        source_id, peer_id, network_id = "a" * 64, "b" * 64, "c" * 64
        source = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                  "anvil.node_id": "plc-1", "anvil.zone_id": "control"}},
                  "State": {"Status": "running"}, "NetworkSettings": {"Networks": {
                      "zone": {"NetworkID": network_id, "IPAddress": "10.0.0.2"}}}}
        peer = {"Config": {"Labels": {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                "anvil.node_id": "plc-2", "anvil.zone_id": "control"}},
                "State": {"Status": "running"}, "NetworkSettings": {"Networks": {
                    "zone": {"NetworkID": network_id, "IPAddress": "10.0.0.3"}}}}
        config = container_config(CAPTURE_IMAGE, NetworkMode=f"container:{source_id}",
                                  CapAdd=["NET_RAW", "SETGID", "SETUID"], Memory=67108864,
                                  NanoCpus=250000000, PidsLimit=8,
                                  Tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"})
        config["User"] = "0:0"
        config["StopSignal"] = "SIGINT"
        config["Labels"].update({"anvil.node_id": f"capture-{capture_id}", "anvil.role": "packet-capture",
            "anvil.capture_id": capture_id, "anvil.capture_target_node_id": "plc-1",
            "anvil.capture_scope": "link", "anvil.capture_zone_id": "", "anvil.zone_id": "control",
            "anvil.capture_peer_node_id": "plc-2", "anvil.capture_peer_container_id": peer_id,
            "anvil.capture_peer_ip": "10.0.0.3"})
        config["Entrypoint"] = ["/bin/sh", "-c"]
        config["Cmd"] = ['tcpdump -i any host 10.0.0.3 -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap \'kill -INT "$child" 2>/dev/null; wait "$child"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0\' INT TERM; wait "$child"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit "$status"']
        config["NetworkingConfig"] = {"EndpointsConfig": {}}
        def inspect(kind, resource_id):
            if kind == "container" and resource_id == source_id: return source
            if kind == "container" and resource_id == peer_id: return peer
            return None
        self.assertTrue(self.auth("POST", "/containers/create", "name=anvil-link-capture", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.capture_peer_ip"] = "192.0.2.10"
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-link-capture", body=config, inspect=inspect)[0])
        config["Labels"]["anvil.capture_zone_id"] = "control"
        config["HostConfig"]["NetworkMode"] = "container:" + "a" * 64
        self.assertFalse(self.auth("POST", "/containers/create", "name=anvil-zone-capture", body=config, inspect=inspect)[0])

    def test_only_capture_tmpfs_can_be_exported(self):
        capture = {"Config": {"Labels": {
            "anvil.managed": "true", "anvil.scenario_id": SCENARIO,
            "anvil.node_id": "capture-1", "anvil.role": "packet-capture",
        }}}
        inspect = lambda *_: capture
        self.assertTrue(self.auth("GET", "/containers/abcdef012345/archive", "path=%2Ftmp", inspect=inspect)[0])
        self.assertFalse(self.auth("GET", "/containers/abcdef012345/archive", "path=%2F", inspect=inspect)[0])

    def test_packet_capture_container_cannot_be_used_as_exec_target(self):
        labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                  "anvil.node_id": "capture-12345678", "anvil.role": "packet-capture"}
        capture = {"Config": {"Labels": labels}}
        inspect = lambda *_: capture
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec", body={"User": "65534:65534"}, inspect=inspect)[0])
        self.assertFalse(self.auth("POST", "/exec/exec-id/start", body={}, inspect=inspect)[0])

    def test_tcp_route_gateway_cannot_be_used_as_exec_target(self):
        labels = {"anvil.managed": "true", "anvil.scenario_id": SCENARIO,
                  "anvil.node_id": "__anvil_route_test", "anvil.role": "tcp-gateway"}
        gateway = {"Config": {"Labels": labels}}
        inspect = lambda *_: gateway
        self.assertFalse(self.auth("POST", "/containers/abcdef012345/exec",
                                   body={"Cmd": ["id"], "User": "65534:65534"}, inspect=inspect)[0])
        self.assertFalse(self.auth("POST", "/exec/exec-id/start", body={}, inspect=inspect)[0])


if __name__ == "__main__":
    unittest.main()
