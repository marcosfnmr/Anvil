"""Minimal Docker Engine API gateway for Anvil's managed containers/networks."""

from __future__ import annotations

import json
import os
import re
import hashlib
import socket
import http.client
import select
import threading
import urllib.parse
from threading import Lock
from pathlib import Path
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


SOCKET_PATH = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
ALLOWED_IMAGES = {item for item in os.environ.get("ANVIL_ALLOWED_IMAGES", "").split(",") if item}
CAPTURE_IMAGE = os.environ.get("ANVIL_CAPTURE_IMAGE", "anvil/capture:dev")
EGRESS_IMAGE = os.environ.get("ANVIL_EGRESS_IMAGE", "anvil/egress-proxy:dev")
PLC_IMAGE = os.environ.get("ANVIL_PLC_IMAGE", "anvil/plc-s7:dev")
MODBUS_IMAGE = os.environ.get("ANVIL_MODBUS_IMAGE", "anvil/modbus-tcp:dev")
SCADA_IMAGE = os.environ.get("ANVIL_SCADA_IMAGE", "anvil/scada:dev")
INGRESS_IMAGE = os.environ.get("ANVIL_INGRESS_IMAGE", "anvil/protocol-ingress:dev")
SCENARIO_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
RESOURCE_ID = re.compile(r"^[a-f0-9]{12,64}$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_BODY = 1_048_576
INTERACTIVE_EXEC_ROLES = {"attacker", "engineering"}
RUNTIME_EXEC_POLICY = json.loads(Path(__file__).with_name("runtime_exec_policy.json").read_text())


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("docker", timeout=30)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _api_path(path: str) -> str:
    parts = path.split("/", 2)
    if len(parts) >= 3 and re.fullmatch(r"v\d+\.\d+", parts[1]):
        return "/" + parts[2]
    return path


def _labels(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _owned(labels: object) -> bool:
    labels = _labels(labels)
    return (labels.get("anvil.managed") == "true"
            and bool(SCENARIO_ID.fullmatch(str(labels.get("anvil.scenario_id", ""))))
            and bool(str(labels.get("anvil.node_id", ""))))


def _owned_network(labels: object) -> bool:
    labels = _labels(labels)
    return (labels.get("anvil.managed") == "true"
            and bool(SCENARIO_ID.fullmatch(str(labels.get("anvil.scenario_id", ""))))
            and bool(str(labels.get("anvil.zone_id", ""))))


def _owned_retention_volume(labels: object) -> bool:
    labels = _labels(labels)
    scenario = str(labels.get("anvil.scenario_id", ""))
    node = str(labels.get("anvil.node_id", ""))
    expected = f"anvil-retain-{scenario}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
    return (labels.get("anvil.managed") == "true" and labels.get("anvil.purpose") == "plc-retention"
            and bool(SCENARIO_ID.fullmatch(scenario)) and bool(node) and labels.get("anvil.volume_name") == expected)


def _owned_scada_history_volume(labels: object) -> bool:
    labels = _labels(labels)
    scenario = str(labels.get("anvil.scenario_id", ""))
    node = str(labels.get("anvil.node_id", ""))
    expected = f"anvil-scada-history-{scenario}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
    return (labels.get("anvil.managed") == "true" and labels.get("anvil.purpose") == "scada-history"
            and bool(SCENARIO_ID.fullmatch(scenario)) and bool(node)
            and labels.get("anvil.volume_name") == expected)


def _owned_protocol_audit_volume(labels: object) -> bool:
    labels = _labels(labels)
    scenario = str(labels.get("anvil.scenario_id", ""))
    node = str(labels.get("anvil.node_id", ""))
    expected = f"anvil-protocol-audit-{scenario}-{hashlib.sha256(node.encode()).hexdigest()[:16]}"
    return (labels.get("anvil.managed") == "true" and labels.get("anvil.purpose") == "protocol-audit"
            and bool(SCENARIO_ID.fullmatch(scenario)) and bool(node)
            and labels.get("anvil.volume_name") == expected)


def _owned_data_volume(labels: object) -> bool:
    return (_owned_retention_volume(labels) or _owned_scada_history_volume(labels)
            or _owned_protocol_audit_volume(labels))


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _valid_egress_rules(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        return False
    destinations: set[tuple[str, int]] = set()
    for rule in value:
        if not isinstance(rule, dict) or set(rule) != {"hostname", "tcp_port"}:
            return False
        hostname, port = rule["hostname"], rule["tcp_port"]
        if (not isinstance(hostname, str) or len(hostname) > 253
                or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname)
                or _is_ip_address(hostname)
                or any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in (
                    "localhost", "local", "internal", "test", "invalid", "example", "home.arpa"
                ))
                or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
            return False
        destination = (hostname, port)
        if destination in destinations:
            return False
        destinations.add(destination)
    return True


class DockerGate:
    """Endpoint and resource policy, separated for unit testing."""

    def _runtime_operations_container_valid(self, inspected: dict) -> bool:
        config = inspected.get("Config") or {}
        labels = _labels(config.get("Labels"))
        return (_owned(labels) and config.get("User") in {"65534", "65534:65534"}
                and labels.get("anvil.device_type") in {"field-runtime", "plc-s7", "modbus-tcp", "hmi", "scada"}
                and (labels.get("anvil.image_reference") or config.get("Image")) in self.images)

    def _runtime_operations_exec_valid(self, inspected: dict, config: dict) -> bool:
        if not self._runtime_operations_container_valid(inspected):
            return False
        if (config.get("Privileged", False) is not False
                or config.get("User") not in {None, "", "65534", "65534:65534"}
                or config.get("Env") not in (None, [])
                or config.get("WorkingDir") not in (None, "", "/app")
                or config.get("AttachStdin", False) is not False
                or config.get("Tty", False) is not False):
            return False
        cmd = config.get("Cmd")
        if not isinstance(cmd, list) or not all(isinstance(x, str) and len(x) <= 65536 for x in cmd):
            return False
        kind = _labels(inspected["Config"].get("Labels")).get("anvil.device_type")
        if kind == "hmi":
            if len(cmd) != 6 or cmd[:2] != ["python", "/app/hmi_operations.py"] or cmd[3] not in {"read", "write"}:
                return False
            try:
                target = json.loads(cmd[2])
                return isinstance(target, dict) and isinstance(target.get("host"), str) and ip_address(target["host"]).is_private
            except (ValueError, TypeError):
                return False
        if len(cmd) < 3 or cmd[:2] != ["python", "-c"]:
            return False
        digest = hashlib.sha256(cmd[2].encode()).hexdigest()
        if not any(rule["device_type"] == kind and rule["sha256"] == digest and rule["argc"] == len(cmd)
                   for rule in RUNTIME_EXEC_POLICY):
            return False
        if kind == "field-runtime":
            return cmd[3] in {"snapshot", "control"}
        if kind == "scada":
            path = urlsplit(cmd[3])
            return (not path.scheme and not path.netloc and path.path in {
                "snapshot", "history", "history/pending", "history/acknowledge",
                "alarms", "alarms/acknowledge", "alarms/restore", "write",
            } and not path.fragment)
        return True

    def __init__(self, images: set[str] | None = None, *, plc_images: set[str] | None = None,
                 modbus_images: set[str] | None = None, egress_images: set[str] | None = None,
                 ingress_images: set[str] | None = None, scada_images: set[str] | None = None):
        self.images = images if images is not None else ALLOWED_IMAGES
        self.plc_images = plc_images if plc_images is not None else {PLC_IMAGE}
        self.modbus_images = modbus_images if modbus_images is not None else {MODBUS_IMAGE}
        self.scada_images = scada_images if scada_images is not None else {SCADA_IMAGE}
        self.egress_images = egress_images if egress_images is not None else {EGRESS_IMAGE}
        self.ingress_images = ingress_images if ingress_images is not None else {INGRESS_IMAGE}

    @staticmethod
    def _filters(query: str) -> dict:
        filters = parse_qs(query).get("filters", [None])[0]
        try:
            value = json.loads(filters) if filters else {}
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _protocol_audit_container_valid(self, inspected: dict, inspect_resource) -> bool:
        config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
        labels = _labels(config.get("Labels"))
        if (not _owned(labels) or labels.get("anvil.protocol_audit_queue") != "true"
                or labels.get("anvil.device_type") not in {"plc-s7", "modbus-tcp", "tcp-gateway"}):
            return False
        env = config.get("Env") or []
        env_values = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
        token = env_values.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            return False
        runtime_ref = labels.get("anvil.image_reference")
        if labels.get("anvil.device_type") == "plc-s7":
            runtime_valid = runtime_ref in self.plc_images and "PLC_CONFIG" in env_values
        elif labels.get("anvil.device_type") == "modbus-tcp":
            try:
                modbus_config = json.loads(env_values.get("MODBUS_CONFIG", "{}"))
            except (ValueError, TypeError):
                return False
            runtime_valid = runtime_ref in self.modbus_images and modbus_config.get("transport", "tcp") in {"tcp", "rtu"}
        else:
            runtime_valid = (runtime_ref in self.plc_images
                             and self._tcp_gateway_container_valid(inspected)
                             and labels.get("anvil.route_inspect_protocol") == "modbus-tcp")
        if not runtime_valid:
            return False
        host = inspected.get("HostConfig") if isinstance(inspected.get("HostConfig"), dict) else {}
        mounts = host.get("Mounts") or []
        matches = [mount for mount in mounts if isinstance(mount, dict)
                   and mount.get("Target") == "/var/lib/anvil/protocol-audit"]
        if len(matches) != 1:
            return False
        mount = matches[0]
        volume = inspect_resource("volume", mount.get("Source", "")) if inspect_resource else None
        volume_labels = _labels((volume or {}).get("Labels"))
        return (
            set(mount) == {"Type", "Source", "Target", "ReadOnly"}
            and mount.get("Type") == "volume" and mount.get("ReadOnly") is False
            and _owned_protocol_audit_volume(volume_labels)
            and volume_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
            and volume_labels.get("anvil.node_id") == labels.get("anvil.node_id")
        )

    def _protocol_audit_exec_config_valid(self, inspected: dict, exec_config: dict,
                                          container_id: str, inspect_resource) -> bool:
        if not self._protocol_audit_container_valid(inspected, inspect_resource):
            return False
        expected_keys = {"Container", "User", "Privileged", "Tty", "AttachStdin",
                         "AttachStdout", "AttachStderr", "Cmd", "Env", "WorkingDir"}
        if (set(exec_config) != expected_keys or exec_config.get("Container") != container_id
                or exec_config.get("User") != "65534:65534"
                or exec_config.get("Privileged") is not False
                or exec_config.get("Tty") is not False
                or exec_config.get("AttachStdin") is not False
                or exec_config.get("AttachStdout") is not True
                or exec_config.get("AttachStderr") is not True
                or exec_config.get("WorkingDir") != "/app"):
            return False
        command = exec_config.get("Cmd")
        env = exec_config.get("Env")
        if (isinstance(command, list) and len(command) == 4
                and command[:3] == ["python", "protocol_audit.py", "pending"]
                and re.fullmatch(r"[1-9][0-9]{0,4}", str(command[3]))
                and int(command[3]) <= 10_000):
            return env in (None, [])
        if (isinstance(command, list) and len(command) == 4
                and command[:3] == ["python", "protocol_audit.py", "ack"]
                and re.fullmatch(r"0|[1-9][0-9]{0,18}", str(command[3]))):
            config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
            env_values = dict(item.split("=", 1) for item in (config.get("Env") or [])
                              if isinstance(item, str) and "=" in item)
            token = env_values.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN", "")
            return env == [f"ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN={token}"]
        return False

    def _tcp_gateway_container_valid(self, inspected: dict) -> bool:
        config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
        labels = _labels(config.get("Labels"))
        env = config.get("Env") or []
        env_values = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
        try:
            gateway = json.loads(env_values.get("ANVIL_TCP_GATEWAY_CONFIG", "{}"))
            node_id = str(labels.get("anvil.node_id", ""))
            route_id = node_id.removeprefix("__anvil_route_")
            control_token = env_values.get("ANVIL_TCP_GATEWAY_CONTROL_TOKEN", "")
            inspect_protocol = labels.get("anvil.route_inspect_protocol", "none")
            inspect_protocol = None if inspect_protocol == "none" else inspect_protocol
            listen_port = int(labels.get("anvil.route_listen_port", "0"))
            target_port = int(labels.get("anvil.route_target_port", "0"))
            latency_ms = int(labels.get("anvil.route_latency_ms", "-1"))
            image_reference = labels.get("anvil.image_reference") or config.get("Image")
            listen_ip = ip_address(labels.get("anvil.route_listen_ip", ""))
            source_ip = ip_address(labels.get("anvil.route_source_ip", ""))
            target_ip = ip_address(labels.get("anvil.route_target_ip", ""))
            valid = (
                _owned(labels) and labels.get("anvil.role") == "tcp-gateway"
                and config.get("Cmd") == ["python", "-u", "tcp_gateway.py"]
                and image_reference in self.plc_images
                and re.fullmatch(r"[A-Za-z0-9_-]{43}", control_token) is not None
                and gateway.get("control_token") == control_token
                and set(gateway) == {"route_id", "listen_ip", "listen_port", "source_ip", "target_ip",
                                     "target_port", "latency_ms", "inspect_protocol", "control_token"}
                and isinstance(gateway.get("route_id"), str)
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", route_id) is not None
                and gateway.get("route_id") == route_id
                and isinstance(listen_ip, IPv4Address) and isinstance(source_ip, IPv4Address)
                and isinstance(target_ip, IPv4Address)
                and gateway.get("listen_ip") == labels.get("anvil.route_listen_ip")
                and gateway.get("source_ip") == labels.get("anvil.route_source_ip")
                and gateway.get("target_ip") == labels.get("anvil.route_target_ip")
                and type(gateway.get("target_port")) is int and gateway.get("target_port") == target_port
                and 1 <= target_port <= 65535
                and type(gateway.get("listen_port")) is int and gateway.get("listen_port") == listen_port
                and 1024 <= listen_port <= 65535
                and type(gateway.get("latency_ms")) is int and gateway.get("latency_ms") == latency_ms
                and 0 <= latency_ms <= 10000
                and gateway.get("inspect_protocol") == inspect_protocol
                and gateway.get("inspect_protocol") in {None, "modbus-tcp"}
            )
            return bool(valid)
        except (TypeError, ValueError, AttributeError):
            return False

    def _tcp_route_control_exec_valid(self, inspected: dict, exec_config: dict,
                                      container_id: str) -> bool:
        if not self._tcp_gateway_container_valid(inspected):
            return False
        if _labels((inspected.get("Config") or {}).get("Labels")).get("anvil.route_inspect_protocol") != "modbus-tcp":
            return False
        expected_keys = {"Container", "User", "Privileged", "Tty", "AttachStdin",
                         "AttachStdout", "AttachStderr", "Cmd", "Env", "WorkingDir"}
        if (set(exec_config) != expected_keys or exec_config.get("Container") != container_id
                or exec_config.get("User") != "65534:65534"
                or exec_config.get("Privileged") is not False
                or exec_config.get("Tty") is not False
                or exec_config.get("AttachStdin") is not False
                or exec_config.get("AttachStdout") is not True
                or exec_config.get("AttachStderr") is not True
                or exec_config.get("WorkingDir") != "/app"):
            return False
        config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
        env_values = dict(item.split("=", 1) for item in (config.get("Env") or [])
                          if isinstance(item, str) and "=" in item)
        token = env_values.get("ANVIL_TCP_GATEWAY_CONTROL_TOKEN", "")
        command = exec_config.get("Cmd")
        env = exec_config.get("Env")
        if env != [f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"]:
            return False
        if command == ["python", "tcp_route_control.py", "clear"]:
            return True
        if not (isinstance(command, list) and len(command) == 4
                and command[:3] == ["python", "tcp_route_control.py", "set"]
                and isinstance(command[3], str) and len(command[3]) <= 4096):
            return False
        try:
            rules = json.loads(command[3])
        except (TypeError, ValueError):
            return False
        if not isinstance(rules, list) or len(rules) > 32:
            return False
        seen: set[tuple[int, int, str, int]] = set()
        ids: set[str] = set()
        for rule in rules:
            fields = {"rule_id", "unit_id", "function_code", "address", "replacement_value"}
            optional = {"phase"} if isinstance(rule, dict) and rule.get("function_code") == 23 else set()
            if not isinstance(rule, dict) or set(rule) not in (
                    fields, fields | {"exercise_id", "event_id"}, fields | optional,
                    fields | optional | {"exercise_id", "event_id"}):
                return False
            if "exercise_id" in rule:
                if any(not isinstance(rule.get(key), str)
                       or re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", rule[key]) is None
                       for key in ("exercise_id", "event_id")):
                    return False
            rule_id, unit_id = rule["rule_id"], rule["unit_id"]
            function_code, address, replacement = rule["function_code"], rule["address"], rule["replacement_value"]
            phase = rule.get("phase", "read")
            if (not isinstance(rule_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", rule_id)
                    or rule_id in ids or type(unit_id) is not int or not 0 <= unit_id <= 255
                    or type(function_code) is not int or function_code not in {1, 2, 3, 4, 5, 6, 15, 16, 22, 23}
                    or type(address) is not int or not 0 <= address <= 65535
                    or type(replacement) is not int or not 0 <= replacement <= (1 if function_code in {1, 2, 5, 15} else 65535)
                    or phase not in {"read", "write"}
                    or (function_code != 23 and phase != "read")):
                return False
            identity = (unit_id, function_code, phase, address)
            if identity in seen:
                return False
            seen.add(identity)
            ids.add(rule_id)
        return True

    def authorize(self, method: str, path: str, query: str, body: bytes, inspect_resource=None) -> tuple[bool, str]:
        path = _api_path(path).rstrip("/") or "/"
        if method == "GET" and path in {"/_ping", "/version"}:
            return True, ""
        if path == "/volumes" and method == "GET":
            filters = self._filters(query)
            labels = filters.get("label", [])
            if isinstance(labels, str):
                labels = [labels]
            return (True, "") if any(item == "anvil.managed=true" or item.startswith("anvil.scenario_id=") for item in labels if isinstance(item, str)) else (False, "Volume listing requires an Anvil ownership filter")
        if method == "POST" and path == "/volumes/create":
            try:
                volume = json.loads(body)
            except (ValueError, TypeError):
                return False, "Invalid volume configuration"
            labels = _labels(volume.get("Labels")) if isinstance(volume, dict) else {}
            if (not isinstance(volume, dict) or set(volume) - {"Name", "Driver", "DriverOpts", "Labels"}
                    or volume.get("Name") != labels.get("anvil.volume_name")
                    or volume.get("Driver", "local") != "local"
                    or volume.get("DriverOpts") not in (None, {}) or not _owned_data_volume(labels)):
                return False, "Only owned Anvil PLC retention, SCADA history, or protocol audit volumes with the local driver can be created"
            return True, ""
        volume_match = re.fullmatch(r"/volumes/([^/]+)", path)
        if volume_match and method in {"GET", "DELETE"} and inspect_resource:
            params = parse_qs(query)
            if method == "DELETE" and (set(params) - {"force"} or params.get("force", ["false"])[0].lower() != "false"):
                return False, "Managed data volume deletion cannot force removal"
            volume = inspect_resource("volume", volume_match.group(1))
            if volume is None and method == "GET":
                # Let Docker return 404 for a well formed, absent managed name.
                # The SDK needs that response before creating a new data volume.
                candidate = re.fullmatch(
                    r"anvil-(?:retain|scada-history|protocol-audit)-(.+)-[0-9a-f]{16}",
                    volume_match.group(1),
                )
                if candidate and SCENARIO_ID.fullmatch(candidate.group(1)):
                    return True, ""
            if not volume or not _owned_data_volume(volume.get("Labels")) or volume.get("Name") != volume_match.group(1):
                return False, "Volume is not an owned Anvil data volume"
            return True, ""
        if path in {"/containers/json", "/networks"} and method == "GET":
            filters = self._filters(query)
            if path == "/containers/json":
                labels = filters.get("label", [])
                if isinstance(labels, str):
                    labels = [labels]
                allowed = any(item == "anvil.managed=true" or item.startswith("anvil.scenario_id=")
                              for item in labels if isinstance(item, str))
            else:
                params = parse_qs(query)
                names = params.get("filters", ["{}"])
                try:
                    decoded = json.loads(names[0])
                except (ValueError, TypeError):
                    decoded = {}
                network_names = decoded.get("name", []) if isinstance(decoded, dict) else []
                if isinstance(network_names, str):
                    network_names = [network_names]
                labels = decoded.get("label", []) if isinstance(decoded, dict) else []
                if isinstance(labels, str):
                    labels = [labels]
                allowed = (bool(network_names) and all(str(name).startswith("anvil-") for name in network_names)
                           or any(item == "anvil.managed=true" or str(item).startswith("anvil.scenario_id=") for item in labels))
            return (True, "") if allowed else (False, "Listing requires an Anvil resource filter")

        if method == "POST" and path == "/networks/create":
            try:
                config = json.loads(body)
            except (ValueError, TypeError):
                return False, "Invalid network configuration"
            if not isinstance(config, dict):
                return False, "Invalid network configuration"
            allowed_keys = {"Name", "CheckDuplicate", "Driver", "Options", "Internal", "EnableIPv6", "IPAM", "Labels", "Attachable"}
            options = config.get("Options") or {}
            ipam = config.get("IPAM") or {}
            ipam_configs = ipam.get("Config", []) if isinstance(ipam, dict) else None
            labels = _labels(config.get("Labels"))
            ingress_network = (config.get("Internal") is False and options in (None, {})
                               and labels.get("anvil.managed") == "true"
                               and labels.get("anvil.purpose") == "protocol-ingress"
                               and labels.get("anvil.zone_id") == "__ingress__"
                               and bool(SCENARIO_ID.fullmatch(str(labels.get("anvil.scenario_id", "")))))
            if (set(config) - allowed_keys
                    or not str(config.get("Name", "")).startswith("anvil-")
                    or config.get("Driver") != "bridge" or not (config.get("Internal") is True or ingress_network)
                    or config.get("EnableIPv6", False) is not False
                    or config.get("Attachable", False) is not False
                    or not (options == {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"} or ingress_network)
                    or not isinstance(ipam, dict) or set(ipam) - {"Driver", "Options", "Config"}
                    or ipam.get("Driver") not in {None, "", "default"}
                    or ipam.get("Options") not in (None, {})
                    or not isinstance(ipam_configs, list)
                    or (not ingress_network and (len(ipam_configs) != 1 or not isinstance(ipam_configs[0], dict)
                        or set(ipam_configs[0]) - {"Subnet", "IPRange", "Gateway", "AuxiliaryAddresses"}
                        or any(ipam_configs[0].get(key) is not None for key in ("IPRange", "Gateway", "AuxiliaryAddresses"))))
                    or (ingress_network and ipam_configs not in (None, [], [{}]))
                    or not (_owned_network(labels) and not ingress_network or ingress_network)):
                return False, "Only internal Anvil-labelled bridges or a scenario-owned ingress bridge can be created"
            return True, ""

        bridge_connect = re.fullmatch(r"/networks/([^/]+)/connect", path)
        if method == "POST" and bridge_connect:
            try:
                request = json.loads(body)
            except (ValueError, TypeError):
                return False, "Invalid network connection request"
            network = inspect_resource("network", bridge_connect.group(1)) if inspect_resource else None
            container_id = request.get("Container") if isinstance(request, dict) else None
            container = (inspect_resource("container", container_id)
                         if inspect_resource and isinstance(container_id, str)
                         and RESOURCE_ID.fullmatch(container_id) else None)
            labels = _labels((container or {}).get("Config", {}).get("Labels"))
            networks = (container or {}).get("NetworkSettings", {}).get("Networks") or {}
            endpoint = request.get("EndpointConfig") if isinstance(request, dict) else None
            ingress_connect = labels.get("anvil.role") == "protocol-ingress"
            ingress_valid = False
            if ingress_connect and isinstance(request, dict) and isinstance(endpoint, dict) and network:
                network_labels = _labels(network.get("Labels"))
                try:
                    env = dict(item.split("=", 1) for item in ((container.get("Config") or {}).get("Env") or [])
                               if isinstance(item, str) and "=" in item)
                    ingress_config = json.loads(env["ANVIL_INGRESS_CONFIG"])
                    requested_ip = ((endpoint.get("IPAMConfig") or {}).get("IPv4Address"))
                    ingress_valid = (
                        labels.get("anvil.image_reference") in self.ingress_images
                        and labels.get("anvil.ingress_zone_network") == network.get("Name")
                        and _owned_network(network_labels)
                        and network.get("Internal") is True
                        and network_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and network_labels.get("anvil.zone_id") == labels.get("anvil.zone_id")
                        and set(networks) == {"bridge"}
                        and isinstance(requested_ip, str)
                        and requested_ip == ingress_config.get("listen_ip")
                        and set(endpoint) <= {"IPAMConfig", "Links", "Aliases", "DriverOpts", "MacAddress"}
                        and not endpoint.get("Links") and not endpoint.get("Aliases")
                        and not endpoint.get("DriverOpts") and not endpoint.get("MacAddress")
                    )
                except (KeyError, TypeError, ValueError):
                    ingress_valid = False
            if (not isinstance(request, dict) or set(request) != {"Container", "EndpointConfig"}
                    or not isinstance(endpoint, dict)
                    or set(endpoint) - {"IPAMConfig", "Links", "Aliases", "DriverOpts", "MacAddress"}
                    or not network or not container or not _owned(labels)
                    or not (ingress_valid or (
                        network.get("Name") == "bridge" and network.get("Driver") == "bridge"
                        and network.get("Internal") is False
                        and labels.get("anvil.role") == "egress-gateway"
                        and labels.get("anvil.image_reference") in self.egress_images
                        and len(networks) == 1
                        and all(value in (None, {}, []) for value in endpoint.values())
                    ))):
                return False, "Only validated Anvil egress or single-zone protocol ingress gateways may connect networks"
            if ingress_valid:
                return True, ""
            source_network_name = next(iter(networks))
            source_network = inspect_resource("network", source_network_name) if inspect_resource else None
            source_network_labels = _labels((source_network or {}).get("Labels"))
            source_subnets = [
                item.get("Subnet") for item in ((source_network or {}).get("IPAM") or {}).get("Config", [])
                if isinstance(item, dict) and item.get("Subnet")
            ]
            try:
                source_ip = ip_address(labels.get("anvil.egress_source_ip", ""))
                rules = json.loads(labels.get("anvil.egress_allowlist", ""))
                valid_rules = (isinstance(rules, list) and 1 <= len(rules) <= 32
                               and all(isinstance(rule, dict) and set(rule) == {"hostname", "tcp_port"}
                                       and isinstance(rule["hostname"], str)
                                       and re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", rule["hostname"])
                                       and not _is_ip_address(rule["hostname"])
                                       and not any(rule["hostname"] == suffix or rule["hostname"].endswith(f".{suffix}")
                                                   for suffix in ("localhost", "local", "internal", "test", "invalid", "example", "home.arpa"))
                                       and isinstance(rule["tcp_port"], int) and not isinstance(rule["tcp_port"], bool)
                                       and 1 <= rule["tcp_port"] <= 65535 for rule in rules))
                valid_gateway = (
                    isinstance(source_ip, IPv4Address)
                    and source_network_labels.get("anvil.managed") == "true"
                    and source_network_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                    and source_network_labels.get("anvil.zone_id") == labels.get("anvil.egress_source_zone")
                    and labels.get("anvil.zone_id") == labels.get("anvil.egress_source_zone")
                    and source_network.get("Internal") is True
                    and len(source_subnets) == 1
                    and source_ip in ip_network(source_subnets[0], strict=True)
                    and labels.get("anvil.egress_source_node_id")
                    and valid_rules
                )
            except (TypeError, ValueError, KeyError):
                valid_gateway = False
            if not valid_gateway:
                return False, "Egress gateway attachment lacks a valid, scoped destination allowlist"
            return True, ""

        image_inspect = re.fullmatch(r"/images/(.+)/json", path)
        if method == "GET" and image_inspect:
            image_ref = urllib.parse.unquote(image_inspect.group(1))
            if image_ref in self.images:
                inspected = inspect_resource("image", image_ref) if inspect_resource else None
                return (True, "") if inspected else (False, "Allowlisted Docker image is unavailable")
            if re.fullmatch(r"anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}", image_ref):
                # Let Docker return its normal 404 for a not-yet-created pin so
                # docker-py can distinguish absence from a policy denial.
                return True, ""
            return False, "Only allowlisted source images and verified Anvil image pins can be inspected"

        image_tag = re.fullmatch(r"/images/([^/]+)/tag", path)
        if method == "POST" and image_tag:
            source_id = urllib.parse.unquote(image_tag.group(1))
            params = parse_qs(query, keep_blank_values=True)
            source = (inspect_resource("image", source_id)
                      if inspect_resource and re.fullmatch(r"sha256:[a-f0-9]{64}", source_id)
                      else None)
            repo = params.get("repo", [""])[0]
            tag = params.get("tag", [""])[0]
            source_tags = (source or {}).get("RepoTags") or []
            allowlisted_repositories = set()
            for source_tag in source_tags:
                if source_tag not in self.images:
                    continue
                repository = source_tag.split("@", 1)[0]
                colon = repository.rfind(":")
                if colon > repository.rfind("/"):
                    repository = repository[:colon]
                allowlisted_repositories.add(repository)
            expected_repositories = {
                f"anvil-pinned/{hashlib.sha256(repository.encode()).hexdigest()[:12]}"
                for repository in allowlisted_repositories
            }
            valid_pin = (
                set(params) == {"repo", "tag", "force"}
                and params.get("force") == ["0"]
                and bool(source)
                and source.get("Id") == source_id
                and repo in expected_repositories
                and tag == source_id.removeprefix("sha256:")
            )
            if not valid_pin:
                return False, "Only a non-forced Anvil image pin matching an allowlisted source image can be created"
            return True, ""

        match = re.fullmatch(r"/networks/([^/]+)", path)
        if match and method in {"GET", "DELETE"}:
            if method == "GET" and match.group(1) == "bridge" and inspect_resource is not None:
                bridge = inspect_resource("network", "bridge")
                if bridge and bridge.get("Name") == "bridge" and bridge.get("Driver") == "bridge" and bridge.get("Internal") is False:
                    return True, ""
            if not RESOURCE_ID.fullmatch(match.group(1)) or inspect_resource is None:
                return False, "Network ID is not permitted"
            resource = inspect_resource("network", match.group(1))
            labels = _labels((resource or {}).get("Labels"))
            ingress_network = (labels.get("anvil.managed") == "true"
                               and bool(SCENARIO_ID.fullmatch(str(labels.get("anvil.scenario_id", ""))))
                               and labels.get("anvil.purpose") == "protocol-ingress")
            if not resource or not (resource.get("Internal") is True and _owned_network(labels)
                                    or resource.get("Internal") is False and ingress_network):
                return False, "Network is not an owned internal Anvil network"
            return True, ""

        if method == "POST" and path == "/containers/create":
            params = parse_qs(query)
            name = params.get("name", [""])[0]
            try:
                config = json.loads(body)
            except (ValueError, TypeError):
                return False, "Invalid container configuration"
            if not isinstance(config, dict) or not name.startswith("anvil-") or not _owned(config.get("Labels")):
                return False, "Only allowlisted images with Anvil ownership labels can be created"
            labels = config.get("Labels") or {}
            is_capture = (config.get("Image") == CAPTURE_IMAGE
                          or labels.get("anvil.role") == "packet-capture")
            is_egress = labels.get("anvil.role") == "egress-gateway"
            is_ingress = labels.get("anvil.role") == "protocol-ingress"
            image_ref = str(config.get("Image", ""))
            if image_ref not in self.images:
                configured_ref = str(labels.get("anvil.image_reference", ""))
                image_id = str(labels.get("anvil.image_id", ""))
                image_pin = str(labels.get("anvil.image_pin", ""))
                inspected_image = (inspect_resource("image", image_id)
                                   if inspect_resource and re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
                                   else None)
                repo_tags = (inspected_image or {}).get("RepoTags") or []
                valid_pinned_image = (
                    configured_ref in self.images
                    and image_ref == image_id
                    and re.fullmatch(r"anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}", image_pin) is not None
                    and (inspected_image or {}).get("Id") == image_id
                    and image_pin in repo_tags
                )
                if not valid_pinned_image:
                    return False, "Image ID must resolve to its immutable pin and an allowlisted configured image"
            if is_egress:
                try:
                    env_items = config.get("Env") or []
                    env_values = dict(item.split("=", 1) for item in env_items
                                      if isinstance(item, str) and "=" in item)
                    egress_config = json.loads(env_values["ANVIL_EGRESS_CONFIG"])
                    egress_rules = json.loads(labels.get("anvil.egress_allowlist", ""))
                    endpoint_configs = (config.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
                    endpoint = next(iter(endpoint_configs.values())) if len(endpoint_configs) == 1 else None
                    gateway_ip = ((endpoint or {}).get("IPAMConfig") or {}).get("IPv4Address")
                    valid_egress_container = (
                        labels.get("anvil.image_reference") in self.egress_images
                        and config.get("Cmd") == ["python", "-u", "egress_proxy.py"]
                        and _valid_egress_rules(egress_rules)
                        and set(egress_config) == {"listen_ip", "source_ip", "rules"}
                        and egress_config.get("listen_ip") == gateway_ip
                        and egress_config.get("source_ip") == labels.get("anvil.egress_source_ip")
                        and egress_config.get("rules") == egress_rules
                        and labels.get("anvil.egress_source_node_id")
                        and labels.get("anvil.egress_source_zone") == labels.get("anvil.zone_id")
                    )
                except (TypeError, ValueError, KeyError, StopIteration):
                    valid_egress_container = False
                if not valid_egress_container:
                    return False, "Egress container must use its fixed proxy command and validated host/port allowlist"
            if is_ingress:
                try:
                    env_items = config.get("Env") or []
                    env_values = dict(item.split("=", 1) for item in env_items if isinstance(item, str) and "=" in item)
                    ingress_config = json.loads(env_values["ANVIL_INGRESS_CONFIG"])
                    endpoints = (config.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
                    zone_network_name = labels.get("anvil.ingress_zone_network", "")
                    target_network = inspect_resource("network", zone_network_name) if zone_network_name and inspect_resource else None
                    private_ip = ingress_config.get("listen_ip")
                    expected_port = "25000"
                    target_id = labels.get("anvil.ingress_target_node_id", "")
                    target_port = int(labels.get("anvil.ingress_target_port", "0"))
                    default_bridge = inspect_resource("network", "bridge") if inspect_resource else None
                    default_gateway = next((item.get("Gateway") for item in
                                            ((default_bridge or {}).get("IPAM") or {}).get("Config", [])
                                            if item.get("Gateway")), None)
                    target_ip = ingress_config.get("target_ip")
                    matching_endpoint = next(((container_id, entry) for container_id, entry in
                                              (target_network or {}).get("Containers", {}).items()
                                              if str(entry.get("IPv4Address", "")).split("/", 1)[0] == target_ip), None)
                    target_container = inspect_resource("container", matching_endpoint[0]) if matching_endpoint and inspect_resource else None
                    target_labels = _labels(((target_container or {}).get("Config") or {}).get("Labels"))
                    target_env = dict(item.split("=", 1) for item in (((target_container or {}).get("Config") or {}).get("Env") or [])
                                      if isinstance(item, str) and "=" in item)
                    target_image = target_labels.get("anvil.image_reference") or (target_container or {}).get("Config", {}).get("Image")
                    target_type = target_labels.get("anvil.device_type") or ("plc-s7" if target_image in self.plc_images else
                                   "modbus-tcp" if target_image in self.modbus_images else None)
                    target_modbus = json.loads(target_env.get("MODBUS_CONFIG", "{}")) if target_type == "modbus-tcp" else {}
                    target_protocol_port = 102 if target_type == "plc-s7" else int(target_modbus.get("port", 0))
                    valid_ingress = (
                        labels.get("anvil.image_reference") in self.ingress_images
                        and config.get("Image") == labels.get("anvil.image_id")
                        and config.get("Cmd") == ["python", "-u", "ingress_proxy.py"]
                        and set(ingress_config) == {"listen_ip", "listen_port", "target_ip", "target_port", "allowed_peer_ip", "bind_ip"}
                        and ingress_config.get("listen_ip") == private_ip
                        and ingress_config.get("bind_ip") == "0.0.0.0"
                        and ingress_config.get("listen_port") == int(expected_port)
                        and ingress_config.get("allowed_peer_ip") == labels.get("anvil.ingress_allowed_peer_ip")
                        and ingress_config.get("allowed_peer_ip") == default_gateway
                        and ingress_config.get("target_port") == target_port == target_protocol_port
                        and ingress_config.get("target_ip") == target_ip
                        and target_network is not None
                        and (target_network.get("Internal") is True)
                        and _owned_network(target_network.get("Labels"))
                        and _labels(target_network.get("Labels")).get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and _labels(target_network.get("Labels")).get("anvil.zone_id") == labels.get("anvil.zone_id")
                        and target_labels.get("anvil.managed") == "true"
                        and target_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and target_labels.get("anvil.node_id") == target_id
                        and target_type in {"plc-s7", "modbus-tcp"}
                        and target_labels.get("anvil.zone_id", "default") == labels.get("anvil.zone_id")
                        and labels.get("anvil.ingress_protocol") == ("s7" if target_type == "plc-s7" else "modbus-tcp")
                        and len(endpoints) == 0
                    )
                except (TypeError, ValueError, KeyError, StopIteration, AttributeError):
                    valid_ingress = False
                if not valid_ingress:
                    return False, "Protocol ingress must use its fixed relay profile and one same-scenario S7/Modbus destination"
            host = config.get("HostConfig") if isinstance(config.get("HostConfig"), dict) else {}
            configured_runtime_ref = labels.get("anvil.image_reference") or config.get("Image")
            proxy_role = labels.get("anvil.role") in {
                "tcp-gateway", "protocol-ingress", "egress-gateway"
            }
            protocol_runtime = (
                labels.get("anvil.device_type") in {"plc-s7", "modbus-tcp"}
                or (labels.get("anvil.device_type") == "tcp-gateway"
                    and labels.get("anvil.route_inspect_protocol") == "modbus-tcp")
                or (not proxy_role and configured_runtime_ref in (self.plc_images | self.modbus_images))
            )
            expected_protocol_log_config = {
                "Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}
            }
            log_config = host.get("LogConfig")
            valid_log_config = (
                log_config == expected_protocol_log_config if protocol_runtime
                else log_config in (None, {"Type": "json-file", "Config": {}})
            )
            env = config.get("Env") or []
            env_values = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
            retention_mounts = host.get("Mounts") or []
            def mount_at(target: str) -> dict:
                matches = [item for item in retention_mounts
                           if isinstance(item, dict) and item.get("Target") == target]
                return matches[0] if len(matches) == 1 else {}
            retained_plc = False
            retained_scada = False
            retained_protocol_audit = False
            if labels.get("anvil.retain_logic_state") == "true":
                try:
                    plc_config = json.loads(env_values.get("PLC_CONFIG", "{}"))
                    mount = mount_at("/var/lib/anvil/retention")
                    volume = inspect_resource("volume", mount.get("Source", "")) if inspect_resource and isinstance(mount, dict) else None
                    volume_labels = _labels((volume or {}).get("Labels"))
                    retained_plc = (
                        configured_runtime_ref in self.plc_images and plc_config.get("retain_logic_state") is True
                        and set(mount) == {"Type", "Source", "Target", "ReadOnly"}
                        and mount.get("Type") == "volume" and mount.get("Target") == "/var/lib/anvil/retention"
                        and mount.get("ReadOnly") is False and mount.get("Source") == volume_labels.get("anvil.volume_name")
                        and _owned_retention_volume(volume_labels)
                        and volume_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and volume_labels.get("anvil.node_id") == labels.get("anvil.node_id")
                    )
                except (TypeError, ValueError, KeyError):
                    retained_plc = False
            if labels.get("anvil.scada_history_buffer") == "true":
                try:
                    scada_config = json.loads(env_values.get("SCADA_CONFIG", "{}"))
                    history_ack_token = env_values.get("SCADA_HISTORY_ACK_TOKEN", "")
                    mount = mount_at("/var/lib/anvil/scada-history")
                    volume = inspect_resource("volume", mount.get("Source", "")) if inspect_resource and isinstance(mount, dict) else None
                    volume_labels = _labels((volume or {}).get("Labels"))
                    capacity = scada_config.get("durable_history_capacity", 100_000)
                    max_bytes = scada_config.get("durable_history_max_bytes", 134_217_728)
                    retained_scada = (
                        configured_runtime_ref in self.scada_images
                        and re.fullmatch(r"[A-Za-z0-9_-]{43}", history_ack_token) is not None
                        and scada_config.get("durable_history_path") == "/var/lib/anvil/scada-history/history.sqlite3"
                        and type(capacity) is int and 1000 <= capacity <= 100_000
                        and type(max_bytes) is int and 16_777_216 <= max_bytes <= 536_870_912
                        and set(mount) == {"Type", "Source", "Target", "ReadOnly"}
                        and mount.get("Type") == "volume"
                        and mount.get("Target") == "/var/lib/anvil/scada-history"
                        and mount.get("ReadOnly") is False
                        and mount.get("Source") == volume_labels.get("anvil.volume_name")
                        and _owned_scada_history_volume(volume_labels)
                        and volume_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and volume_labels.get("anvil.node_id") == labels.get("anvil.node_id")
                    )
                except (TypeError, ValueError, KeyError):
                    retained_scada = False
            if labels.get("anvil.protocol_audit_queue") == "true":
                try:
                    plc_config = json.loads(env_values.get("PLC_CONFIG", "{}"))
                    modbus_config = json.loads(env_values.get("MODBUS_CONFIG", "{}"))
                    ack_token = env_values.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN", "")
                    mount = mount_at("/var/lib/anvil/protocol-audit")
                    volume = (inspect_resource("volume", mount.get("Source", ""))
                              if inspect_resource and isinstance(mount, dict) else None)
                    volume_labels = _labels((volume or {}).get("Labels"))
                    device_type = labels.get("anvil.device_type")
                    retained_protocol_audit = (
                        re.fullmatch(r"[A-Za-z0-9_-]{43}", ack_token) is not None
                        and ((device_type == "plc-s7" and configured_runtime_ref in self.plc_images
                              and "PLC_CONFIG" in env_values)
                             or (device_type == "modbus-tcp" and configured_runtime_ref in self.modbus_images
                                 and modbus_config.get("transport", "tcp") in {"tcp", "rtu"})
                             or (device_type == "tcp-gateway" and configured_runtime_ref in self.plc_images
                                 and self._tcp_gateway_container_valid(inspected)
                                 and labels.get("anvil.route_inspect_protocol") == "modbus-tcp"))
                        and set(mount) == {"Type", "Source", "Target", "ReadOnly"}
                        and mount.get("Type") == "volume"
                        and mount.get("Target") == "/var/lib/anvil/protocol-audit"
                        and mount.get("ReadOnly") is False
                        and mount.get("Source") == volume_labels.get("anvil.volume_name")
                        and _owned_protocol_audit_volume(volume_labels)
                        and volume_labels.get("anvil.scenario_id") == labels.get("anvil.scenario_id")
                        and volume_labels.get("anvil.node_id") == labels.get("anvil.node_id")
                    )
                except (TypeError, ValueError, KeyError):
                    retained_protocol_audit = False
            audit_expected = ((protocol_runtime and not proxy_role)
                              or (labels.get("anvil.device_type") == "tcp-gateway"
                                  and labels.get("anvil.route_inspect_protocol") == "modbus-tcp"))
            expected_mount_targets = set()
            if retained_plc:
                expected_mount_targets.add("/var/lib/anvil/retention")
            if retained_scada:
                expected_mount_targets.add("/var/lib/anvil/scada-history")
            if retained_protocol_audit:
                expected_mount_targets.add("/var/lib/anvil/protocol-audit")
            actual_mount_targets = [item.get("Target") for item in retention_mounts if isinstance(item, dict)]
            mounts_valid = (
                len(retention_mounts) == len(actual_mount_targets)
                and len(actual_mount_targets) == len(set(actual_mount_targets))
                and set(actual_mount_targets) == expected_mount_targets
            )
            if is_egress and host.get("PortBindings"):
                return False, "Egress gateway ports must remain private to the scenario networks"
            hostname = labels.get("anvil.hostname")
            allowed_users = {"0:0", "0"} if is_capture else {"65534:65534", "65534"}
            if (config.get("User") not in allowed_users
                    or host.get("Privileged", False) is True
                    or host.get("ReadonlyRootfs") is not True
                    or "ALL" not in host.get("CapDrop", [])
                    or not set(host.get("CapAdd", [])).issubset({"NET_RAW", "SETGID", "SETUID"} if is_capture else {"NET_BIND_SERVICE"})
                    or "no-new-privileges:true" not in host.get("SecurityOpt", [])
                    or host.get("Binds") or not mounts_valid or host.get("Devices")
                    or (labels.get("anvil.scada_history_buffer") == "true" and not retained_scada)
                    or (audit_expected and (labels.get("anvil.protocol_audit_queue") != "true"
                                            or not retained_protocol_audit))
                    or host.get("PidMode") == "host"
                    or (host.get("NetworkMode") == "host"
                        and not (is_capture and labels.get("anvil.capture_scope") == "segment"))
                    or not isinstance(host.get("Memory"), int) or host.get("Memory") <= 0
                    or not isinstance(host.get("NanoCpus"), int) or host.get("NanoCpus") <= 0
                    or not isinstance(host.get("PidsLimit"), int) or not 1 <= host.get("PidsLimit") <= 32):
                return False, "Container does not meet Anvil's enforced runtime security policy"
            if not is_capture and (not isinstance(hostname, str) or not DNS_LABEL.fullmatch(hostname)
                                   or config.get("Hostname") != hostname):
                return False, "Managed container hostname must match its Anvil hostname label"
            if (config.get("Volumes") or host.get("VolumesFrom") or host.get("DeviceRequests")
                    or host.get("Sysctls") or host.get("CgroupParent")
                    or host.get("Runtime") not in (None, "", "runc")
                    or not valid_log_config
                    or host.get("StorageOpt") or host.get("Ulimits")
                    or host.get("Tmpfs") not in (None, {}, {"/tmp": "rw,nosuid,nodev,noexec,size=16m"})
                    or host.get("IpcMode", "private") not in {"", "private"}
                    or host.get("PidMode", "") not in {"", "private"}
                    or host.get("UTSMode", "") not in {"", "private"}
                    or host.get("UsernsMode", "") not in {"", "private"}
                    or host.get("CgroupnsMode", "") not in {"", "private"}
                    or host.get("SecurityOpt") != ["no-new-privileges:true"]):
                return False, "Container requests a host namespace or unsupported security option"
            network_mode = host.get("NetworkMode", "")
            if is_capture:
                target_match = re.fullmatch(r"container:([a-f0-9]{12,64})", network_mode)
                capture_id = str(labels.get("anvil.capture_id", ""))
                target_node_id = str(labels.get("anvil.capture_target_node_id", ""))
                capture_scope = labels.get("anvil.capture_scope", "device")
                capture_zone_id = str(labels.get("anvil.capture_zone_id", ""))
                capture_network_id = str(labels.get("anvil.capture_network_id", ""))
                capture_interface = str(labels.get("anvil.capture_interface", ""))
                capture_peer_node_id = str(labels.get("anvil.capture_peer_node_id", ""))
                capture_peer_container_id = str(labels.get("anvil.capture_peer_container_id", ""))
                capture_peer_ip = str(labels.get("anvil.capture_peer_ip", ""))
                if (labels.get("anvil.role") != "packet-capture"
                        or labels.get("anvil.node_id") != f"capture-{capture_id}"
                        or config.get("User") not in {"0:0", "0"}
                        or not SCENARIO_ID.fullmatch(capture_id)
                        or capture_scope not in {"device", "link", "segment"}
                        or config.get("Entrypoint") != ["/bin/sh", "-c"]
                        or config.get("StopSignal") != "SIGINT"
                        or host.get("PortBindings") or (config.get("NetworkingConfig") or {}).get("EndpointsConfig")):
                    return False, "Capture containers must use the fixed, bounded Anvil packet-capture profile"
                if capture_scope == "device":
                    if (not target_match or not target_node_id or capture_zone_id or capture_network_id
                            or capture_interface or capture_peer_node_id or capture_peer_container_id or capture_peer_ip
                            or host.get("CapAdd") != ["NET_RAW", "SETGID", "SETUID"]
                            or host.get("NetworkMode") != network_mode
                            or config.get("Cmd") != ["tcpdump -i any -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""]):
                        return False, "Device capture must use the fixed, bounded device namespace profile"
                    target = inspect_resource("container", target_match.group(1)) if inspect_resource else None
                    target_labels = (target or {}).get("Config", {}).get("Labels", {})
                    if (not target or not _owned(target_labels)
                            or target_labels.get("anvil.scenario_id") != labels.get("anvil.scenario_id")
                            or target_labels.get("anvil.node_id") != target_node_id
                            or target_labels.get("anvil.zone_id", "default") != labels.get("anvil.zone_id", "default")
                            or (target.get("State") or {}).get("Status") != "running"):
                        return False, "Capture target must be a running device in the same Anvil scenario and zone"
                elif capture_scope == "link":
                    expected_command = [f"tcpdump -i any host {capture_peer_ip} -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""]
                    if (not target_match or not target_node_id or not capture_peer_node_id
                            or capture_peer_node_id == target_node_id
                            or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,32}", labels.get("anvil.zone_id", "default"))
                            or capture_zone_id or capture_network_id or capture_interface
                            or not re.fullmatch(r"[a-f0-9]{12,64}", capture_peer_container_id)
                            or host.get("CapAdd") != ["NET_RAW", "SETGID", "SETUID"]
                            or host.get("NetworkMode") != network_mode
                            or not config.get("Cmd") or config.get("Cmd") != expected_command):
                        return False, "Link capture must use the fixed, bounded peer-filtered device namespace profile"
                    source = inspect_resource("container", target_match.group(1)) if inspect_resource else None
                    source_labels = (source or {}).get("Config", {}).get("Labels", {})
                    peer = inspect_resource("container", capture_peer_container_id) if inspect_resource else None
                    peer_labels = (peer or {}).get("Config", {}).get("Labels", {})
                    source_networks = (source or {}).get("NetworkSettings", {}).get("Networks", {})
                    peer_networks = (peer or {}).get("NetworkSettings", {}).get("Networks", {})
                    shared_network_ids = {
                        item.get("NetworkID") for item in source_networks.values()
                        if item.get("NetworkID")
                    } & {
                        item.get("NetworkID") for item in peer_networks.values()
                        if item.get("NetworkID")
                    }
                    peer_ips = {
                        item.get("IPAddress") for item in peer_networks.values()
                        if item.get("NetworkID") in shared_network_ids
                    }
                    try:
                        peer_address = ip_address(capture_peer_ip)
                        valid_peer_ip = isinstance(peer_address, IPv4Address) and capture_peer_ip in peer_ips
                    except ValueError:
                        valid_peer_ip = False
                    if (not source or not _owned(source_labels)
                            or source_labels.get("anvil.scenario_id") != labels.get("anvil.scenario_id")
                            or source_labels.get("anvil.node_id") != target_node_id
                            or source_labels.get("anvil.zone_id", "default") != labels.get("anvil.zone_id", "default")
                            or (source.get("State") or {}).get("Status") != "running"
                            or not peer or not _owned(peer_labels)
                            or peer_labels.get("anvil.scenario_id") != labels.get("anvil.scenario_id")
                            or peer_labels.get("anvil.node_id") != capture_peer_node_id
                            or peer_labels.get("anvil.zone_id", "default") != labels.get("anvil.zone_id", "default")
                            or (peer.get("State") or {}).get("Status") != "running"
                            or not shared_network_ids or not valid_peer_ip):
                        return False, "Link capture endpoints must be running devices in the same scenario and shared network"
                else:
                    if (target_match or target_node_id or capture_peer_node_id or capture_peer_container_id or capture_peer_ip
                            or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,32}", capture_zone_id)
                            or network_mode != "host" or host.get("NetworkMode") != "host"
                            or host.get("CapAdd") != ["NET_RAW", "SETGID", "SETUID"]
                            or not re.fullmatch(r"br-[a-f0-9]{12}", capture_interface)
                            or not re.fullmatch(r"[a-f0-9]{64}", capture_network_id)):
                        return False, "Segment capture must use the fixed bridge-only profile"
                    expected_command = [f"tcpdump -i {capture_interface} -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""]
                    if config.get("Cmd") != expected_command:
                        return False, "Segment capture command must be fixed to the declared bridge interface"
                    network = inspect_resource("network", capture_network_id) if inspect_resource else None
                    network_labels = (network or {}).get("Labels", {})
                    network_options = (network or {}).get("Options", {})
                    expected_interface = f"br-{capture_network_id[:12]}"
                    if (not network or network.get("Id") != capture_network_id
                            or not _owned_network(network_labels)
                            or network_labels.get("anvil.scenario_id") != labels.get("anvil.scenario_id")
                            or network_labels.get("anvil.zone_id") != capture_zone_id
                            or network.get("Driver") != "bridge" or network.get("Internal") is not True
                            or network_options.get("com.docker.network.bridge.name", expected_interface) != expected_interface
                            or capture_interface != expected_interface):
                        return False, "Segment capture must target the owned internal bridge for its declared zone"
            elif (network_mode not in {"", "none"} and not network_mode.startswith("anvil-")
                  and not (labels.get("anvil.role") == "protocol-ingress" and network_mode == "bridge")):
                return False, "Containers may use only an Anvil network or no network"
            if not is_capture and network_mode.startswith("anvil-"):
                network = inspect_resource("network", network_mode) if inspect_resource else None
                if not network or not _owned_network(network.get("Labels")) or network.get("Internal") is not True:
                    return False, "Container network must be an owned internal Anvil network"
            port_bindings = host.get("PortBindings") or {}
            if is_ingress and (not port_bindings or len(port_bindings) != 1):
                return False, "Protocol ingress must publish exactly one configured host protocol port"
            if is_ingress:
                try:
                    ingress_config = json.loads(dict(item.split("=", 1) for item in (config.get("Env") or [])
                                                     if isinstance(item, str) and "=" in item)["ANVIL_INGRESS_CONFIG"])
                    external_ip = ip_address(labels.get("anvil.external_host_ip", ""))
                    host_port = str(int(labels.get("anvil.external_port", "")))
                    container_port = str(int(labels.get("anvil.external_container_port", "")))
                    valid_ingress_binding = (
                        isinstance(external_ip, IPv4Address) and not external_ip.is_unspecified and not external_ip.is_multicast
                        and container_port == str(ingress_config["listen_port"]) == "25000"
                        and set(port_bindings) == {f"{container_port}/tcp"}
                        and len(port_bindings[f"{container_port}/tcp"]) == 1
                        and port_bindings[f"{container_port}/tcp"][0].get("HostIp") == str(external_ip)
                        and port_bindings[f"{container_port}/tcp"][0].get("HostPort") == host_port
                    )
                except (TypeError, ValueError, KeyError):
                    valid_ingress_binding = False
                if not valid_ingress_binding:
                    return False, "Protocol ingress published port must match its explicit loopback/interface configuration"
            if labels.get("anvil.external_access") == "true" and not port_bindings:
                return False, "Explicit external access labels require a matching published protocol port"
            if port_bindings:
                if not is_ingress:
                    return False, "Protocol ports may be published only by the dedicated ingress relay"
                try:
                    if is_ingress:
                        raise StopIteration
                    external_ip = ip_address(labels.get("anvil.external_host_ip", ""))
                    host_port = str(int(labels.get("anvil.external_port", "")))
                    container_port = str(int(labels.get("anvil.external_container_port", "")))
                    runtime_env = config.get("Env") or []
                    runtime_env = dict(item.split("=", 1) for item in runtime_env if isinstance(item, str) and "=" in item)
                    expected_container_ports = set()
                    if config.get("Image") in self.plc_images and "PLC_CONFIG" in runtime_env:
                        expected_container_ports.add("102")
                    if config.get("Image") in self.modbus_images and "MODBUS_CONFIG" in runtime_env:
                        modbus_config = json.loads(runtime_env["MODBUS_CONFIG"])
                        if isinstance(modbus_config, dict):
                            expected_container_ports.add(str(int(modbus_config.get("port", 0))))
                    valid_external_binding = (
                        isinstance(external_ip, IPv4Address)
                        and not external_ip.is_unspecified
                        and not external_ip.is_multicast
                        and 1 <= int(host_port) <= 65535
                        and container_port in expected_container_ports
                        and set(port_bindings) == {f"{container_port}/tcp"}
                        and len(port_bindings[f"{container_port}/tcp"]) == 1
                        and port_bindings[f"{container_port}/tcp"][0].get("HostIp") == str(external_ip)
                        and port_bindings[f"{container_port}/tcp"][0].get("HostPort") == host_port
                    )
                except (TypeError, ValueError, KeyError):
                    valid_external_binding = False
                except StopIteration:
                    valid_external_binding = True
                if not valid_external_binding:
                    return False, "Published port must match the explicitly enabled and validated Anvil host binding"
            endpoints = (config.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
            if not is_capture and (any(not str(network).startswith("anvil-")
                                       and not (is_ingress and network == "bridge") for network in endpoints)
                                   or len(endpoints) not in {0, 1, 2}):
                return False, "Containers may connect only to one Anvil network, except an explicit TCP route gateway"
            if not is_capture and labels.get("anvil.role") not in {"tcp-gateway", "protocol-ingress"} and len(endpoints) > 1:
                return False, "Only an Anvil TCP route or protocol ingress gateway may connect to two networks"
            if not is_capture and labels.get("anvil.role") == "tcp-gateway":
                if not self._tcp_gateway_container_valid({"Config": config}):
                    return False, "TCP route gateway must use the fixed Anvil relay and authenticated local control socket"
                route_port = labels.get("anvil.route_target_port", "")
                listen_port = labels.get("anvil.route_listen_port", "")
                latency_ms = labels.get("anvil.route_latency_ms", "0")
                source_ip = labels.get("anvil.route_source_ip", "")
                target_ip = labels.get("anvil.route_target_ip", "")
                try:
                    source_address = ip_address(source_ip)
                    target_address = ip_address(target_ip)
                    valid_route = (len(endpoints) == 2
                                   and labels.get("anvil.route_protocol") in {"s7", "modbus-tcp", "field-http", "tcp"}
                                   and bool(labels.get("anvil.route_target_node_id"))
                                   and bool(labels.get("anvil.route_source_node_id"))
                                   and isinstance(source_address, IPv4Address)
                                   and isinstance(target_address, IPv4Address)
                                   and 1 <= int(route_port) <= 65535
                                   and 1024 <= int(listen_port) <= 65535
                                   and 0 <= int(latency_ms) <= 10000
                                   and labels.get("anvil.route_source_zone") != labels.get("anvil.route_target_zone")
                                   and (labels.get("anvil.route_protocol") != "s7" or int(route_port) == 102)
                                   and (labels.get("anvil.route_protocol") != "field-http"
                                        or labels.get("anvil.route_target_node_id") == "__anvil_field__"
                                        and int(route_port) == 8765))
                except (TypeError, ValueError):
                    valid_route = False
                if not valid_route:
                    return False, "TCP route gateway requires one explicit, bounded cross-zone route"
            for network_name, endpoint in (endpoints.items() if not is_capture else []):
                network = inspect_resource("network", network_name) if inspect_resource else None
                network_labels = _labels((network or {}).get("Labels"))
                is_ingress_endpoint = labels.get("anvil.role") == "protocol-ingress" and network_name == "bridge"
                if not network:
                    return False, "Container endpoints must use owned internal Anvil networks"
                if is_ingress_endpoint:
                    if network.get("Name") != "bridge" or network.get("Driver") != "bridge" or network.get("Internal") is not False:
                        return False, "Protocol ingress may connect only to Docker's standard bridge for host publication"
                elif (not _owned_network(network_labels) or network.get("Internal") is not True
                      or network_labels.get("anvil.scenario_id") != labels.get("anvil.scenario_id")):
                    return False, "Container endpoints must use owned internal Anvil networks"
                if labels.get("anvil.role") == "protocol-ingress" and not is_ingress_endpoint:
                    if network.get("Internal") is not True or network_labels.get("anvil.purpose"):
                        return False, "Protocol ingress may use only its scenario zone and dedicated ingress bridge"
                if labels.get("anvil.role") == "tcp-gateway":
                    zone_id = network_labels.get("anvil.zone_id")
                    if zone_id not in {labels.get("anvil.route_source_zone"), labels.get("anvil.route_target_zone")}:
                        return False, "TCP route gateway may attach only to its declared source and target zones"
                    if not isinstance(endpoint, dict):
                        return False, "TCP route endpoint must declare its static IPv4 address"
                    ipam = network.get("IPAM") or {}
                    ipam_config = ipam.get("Config", []) if isinstance(ipam, dict) else None
                    if not isinstance(ipam_config, list):
                        return False, "TCP route network must declare its IPv4 subnet"
                    subnets = [item.get("Subnet") for item in ipam_config
                               if isinstance(item, dict) and item.get("Subnet")]
                    try:
                        if len(subnets) != 1:
                            return False, "TCP route networks must each declare one IPv4 subnet"
                        subnet = ip_network(subnets[0], strict=True)
                        endpoint_ip = ip_address(((endpoint.get("IPAMConfig") or {}).get("IPv4Address")))
                    except (TypeError, ValueError):
                        return False, "TCP route network and endpoint addresses must be valid IPv4 values"
                    if not isinstance(subnet, IPv4Network) or not isinstance(endpoint_ip, IPv4Address):
                        return False, "TCP route networks and endpoints must use IPv4"
                    if zone_id == labels.get("anvil.route_source_zone"):
                        if (source_address not in subnet
                                or source_address in {subnet.network_address, subnet.broadcast_address, subnet.network_address + 1}
                                or endpoint_ip == source_address):
                            return False, "TCP route source must be an assigned node in its source zone"
                    elif (target_address not in subnet
                          or target_address in {subnet.network_address, subnet.broadcast_address, subnet.network_address + 1}
                          or endpoint_ip == target_address):
                        return False, "TCP route target must be inside its target zone"
                if (not isinstance(endpoint, dict)
                        or set(endpoint) - {"IPAMConfig", "Links", "Aliases", "DriverOpts", "MacAddress"}
                        or endpoint.get("Links") or endpoint.get("DriverOpts")
                        or endpoint.get("MacAddress")):
                    return False, "Container endpoint must use only its declared Anvil DNS alias and static IP"
                aliases = endpoint.get("Aliases") or []
                expected_aliases = ([hostname] if labels.get("anvil.role") != "tcp-gateway"
                                    or network_labels.get("anvil.zone_id") == labels.get("anvil.route_source_zone")
                                    else [])
                if labels.get("anvil.role") == "protocol-ingress" and is_ingress_endpoint:
                    expected_aliases = []
                if aliases != expected_aliases:
                    return False, "Container endpoint aliases must match the declared route side"
                ipam_config = endpoint.get("IPAMConfig") or {}
                if not isinstance(ipam_config, dict) or set(ipam_config) - {"IPv4Address"}:
                    return False, "Only a static IPv4 address is allowed on Anvil endpoints"
            if labels.get("anvil.role") == "tcp-gateway":
                attached_zones = {
                    _labels(inspect_resource("network", name).get("Labels")).get("anvil.zone_id")
                    for name in endpoints
                    if inspect_resource and inspect_resource("network", name)
                }
                if attached_zones != {labels.get("anvil.route_source_zone"), labels.get("anvil.route_target_zone")}:
                    return False, "TCP route gateway endpoints must be exactly its declared source and target zones"
            if labels.get("anvil.role") == "protocol-ingress":
                private_networks = [name for name in endpoints if name != "bridge"]
                # Docker attaches the default bridge implicitly from
                # HostConfig.NetworkMode. The scenario-zone endpoint is added
                # with /networks/{id}/connect after creation so the published
                # port always targets the primary bridge interface.
                if endpoints:
                    valid_attached_ingress = (
                        len(endpoints) == 2 and "bridge" in endpoints and len(private_networks) == 1
                    )
                    if not valid_attached_ingress:
                        return False, "Protocol ingress endpoints must be the default bridge and one private scenario zone"
                elif network_mode != "bridge":
                    return False, "Protocol ingress endpoints must be exactly one private zone and Docker's standard bridge"
            return True, ""

        container_match = re.fullmatch(r"/containers/([^/]+)(?:/(json|start|stop|restart|pause|unpause|kill|wait|logs|stats|exec))?", path)
        archive_match = re.fullmatch(r"/containers/([^/]+)/archive", path)
        if archive_match and method == "GET":
            params = parse_qs(query)
            if params.get("path") != ["/tmp"] or set(params) != {"path"}:
                return False, "Only the bounded capture tmpfs can be exported"
            resource_id = archive_match.group(1)
            inspected = inspect_resource("container", resource_id) if inspect_resource and RESOURCE_ID.fullmatch(resource_id) else None
            labels = (inspected or {}).get("Config", {}).get("Labels", {})
            if not inspected or not _owned(labels) or labels.get("anvil.role") != "packet-capture":
                return False, "Only Anvil capture containers can export their tmpfs"
            return True, ""
        if container_match:
            resource_id, action = container_match.groups()
            if action == "stats" and method == "GET":
                params = parse_qs(query)
                if (params.get("stream", ["false"])[0].lower() != "false"
                        or params.get("one-shot", ["false"])[0].lower() != "true"
                        or set(params) - {"stream", "one-shot"}):
                    return False, "Only single-sample Docker stats are enabled"
                if not RESOURCE_ID.fullmatch(resource_id) or not inspect_resource:
                    return False, "Container ID is not permitted"
                inspected = inspect_resource("container", resource_id)
                if not inspected or not _owned(inspected.get("Config", {}).get("Labels")):
                    return False, "Container is not managed by Anvil"
                return True, ""
            if method == "POST" and action == "exec":
                if not inspect_resource:
                    return False, "Container is not managed by Anvil"
                inspected = inspect_resource("container", resource_id)
                labels = _labels((inspected or {}).get("Config", {}).get("Labels"))
                if not inspected or not _owned(labels):
                    return False, "Container is not managed by Anvil"
                try:
                    config = json.loads(body)
                except (ValueError, TypeError):
                    return False, "Invalid exec configuration"
                if not isinstance(config, dict):
                    return False, "Invalid exec configuration"
                if self._runtime_operations_exec_valid(inspected, config):
                    return True, ""
                if (labels.get("anvil.protocol_audit_queue") == "true"
                        and self._protocol_audit_exec_config_valid(
                            inspected, config, resource_id, inspect_resource,
                        )):
                    return True, ""
                if (labels.get("anvil.role") == "tcp-gateway"
                        and self._tcp_route_control_exec_valid(inspected, config, resource_id)):
                    return True, ""
                if (labels.get("anvil.role") not in INTERACTIVE_EXEC_ROLES
                        or labels.get("anvil.terminal_access_enabled") != "true"):
                    return False, "Exec is restricted to stations with explicit terminal access or the fixed protocol audit reader"
                if not isinstance(config, dict) or config.get("Privileged") is True or config.get("User") not in {None, "", "65534", "65534:65534"}:
                    return False, "Exec must keep the managed container's unprivileged identity"
                return True, ""
            if action is None and method == "DELETE" or action in {"json", "logs"} and method == "GET" or action in {"start", "stop", "restart", "pause", "unpause", "kill"} and method == "POST":
                if not RESOURCE_ID.fullmatch(resource_id) or not inspect_resource:
                    return False, "Container ID is not permitted"
                inspected = inspect_resource("container", resource_id)
                if not inspected or not _owned(inspected.get("Config", {}).get("Labels")):
                    return False, "Container is not managed by Anvil"
                if action == "kill" or action == "wait":
                    return False, "Container action is not allowed"
                return True, ""

        exec_match = re.fullmatch(r"/exec/([^/]+)/(start|json)", path)
        if exec_match and method == ("POST" if exec_match.group(2) == "start" else "GET"):
            info = inspect_resource("exec", exec_match.group(1)) if inspect_resource else None
            labels = _labels((info or {}).get("Config", {}).get("Labels"))
            if not info or not _owned(labels):
                return False, "Exec session is not attached to an Anvil container"
            if self._runtime_operations_container_valid(info):
                return True, ""
            if (labels.get("anvil.protocol_audit_queue") == "true"
                    and self._protocol_audit_container_valid(info, inspect_resource)):
                return True, ""
            if labels.get("anvil.role") == "tcp-gateway" and self._tcp_gateway_container_valid(info):
                return True, ""
            if (labels.get("anvil.role") not in INTERACTIVE_EXEC_ROLES
                    or labels.get("anvil.terminal_access_enabled") != "true"):
                return False, "Exec is restricted to stations with explicit terminal access enabled"
            return True, ""
        return False, "Docker API endpoint is not enabled by the Anvil gateway"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    gate = DockerGate()

    def log_message(self, fmt, *args):
        pass

    def _send(self, status: int, payload: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if payload:
            self.wfile.write(payload)
        self.close_connection = True

    def _inspect(self, kind: str, resource_id: str):
        if kind == "exec":
            with self.server.exec_containers_lock:
                container_id = self.server.exec_containers.get(resource_id)
            if not container_id:
                return None
            inspected = self._inspect("container", container_id)
            if inspected is None:
                with self.server.exec_containers_lock:
                    self.server.exec_containers.pop(resource_id, None)
                return None
            return inspected
        if kind == "image":
            endpoint = f"/images/{urllib.parse.quote(resource_id, safe=':@')}/json"
        elif kind == "volume":
            endpoint = f"/volumes/{urllib.parse.quote(resource_id, safe='')}"
        else:
            endpoint = f"/networks/{resource_id}" if kind == "network" else f"/containers/{resource_id}/json"
        response = self._upstream("GET", endpoint, b"", {})
        if response.status != 200:
            response.close()
            return None
        try:
            return json.loads(response.read())
        except ValueError:
            return None
        finally:
            response.close()

    def _schedule_exec_cleanup(self, exec_id: str) -> None:
        timers = getattr(self.server, "exec_cleanup_timers", None)
        if timers is None:
            return
        with self.server.exec_containers_lock:
            existing = timers.pop(exec_id, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(300, self._expire_exec_tracking, args=(exec_id,))
            timer.daemon = True
            timers[exec_id] = timer
            timer.start()

    def _expire_exec_tracking(self, exec_id: str) -> None:
        with self.server.exec_containers_lock:
            self.server.exec_containers.pop(exec_id, None)
            self.server.exec_cleanup_timers.pop(exec_id, None)

    def _upstream(self, method: str, path: str, body: bytes, headers: dict[str, str]):
        connection = UnixHTTPConnection(SOCKET_PATH)
        connection.request(method, path, body=body if body else None, headers=headers)
        response = connection.getresponse()
        return response

    def _exec_tunnel(self, split, body):
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.settimeout(30)
        tunnel_established = False
        try:
            upstream.connect(SOCKET_PATH)
            path = split.path + ("?" + split.query if split.query else "")
            request_headers = [f"{key}: {value}" for key, value in self.headers.items()
                               if key.lower() not in {"host", "connection", "upgrade", "content-length", "transfer-encoding"}]
            request_headers.extend(("Host: docker", "Connection: Upgrade", "Upgrade: tcp", f"Content-Length: {len(body)}"))
            request = f"{self.command} {path} HTTP/1.1\r\n" + "\r\n".join(request_headers) + "\r\n\r\n"
            upstream.sendall(request.encode() + body)
            response_head = bytearray()
            while b"\r\n\r\n" not in response_head:
                block = upstream.recv(4096)
                if not block:
                    raise OSError("Docker closed the exec stream before sending response headers")
                response_head.extend(block)
            end = response_head.index(b"\r\n\r\n") + 4
            tunnel_established = b" 101 " in bytes(response_head[:end]).split(b"\r\n", 1)[0]
            self.connection.sendall(response_head[:end])
            if end < len(response_head):
                self.connection.sendall(response_head[end:])
            self.close_connection = True
            sockets = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 300)
                if not readable:
                    raise TimeoutError("Docker exec tunnel timed out")
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(data)
        finally:
            # A dropped API worker or browser otherwise leaves `/bin/sh -i` running
            # in the station. EOF terminates that exec without touching the container.
            if tunnel_established:
                try:
                    upstream.sendall(b"\x04")
                except OSError:
                    pass
            upstream.close()
            exec_match = re.fullmatch(r"/exec/([^/]+)/start", _api_path(split.path))
            if exec_match:
                exec_id = exec_match.group(1)
                with self.server.exec_containers_lock:
                    timer = getattr(self.server, "exec_cleanup_timers", {}).pop(exec_id, None)
                    tracked = exec_id in self.server.exec_containers
                if timer:
                    timer.cancel()
                # Docker clients inspect the exec result after the upgraded start
                # stream closes. Keep the ownership mapping until that inspection
                # confirms the process ended; otherwise the gateway rejects it.
                if tracked:
                    self._schedule_exec_cleanup(exec_id)

    def _handle(self):
        raw_path = self.path
        split = urlsplit(raw_path)
        path = _api_path(split.path)
        if self.command == "GET" and path == "/health":
            self._send(200, b'{"status":"ok"}')
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > MAX_BODY:
            self._send(413, b'{"message":"request body is too large"}')
            return
        body = self.rfile.read(length) if length else b""
        allowed, reason = self.gate.authorize(self.command, split.path, split.query, body, self._inspect)
        if not allowed:
            self._send(403, json.dumps({"message": reason}).encode())
            return
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in {"host", "connection", "content-length", "transfer-encoding"}}
        headers["Host"] = "docker"
        try:
            if self.command == "POST" and re.fullmatch(r"/exec/[^/]+/start", path) and self.headers.get("Upgrade", "").lower() == "tcp":
                self._exec_tunnel(split, body)
                return
            response = self._upstream(self.command, path + ("?" + split.query if split.query else ""), body, headers)
            payload = response.read()
            if self.command == "GET" and path in {"/containers/json", "/networks"} and response.status == 200:
                try:
                    resources = json.loads(payload)
                    if isinstance(resources, list):
                        if path == "/containers/json":
                            resources = [item for item in resources if _owned(item.get("Labels"))]
                        else:
                            resources = [item for item in resources if _owned_network(item.get("Labels"))]
                        payload = json.dumps(resources).encode()
                except (ValueError, TypeError, AttributeError):
                    payload = b"[]"
            exec_create = re.fullmatch(r"/containers/([^/]+)/exec", path)
            exec_inspect = re.fullmatch(r"/exec/([^/]+)/json", path)
            if self.command == "GET" and exec_inspect and response.status == 200:
                try:
                    exec_state = json.loads(payload)
                    if exec_state.get("Running") is False:
                        exec_id = exec_inspect.group(1)
                        with self.server.exec_containers_lock:
                            timer = self.server.exec_cleanup_timers.pop(exec_id, None)
                            if timer:
                                timer.cancel()
                            self.server.exec_containers.pop(exec_id, None)
                    else:
                        self._schedule_exec_cleanup(exec_inspect.group(1))
                except (ValueError, AttributeError):
                    pass
            if self.command == "DELETE" and re.fullmatch(r"/containers/[^/]+", path) and response.status in {204, 404}:
                deleted_id = path.rsplit("/", 1)[-1]
                with self.server.exec_containers_lock:
                    removed_ids = [exec_id for exec_id, container_id in self.server.exec_containers.items() if container_id == deleted_id]
                    self.server.exec_containers = {exec_id: container_id for exec_id, container_id in self.server.exec_containers.items() if container_id != deleted_id}
                    for exec_id in removed_ids:
                        timer = self.server.exec_cleanup_timers.pop(exec_id, None)
                        if timer:
                            timer.cancel()
            if self.command == "POST" and exec_create and response.status == 201:
                try:
                    exec_id = json.loads(payload).get("Id")
                    if exec_id:
                        with self.server.exec_containers_lock:
                            self.server.exec_containers[exec_id] = exec_create.group(1)
                            self.server.exec_cleanup_timers[exec_id] = None
                except (ValueError, AttributeError):
                    pass
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if payload:
                self.wfile.write(payload)
            response.close()
        except (OSError, http.client.HTTPException) as error:
            self._send(502, json.dumps({"message": f"Docker daemon unavailable: {error}"}).encode())
        finally:
            self.close_connection = True

    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle


def main():
    if not ALLOWED_IMAGES:
        raise SystemExit("ANVIL_ALLOWED_IMAGES must list the runtime images")
    server = ThreadingHTTPServer(("0.0.0.0", 12375), Handler)
    server.exec_containers = {}
    server.exec_containers_lock = Lock()
    server.exec_cleanup_timers = {}
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
