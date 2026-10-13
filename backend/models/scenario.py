"""Public scenario schemas and the private SQLite record."""

from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as PydanticField, StrictBool, StrictInt, field_validator, model_validator
from sqlmodel import Field, SQLModel
from models.field import FieldObject, ProcessConnection, SignalConnection, validate_connections, validate_process_connections



ScenarioState = Literal["stopped", "deploying", "running", "paused", "partial", "error"]


def default_hostname(node_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9-]+", "-", node_id).strip("-").lower() or "node"
    if len(normalized) > 63:
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized[:54].rstrip('-')}-{digest}"
    return normalized


class NetworkEgressRule(BaseModel):
    """One opt-in TCP destination that may be reached through an egress proxy."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str = PydanticField(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    source_node_id: str = PydanticField(min_length=1, max_length=128)
    hostname: str = PydanticField(min_length=3, max_length=253)
    tcp_port: StrictInt = PydanticField(default=443, ge=1, le=65535)
    enabled: StrictBool = True

    @field_validator("hostname")
    @classmethod
    def validate_public_hostname(cls, value: str) -> str:
        hostname = value.strip().lower().rstrip(".")
        if not hostname or not hostname.isascii() or len(hostname) > 253:
            raise ValueError("egress hostname must be an ASCII DNS name (use IDNA for international names)")
        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("egress destinations must be DNS names, not IP literals")
        labels = hostname.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("egress hostname must be a fully qualified DNS name without wildcards")
        if any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in (
            "localhost", "local", "internal", "test", "invalid", "example", "home.arpa"
        )):
            raise ValueError("egress hostname cannot use a local or reserved test domain")
        return hostname


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subnet: str
    zones: list["NetworkZone"] = PydanticField(default_factory=list)
    egress_rules: list[NetworkEgressRule] = PydanticField(default_factory=list, max_length=32)

    @field_validator("subnet")
    @classmethod
    def validate_subnet(cls, value: str) -> str:
        network = ip_network(value, strict=True)
        if not isinstance(network, IPv4Network):
            raise ValueError("only IPv4 scenario networks are supported")
        if network.prefixlen > 30:
            raise ValueError("scenario networks need space for a gateway and devices (prefix at most /30)")
        return str(network)


class NetworkZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_id: str = PydanticField(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = PydanticField(min_length=1, max_length=80)
    purdue_level: Literal["L0", "L1", "L2", "L3", "L4", "L5", "DMZ"]
    subnet: str

    @field_validator("subnet")
    @classmethod
    def validate_zone_subnet(cls, value: str) -> str:
        network = ip_network(value, strict=True)
        if not isinstance(network, IPv4Network):
            raise ValueError("only IPv4 zone networks are supported")
        if network.prefixlen > 30:
            raise ValueError("zone networks need space for a gateway and devices (prefix at most /30)")
        return str(network)


class NetworkRoute(BaseModel):
    """An explicit one-way service path through a dedicated TCP proxy."""
    model_config = ConfigDict(extra="forbid")

    route_id: str = PydanticField(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    source_node_id: str = PydanticField(min_length=1)
    target_node_id: str = PydanticField(min_length=1)
    protocol: Literal["s7", "modbus-tcp", "tcp"]
    tcp_port: StrictInt | None = PydanticField(default=None, ge=1, le=65535, exclude_if=lambda value: value is None)
    latency_ms: StrictInt = PydanticField(default=0, ge=0, le=10000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_tcp_port(self) -> "NetworkRoute":
        if self.protocol == "tcp" and self.tcp_port is None:
            raise ValueError("tcp_port is required for a generic TCP route")
        if self.protocol != "tcp" and self.tcp_port is not None:
            raise ValueError("tcp_port is only valid for a generic TCP route")
        return self

    @property
    def target_port(self) -> int:
        if self.protocol == "tcp":
            assert self.tcp_port is not None
            return self.tcp_port
        return 102 if self.protocol == "s7" else 502

    @property
    def listen_port(self) -> int:
        # Reserve a deterministic unprivileged port for each route.
        return 20000 + int(hashlib.sha256(self.route_id.encode()).hexdigest()[:4], 16) % 40000


class NetworkConnection(BaseModel):
    """A same-zone Ethernet link; links to a declared switch map to its zone bridge."""
    model_config = ConfigDict(extra="forbid")

    connection_id: str = PydanticField(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    source_node_id: str = PydanticField(min_length=1, max_length=128)
    target_node_id: str = PydanticField(min_length=1, max_length=128)
    label: str = PydanticField(default="Ethernet", max_length=80)


class DeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = PydanticField(min_length=1)
    hostname: str | None = PydanticField(default=None, min_length=1, max_length=63)
    device_type: str = PydanticField(min_length=1)
    name: str = PydanticField(min_length=1)
    ip: str
    zone_id: str = PydanticField(default="default", min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    external_access: bool = False
    external_port: int | None = PydanticField(default=None, ge=1, le=65535)
    external_host_ip: str = "127.0.0.1"
    config: Any

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
            raise ValueError("hostname must be a valid DNS label (letters, digits and internal hyphens)")
        return value

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        address = ip_address(value)
        if not isinstance(address, IPv4Address):
            raise ValueError("only IPv4 device addresses are supported")
        return str(address)

    @model_validator(mode="after")
    def validate_external_access(self) -> "DeviceInput":
        if self.hostname is None:
            object.__setattr__(self, "hostname", default_hostname(self.node_id))
        adapter = __import__("devices", fromlist=["DEVICE_REGISTRY"]).DEVICE_REGISTRY.get(self.device_type)
        service_port = adapter.external_port(self.config) if adapter and self.external_access else None
        if self.external_access and service_port is None:
            raise ValueError(f"Device type '{self.device_type}' does not support external access")
        if self.external_access and self.external_port is None:
            raise ValueError("external_port is required when external_access is enabled")
        if not self.external_access and self.external_port is not None:
            raise ValueError("external_port requires external_access")
        host_ip = ip_address(self.external_host_ip)
        if not isinstance(host_ip, IPv4Address) or host_ip.is_unspecified or host_ip.is_multicast:
            raise ValueError("external_host_ip must be a specific IPv4 address, not a wildcard or multicast address")
        if not self.external_access and str(host_ip) != "127.0.0.1":
            raise ValueError("external_host_ip requires external_access")
        object.__setattr__(self, "external_host_ip", str(host_ip))
        return self

    @model_validator(mode="before")
    @classmethod
    def validate_device_config(cls, values: Any) -> Any:
        if not isinstance(values, dict) or "config" not in values:
            return values
        device_type, config = values.get("device_type"), values["config"]
        if isinstance(config, BaseModel):
            config = config.model_dump(mode="json")
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        if device_type:
            from devices import validate_device_config

            values = dict(values)
            values["config"] = validate_device_config(device_type, config)
        return values


class ScenarioPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = PydanticField(min_length=1)
    network: NetworkInput
    devices: list[DeviceInput] = PydanticField(default_factory=list)
    field_objects: list[FieldObject] = PydanticField(default_factory=list)
    signal_connections: list[SignalConnection] = PydanticField(default_factory=list)
    process_connections: list[ProcessConnection] = PydanticField(default_factory=list)
    network_routes: list[NetworkRoute] = PydanticField(default_factory=list)
    network_connections: list[NetworkConnection] = PydanticField(default_factory=list)
    canvas: dict[str, Any] = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def validate_topology(self) -> "ScenarioPayload":
        if not self.devices and not self.field_objects:
            raise ValueError("a scenario needs at least one device or field object")
        base_network = ip_network(self.network.subnet)
        zones = {zone.zone_id: ip_network(zone.subnet) for zone in self.network.zones}
        if len(zones) != len(self.network.zones):
            raise ValueError("duplicate zone_id")
        for item in self.field_objects:
            if zones and item.zone_id not in zones:
                raise ValueError(f"field object '{item.object_id}' references unknown zone '{item.zone_id}'")
            if not zones and item.zone_id != "default":
                raise ValueError(f"field object '{item.object_id}' uses a zone but no Purdue zones are defined")
        zone_values = list(zones.items())
        for index, (zone_id, network) in enumerate(zone_values):
            if any(network.overlaps(other) for _, other in zone_values[index + 1:]):
                raise ValueError(f"zone '{zone_id}' subnet overlaps another zone subnet")
        node_ids: set[str] = set()
        addresses: set[tuple[str, str]] = set()
        hostnames: set[tuple[str, str]] = set()

        for device in self.devices:
            from devices import DEVICE_REGISTRY

            if device.device_type not in DEVICE_REGISTRY:
                raise ValueError(f"Unsupported device type '{device.device_type}'")
            if getattr(DEVICE_REGISTRY[device.device_type], "internal", False):
                raise ValueError("internal runtime devices cannot be configured directly")
            if device.node_id in node_ids:
                raise ValueError(f"duplicate node_id '{device.node_id}'")
            if zones:
                network = zones.get(device.zone_id)
                if network is None:
                    raise ValueError(f"device '{device.node_id}' references unknown zone '{device.zone_id}'")
            else:
                if device.zone_id != "default":
                    raise ValueError(f"device '{device.node_id}' uses a zone but no Purdue zones are defined")
                network = base_network
            address_key = (device.zone_id, device.ip)
            if address_key in addresses:
                raise ValueError(f"duplicate device IP '{device.ip}' in zone '{device.zone_id}'")
            if ip_address(device.ip) not in network:
                raise ValueError(
                    f"device '{device.node_id}' IP {device.ip} is outside {network}"
                )
            if ip_address(device.ip) in {network.network_address, network.broadcast_address, network.network_address + 1}:
                raise ValueError(f"device '{device.node_id}' uses a reserved network, broadcast or Docker gateway address")
            node_ids.add(device.node_id)
            addresses.add(address_key)
            hostname_key = (device.zone_id, device.hostname or default_hostname(device.node_id))
            if hostname_key in hostnames:
                raise ValueError(f"duplicate hostname '{hostname_key[1]}' in zone '{device.zone_id}'")
            hostnames.add(hostname_key)
        for device in self.devices:
            config = device.config
            targets = getattr(config, "target_node_ids", "")
            if device.device_type == "scada" and not targets.strip():
                raise ValueError(f"SCADA '{device.node_id}' needs at least one S7 or Modbus target")
            if targets:
                selected = [value.strip() for value in targets.split(",") if value.strip()]
                if len(selected) != len(set(selected)):
                    raise ValueError(f"device '{device.node_id}' has duplicate target node IDs")
                unknown = set(selected) - node_ids
                if unknown:
                    raise ValueError(f"device '{device.node_id}' references unknown nodes: {', '.join(sorted(unknown))}")
                if device.node_id in selected:
                    raise ValueError(f"device '{device.node_id}' cannot target itself")
                for target_id in selected:
                    target = next(item for item in self.devices if item.node_id == target_id)
                    if device.device_type == "scada" and target.device_type not in {"plc-s7", "modbus-tcp"}:
                        raise ValueError(f"SCADA '{device.node_id}' can only acquire PLC S7 or Modbus TCP devices")
                    if device.device_type == "hmi" and target.device_type not in {"plc-s7", "modbus-tcp"}:
                        raise ValueError(f"HMI '{device.node_id}' can only connect to PLC S7 or Modbus TCP devices")
                    if self.network.zones and target.zone_id != device.zone_id:
                        route_exists = any(route.source_node_id == device.node_id
                                           and route.target_node_id == target_id for route in self.network_routes)
                        if not route_exists:
                            raise ValueError(f"Device '{device.node_id}' and target '{target_id}' are in different zones; no inter-zone route is configured")
            if device.device_type == "hmi":
                alarm_sources = [value.strip() for value in config.alarm_source_node_ids.split(",") if value.strip()]
                if len(alarm_sources) != len(set(alarm_sources)):
                    raise ValueError(f"HMI '{device.node_id}' has duplicate alarm source node IDs")
                for source_id in alarm_sources:
                    source = next((item for item in self.devices if item.node_id == source_id), None)
                    if source is None or source.device_type != "scada":
                        raise ValueError(f"HMI '{device.node_id}' alarm sources must reference SCADA devices")
                    if source.zone_id != device.zone_id:
                        raise ValueError(f"HMI '{device.node_id}' and alarm source '{source_id}' must be in the same zone")
                if config.alarms:
                    from services.io_map import build_scenario_io_map
                    from devices import DEVICE_REGISTRY

                    target_devices = [next(item for item in self.devices if item.node_id == rule.target_node_id)
                                      for rule in config.alarms]
                    runtime_targets = [DEVICE_REGISTRY[item.device_type]().build_runtime_device(item)
                                       for item in {item.node_id: item for item in target_devices}.values()]
                    available = {(point.node_id, point.address): point.data_type
                                 for point in build_scenario_io_map(str(uuid4()), runtime_targets).points}
                    for rule in config.alarms:
                        data_type = available.get((rule.target_node_id, rule.address))
                        valid_type = data_type == "BOOL" if rule.mode == "state" else data_type in {"REAL", "UINT16"}
                        if not valid_type:
                            raise ValueError(
                                f"HMI {rule.mode} alarm '{rule.target_node_id}:{rule.address}' references an incompatible selected tag"
                            )
            if device.device_type == "scada":
                known = {item.node_id for item in self.devices}
                for rule in device.config.alarms:
                    if rule.target_node_id not in known:
                        raise ValueError(f"SCADA alarm references unknown target '{rule.target_node_id}'")
        published_ports: dict[int, str] = {}
        for device in self.devices:
            if not device.external_access or device.external_port is None:
                continue
            previous = published_ports.get(device.external_port)
            if previous is not None:
                raise ValueError(
                    f"external host port {device.external_port} is assigned to both "
                    f"'{previous}' and '{device.node_id}'"
                )
            published_ports[device.external_port] = device.node_id
        route_ids: set[str] = set()
        route_listeners: set[tuple[str, int]] = set()
        by_id = {device.node_id: device for device in self.devices}
        for route in self.network_routes:
            if route.route_id in route_ids:
                raise ValueError(f"duplicate network route id '{route.route_id}'")
            route_ids.add(route.route_id)
            source, target = by_id.get(route.source_node_id), by_id.get(route.target_node_id)
            if source is None or target is None:
                raise ValueError(f"network route '{route.route_id}' references an unknown device")
            if route.protocol != "tcp" and source.device_type not in {"scada", "hmi", "plc-s7"}:
                raise ValueError("network route source must be an HMI, SCADA or S7 PLC device")
            expected_type = "plc-s7" if route.protocol == "s7" else "modbus-tcp"
            if route.protocol != "tcp" and target.device_type != expected_type:
                raise ValueError(f"network route '{route.route_id}' protocol does not match its target")
            if source.device_type == "plc-s7" and route.protocol not in {"s7", "tcp"}:
                raise ValueError("PLC peer routes only support S7")
            if source.node_id == target.node_id or source.zone_id == target.zone_id:
                raise ValueError(f"network route '{route.route_id}' must connect different zones")
            if route.protocol == "tcp":
                from devices import DEVICE_REGISTRY
                for endpoint in (source, target):
                    adapter = DEVICE_REGISTRY[endpoint.device_type]()
                    if adapter.network_mode(adapter.build_runtime_device(endpoint)) is not None:
                        raise ValueError(f"generic TCP route '{route.route_id}' requires endpoints attached to a scenario network")
            listener = (source.node_id, route.listen_port)
            if listener in route_listeners:
                raise ValueError("network routes have a deterministic TCP listener collision")
            route_listeners.add(listener)
        egress_rule_ids: set[str] = set()
        egress_destinations: set[tuple[str, str, int]] = set()
        for rule in self.network.egress_rules:
            if rule.rule_id in egress_rule_ids:
                raise ValueError(f"duplicate egress rule id '{rule.rule_id}'")
            egress_rule_ids.add(rule.rule_id)
            key = (rule.source_node_id, rule.hostname, rule.tcp_port)
            if key in egress_destinations:
                raise ValueError("duplicate egress destination for one source device")
            egress_destinations.add(key)
            source = by_id.get(rule.source_node_id)
            if source is None:
                raise ValueError(f"egress rule '{rule.rule_id}' references an unknown source device")
            if source.device_type not in {"attacker-station", "engineering-station"}:
                raise ValueError("egress is available only to an explicitly network-enabled workstation")
            access_enabled = (
                source.config.participant_access_enabled if source.device_type == "attacker-station"
                else source.config.engineering_access_enabled
            )
            if not access_enabled:
                raise ValueError("enable the workstation's network access before configuring egress")
        connection_ids: set[str] = set()
        connection_pairs: set[frozenset[str]] = set()
        for connection in self.network_connections:
            if connection.connection_id in connection_ids:
                raise ValueError(f"duplicate network connection id '{connection.connection_id}'")
            connection_ids.add(connection.connection_id)
            source, target = by_id.get(connection.source_node_id), by_id.get(connection.target_node_id)
            if source is None or target is None:
                raise ValueError(f"network connection '{connection.connection_id}' references an unknown device")
            if source.node_id == target.node_id:
                raise ValueError(f"network connection '{connection.connection_id}' cannot connect a device to itself")
            if source.zone_id != target.zone_id:
                raise ValueError(f"network connection '{connection.connection_id}' crosses Purdue zones; use an explicit protocol route")
            from devices import DEVICE_REGISTRY
            for endpoint in (source, target):
                adapter = DEVICE_REGISTRY[endpoint.device_type]()
                if adapter.network_mode(adapter.build_runtime_device(endpoint)) is not None:
                    raise ValueError(f"network connection '{connection.connection_id}' requires both devices attached to a scenario network")
            pair = frozenset((source.node_id, target.node_id))
            if pair in connection_pairs:
                raise ValueError("duplicate network connection between the same devices")
            connection_pairs.add(pair)
        switches_by_zone: dict[str, list[Any]] = {}
        for device in self.devices:
            if device.device_type == "ethernet-switch":
                switches_by_zone.setdefault(device.zone_id, []).append(device)
        for zone_id, switches in switches_by_zone.items():
            if len(switches) > 1:
                raise ValueError(f"zone '{zone_id}' can have only one ethernet switch")
            switch = switches[0]
            attached: set[str] = set()
            for connection in self.network_connections:
                if switch.node_id in {connection.source_node_id, connection.target_node_id}:
                    attached.add(connection.target_node_id if connection.source_node_id == switch.node_id else connection.source_node_id)
            if len(attached) > switch.config.switch_port_capacity:
                raise ValueError(f"ethernet switch '{switch.node_id}' exceeds its configured port capacity")
            from devices import DEVICE_REGISTRY
            expected = set()
            for device in self.devices:
                if device.zone_id != zone_id or device.node_id == switch.node_id:
                    continue
                adapter = DEVICE_REGISTRY[device.device_type]()
                if adapter.network_mode(adapter.build_runtime_device(device)) is None:
                    expected.add(device.node_id)
            missing = expected - attached
            if missing:
                raise ValueError(f"devices in zone '{zone_id}' must connect to ethernet switch '{switch.node_id}': {', '.join(sorted(missing))}")
        for route in self.network_routes:
            source = by_id.get(route.source_node_id)
            target = by_id.get(route.target_node_id)
            if route.protocol == "tcp":
                continue
            if source and source.device_type in {"scada", "hmi"} and target:
                selected = {value.strip() for value in source.config.target_node_ids.split(",") if value.strip()}
                if target.node_id not in selected:
                    raise ValueError(f"network route '{route.route_id}' target must be selected by its HMI/SCADA source")
            elif source and source.device_type == "plc-s7" and target:
                if not any(component.type == "remote_input" and component.source_node_id == target.node_id
                           for component in source.config.components):
                    raise ValueError(f"network route '{route.route_id}' target must be referenced by a remote_input on its source PLC")
        from devices import DEVICE_REGISTRY
        from services.s7_links import resolve_s7_links

        if any(device.device_type == "plc-s7" and any(component.type == "remote_input" for component in device.config.components) for device in self.devices):
            if self.network.zones:
                by_id = {device.node_id: device for device in self.devices}
                for device in self.devices:
                    if device.device_type != "plc-s7":
                        continue
                    for component in device.config.components:
                        source = by_id.get(component.source_node_id) if component.type == "remote_input" else None
                        if source and source.zone_id != device.zone_id:
                            route_exists = any(route.source_node_id == device.node_id
                                               and route.target_node_id == source.node_id
                                               and route.protocol == "s7" for route in self.network_routes)
                            if not route_exists:
                                raise ValueError(f"S7 remote_input from '{device.node_id}' crosses zones; add an explicit S7 network route")
            resolve_s7_links([DEVICE_REGISTRY[device.device_type]().build_runtime_device(device) for device in self.devices])
        if self.field_objects or self.signal_connections:
            if any(link.route_enabled for link in self.signal_connections) and not zones:
                raise ValueError("field gateway routes require declared Purdue zones")
            if zones:
                by_node = {device.node_id: device for device in self.devices}
                by_field = {item.object_id: item for item in self.field_objects}
                connected_field_zones = set()
                routes_by_device: dict[str, set[bool | None]] = {}
                for link in self.signal_connections:
                    field_endpoint = link.source if link.source.kind == "field" else link.target
                    device_endpoint = link.target if link.target.kind == "device" else link.source
                    connected_field_zones.add(by_field[field_endpoint.object_id].zone_id)
                if len(connected_field_zones) > 1:
                    raise ValueError("connected field objects span multiple Purdue zones; one field runtime zone is required")
                field_runtime_zone = next(iter(connected_field_zones), self.field_objects[0].zone_id if self.field_objects else None)
                for link in self.signal_connections:
                    field_endpoint = link.source if link.source.kind == "field" else link.target
                    device_endpoint = link.target if link.target.kind == "device" else link.source
                    crosses_zone = by_node[device_endpoint.node_id].zone_id != field_runtime_zone
                    if crosses_zone and link.route_enabled is None:
                        raise ValueError(f"signal connection '{link.connection_id}' crosses zones; declare its explicit field gateway route")
                    if crosses_zone:
                        routes_by_device.setdefault(device_endpoint.node_id, set()).add(link.route_enabled)
                    if not crosses_zone and link.route_enabled is True:
                        raise ValueError(f"signal connection '{link.connection_id}' is in one zone; an inter-zone route is unnecessary")
                if any(len(states) > 1 for states in routes_by_device.values()):
                    raise ValueError("all cross-zone field signals from one PLC must share the same gateway route state")
            validate_connections(self.field_objects, self.signal_connections, [
                DEVICE_REGISTRY[device.device_type]().build_runtime_device(device)
                for device in self.devices
            ])
        validate_process_connections(self.field_objects, self.process_connections)
        return self


class ScenarioCreate(ScenarioPayload):
    pass


class ScenarioUpdate(ScenarioPayload):
    # Older UI clients PUT the full ScenarioRead response, including metadata
    # that is not part of the editable topology. Keep that request shape working.
    model_config = ConfigDict(extra="ignore")


class ScenarioCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = PydanticField(default=None, min_length=1, max_length=80)


class ScenarioExport(BaseModel):
    """Current versioned interchange envelope."""
    model_config = ConfigDict(extra="forbid")

    format: Literal["anvil-scenario"] = "anvil-scenario"
    schema_version: Literal[2] = 2
    scenario: ScenarioCreate


class ScenarioImportEnvelope(BaseModel):
    """Raw versioned input; scenario data is migrated before model validation."""
    model_config = ConfigDict(extra="forbid")

    format: Literal["anvil-scenario"]
    schema_version: StrictInt = PydanticField(ge=1)
    scenario: dict[str, Any]


class ScenarioRead(ScenarioPayload):
    id: UUID
    status: ScenarioState
    created_at: datetime
    owner_user_id: str | None = None
    access_role: str | None = None


class ContainerStatus(BaseModel):
    node_id: str
    name: str
    status: str
    health_status: str | None = None
    health_error: str | None = None
    runtime_error: str | None = None
    cpu_percent: float | None = None
    memory_usage_bytes: int | None = None
    memory_limit_bytes: int | None = None
    pids: int | None = None
    resource_metrics_status: Literal["good", "unavailable", "not_running", "missing", "not_applicable", "unknown"] = "unknown"
    resource_metrics_sampled_at: datetime | None = None
    runtime_kind: str = "container"
    cpu_percent: float | None = None
    memory_usage_bytes: int | None = None
    memory_limit_bytes: int | None = None
    pids: int | None = None


class ScenarioStatusRead(BaseModel):
    scenario_id: UUID
    status: ScenarioState
    containers: list[ContainerStatus] = PydanticField(default_factory=list)
    deployment_duration_seconds: float | None = PydanticField(default=None, ge=0)


class IoPointRead(BaseModel):
    node_id: str
    device_name: str
    component_name: str
    component_type: str
    db: int | None = None
    area: str = "DB"
    address: str
    data_type: str
    access: Literal["read", "read_write"]
    unit: str | None = None
    source_node_id: str | None = None
    source_address: str | None = None
    min_value: float | None = None
    max_value: float | None = None


class ScenarioIoMapRead(BaseModel):
    scenario_id: UUID
    points: list[IoPointRead] = PydanticField(default_factory=list)


class ContainerLogsRead(BaseModel):
    node_id: str
    name: str
    lines: list[str] = PydanticField(default_factory=list)


class ScenarioLogsRead(BaseModel):
    scenario_id: UUID
    containers: list[ContainerLogsRead] = PydanticField(default_factory=list)


class ScenarioRecord(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(index=True)
    network_json: str
    devices_json: str
    runtime_devices_json: str
    canvas_json: str | None = Field(default=None)
    field_objects_json: str = Field(default="[]")
    signal_connections_json: str = Field(default="[]")
    process_connections_json: str = Field(default="[]")
    network_routes_json: str = Field(default="[]", nullable=False, sa_column_kwargs={"server_default": "'[]'"})
    network_connections_json: str = Field(default="[]", nullable=False, sa_column_kwargs={"server_default": "'[]'"})
    status: str = Field(default="stopped", index=True)
    owner_user_id: str | None = Field(default=None, index=True, foreign_key="platform_users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def to_read_model(self) -> ScenarioRead:
        return ScenarioRead.model_validate(
            {
                "id": self.id,
                "name": self.name,
                "network": json.loads(self.network_json),
                "devices": json.loads(self.devices_json),
                "canvas": json.loads(self.canvas_json or "{}"),
                "field_objects": json.loads(self.field_objects_json or "[]"),
                "signal_connections": json.loads(self.signal_connections_json or "[]"),
                "process_connections": json.loads(self.process_connections_json or "[]"),
                "network_routes": json.loads(self.network_routes_json or "[]"),
                "network_connections": json.loads(self.network_connections_json or "[]"),
                "status": self.status,
                "created_at": self.created_at,
                "owner_user_id": self.owner_user_id,
            }
        )

    def runtime_devices(self) -> list[dict[str, Any]]:
        return json.loads(self.runtime_devices_json)


class ScenarioOperationLease(SQLModel, table=True):
    """Cross-worker lease protecting scenario deploy/stop and crash recovery."""

    __tablename__ = "scenario_operation_leases"
    scenario_id: str = Field(primary_key=True, foreign_key="scenarios.id")
    owner_id: str = Field(max_length=128)
    operation: str = Field(max_length=24)
    expires_at: float = Field(index=True)


class ScenarioNodeOperation(SQLModel, table=True):
    """Durable intent/result journal for an individual Docker node operation."""

    __tablename__ = "scenario_node_operations"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    scenario_id: str = Field(index=True, max_length=36)
    node_id: str = Field(max_length=128)
    action: str = Field(max_length=24)
    source: str = Field(default="api", max_length=24)
    idempotency_key: str | None = Field(default=None, unique=True, index=True, max_length=160)
    status: str = Field(default="pending", max_length=24)
    attempts: int = Field(default=0)
    precondition_json: str = Field(default="{}")
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    result_json: str = Field(default="{}")
    last_error: str | None = Field(default=None, max_length=1000)
