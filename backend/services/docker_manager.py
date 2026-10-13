"""Thin docker-py wrapper for scenario networks and containers."""

from __future__ import annotations

import re
import time
import base64
import hashlib
import json
import os
import secrets
import socket
import urllib.parse
from datetime import datetime, timezone
from ipaddress import IPv4Address, ip_address
from threading import Lock
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound
from docker.types import IPAMConfig, IPAMPool, LogConfig, Mount

from devices import DEVICE_REGISTRY
from services.pcap_merge import merge_pcaps


_METRICS_CACHE_TTL_SECONDS = 3.0
_CAPTURE_IMAGE = os.environ.get("ANVIL_CAPTURE_IMAGE", "anvil/capture:dev")


def _positive_integer_setting(name: str, default: int) -> int:
    value = os.environ.get(name)
    try:
        parsed = int(value) if value is not None else default
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return parsed


_SCENARIO_MAX_NODES = _positive_integer_setting("ANVIL_SCENARIO_MAX_NODES", 64)
_SCENARIO_MAX_MEMORY_BYTES = _positive_integer_setting("ANVIL_SCENARIO_MAX_MEMORY_BYTES", 4 * 1024**3)
_SCENARIO_MAX_NANO_CPUS = _positive_integer_setting("ANVIL_SCENARIO_MAX_NANO_CPUS", 15_000_000_000)
_CAPTURE_COMMAND = "tcpdump -i any -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\""


def _segment_capture_command(interface: str) -> str:
    if not re.fullmatch(r"br-[a-f0-9]{12}", interface):
        raise DockerManagerError("Docker bridge interface name is not safe to use for capture")
    return (f"tcpdump -i {interface} -nn -s 128 -U -c 40000 -Z root -w /tmp/anvil-capture.pcap & child=$!; "
            "trap 'kill -INT \"$child\" 2>/dev/null; wait \"$child\"; base64 -w 0 /tmp/anvil-capture.pcap; exit 0' INT TERM; "
            "wait \"$child\"; status=$?; base64 -w 0 /tmp/anvil-capture.pcap; exit \"$status\"")


def _link_capture_command(peer_ip: str) -> str:
    try:
        parsed = ip_address(peer_ip)
    except ValueError as error:
        raise DockerManagerError("Link capture peer must have a valid IP address") from error
    if not isinstance(parsed, IPv4Address) or parsed.is_unspecified or parsed.is_multicast:
        raise DockerManagerError("Link capture peer must have a specific IPv4 address")
    command = _CAPTURE_COMMAND.replace("tcpdump -i any -nn", f"tcpdump -i any host {parsed} -nn", 1)
    return command
_metrics_cache: dict[str, tuple[float, dict[str, Any] | None, dict[str, Any]]] = {}
_metrics_cache_lock = Lock()
_PARTICIPANT_TERMINAL_IDLE_TIMEOUT_SECONDS = 300


class _PrefixedSocket:
    """Keep bytes read past an HTTP upgrade header while exposing a duplex socket API."""

    def __init__(self, stream: socket.socket, prefix: bytes) -> None:
        self.stream = stream
        self.prefix = bytearray(prefix)

    def recv(self, size: int) -> bytes:
        if self.prefix:
            data = bytes(self.prefix[:size])
            del self.prefix[:size]
            return data
        return self.stream.recv(size)

    def read_prefix(self, size: int) -> bytes:
        """Return buffered upgrade bytes without touching the underlying socket."""
        if not self.prefix:
            return b""
        data = bytes(self.prefix[:size])
        del self.prefix[:size]
        return data

    def sendall(self, data: bytes) -> None:
        self.stream.sendall(data)

    def settimeout(self, timeout: float | None) -> None:
        self.stream.settimeout(timeout)

    def shutdown(self, how: int) -> None:
        self.stream.shutdown(how)

    def close(self) -> None:
        self.stream.close()


class DockerManagerError(RuntimeError):
    pass


def _retention_volume_name(scenario_id: str, node_id: str) -> str:
    return f"anvil-retain-{scenario_id}-{hashlib.sha256(node_id.encode()).hexdigest()[:16]}"


def _scada_history_volume_name(scenario_id: str, node_id: str) -> str:
    return f"anvil-scada-history-{scenario_id}-{hashlib.sha256(node_id.encode()).hexdigest()[:16]}"


def _protocol_audit_volume_name(scenario_id: str, node_id: str) -> str:
    return f"anvil-protocol-audit-{scenario_id}-{hashlib.sha256(node_id.encode()).hexdigest()[:16]}"


def _scenario_data_volume_labels(scenario_id: str, node_id: str, purpose: str, name: str) -> dict[str, str]:
    return {"anvil.managed": "true", "anvil.purpose": purpose,
            "anvil.scenario_id": scenario_id, "anvil.node_id": node_id,
            "anvil.volume_name": name}


def _docker_error_message(error: Exception) -> str:
    if isinstance(error, APIError) and error.explanation:
        detail = str(error.explanation).strip()
    else:
        detail = str(error).strip()
    lowered = detail.casefold()
    guidance = (
        ("no such image", "La imagen del dispositivo no está disponible. Construye o descarga la imagen configurada y vuelve a desplegar."),
        ("pull access denied", "No se pudo obtener la imagen del dispositivo. Comprueba el nombre, la etiqueta y el acceso al registry."),
        ("port is already allocated", "El puerto solicitado ya está ocupado en el host. Libera ese puerto o configura otro puerto externo."),
        ("address already in use", "La dirección o el puerto solicitado ya está en uso. Elige una IP/puerto disponible."),
        ("pool overlaps with other one", "La subred del escenario se solapa con una red Docker existente. Configura una subred distinta."),
        ("overlapping subnet", "La subred del escenario se solapa con una red existente. Configura una subred distinta."),
        ("cannot connect to the docker daemon", "No se puede contactar con Docker. Comprueba que el daemon y el gateway Docker estén activos y que DOCKER_HOST sea correcto."),
        ("permission denied", "Docker rechazó el acceso. Comprueba los permisos del socket/daemon configurado para Anvil."),
        ("network has active endpoints", "La red todavía tiene contenedores conectados. Detén o limpia primero los dispositivos que la usan."),
        ("endpoint with name", "Ya existe un endpoint con ese nombre en la red. Detén el despliegue anterior o elimina el recurso Anvil huérfano."),
    )
    for marker, message in guidance:
        if marker in lowered:
            return f"{message} Detalle Docker: {detail}"
    return detail or "Docker devolvió un error sin detalles. Revisa el estado y los logs del daemon."


def _healthcheck_failure(container_name: str, detail: str, *, timeout: float | None = None) -> str:
    """Return the healthcheck output with concrete, non-speculative recovery steps."""
    if timeout is not None:
        unit = "segundo" if timeout == 1 else "segundos"
        failure = f"no alcanzó estado saludable en {timeout:g} {unit}"
    else:
        failure = "no superó su healthcheck"
    output = detail.strip()[:500]
    diagnostic = f" Detalle del healthcheck: {output}." if output else ". El healthcheck no devolvió detalle."
    return (
        f"El contenedor '{container_name}' {failure}{diagnostic} "
        "Comprueba que la imagen corresponde al tipo de dispositivo, que la configuración y el puerto interno "
        "coinciden, y revisa los logs del contenedor antes de volver a desplegar."
    )


class DockerManager:
    _CONTAINER_LIMITS = {
        "plc-s7": ("128m", 500_000_000),
        "modbus-tcp": ("96m", 500_000_000),
        "field-runtime": ("128m", 500_000_000),
        "scada": ("128m", 500_000_000),
        "hmi": ("64m", 250_000_000),
        "gateway": ("64m", 250_000_000),
        "firewall": ("64m", 250_000_000),
        "attacker-station": ("64m", 250_000_000),
        "engineering-station": ("96m", 500_000_000),
        "protocol-ingress": ("64m", 250_000_000),
    }
    _MEMORY_UNIT_BYTES = {"k": 1024, "m": 1024**2, "g": 1024**3}

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or docker.from_env()
        try:
            self.client.ping()
        except DockerException as error:
            raise DockerManagerError(f"Docker daemon is unavailable: {error}") from error

    def pin_runtime_images(self, runtime_devices: list[dict[str, Any]]) -> None:
        """Resolve configured tags once and persist an immutable local image pin."""
        for runtime_device in runtime_devices:
            adapter = DEVICE_REGISTRY.get(runtime_device.get("device_type"))
            if adapter is None or getattr(adapter, "network_resource", False):
                continue
            config = adapter().build_container_config("image-resolution", runtime_device)
            configured_ref = runtime_device.get("image_ref") or config.get("image")
            if not configured_ref:
                continue
            image_id = runtime_device.get("image_id")
            image_pin = runtime_device.get("image_pin")
            try:
                stored_image_id = image_id
                image = self.client.images.get(image_pin or configured_ref)
                image_id = getattr(image, "id", None) or (image.attrs or {}).get("Id")
                attrs = image.attrs if isinstance(image.attrs, dict) else {}
                if image_pin and (image_pin not in (attrs.get("RepoTags") or [])
                                  or stored_image_id and image_id != stored_image_id):
                    raise DockerManagerError(f"Docker image pin '{image_pin}' no longer matches its stored ID")
                if not image_pin:
                    source_repo = configured_ref.split("@", 1)[0]
                    colon = source_repo.rfind(":")
                    if colon > source_repo.rfind("/"):
                        source_repo = source_repo[:colon]
                    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                        raise DockerManagerError(
                            f"Docker returned no valid immutable image ID for '{configured_ref}'"
                        )
                    repo_key = hashlib.sha256(source_repo.encode()).hexdigest()[:12]
                    image_pin = f"anvil-pinned/{repo_key}:{image_id.removeprefix('sha256:')}"
                    try:
                        existing_pin = self.client.images.get(image_pin)
                    except ImageNotFound:
                        existing_pin = None
                    if existing_pin is not None:
                        existing_id = getattr(existing_pin, "id", None) or (existing_pin.attrs or {}).get("Id")
                        if existing_id != image_id:
                            raise DockerManagerError(f"Image pin '{image_pin}' already refers to another image and was preserved")
                    elif not image.tag(f"anvil-pinned/{repo_key}", image_id.removeprefix("sha256:")):
                        raise DockerManagerError(f"Unable to create immutable local image pin for '{configured_ref}'")
                    else:
                        pinned_image = self.client.images.get(image_pin)
                        pinned_id = getattr(pinned_image, "id", None) or (pinned_image.attrs or {}).get("Id")
                        if pinned_id != image_id:
                            raise DockerManagerError(f"New image pin '{image_pin}' did not resolve to its source image")
            except DockerException as error:
                raise DockerManagerError(
                    f"Unable to resolve image '{image_id or configured_ref}' for node "
                    f"'{runtime_device.get('node_id', 'unknown')}': {_docker_error_message(error)}"
                ) from error
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                raise DockerManagerError(
                    f"Docker returned no valid immutable image ID for '{configured_ref}'"
                )
            if not isinstance(image_pin, str) or not re.fullmatch(r"anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}", image_pin):
                raise DockerManagerError(
                    f"Docker did not return a valid immutable image pin for '{configured_ref}'"
                )
            runtime_device["image_ref"] = configured_ref
            runtime_device["image_id"] = image_id
            runtime_device["image_pin"] = image_pin

    @staticmethod
    def _network_name(scenario_id: str, zone_id: str = "default", zoned: bool = False) -> str:
        if not zoned or zone_id == "default":
            return f"anvil-{scenario_id[:12]}"
        safe_zone = re.sub(r"[^a-zA-Z0-9_.-]+", "-", zone_id).strip("-._").lower() or "zone"
        return f"anvil-{scenario_id[:8]}-{safe_zone}"[:63]

    @staticmethod
    def _ingress_network_name(scenario_id: str) -> str:
        return f"anvil-{scenario_id[:8]}-ingress"

    def _create_ingress_network(self, scenario_id: str) -> Any:
        del scenario_id
        return self.client.networks.get("bridge")

    def ensure_managed_scenario_networks(
        self, scenario_id: str, subnet: str, runtime_devices: list[dict[str, Any]],
    ) -> list[str]:
        """Recreate only missing, scenario-owned networks from a persisted runtime plan."""
        zone_specs: dict[str, tuple[str, str, bool, str | None]] = {}
        for item in runtime_devices:
            if item.get("network_mode") == "none":
                continue
            zone_id = item.get("zone_id", "default")
            zone_subnet = item.get("zone_subnet", subnet)
            zoned = bool(item.get("zoned", False))
            name = self._network_name(scenario_id, zone_id, zoned)
            previous = zone_specs.get(zone_id)
            if previous and previous[1] != zone_subnet:
                raise DockerManagerError(f"Devices in zone '{zone_id}' have conflicting subnets")
            switch_node_id = item.get("node_id") if (
                DEVICE_REGISTRY.get(item.get("device_type"))
                and getattr(DEVICE_REGISTRY[item.get("device_type")], "network_resource", False)
            ) else (previous[3] if previous else None)
            zone_specs[zone_id] = (name, zone_subnet, zoned, switch_node_id)
            if item.get("device_type") == "tcp-gateway":
                target_zone_id = item["config"]["target_zone_id"]
                target_subnet = item["config"]["target_zone_subnet"]
                target_name = self._network_name(scenario_id, target_zone_id, zoned)
                target_previous = zone_specs.get(target_zone_id)
                if target_previous and target_previous[1] != target_subnet:
                    raise DockerManagerError(f"Devices in zone '{target_zone_id}' have conflicting subnets")
                zone_specs.setdefault(target_zone_id, (target_name, target_subnet, zoned, None))

        if not zone_specs:
            name = self._network_name(scenario_id)
            zone_specs["default"] = (name, subnet, False, None)

        existing_by_zone: dict[str, Any] = {}
        try:
            managed = self.client.networks.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
            for network in managed:
                labels = network.attrs.get("Labels") or {}
                if labels.get("anvil.managed") != "true" or labels.get("anvil.scenario_id") != scenario_id:
                    continue
                zone_id = labels.get("anvil.zone_id", "default")
                if zone_id in existing_by_zone:
                    raise DockerManagerError(f"Multiple managed networks found for scenario zone '{zone_id}'")
                existing_by_zone[zone_id] = network

            created: list[str] = []
            for zone_id, (network_name, zone_subnet, _zoned, switch_node_id) in zone_specs.items():
                if zone_id in existing_by_zone:
                    continue
                for collision in self.client.networks.list(names=[network_name]):
                    if collision.name == network_name:
                        raise DockerManagerError(
                            "A network with the required scenario name exists outside this scenario; it has been preserved"
                        )
                self.client.networks.create(
                    network_name,
                    driver="bridge",
                    internal=True,
                    options={"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                    labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                            "anvil.zone_id": zone_id,
                            **({"anvil.switch_node_id": switch_node_id} if switch_node_id else {})},
                    ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=zone_subnet)]),
                )
                created.append(zone_id)
            if any(item.get("device_type") == "protocol-ingress" for item in runtime_devices):
                self._create_ingress_network(scenario_id)
            return created
        except DockerManagerError:
            raise
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to restore managed scenario networks: {_docker_error_message(error)}"
            ) from error

    @staticmethod
    def _hostname(runtime_device: dict[str, Any]) -> str:
        value = runtime_device.get("hostname") or runtime_device.get("node_id", "node")
        normalized = re.sub(r"[^a-zA-Z0-9-]+", "-", value).strip("-").lower() or "node"
        return normalized[:63].rstrip("-") or "node"

    def deploy(
        self,
        scenario_id: str,
        subnet: str,
        runtime_devices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        deployment_started = time.monotonic()
        networks: dict[str, Any] = {}
        created_containers = []
        published_ports: list[int] = []
        for runtime_device in runtime_devices:
            if runtime_device.get("external_access"):
                port = runtime_device.get("external_port")
                if port is None or port in published_ports:
                    raise DockerManagerError(
                        "External host ports must be present and unique within a scenario"
                    )
                published_ports.append(port)
        self._validate_scenario_budget(runtime_devices)
        retention_requested = any(item.get("device_type") == "plc-s7"
                                  and item.get("config", {}).get("retain_logic_state") is True
                                  for item in runtime_devices)
        scada_history_requested = any(item.get("device_type") == "scada" for item in runtime_devices)
        protocol_audit_requested = any(
            item.get("device_type") in {"plc-s7", "modbus-tcp"}
            or (item.get("device_type") == "tcp-gateway"
                and item.get("config", {}).get("inspect_protocol") == "modbus-tcp")
            for item in runtime_devices
        )
        data_volume_requested = retention_requested or scada_history_requested or protocol_audit_requested
        existing_retention_volumes: set[str] = set()
        if data_volume_requested:
            try:
                existing_retention_volumes = {
                    volume.name for volume in self.client.volumes.list(
                        filters={"label": f"anvil.scenario_id={scenario_id}"})
                }
            except DockerException as error:
                raise DockerManagerError(f"Unable to inspect PLC retention data: {_docker_error_message(error)}") from error
        try:
            if self._scenario_containers(scenario_id):
                raise DockerManagerError("Scenario already has managed containers")
            zone_specs: dict[str, tuple[str, str, bool]] = {}
            switch_by_zone: dict[str, str] = {}
            for item in runtime_devices:
                if item.get("network_mode") == "none":
                    continue
                zone_id = item.get("zone_id", "default")
                zone_subnet = item.get("zone_subnet", subnet)
                zoned = bool(item.get("zoned", False))
                name = self._network_name(scenario_id, zone_id, zoned)
                previous = zone_specs.get(zone_id)
                if previous and previous[1] != zone_subnet:
                    raise DockerManagerError(f"Devices in zone '{zone_id}' have conflicting subnets")
                zone_specs[zone_id] = (name, zone_subnet, zoned)
                adapter = DEVICE_REGISTRY.get(item.get("device_type"))
                if adapter and getattr(adapter, "network_resource", False):
                    switch_by_zone[zone_id] = item["node_id"]
                if item.get("device_type") == "tcp-gateway":
                    target_zone_id = item["config"]["target_zone_id"]
                    target_subnet = item["config"]["target_zone_subnet"]
                    target_name = self._network_name(scenario_id, target_zone_id, zoned)
                    previous_target = zone_specs.get(target_zone_id)
                    if previous_target and previous_target[1] != target_subnet:
                        raise DockerManagerError(f"Devices in zone '{target_zone_id}' have conflicting subnets")
                    zone_specs[target_zone_id] = (target_name, target_subnet, zoned)
            if not zone_specs:
                name = self._network_name(scenario_id)
                zone_specs["default"] = (name, subnet, False)
            for zone_id, (network_name, zone_subnet, _zoned) in zone_specs.items():
                for stale_network in self.client.networks.list(names=[network_name]):
                    if stale_network.name != network_name:
                        continue
                    labels = stale_network.attrs.get("Labels") or {}
                    if labels.get("anvil.managed") != "true" or labels.get("anvil.scenario_id") != scenario_id:
                        raise DockerManagerError("A network with this name exists outside this scenario; it has been preserved")
                    stale_network.remove()
                networks[zone_id] = self.client.networks.create(
                    network_name,
                    driver="bridge",
                    # Each zone is internal and has no implicit external route.
                    internal=True,
                    # Engine 28+ removes the bridge's host-side gateway address,
                    # so containers cannot use host services through that bridge.
                    options={"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                    labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                            "anvil.zone_id": zone_id,
                            **({"anvil.switch_node_id": switch_by_zone[zone_id]} if zone_id in switch_by_zone else {})},
                            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=zone_subnet)]),
                )
            if any(item.get("device_type") == "protocol-ingress" for item in runtime_devices):
                networks["__ingress__"] = self._create_ingress_network(scenario_id)
            for runtime_device in runtime_devices:
                adapter = DEVICE_REGISTRY.get(runtime_device.get("device_type"))
                if adapter and getattr(adapter, "network_resource", False):
                    continue
                container = self._create_runtime_container(scenario_id, runtime_device, networks)
                created_containers.append(container)
                if runtime_device.get("device_type") == "egress-gateway":
                    self.client.networks.get("bridge").connect(container)
                container.start()
            for container in created_containers:
                self._wait_until_healthy(container)
        except DockerManagerError as error:
            cleanup_failures = self._cleanup_failed_deploy(
                scenario_id, list(networks.values()), created_containers
            )
            if data_volume_requested:
                cleanup_failures.extend(self._remove_new_retention_volumes(scenario_id, existing_retention_volumes))
            if cleanup_failures:
                raise DockerManagerError(
                    f"{error}. Deployment rollback incomplete: {'; '.join(cleanup_failures)}"
                ) from error
            raise
        except Exception as error:
            cleanup_failures = self._cleanup_failed_deploy(
                scenario_id, list(networks.values()), created_containers
            )
            if data_volume_requested:
                cleanup_failures.extend(self._remove_new_retention_volumes(scenario_id, existing_retention_volumes))
            detail = _docker_error_message(error)
            if "overlap" in detail.lower():
                detail = (
                    "The scenario subnet overlaps an existing Docker network. "
                    "Choose a different subnet."
                )
            if cleanup_failures:
                detail += f" Deployment rollback incomplete: {'; '.join(cleanup_failures)}"
            raise DockerManagerError(f"Unable to deploy scenario: {detail}") from error
        result = self.status(scenario_id)
        result["deployment_duration_seconds"] = round(time.monotonic() - deployment_started, 3)
        return result

    @classmethod
    def _apply_container_security(cls, config: dict[str, Any], runtime_device: dict[str, Any]) -> None:
        """Enforce a fixed, unprivileged runtime policy for scenario containers."""

        device_type = runtime_device["device_type"]
        if config.get("privileged") or config.get("network_mode") == "host":
            raise DockerManagerError("Privileged and host-network scenario containers are not allowed")
        if config.get("devices") or config.get("volumes") or config.get("mounts"):
            raise DockerManagerError("Host devices and mounts are not allowed in scenario containers")

        memory, nano_cpus = cls._CONTAINER_LIMITS.get(device_type, ("96m", 500_000_000))
        protocol_port = 102 if device_type == "plc-s7" else runtime_device.get("config", {}).get("port", 502)
        needs_low_port_capability = device_type in {"plc-s7", "modbus-tcp"} and protocol_port < 1024
        config.update(
            user="65534:65534",
            read_only=True,
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE"] if needs_low_port_capability else [],
            security_opt=["no-new-privileges:true"],
            pids_limit=32,
            mem_limit=memory,
            nano_cpus=nano_cpus,
            tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"},
        )
        if (device_type in {"plc-s7", "modbus-tcp", "protocol-ingress"}
                or (device_type == "tcp-gateway"
                    and runtime_device.get("config", {}).get("inspect_protocol") == "modbus-tcp")):
            # Keep semantic protocol events available to the asynchronous audit
            # collector while bounding per-container Docker log disk usage.
            config["log_config"] = LogConfig(
                type="json-file", config={"max-size": "10m", "max-file": "3"}
            )

    @classmethod
    def _validate_scenario_budget(cls, runtime_devices: list[dict[str, Any]]) -> None:
        """Reject scenario plans whose bounded containers exceed configured budgets."""
        if min(_SCENARIO_MAX_NODES, _SCENARIO_MAX_MEMORY_BYTES, _SCENARIO_MAX_NANO_CPUS) <= 0:
            raise DockerManagerError("Scenario node, memory, and CPU budgets must be positive")
        container_devices = [device for device in runtime_devices
                             if not (DEVICE_REGISTRY.get(device.get("device_type"))
                                     and getattr(DEVICE_REGISTRY[device["device_type"]], "network_resource", False))]
        node_count = len(runtime_devices)
        if node_count > _SCENARIO_MAX_NODES:
            raise DockerManagerError(
                f"Scenario contains {node_count} runtime nodes; configured maximum is {_SCENARIO_MAX_NODES}"
            )
        memory_bytes = 0
        nano_cpus = 0
        for device in container_devices:
            memory, cpu = cls._CONTAINER_LIMITS.get(device.get("device_type"), ("96m", 500_000_000))
            match = re.fullmatch(r"([1-9][0-9]*)([kmg])", memory.lower())
            if match is None:
                raise DockerManagerError(f"Invalid internal memory limit for '{device.get('device_type')}'")
            memory_bytes += int(match.group(1)) * cls._MEMORY_UNIT_BYTES[match.group(2)]
            nano_cpus += cpu
        if memory_bytes > _SCENARIO_MAX_MEMORY_BYTES:
            raise DockerManagerError(
                f"Scenario memory limit total {memory_bytes} bytes exceeds configured budget "
                f"{_SCENARIO_MAX_MEMORY_BYTES} bytes"
            )
        if nano_cpus > _SCENARIO_MAX_NANO_CPUS:
            raise DockerManagerError(
                f"Scenario CPU limit total {nano_cpus} nanocpus exceeds configured budget "
                f"{_SCENARIO_MAX_NANO_CPUS} nanocpus"
            )

    def stop(self, scenario_id: str) -> dict[str, Any]:
        failures = []
        try:
            containers = [container for container in self._scenario_containers(scenario_id)
                          if (getattr(container, "labels", {}) or {}).get("anvil.managed") == "true"
                          and (getattr(container, "labels", {}) or {}).get("anvil.scenario_id") == scenario_id]
        except DockerException as error:
            containers = []
            failures.append(f"container inventory: {_docker_error_message(error)}")
        for container in containers:
            try:
                container.remove(force=True)
            except DockerException as error:
                failures.append(
                    f"container '{getattr(container, 'name', 'unknown')}': {_docker_error_message(error)}"
                )
        try:
            networks = self.client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"}
            )
            networks = [network for network in networks
                        if ((getattr(network, "attrs", {}) or {}).get("Labels") or {}).get("anvil.managed") == "true"
                        and ((getattr(network, "attrs", {}) or {}).get("Labels") or {}).get("anvil.scenario_id") == scenario_id]
        except DockerException as error:
            networks = []
            failures.append(f"network inventory: {_docker_error_message(error)}")
        for network in networks:
            try:
                network.remove()
            except DockerException as error:
                failures.append(
                    f"network '{getattr(network, 'name', 'unknown')}': {_docker_error_message(error)}"
                )
        if failures:
            raise DockerManagerError(
                f"Unable to stop scenario; cleanup incomplete: {'; '.join(failures)}"
            )
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def restart_scenario(
        self, scenario_id: str, runtime_devices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Restart every runtime container in the scenario and wait for recovery.

        Device-backed networks (for example the logical Ethernet switch) have
        no container to restart. Verify all other planned nodes are present
        before touching Docker so a partial inventory is reported as such.
        """
        try:
            containers = self._scenario_containers(scenario_id)
            planned_node_ids = {
                item["node_id"] for item in runtime_devices
                if not (DEVICE_REGISTRY.get(item.get("device_type"))
                        and getattr(DEVICE_REGISTRY[item.get("device_type")], "network_resource", False))
            }
            actual_node_ids = {
                (getattr(container, "labels", {}) or {}).get("anvil.node_id")
                for container in containers
            }
            missing = sorted(planned_node_ids - actual_node_ids)
            if missing:
                raise DockerManagerError(
                    "Scenario restart requires every runtime node; missing: " + ", ".join(missing)
                )
            for container in sorted(
                containers,
                key=lambda item: (getattr(item, "labels", {}) or {}).get("anvil.node_id", ""),
            ):
                container.restart(timeout=5)
                self._wait_until_recovered(container)
        except DockerManagerError:
            raise
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to restart scenario: {_docker_error_message(error)}"
            ) from error
        return self.status(scenario_id)

    def delete_retention_volumes(self, scenario_id: str) -> None:
        """Remove only verified Anvil persistent device data after containers stop."""
        try:
            volumes = self.client.volumes.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
            for volume in volumes:
                volume.reload()
                labels = volume.attrs.get("Labels") or {}
                node_id = labels.get("anvil.node_id", "")
                expected = (_retention_volume_name(scenario_id, node_id)
                            if labels.get("anvil.purpose") == "plc-retention" else
                            _scada_history_volume_name(scenario_id, node_id)
                            if labels.get("anvil.purpose") == "scada-history" else
                            _protocol_audit_volume_name(scenario_id, node_id)
                            if labels.get("anvil.purpose") == "protocol-audit" else None)
                if (labels.get("anvil.managed") != "true" or labels.get("anvil.scenario_id") != scenario_id
                        or expected is None or labels.get("anvil.volume_name") != expected
                        or getattr(volume, "name", None) != expected):
                    continue
                volume.remove(force=False)
        except DockerException as error:
            raise DockerManagerError(f"Unable to remove scenario persistent data: {_docker_error_message(error)}") from error

    def _remove_new_retention_volumes(self, scenario_id: str, previous_names: set[str]) -> list[str]:
        failures = []
        try:
            volumes = self.client.volumes.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        except DockerException as error:
            return [f"retention volume inventory: {_docker_error_message(error)}"]
        for volume in volumes:
            labels = volume.attrs.get("Labels") or {}
            name = getattr(volume, "name", None)
            node_id = labels.get("anvil.node_id", "")
            expected = (_retention_volume_name(scenario_id, node_id)
                        if labels.get("anvil.purpose") == "plc-retention" else
                        _scada_history_volume_name(scenario_id, node_id)
                        if labels.get("anvil.purpose") == "scada-history" else
                        _protocol_audit_volume_name(scenario_id, node_id)
                        if labels.get("anvil.purpose") == "protocol-audit" else None)
            if (name in previous_names or labels.get("anvil.managed") != "true"
                    or labels.get("anvil.scenario_id") != scenario_id or expected is None
                    or labels.get("anvil.volume_name") != expected or name != expected):
                continue
            try:
                volume.remove(force=False)
            except DockerException as error:
                failures.append(f"volume '{name}': {_docker_error_message(error)}")
        return failures

    def pause_scenario(self, scenario_id: str) -> dict[str, Any]:
        """Pause every running container, rolling back if any pause fails."""
        changed: list[Any] = []
        try:
            containers = self._scenario_containers(scenario_id)
            if not containers:
                raise DockerManagerError("Scenario is not deployed")
            for container in containers:
                container.reload()
                if container.status != "running":
                    raise DockerManagerError("Pause requires every scenario node to be running")
                container.pause()
                changed.append(container)
        except DockerManagerError:
            for container in reversed(changed):
                try:
                    container.unpause()
                except DockerException:
                    pass
            raise
        except DockerException as error:
            for container in reversed(changed):
                try:
                    container.unpause()
                except DockerException:
                    pass
            raise DockerManagerError(f"Unable to pause scenario: {_docker_error_message(error)}") from error
        return self.status(scenario_id)

    def resume_scenario(self, scenario_id: str) -> dict[str, Any]:
        """Resume every paused container, rolling back if any resume fails."""
        changed: list[Any] = []
        try:
            containers = self._scenario_containers(scenario_id)
            if not containers:
                raise DockerManagerError("Scenario is not deployed")
            for container in containers:
                container.reload()
                if container.status != "paused":
                    raise DockerManagerError("Resume requires every scenario node to be paused")
                container.unpause()
                changed.append(container)
            for container in changed:
                self._wait_until_recovered(container)
        except DockerManagerError:
            for container in reversed(changed):
                try:
                    container.pause()
                except DockerException:
                    pass
            raise
        except DockerException as error:
            for container in reversed(changed):
                try:
                    container.pause()
                except DockerException:
                    pass
            raise DockerManagerError(f"Unable to resume scenario: {_docker_error_message(error)}") from error
        return self.status(scenario_id)

    def node_lifecycle(self, scenario_id: str, node_id: str, action: str) -> dict[str, Any]:
        """Apply a lifecycle action only to a container owned by this scenario/node."""
        if action not in {"start", "stop", "restart", "pause", "resume"}:
            raise DockerManagerError("Unsupported node lifecycle action")
        try:
            matches = [
                container for container in self._scenario_containers(scenario_id)
                if container.labels.get("anvil.node_id") == node_id
            ]
            if len(matches) != 1:
                raise DockerManagerError(f"Expected one managed container for node '{node_id}'")
            container = matches[0]
            if action == "start":
                container.start()
            elif action == "stop":
                container.stop(timeout=5)
            elif action == "pause":
                container.pause()
            elif action == "resume":
                container.unpause()
                try:
                    self._wait_until_recovered(container)
                except DockerManagerError:
                    try:
                        container.pause()
                    except DockerException:
                        pass
                    raise
            else:
                container.restart(timeout=5)
                self._wait_until_recovered(container)
        except DockerManagerError:
            raise
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to {action} node '{node_id}': {_docker_error_message(error)}"
            ) from error
        return self.status(scenario_id)

    def recreate_node(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        """Recreate one missing scenario container from its persisted runtime plan."""
        node_id = runtime_device.get("node_id")
        if not node_id or runtime_device.get("network_mode") == "none":
            # Network-isolated auxiliary nodes need no Docker network; they may
            # still be safely recreated from their stored plan.
            if not node_id:
                raise DockerManagerError("Stored runtime device has no node_id")
        try:
            existing = [item for item in self._scenario_containers(scenario_id)
                        if item.labels.get("anvil.node_id") == node_id]
            if existing:
                raise DockerManagerError(f"Node '{node_id}' already has a managed container")
            networks: dict[str, Any] = {}
            if runtime_device.get("network_mode") != "none":
                for network in self.client.networks.list(
                    filters={"label": f"anvil.scenario_id={scenario_id}"}
                ):
                    labels = network.attrs.get("Labels") or {}
                    if labels.get("anvil.managed") == "true" and labels.get("anvil.scenario_id") == scenario_id:
                        if labels.get("anvil.purpose") == "protocol-ingress":
                            continue
                        networks[labels.get("anvil.zone_id", "default")] = network
                if any(item.get("device_type") == "protocol-ingress" for item in [runtime_device]):
                    networks["__ingress__"] = self.client.networks.get("bridge")
                required = {runtime_device.get("zone_id", "default")}
                if runtime_device.get("device_type") == "tcp-gateway":
                    required.add(runtime_device["config"]["target_zone_id"])
                missing = required - networks.keys()
                if missing:
                    raise DockerManagerError(
                        "Cannot recreate node because its managed scenario network is missing: "
                        + ", ".join(sorted(missing))
                    )
            container = self._create_runtime_container(scenario_id, runtime_device, networks)
            try:
                if runtime_device.get("device_type") == "egress-gateway":
                    self.client.networks.get("bridge").connect(container)
                container.start()
                self._wait_until_healthy(container)
            except Exception:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
                raise
        except DockerManagerError:
            raise
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to recreate node '{node_id}': {_docker_error_message(error)}"
            ) from error
        return self.status(scenario_id)

    def node_operation_checkpoint(self, scenario_id: str, node_id: str) -> dict[str, Any]:
        """Capture Docker identity/state needed to distinguish an unstarted restart intent."""
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if container.labels.get("anvil.node_id") == node_id]
            if len(matches) != 1:
                return {}
            container = matches[0]
            container.reload()
            attrs = container.attrs if isinstance(container.attrs, dict) else {}
            state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
            return {
                "container_id": getattr(container, "id", None),
                "restart_count": attrs.get("RestartCount"),
                "started_at": state.get("StartedAt"),
                "status": self._container_status(container),
            }
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to checkpoint node '{node_id}' before lifecycle action: {_docker_error_message(error)}"
            ) from error

    def recover_node_operation(self, scenario_id: str, runtime_device: dict[str, Any], action: str,
                               precondition: dict[str, Any] | None = None) -> dict[str, Any]:
        """Converge a journaled node operation after an interrupted request."""
        node_id = runtime_device.get("node_id")
        if not node_id or action not in {"start", "stop", "restart", "pause", "resume", "recreate"}:
            raise DockerManagerError("Stored node operation is invalid")
        observed = self.status(scenario_id)
        state = next((item for item in observed.get("containers", []) if item.get("node_id") == node_id), None)

        if action == "recreate":
            matches = [container for container in self._scenario_containers(scenario_id)
                       if (getattr(container, "labels", {}) or {}).get("anvil.managed") == "true"
                       and (getattr(container, "labels", {}) or {}).get("anvil.scenario_id") == scenario_id
                       and (getattr(container, "labels", {}) or {}).get("anvil.node_id") == node_id]
            if len(matches) > 1:
                raise DockerManagerError(f"Found multiple managed containers for node '{node_id}'; preserving them for inspection")
            if matches and state and state.get("status") in {"running", "paused"} \
                    and state.get("health_status") not in {"starting", "unhealthy"} \
                    and not state.get("runtime_error"):
                return observed
            if matches:
                try:
                    matches[0].remove(force=True)
                except DockerException as error:
                    raise DockerManagerError(
                        f"Unable to remove incomplete recreated node '{node_id}': {_docker_error_message(error)}"
                    ) from error
            return self.recreate_node(scenario_id, runtime_device)

        if action == "restart" and state is not None:
            checkpoint = precondition or {}
            container_id = None
            restart_count = None
            started_at = None
            try:
                matches = [container for container in self._scenario_containers(scenario_id)
                           if container.labels.get("anvil.node_id") == node_id]
                if len(matches) == 1:
                    container = matches[0]
                    container.reload()
                    attrs = container.attrs if isinstance(container.attrs, dict) else {}
                    docker_state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
                    container_id = getattr(container, "id", None)
                    restart_count = attrs.get("RestartCount")
                    started_at = docker_state.get("StartedAt")
            except DockerException as error:
                raise DockerManagerError(
                    f"Unable to inspect node '{node_id}' during restart recovery: {_docker_error_message(error)}"
                ) from error
            same_container = checkpoint.get("container_id") and container_id == checkpoint.get("container_id")
            comparisons = []
            if checkpoint.get("restart_count") is not None:
                comparisons.append(restart_count == checkpoint.get("restart_count"))
            if checkpoint.get("started_at") is not None:
                comparisons.append(started_at == checkpoint.get("started_at"))
            unchanged = bool(same_container and comparisons and all(comparisons))
            if unchanged and state.get("status") in {"running", "paused"}:
                return self.node_lifecycle(scenario_id, node_id, "restart")
            if same_container and comparisons and not unchanged:
                if state.get("status") == "running" and state.get("health_status") in {"starting", "unhealthy"}:
                    try:
                        self._wait_until_healthy(matches[0])
                    except DockerManagerError:
                        raise
                    return self.status(scenario_id)
                if state.get("status") == "running":
                    return observed

        desired = {
            "start": "running", "stop": "stopped", "pause": "paused", "resume": "running",
        }
        if action in desired and state is not None and state.get("status") == desired[action]:
            if action not in {"start", "resume"} or state.get("health_status") not in {"starting", "unhealthy"}:
                return observed
        if state is None and action == "stop":
            # A missing container is already in the desired stopped state; the
            # scenario-level status still reports the missing planned node.
            return observed
        if state is None and action in {"start", "restart"}:
            return self.recreate_node(scenario_id, runtime_device)
        return self.node_lifecycle(scenario_id, node_id, action)

    def _create_runtime_container(
        self, scenario_id: str, runtime_device: dict[str, Any], networks: dict[str, Any]
    ) -> Any:
        device_type = runtime_device.get("device_type")
        device_class = DEVICE_REGISTRY.get(device_type)
        if device_class is None:
            raise DockerManagerError(f"Unsupported device type '{device_type}'")
        config = device_class().build_container_config(scenario_id, runtime_device)
        image_id = runtime_device.get("image_id")
        image_pin = runtime_device.get("image_pin")
        if image_id:
            config["image"] = image_id
        self._apply_container_security(config, runtime_device)
        config.setdefault("labels", {})["anvil.zone_id"] = runtime_device.get("zone_id", "default")
        config["labels"]["anvil.device_type"] = device_type
        if image_id:
            config["labels"]["anvil.image_id"] = image_id
            config["labels"]["anvil.image_reference"] = runtime_device["image_ref"]
            config["labels"]["anvil.image_pin"] = image_pin
        hostname = self._hostname(runtime_device)
        config["hostname"] = hostname
        config["labels"]["anvil.hostname"] = hostname
        data_mounts = []
        if device_type == "plc-s7" and runtime_device.get("config", {}).get("retain_logic_state") is True:
            node_id = str(runtime_device.get("node_id", ""))
            if not node_id:
                raise DockerManagerError("Retentive PLC requires a stable node_id")
            volume_name = _retention_volume_name(scenario_id, node_id)
            labels = _scenario_data_volume_labels(scenario_id, node_id, "plc-retention", volume_name)
            self._ensure_scenario_data_volume(volume_name, labels, "PLC retention")
            config["labels"]["anvil.retain_logic_state"] = "true"
            data_mounts.append(Mount(target="/var/lib/anvil/retention", source=volume_name,
                                     type="volume", read_only=False))
        elif device_type == "scada":
            node_id = str(runtime_device.get("node_id", ""))
            if not node_id:
                raise DockerManagerError("SCADA history requires a stable node_id")
            volume_name = _scada_history_volume_name(scenario_id, node_id)
            labels = _scenario_data_volume_labels(scenario_id, node_id, "scada-history", volume_name)
            self._ensure_scenario_data_volume(volume_name, labels, "SCADA history")
            scada_config = json.loads(config["environment"]["SCADA_CONFIG"])
            scada_config["durable_history_path"] = "/var/lib/anvil/scada-history/history.sqlite3"
            config["environment"]["SCADA_CONFIG"] = json.dumps(scada_config)
            config["environment"]["SCADA_HISTORY_ACK_TOKEN"] = secrets.token_urlsafe(32)
            config["labels"]["anvil.scada_history_buffer"] = "true"
            data_mounts.append(Mount(target="/var/lib/anvil/scada-history", source=volume_name,
                                     type="volume", read_only=False))
        if (device_type in {"plc-s7", "modbus-tcp"}
                or (device_type == "tcp-gateway"
                    and runtime_device.get("config", {}).get("inspect_protocol") == "modbus-tcp")):
            node_id = str(runtime_device.get("node_id", ""))
            if not node_id:
                raise DockerManagerError("Protocol audit queue requires a stable node_id")
            volume_name = _protocol_audit_volume_name(scenario_id, node_id)
            labels = _scenario_data_volume_labels(scenario_id, node_id, "protocol-audit", volume_name)
            self._ensure_scenario_data_volume(volume_name, labels, "Protocol audit")
            config["environment"]["ANVIL_PROTOCOL_AUDIT_ACK_TOKEN"] = secrets.token_urlsafe(32)
            config["labels"]["anvil.protocol_audit_queue"] = "true"
            data_mounts.append(Mount(target="/var/lib/anvil/protocol-audit", source=volume_name,
                                     type="volume", read_only=False))
        if data_mounts:
            config["mounts"] = data_mounts
        network_mode = runtime_device.get("network_mode")
        if network_mode:
            config["network_mode"] = network_mode
        elif device_type == "protocol-ingress":
            config["network_mode"] = "bridge"
        else:
            zone_id = runtime_device.get("zone_id", "default")
            network = networks.get(zone_id)
            if network is None:
                raise DockerManagerError(f"Managed scenario network for zone '{zone_id}' is missing")
            config["network"] = network.name
            endpoint = self.client.api.create_endpoint_config(
                ipv4_address=runtime_device["ip"], aliases=[hostname]
            )
            config["networking_config"] = {network.name: endpoint}
            if device_type == "tcp-gateway":
                target_zone_id = runtime_device["config"]["target_zone_id"]
                target_network = networks.get(target_zone_id)
                if target_network is None:
                    raise DockerManagerError(f"Managed scenario network for zone '{target_zone_id}' is missing")
                target_endpoint = self.client.api.create_endpoint_config(
                    ipv4_address=runtime_device["config"]["target_interface_ip"]
                )
                config["networking_config"][target_network.name] = target_endpoint
        if device_type == "protocol-ingress":
            ingress_network = networks.get("__ingress__")
            if ingress_network is None:
                raise DockerManagerError("Managed protocol ingress network is missing")
            bridge_attrs = ingress_network.attrs if isinstance(ingress_network.attrs, dict) else {}
            gateways = (bridge_attrs.get("IPAM") or {}).get("Config") or []
            host_gateway_ip = next((item.get("Gateway") for item in gateways if item.get("Gateway")), None)
            if not host_gateway_ip:
                raise DockerManagerError("Docker default bridge has no IPv4 gateway for protocol ingress source validation")
            environment = config.get("environment") or {}
            ingress_config = json.loads(environment.get("ANVIL_INGRESS_CONFIG", "{}"))
            ingress_config["allowed_peer_ip"] = host_gateway_ip
            environment["ANVIL_INGRESS_CONFIG"] = json.dumps(ingress_config, separators=(",", ":"))
            config["environment"] = environment
            config["labels"]["anvil.ingress_allowed_peer_ip"] = host_gateway_ip
            private_network = networks.get(runtime_device.get("zone_id", "default"))
            if private_network is None:
                raise DockerManagerError("Managed protocol ingress zone network is missing")
            config["labels"]["anvil.ingress_zone_network"] = private_network.name
        container = self.client.containers.create(**config)
        if device_type == "protocol-ingress":
            network = networks.get(runtime_device.get("zone_id", "default"))
            network.connect(container, ipv4_address=runtime_device["ip"])
        return container

    def _ensure_scenario_data_volume(self, name: str, labels: dict[str, str], purpose: str) -> None:
        try:
            volume = self.client.volumes.get(name)
            volume.reload()
            if volume.attrs.get("Labels") != labels:
                raise DockerManagerError(f"{purpose} volume exists with mismatched ownership labels; it has been preserved")
        except docker.errors.NotFound:
            self.client.volumes.create(name=name, driver="local", labels=labels)

    def status(self, scenario_id: str) -> dict[str, Any]:
        try:
            containers = []
            for container in self._scenario_containers(scenario_id):
                container.reload()
                attrs = container.attrs if isinstance(container.attrs, dict) else {}
                state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
                health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
                health_status = health.get("Status")
                health_log = health.get("Log") if isinstance(health.get("Log"), list) else []
                health_error = None
                last_health_log = health_log[-1] if health_log and isinstance(health_log[-1], dict) else {}
                if health_status == "unhealthy":
                    health_error = str(last_health_log.get("Output") or "").strip()[:500] or None
                runtime_error = str(state.get("Error") or "").strip()[:500] or None
                containers.append(
                    {
                        "node_id": container.labels.get("anvil.node_id", "unknown"),
                        "name": container.name,
                        "status": self._container_status(container),
                        "health_status": health_status,
                        "health_error": health_error,
                        "runtime_error": runtime_error,
                        **(self._cached_resource_usage(container) or {}),
                    }
                )
            scenario_networks = self.client.networks.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
            for network in scenario_networks if isinstance(scenario_networks, list) else []:
                attrs = network.attrs if isinstance(network.attrs, dict) else {}
                labels = attrs.get("Labels") if isinstance(attrs.get("Labels"), dict) else {}
                switch_node_id = labels.get("anvil.switch_node_id")
                if switch_node_id:
                    containers.append({"node_id": switch_node_id, "name": network.name,
                                       "status": "running", "runtime_kind": "network",
                                       "health_status": None, "health_error": None, "runtime_error": None,
                                       "resource_metrics_status": "not_applicable",
                                       "resource_metrics_sampled_at": None})
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to inspect scenario: {_docker_error_message(error)}"
            ) from error
        container_statuses = {item["status"] for item in containers if item.get("runtime_kind") != "network"}
        resource_statuses = {item["status"] for item in containers if item.get("runtime_kind") == "network"}
        statuses = container_statuses or resource_statuses
        if not statuses:
            overall_status = "stopped"
        elif statuses == {"running"}:
            overall_status = "running"
        elif statuses == {"paused"}:
            overall_status = "paused"
        elif statuses & {"dead", "exited", "unhealthy"}:
            overall_status = "error"
        elif statuses <= {"created", "restarting", "starting"}:
            overall_status = "deploying"
        else:
            overall_status = "partial"
        return {
            "scenario_id": scenario_id,
            "status": overall_status,
            "containers": containers,
        }

    def has_managed_scenario_network(self, scenario_id: str) -> bool:
        """Return whether a scenario's isolated network survives container loss."""
        try:
            networks = self.client.networks.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
            for network in networks:
                attrs = getattr(network, "attrs", {}) or {}
                labels = attrs.get("Labels") if isinstance(attrs.get("Labels"), dict) else {}
                if labels.get("anvil.managed") == "true" and labels.get("anvil.scenario_id") == scenario_id:
                    return True
            return False
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to inspect managed scenario networks: {_docker_error_message(error)}"
            ) from error

    def probe_network_route(
        self,
        scenario_id: str,
        *,
        source_node_id: str,
        host: str,
        port: int,
        protocol: str,
        target_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify a declared route from its real source container without writing process data."""
        if protocol not in {"s7", "modbus-tcp", "tcp", "field-http"} or not 1 <= int(port) <= 65535:
            raise DockerManagerError("Invalid protocol route probe parameters")
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if container.labels.get("anvil.node_id") == source_node_id
                       and container.labels.get("anvil.role") != "tcp-gateway"]
            if len(matches) != 1:
                return {"status": "unreachable", "detail": "Source container is missing or ambiguous"}
            container = matches[0]
            container.reload()
            if container.status != "running":
                return {"status": "unreachable", "detail": f"Source container is {container.status}"}

            import ipaddress
            address = str(ipaddress.ip_address(host))
            if protocol == "s7":
                rack = int(target_config.get("rack", 0))
                slot = int(target_config.get("slot", 1))
                script = (
                    "import socket,struct\n"
                    f"h={address!r};p={int(port)};rack={rack};slot={slot}\n"
                    "def exact(s,n):\n"
                    " b=b''\n"
                    " while len(b)<n:\n"
                    "  x=s.recv(n-len(b))\n"
                    "  if not x: raise RuntimeError('peer closed during S7 handshake')\n"
                    "  b+=x\n"
                    " return b\n"
                    "def tpkt(s):\n"
                    " h=exact(s,4);n=struct.unpack('>H',h[2:4])[0]\n"
                    " if n<7 or n>4096: raise RuntimeError('invalid TPKT length')\n"
                    " return h+exact(s,n-4)\n"
                    "s=socket.create_connection((h,p),timeout=1.5);s.settimeout(1.5)\n"
                    "dst=(0x100|(rack<<5)|slot)&0xffff\n"
                    "s.sendall(b'\\x03\\x00\\x00\\x16\\x11\\xe0\\x00\\x00\\x00\\x01\\x00\\xc1\\x02'+struct.pack('>H',0x100)+b'\\xc2\\x02'+struct.pack('>H',dst)+b'\\xc0\\x01\\x0a')\n"
                    "cc=tpkt(s)\n"
                    "if len(cc)<7 or cc[5]!=0xd0: raise RuntimeError('S7 COTP connection confirm was not received')\n"
                    "s.sendall(bytes.fromhex('0300001902f08032010000000100080000f0000001000101e0'))\n"
                    "ack=tpkt(s)\n"
                    "if len(ack)<9 or ack[7]!=0x32 or ack[8]!=0x03 or b'\\xf0' not in ack: raise RuntimeError('S7 setup communication was not acknowledged')\n"
                    "s.close();print('S7 setup acknowledged')\n"
                )
            elif protocol == "modbus-tcp":
                unit_id = int(target_config.get("unit_id", 1))
                if not 0 <= unit_id <= 255:
                    raise DockerManagerError("Invalid Modbus unit ID in route target")
                script = (
                    "import socket,struct\n"
                    f"h={address!r};p={int(port)};unit={unit_id}\n"
                    "def exact(s,n):\n"
                    " b=b''\n"
                    " while len(b)<n:\n"
                    "  x=s.recv(n-len(b))\n"
                    "  if not x: raise RuntimeError('peer closed during Modbus response')\n"
                    "  b+=x\n"
                    " return b\n"
                    "s=socket.create_connection((h,p),timeout=1.5);s.settimeout(1.5)\n"
                    "tid=0xa71;request=struct.pack('>HHHBBHH',tid,0,6,unit,3,0,1);s.sendall(request)\n"
                    "head=exact(s,7);length=struct.unpack('>H',head[4:6])[0];tail=exact(s,length-1)\n"
                    "if struct.unpack('>H',head[:2])[0]!=tid or head[2:4]!=b'\\x00\\x00' or head[6]!=unit: raise RuntimeError('invalid Modbus TCP response header')\n"
                    "if not tail or tail[0] not in (3,0x83): raise RuntimeError('Modbus holding-register request was not acknowledged')\n"
                    "s.close();print('Modbus TCP request acknowledged')\n"
                )
            elif protocol == "field-http":
                script = (
                    "import urllib.error,urllib.request\n"
                    f"url='http://{address}:{int(port)}/__anvil_diagnostic_probe__'\n"
                    "try:\n"
                    " r=urllib.request.urlopen(url,timeout=1.5);code=r.status;r.close()\n"
                    "except urllib.error.HTTPError as e: code=e.code\n"
                    "if code!=404: raise RuntimeError('field HTTP probe expected 404, received %s'%code)\n"
                    "print('field HTTP listener acknowledged read-only probe')\n"
                )
            else:
                script = (
                    "import socket\n"
                    f"s=socket.create_connection(({address!r},{int(port)}),timeout=1.5);s.close();print('TCP connection established')\n"
                )

            created = self.client.api.exec_create(
                container.id,
                cmd=["python", "-c", script],
                stdout=True,
                stderr=True,
                stdin=False,
                tty=False,
                privileged=False,
                user="65534:65534",
            )
            exec_id = created["Id"]
            try:
                output = self.client.api.exec_start(exec_id, demux=True)
                state = self.client.api.exec_inspect(exec_id)
            except DockerException:
                try:
                    self.client.api.exec_inspect(exec_id)
                except DockerException:
                    pass
                raise
            stdout, stderr = output if isinstance(output, tuple) else (output, b"")
            detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()[:300]
            if state.get("ExitCode") != 0:
                return {"status": "unreachable", "detail": detail or "Protocol probe failed"}
            return {"status": "reachable", "detail": detail or "Probe succeeded"}
        except DockerManagerError:
            raise
        except (DockerException, OSError, ValueError, KeyError, TypeError) as error:
            raise DockerManagerError(f"Unable to probe route from '{source_node_id}': {_docker_error_message(error)}") from error

    def set_modbus_route_interception(
        self, scenario_id: str, route_node_id: str, rules: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Set/clear exact Modbus response rewrites through the gateway's local socket."""
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if container.labels.get("anvil.node_id") == route_node_id
                       and container.labels.get("anvil.role") == "tcp-gateway"]
            if len(matches) != 1:
                raise DockerManagerError("The selected Modbus route gateway is missing or ambiguous")
            container = matches[0]
            container.reload()
            labels = container.labels or {}
            if (container.status != "running" or labels.get("anvil.route_inspect_protocol") != "modbus-tcp"):
                raise DockerManagerError("The selected route is not an active Modbus TCP inspection gateway")
            config = container.attrs.get("Config") if isinstance(container.attrs, dict) else {}
            env_values = dict(item.split("=", 1) for item in (config.get("Env") or [])
                              if isinstance(item, str) and "=" in item)
            token = env_values.get("ANVIL_TCP_GATEWAY_CONTROL_TOKEN", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                raise DockerManagerError("The route gateway has no valid local control credential")
            if rules is None:
                command = ["python", "tcp_route_control.py", "clear"]
            else:
                command = ["python", "tcp_route_control.py", "set",
                           json.dumps(rules, separators=(",", ":"))]
            created = self.client.api.exec_create(
                container.id, cmd=command, stdout=True, stderr=True, stdin=False, tty=False,
                privileged=False, user="65534:65534",
                environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"], workdir="/app",
            )
            exec_id = created["Id"]
            output = self.client.api.exec_start(exec_id, demux=True)
            state = self.client.api.exec_inspect(exec_id)
            stdout, stderr = output if isinstance(output, tuple) else (output, b"")
            raw = (stdout or stderr or b"").decode("utf-8", errors="replace").strip()
            if state.get("ExitCode") != 0:
                raise DockerManagerError(raw[:300] or "Route interception control command failed")
            try:
                result = json.loads(raw)
            except ValueError as error:
                raise DockerManagerError("Route interception gateway returned an invalid control response") from error
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise DockerManagerError("Route interception gateway rejected the control request")
            return result
        except DockerManagerError:
            raise
        except (DockerException, OSError, KeyError, TypeError, ValueError) as error:
            raise DockerManagerError(
                f"Unable to update Modbus route interception: {_docker_error_message(error)}"
            ) from error

    def probe_field_bindings(
        self,
        scenario_id: str,
        *,
        source_node_id: str,
        bindings: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Read the source bridge's per-address mapping and freshness without issuing I/O writes."""
        if not bindings:
            return {"status": "reachable", "detail": "No field signal bindings are configured", "bindings": []}
        expected: list[dict[str, str]] = []
        for binding in bindings:
            if not all(isinstance(binding.get(key), str) and binding[key] for key in ("address", "field_id", "field_port")):
                raise DockerManagerError("Invalid field signal binding in runtime configuration")
            expected.append({key: binding[key] for key in ("address", "field_id", "field_port")})
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if container.labels.get("anvil.node_id") == source_node_id
                       and container.labels.get("anvil.role") != "tcp-gateway"]
            if len(matches) != 1:
                return {"status": "unreachable", "detail": "Source container is missing or ambiguous", "bindings": []}
            container = matches[0]
            container.reload()
            if container.status != "running":
                return {"status": "unreachable", "detail": f"Source container is {container.status}", "bindings": []}
            script = (
                "import json,sys,time\n"
                "expected=json.loads(sys.argv[1]);deadline=time.monotonic()+3.0;results=[]\n"
                "while True:\n"
                " try:\n"
                "  with open('/tmp/anvil-field-links.json') as f:data=json.load(f)\n"
                " except (OSError,ValueError):data={}\n"
                " now=time.time();results=[]\n"
                " for binding in expected:\n"
                "  item=data.get(binding['address'])\n"
                "  if item is None or item.get('source_node_id')!=binding['field_id'] or item.get('source_address')!=binding['field_port']:\n"
                "   results.append({'address':binding['address'],'field_id':binding['field_id'],'field_port':binding['field_port'],'status':'missing'})\n"
                "   continue\n"
                "  last=item.get('last_success');age=round((now-last)*1000) if isinstance(last,(int,float)) else None\n"
                "  results.append({'address':binding['address'],'field_id':binding['field_id'],'field_port':binding['field_port'],'status':'good' if age is not None and age<=2000 else 'stale','age_ms':age})\n"
                " if all(item['status']=='good' for item in results) or time.monotonic()>=deadline:break\n"
                " time.sleep(.1)\n"
                "print(json.dumps({'bindings':results},separators=(',',':')))\n"
            )
            created = self.client.api.exec_create(
                container.id,
                cmd=["python", "-c", script, json.dumps(expected, separators=(",", ":"))],
                stdout=True,
                stderr=True,
                stdin=False,
                tty=False,
                privileged=False,
                user="65534:65534",
            )
            exec_id = created["Id"]
            try:
                output = self.client.api.exec_start(exec_id, demux=True)
                state = self.client.api.exec_inspect(exec_id)
            except DockerException:
                try:
                    self.client.api.exec_inspect(exec_id)
                except DockerException:
                    pass
                raise
            stdout, stderr = output if isinstance(output, tuple) else (output, b"")
            raw = (stdout or stderr or b"").decode("utf-8", errors="replace").strip()
            if state.get("ExitCode") != 0:
                return {"status": "unreachable", "detail": raw[:300] or "Unable to read field binding state", "bindings": []}
            report = json.loads(raw)
            results = report.get("bindings", [])
            if len(results) != len(expected):
                return {"status": "unreachable", "detail": "Source bridge returned an incomplete binding report", "bindings": results}
            failed = [item for item in results if item.get("status") != "good"]
            detail = f"{len(expected) - len(failed)}/{len(expected)} field bindings fresh"
            if failed:
                detail += "; not ready: " + ", ".join(item.get("address", "?") for item in failed[:8])
            return {"status": "unreachable" if failed else "reachable", "detail": detail, "bindings": results}
        except DockerManagerError:
            raise
        except (DockerException, OSError, ValueError, KeyError, TypeError) as error:
            raise DockerManagerError(f"Unable to inspect field bindings from '{source_node_id}': {_docker_error_message(error)}") from error

    def start_packet_capture(self, scenario_id: str, node_id: str | None, capture_id: str,
                             *, zone_id: str | None = None,
                             peer_node_id: str | None = None) -> dict[str, str]:
        """Start a bounded node, pair-link, or Docker bridge-segment capture."""
        try:
            if peer_node_id is not None:
                if node_id is None or zone_id is not None or peer_node_id == node_id:
                    raise DockerManagerError("Link capture requires two different device endpoints")
            elif (node_id is None) == (zone_id is None):
                raise DockerManagerError("Choose exactly one device, device pair, or zone for packet capture")
            containers = self._scenario_containers(scenario_id)
            target_matches = [container for container in containers
                              if container.labels.get("anvil.role") not in {"packet-capture", "tcp-gateway"}
                              and (container.labels.get("anvil.node_id") == node_id if node_id else
                                   container.labels.get("anvil.zone_id", "default") == zone_id)]
            if node_id and len(target_matches) != 1:
                raise DockerManagerError(f"Expected one managed network device for node '{node_id}'")
            if zone_id and not target_matches:
                raise DockerManagerError(f"Zone '{zone_id}' has no active managed devices to capture")
            targets = []
            for target in target_matches:
                target.reload()
                if target.status != "running":
                    continue
                if target.attrs.get("NetworkSettings", {}).get("Networks"):
                    targets.append(target)
            if not targets:
                raise DockerManagerError("Packet capture requires running devices connected to a scenario network")
            peer = None
            peer_ip = None
            if peer_node_id is not None:
                peers = [container for container in containers
                         if container.labels.get("anvil.node_id") == peer_node_id
                         and container.labels.get("anvil.role") not in {"packet-capture", "tcp-gateway"}]
                if len(peers) != 1:
                    raise DockerManagerError(f"Expected one managed peer device '{peer_node_id}'")
                peer = peers[0]
                peer.reload()
                if peer.status != "running":
                    raise DockerManagerError("Link capture requires both endpoint devices to be running")
                source = targets[0]
                if source.labels.get("anvil.zone_id", "default") != peer.labels.get("anvil.zone_id", "default"):
                    raise DockerManagerError("Link capture endpoints must belong to the same network zone")
                source_networks = source.attrs.get("NetworkSettings", {}).get("Networks", {})
                peer_networks = peer.attrs.get("NetworkSettings", {}).get("Networks", {})
                shared_networks = source_networks.keys() & peer_networks.keys()
                if not shared_networks:
                    raise DockerManagerError("Link capture endpoints must share a Docker network")
                peer_ip = next((peer_networks[name].get("IPAddress") for name in sorted(shared_networks)
                                if peer_networks[name].get("IPAddress")), None)
                if not peer_ip:
                    raise DockerManagerError("Link capture peer has no address on the shared Docker network")
            segment_network = None
            segment_interface = None
            if zone_id:
                try:
                    network_matches = self.client.networks.list(filters={"label": [
                        "anvil.managed=true", f"anvil.scenario_id={scenario_id}", f"anvil.zone_id={zone_id}",
                    ]})
                except DockerException as error:
                    raise DockerManagerError(f"Unable to inspect the Docker bridge for zone '{zone_id}': {_docker_error_message(error)}") from error
                network_matches = [network for network in network_matches
                                   if (network.attrs.get("Labels") or {}).get("anvil.managed") == "true"
                                   and (network.attrs.get("Labels") or {}).get("anvil.scenario_id") == scenario_id
                                   and (network.attrs.get("Labels") or {}).get("anvil.zone_id") == zone_id]
                if len(network_matches) != 1:
                    raise DockerManagerError(f"Zone '{zone_id}' must have exactly one managed Docker bridge")
                segment_network = network_matches[0]
                segment_network.reload()
                attrs = segment_network.attrs
                if attrs.get("Driver") != "bridge" or attrs.get("Internal") is not True:
                    raise DockerManagerError("Segment capture requires an internal Anvil Docker bridge")
                options = attrs.get("Options") or {}
                segment_interface = options.get("com.docker.network.bridge.name") or f"br-{segment_network.id[:12]}"
                if not re.fullmatch(r"br-[a-f0-9]{12}", segment_interface):
                    raise DockerManagerError("Segment capture requires Docker's managed bridge interface")
            active = [container for container in containers
                      if container.labels.get("anvil.role") == "packet-capture"]
            if zone_id and any(c.labels.get("anvil.zone_id") == zone_id for c in active):
                raise DockerManagerError(f"A packet capture is already active for zone '{zone_id}'")
            if node_id and any(c.labels.get("anvil.capture_target_node_id") == node_id
                               or c.labels.get("anvil.zone_id") == next(
                                   (t.labels.get("anvil.zone_id", "default") for t in targets), "__none__")
                               for c in active):
                raise DockerManagerError(f"A packet capture is already active for node '{node_id}' or its zone")
            if peer_node_id and any(
                c.labels.get("anvil.capture_target_node_id") in {node_id, peer_node_id}
                or c.labels.get("anvil.capture_peer_node_id") in {node_id, peer_node_id}
                or c.labels.get("anvil.zone_id") == targets[0].labels.get("anvil.zone_id", "default")
                for c in active
            ):
                raise DockerManagerError("A packet capture is already active for one of the link endpoints or its zone")
            created = []
            try:
                for index, target in enumerate(targets if not zone_id else [None]):
                    target_node_id = target.labels.get("anvil.node_id", "") if target else ""
                    target_zone = zone_id or target.labels.get("anvil.zone_id", "default")
                    segment_scope = zone_id is not None
                    labels = {
                        "anvil.managed": "true", "anvil.scenario_id": scenario_id,
                        "anvil.node_id": f"capture-{capture_id}", "anvil.role": "packet-capture",
                        "anvil.capture_id": capture_id, "anvil.capture_target_node_id": target_node_id,
                        "anvil.capture_scope": "segment" if segment_scope else "link" if peer else "device",
                        "anvil.capture_zone_id": zone_id or "", "anvil.zone_id": target_zone,
                        "anvil.capture_network_id": segment_network.id if segment_network else "",
                        "anvil.capture_interface": segment_interface or "",
                        "anvil.capture_peer_node_id": peer_node_id or "",
                        "anvil.capture_peer_container_id": peer.id if peer else "",
                        "anvil.capture_peer_ip": peer_ip or "",
                    }
                    capture_command = (_segment_capture_command(segment_interface) if segment_interface else
                                       _link_capture_command(peer_ip) if peer_ip else _CAPTURE_COMMAND)
                    network_mode = "host" if segment_scope else f"container:{target.id}"
                    cap_add = ["NET_RAW", "SETGID", "SETUID"]
                    capture = self.client.containers.create(
                        image=_CAPTURE_IMAGE, entrypoint=["/bin/sh", "-c"], command=[capture_command],
                        name=f"anvil-{scenario_id[:8]}-capture-{capture_id[:8]}-{index}", detach=True,
                        user="0:0", read_only=True, cap_drop=["ALL"], cap_add=cap_add,
                        security_opt=["no-new-privileges:true"], pids_limit=8, mem_limit="64m",
                        nano_cpus=250_000_000, stop_signal="SIGINT",
                        tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"},
                        network_mode=network_mode, labels=labels,
                    )
                    created.append(capture)
                    capture.start()
                    capture_output = b""
                    for _attempt in range(30):
                        capture.reload()
                        if capture.status != "running":
                            break
                        capture_output = capture.logs(stdout=True, stderr=True, tail=10)
                        if b"listening on" in capture_output.lower():
                            break
                        time.sleep(0.1)
                    if capture.status != "running" or b"listening on" not in capture_output.lower():
                        logs = capture.logs(stdout=True, stderr=True, tail=20)
                        detail = logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
                        raise DockerManagerError(f"Packet capture process did not start: {detail[-1000:]}")
            except Exception:
                for capture in created:
                    try:
                        capture.remove(force=True)
                    except DockerException:
                        pass
                raise
            return {"capture_id": capture_id, "node_id": node_id, "zone_id": zone_id,
                    "scope": "segment" if zone_id else "link" if peer_node_id else "device",
                    "source_node_id": node_id if peer_node_id else None,
                    "target_node_id": peer_node_id,
                    "status": "capturing"}
        except DockerManagerError:
            raise
        except DockerException as error:
            raise DockerManagerError(f"Unable to start packet capture: {_docker_error_message(error)}") from error

    def export_packet_capture(self, scenario_id: str, capture_id: str) -> bytes:
        """Stop all capture sidecars, merge their bounded PCAPs and remove them."""
        matches = [container for container in self._scenario_containers(scenario_id)
                   if container.labels.get("anvil.role") == "packet-capture"
                   and container.labels.get("anvil.capture_id") == capture_id]
        if not matches or len(matches) > 16:
            raise DockerManagerError("Packet capture is missing or ambiguous")
        pcaps = []
        export_error: Exception | None = None
        try:
            for capture in matches:
                try:
                    capture.reload()
                    if capture.status == "running":
                        capture.stop(timeout=5)
                    capture_output = capture.logs(stdout=True, stderr=False, tail="all")
                    encoded = capture_output.decode("ascii", errors="strict") if isinstance(capture_output, bytes) else str(capture_output)
                    pcaps.append(base64.b64decode(encoded, validate=True))
                except (DockerException, OSError, ValueError, UnicodeDecodeError) as error:
                    export_error = error
                    break
            if export_error:
                raise DockerManagerError(f"Unable to export packet capture: {_docker_error_message(export_error)}") from export_error
            try:
                return merge_pcaps(pcaps)
            except ValueError as error:
                raise DockerManagerError(str(error)) from error
        except DockerManagerError:
            raise
        except (DockerException, OSError, ValueError, UnicodeDecodeError) as error:
            raise DockerManagerError(f"Unable to export packet capture: {_docker_error_message(error)}") from error
        finally:
            for capture in matches:
                try:
                    capture.remove(force=True)
                except DockerException:
                    pass

    def open_participant_terminal(self, scenario_id: str, node_id: str) -> tuple[str, Any]:
        """Attach a non-root interactive shell to one opted-in participant station."""
        return self._open_device_terminal(scenario_id, node_id, "attacker")

    def open_engineering_terminal(self, scenario_id: str, node_id: str) -> tuple[str, Any]:
        """Attach a non-root interactive shell to one opted-in engineering station."""
        return self._open_device_terminal(scenario_id, node_id, "engineering")

    def _open_device_terminal(self, scenario_id: str, node_id: str, role: str) -> tuple[str, Any]:
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if container.labels.get("anvil.node_id") == node_id
                       and container.labels.get("anvil.role") == role]
            if len(matches) != 1:
                raise DockerManagerError("The requested interactive station is missing or ambiguous")
            container = matches[0]
            container.reload()
            if container.status != "running":
                raise DockerManagerError("The interactive station must be running")
            created = self.client.api.exec_create(
                container.id,
                cmd=["/bin/sh", "-i"],
                stdout=True,
                stderr=True,
                stdin=True,
                tty=True,
                privileged=False,
                user="65534:65534",
                environment=["PS1=anvil$ "],
            )
            exec_id = created["Id"]
            stream = self._connect_exec_terminal(exec_id)
            stream.settimeout(1.0)
            return exec_id, stream
        except DockerManagerError:
            raise
        except (DockerException, OSError, KeyError, ValueError) as error:
            raise DockerManagerError(f"Unable to open station terminal: {_docker_error_message(error)}") from error

    def _connect_exec_terminal(self, exec_id: str) -> Any:
        """Open the Docker exec upgrade as a raw duplex stream through the configured gateway."""
        base_url = str(self.client.api.base_url)
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme == "http" and parsed.hostname and parsed.port:
            stream = socket.create_connection((parsed.hostname, parsed.port), timeout=30)
        elif base_url.startswith("http+docker://"):
            docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
            unix_url = urllib.parse.urlsplit(docker_host)
            if unix_url.scheme != "unix" or not unix_url.path:
                raise DockerManagerError("Participant terminal requires a local Unix socket or the Anvil Docker TCP gateway")
            stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stream.settimeout(30)
            stream.connect(unix_url.path)
        else:
            raise DockerManagerError("Participant terminal does not support a TLS or unknown Docker endpoint")

        path = self.client.api._url(f"/exec/{exec_id}/start")
        body = b'{"Detach":false,"Tty":true}'
        request = (
            f"POST {path} HTTP/1.1\r\n"
            "Host: docker\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: tcp\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii") + body
        try:
            stream.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                block = stream.recv(4096)
                if not block:
                    raise OSError("Docker closed the exec stream during upgrade")
                response.extend(block)
                if len(response) > 16_384:
                    raise OSError("Docker returned oversized exec headers")
            end = response.index(b"\r\n\r\n") + 4
            headers = bytes(response[:end])
            status_line = headers.split(b"\r\n", 1)[0]
            if b" 101 " not in status_line:
                detail = bytes(response[end:]).decode("utf-8", errors="replace")[:500]
                raise OSError(f"Docker exec upgrade rejected ({status_line.decode('ascii', errors='replace')}): {detail}")
            stream.settimeout(_PARTICIPANT_TERMINAL_IDLE_TIMEOUT_SECONDS)
            if end < len(response):
                # Preserve any terminal output sent immediately with the 101 response.
                stream = _PrefixedSocket(stream, bytes(response[end:]))
            return stream
        except Exception:
            stream.close()
            try:
                self.client.api.exec_inspect(exec_id)
            except DockerException:
                pass
            raise

    def close_participant_terminal(self, exec_id: str) -> None:
        """Release the gateway's exec ownership record after a terminal disconnect."""
        try:
            self.client.api.exec_inspect(exec_id)
        except DockerException:
            pass

    @classmethod
    def _cached_resource_usage(cls, container: Any) -> dict[str, Any] | None:
        """Sample Docker stats at a bounded rate; failures never hide lifecycle state."""
        container_id = getattr(container, "id", None)
        if not container_id or getattr(container, "status", None) != "running":
            return {
                "cpu_percent": None, "memory_usage_bytes": None,
                "memory_limit_bytes": None, "pids": None,
                "resource_metrics_status": "not_running",
                "resource_metrics_sampled_at": None,
            }
        now = time.monotonic()
        with _metrics_cache_lock:
            cached = _metrics_cache.get(container_id)
            if cached and now - cached[0] < _METRICS_CACHE_TTL_SECONDS:
                return cached[1]
        try:
            stats = container.stats(stream=False, one_shot=True)
            usage = cls._parse_resource_usage(stats, cached[2] if cached else None)
        except (DockerException, OSError, ValueError, TypeError, KeyError):
            stats = {}
            usage = None
        sampled_at = datetime.now(timezone.utc)
        if usage is None or all(usage.get(key) is None for key in (
            "cpu_percent", "memory_usage_bytes", "memory_limit_bytes", "pids",
        )):
            usage = {
                "cpu_percent": None, "memory_usage_bytes": None,
                "memory_limit_bytes": None, "pids": None,
                "resource_metrics_status": "unavailable",
                "resource_metrics_sampled_at": None,
            }
        else:
            usage = {
                **usage,
                "resource_metrics_status": "good",
                "resource_metrics_sampled_at": sampled_at,
            }
        with _metrics_cache_lock:
            _metrics_cache[container_id] = (now, usage, stats)
            if len(_metrics_cache) > 2048:
                expired = [key for key, (sampled_at, _, _) in _metrics_cache.items()
                           if now - sampled_at >= _METRICS_CACHE_TTL_SECONDS]
                for key in expired:
                    _metrics_cache.pop(key, None)
        return usage

    @staticmethod
    def _parse_resource_usage(stats: Any, previous_stats: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not isinstance(stats, dict):
            return None
        cpu_stats = stats.get("cpu_stats") or {}
        previous_cpu = stats.get("precpu_stats") or {}
        if previous_stats and not previous_cpu.get("system_cpu_usage"):
            previous_cpu = previous_stats.get("cpu_stats") or {}
        cpu_usage = cpu_stats.get("cpu_usage") or {}
        previous_usage = previous_cpu.get("cpu_usage") or {}
        cpu_delta = cpu_usage.get("total_usage", 0) - previous_usage.get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - previous_cpu.get("system_cpu_usage", 0)
        online_cpus = cpu_stats.get("online_cpus") or len(cpu_usage.get("percpu_usage") or []) or 1
        cpu_percent = round(max(0, cpu_delta) / system_delta * online_cpus * 100, 2) if system_delta > 0 else None
        memory_stats = stats.get("memory_stats") or {}
        memory_usage = memory_stats.get("usage")
        memory_limit = memory_stats.get("limit")
        cache = (memory_stats.get("stats") or {}).get("inactive_file", (memory_stats.get("stats") or {}).get("cache", 0))
        if isinstance(memory_usage, int):
            memory_usage = max(0, memory_usage - (cache if isinstance(cache, int) else 0))
        pids = (stats.get("pids_stats") or {}).get("current")
        return {
            "cpu_percent": cpu_percent,
            "memory_usage_bytes": memory_usage if isinstance(memory_usage, int) else None,
            "memory_limit_bytes": memory_limit if isinstance(memory_limit, int) else None,
            "pids": pids if isinstance(pids, int) else None,
        }

    def logs(self, scenario_id: str, *, tail: int | str = 100,
             since_by_node: dict[str, float] | None = None) -> dict[str, Any]:
        """Return a bounded snapshot of stdout/stderr for scenario containers."""

        try:
            containers = []
            for container in self._scenario_containers(scenario_id):
                node_id = container.labels.get("anvil.node_id", "unknown")
                since = (since_by_node or {}).get(node_id)
                raw = container.logs(
                    stdout=True, stderr=True,
                    tail="all" if since is not None else tail,
                    timestamps=True,
                    **({"since": since} if since is not None else {}),
                )
                text = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, bytes)
                    else str(raw)
                )
                containers.append(
                    {
                        "node_id": node_id,
                        "name": container.name,
                        "lines": text.splitlines(),
                    }
                )
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to read scenario logs: {_docker_error_message(error)}"
            ) from error
        return {
            "scenario_id": scenario_id,
            "containers": sorted(
                containers, key=lambda item: (item["node_id"], item["name"])
            ),
        }

    def protocol_audit_queue_nodes(self, scenario_id: str) -> set[str]:
        """Return protocol runtimes whose durable queue replaces Docker-log ingestion."""
        return {
            str(container.labels.get("anvil.node_id"))
            for container in self._scenario_containers(scenario_id)
            if (container.labels or {}).get("anvil.protocol_audit_queue") == "true"
            and (container.labels or {}).get("anvil.scenario_id") == scenario_id
        }

    def read_protocol_audit_queue(self, scenario_id: str, node_id: str, *, limit: int = 1000) -> dict[str, Any] | None:
        """Read a bounded, value-free batch from a running protocol container's durable queue."""
        container = self._protocol_audit_container(scenario_id, node_id)
        if container is None:
            return None
        try:
            container.reload()
            if container.status != "running":
                return None
            result = container.exec_run(
                ["python", "protocol_audit.py", "pending", str(max(1, min(10_000, int(limit))))],
                stdout=True, stderr=True, demux=False, user="65534:65534", workdir="/app",
            )
        except DockerException as error:
            raise DockerManagerError(f"Unable to read protocol audit queue for '{node_id}': {_docker_error_message(error)}") from error
        output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
        if result.exit_code:
            raise DockerManagerError(f"Protocol audit queue read failed for '{node_id}': {output.strip()[:300]}")
        try:
            payload = json.loads(output.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            raise DockerManagerError(f"Protocol audit queue returned invalid JSON for '{node_id}'") from error
        if (not isinstance(payload, dict) or not isinstance(payload.get("events"), list)
                or len(payload["events"]) > 10_000 or type(payload.get("cursor")) is not int
                or payload["cursor"] < 0 or type(payload.get("dropped_events")) is not int
                or payload["dropped_events"] < 0 or type(payload.get("pending_count")) is not int
                or payload["pending_count"] < len(payload["events"])):
            raise DockerManagerError(f"Protocol audit queue returned an invalid batch for '{node_id}'")
        event_ids = []
        for event in payload["events"]:
            if (not isinstance(event, dict) or type(event.get("id")) is not int
                    or event["id"] <= 0 or event["id"] > payload["cursor"]
                    or not isinstance(event.get("line"), str)
                    or not event["line"] or "\n" in event["line"] or "\r" in event["line"]
                    or len(event["line"].encode("utf-8")) > 4096):
                raise DockerManagerError(f"Protocol audit queue returned an invalid event for '{node_id}'")
            event_ids.append(event["id"])
        if event_ids and (event_ids != sorted(set(event_ids)) or event_ids[-1] != payload["cursor"]):
            raise DockerManagerError(f"Protocol audit queue returned an invalid cursor for '{node_id}'")
        return payload

    def acknowledge_protocol_audit_queue(self, scenario_id: str, node_id: str, cursor: int) -> None:
        """Acknowledge a queue cursor after its audit records are committed to SQLite."""
        if type(cursor) is not int or cursor < 0:
            raise DockerManagerError("Protocol audit acknowledgement cursor is invalid")
        container = self._protocol_audit_container(scenario_id, node_id)
        if container is None:
            raise DockerManagerError(f"Protocol audit container '{node_id}' is missing")
        try:
            container.reload()
            if container.status != "running":
                raise DockerManagerError(f"Protocol audit container '{node_id}' is not running")
            attrs = container.attrs if isinstance(container.attrs, dict) else {}
            environment = ((attrs.get("Config") or {}).get("Env") or [])
            env_values = dict(item.split("=", 1) for item in environment if isinstance(item, str) and "=" in item)
            token = env_values.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN", "")
            if not token:
                raise DockerManagerError(f"Protocol audit acknowledgement token is missing for '{node_id}'")
            result = container.exec_run(
                ["python", "protocol_audit.py", "ack", str(cursor)],
                stdout=True, stderr=True, demux=False, user="65534:65534", workdir="/app",
                environment={"ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": token},
            )
        except DockerException as error:
            raise DockerManagerError(f"Unable to acknowledge protocol audit queue for '{node_id}': {_docker_error_message(error)}") from error
        if result.exit_code:
            output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
            raise DockerManagerError(f"Protocol audit queue acknowledgement failed for '{node_id}': {output.strip()[:300]}")

    def _protocol_audit_container(self, scenario_id: str, node_id: str) -> Any | None:
        try:
            matches = [container for container in self._scenario_containers(scenario_id)
                       if (container.labels or {}).get("anvil.node_id") == node_id
                       and (container.labels or {}).get("anvil.protocol_audit_queue") == "true"]
        except DockerException as error:
            raise DockerManagerError(f"Unable to locate protocol audit container '{node_id}': {_docker_error_message(error)}") from error
        if len(matches) > 1:
            raise DockerManagerError(f"Protocol audit container '{node_id}' is missing or ambiguous")
        return matches[0] if matches else None

    def cleanup_orphans(self, known_scenario_ids: set[str]) -> dict[str, Any]:
        """Remove only Anvil-labelled resources with no persisted scenario."""

        removed_containers = 0
        removed_networks = 0
        removed_volumes = 0
        failures = []
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": "anvil.managed=true"}
            )
        except DockerException as error:
            containers = []
            failures.append(f"container inventory: {_docker_error_message(error)}")
        for container in containers:
            scenario_id = container.labels.get("anvil.scenario_id")
            if scenario_id and scenario_id not in known_scenario_ids:
                try:
                    container.remove(force=True)
                    removed_containers += 1
                except DockerException as error:
                    failures.append(
                        f"container '{getattr(container, 'name', 'unknown')}': {_docker_error_message(error)}"
                    )
        try:
            networks = self.client.networks.list(
                filters={"label": "anvil.managed=true"}
            )
        except DockerException as error:
            networks = []
            failures.append(f"network inventory: {_docker_error_message(error)}")
        for network in networks:
            labels = network.attrs.get("Labels") or {}
            scenario_id = labels.get("anvil.scenario_id")
            if scenario_id and scenario_id not in known_scenario_ids:
                try:
                    network.remove()
                    removed_networks += 1
                except DockerException as error:
                    failures.append(
                        f"network '{getattr(network, 'name', 'unknown')}': {_docker_error_message(error)}"
                    )
        try:
            volumes = self.client.volumes.list(filters={"label": "anvil.managed=true"})
        except DockerException as error:
            volumes = []
            failures.append(f"volume inventory: {_docker_error_message(error)}")
        for volume in volumes:
            labels = volume.attrs.get("Labels") or {}
            scenario_id = labels.get("anvil.scenario_id")
            node_id = labels.get("anvil.node_id")
            purpose = labels.get("anvil.purpose")
            expected = (_retention_volume_name(scenario_id, node_id or "") if purpose == "plc-retention" else
                        _scada_history_volume_name(scenario_id, node_id or "") if purpose == "scada-history" else
                        _protocol_audit_volume_name(scenario_id, node_id or "") if purpose == "protocol-audit" else None)
            if (expected and scenario_id and scenario_id not in known_scenario_ids
                    and labels.get("anvil.volume_name") == expected
                    and getattr(volume, "name", None) == expected):
                try:
                    volume.remove(force=False)
                    removed_volumes += 1
                except DockerException as error:
                    failures.append(f"volume '{getattr(volume, 'name', 'unknown')}': {_docker_error_message(error)}")
        return {
            "containers": removed_containers,
            "networks": removed_networks,
            "volumes": removed_volumes,
            "errors": failures,
        }

    def _scenario_containers(self, scenario_id: str) -> list[Any]:
        return self.client.containers.list(
            all=True, filters={"label": f"anvil.scenario_id={scenario_id}"}
        )

    @staticmethod
    def _container_status(container: Any) -> str:
        if container.status != "running":
            return container.status
        attrs = container.attrs if isinstance(container.attrs, dict) else {}
        state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
        health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        health_status = health.get("Status")
        if health_status in {"starting", "unhealthy"}:
            return health_status
        return container.status

    @staticmethod
    def _wait_until_healthy(container: Any, timeout_seconds: float = 20.0) -> None:
        container.reload()
        attrs = container.attrs if isinstance(container.attrs, dict) else {}
        state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
        health = state.get("Health") if isinstance(state.get("Health"), dict) else None
        if health is None:
            return
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = health.get("Status")
            if status == "healthy":
                return
            if status == "unhealthy":
                logs = health.get("Log") or []
                detail = logs[-1].get("Output", "") if logs else ""
                raise DockerManagerError(_healthcheck_failure(container.name, detail))
            time.sleep(0.25)
            container.reload()
            attrs = container.attrs if isinstance(container.attrs, dict) else {}
            state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
            health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        logs = health.get("Log") or []
        detail = logs[-1].get("Output", "") if logs else ""
        raise DockerManagerError(
            _healthcheck_failure(container.name, detail, timeout=timeout_seconds)
        )

    @staticmethod
    def _wait_until_recovered(container: Any, timeout_seconds: float = 20.0) -> None:
        """Wait through transient unhealthy checks caused by Docker pause/unpause."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            container.reload()
            if container.status != "running":
                raise DockerManagerError(
                    f"Container '{container.name}' did not resume; state is {container.status}"
                )
            attrs = container.attrs if isinstance(container.attrs, dict) else {}
            state = attrs.get("State") if isinstance(attrs.get("State"), dict) else {}
            health = state.get("Health") if isinstance(state.get("Health"), dict) else None
            if health is None or health.get("Status") == "healthy":
                return
            if time.monotonic() >= deadline:
                logs = health.get("Log") or []
                detail = logs[-1].get("Output", "") if logs else ""
                raise DockerManagerError(
                    f"Container '{container.name}' health did not recover after lifecycle action: {detail.strip()}"
                )
            time.sleep(0.25)

    def _cleanup_failed_deploy(
        self, scenario_id: str, networks: list[Any], containers: list[Any]
    ) -> list[str]:
        """Roll back known resources and discover resources whose create response was lost."""
        failures = []
        scenario_containers = list(containers)
        try:
            discovered = self.client.containers.list(
                all=True, filters={"label": f"anvil.scenario_id={scenario_id}"}
            )
            known_ids = {getattr(item, "id", None) for item in scenario_containers}
            for container in discovered:
                labels = getattr(container, "labels", {}) or {}
                if (labels.get("anvil.managed") == "true"
                        and labels.get("anvil.scenario_id") == scenario_id
                        and getattr(container, "id", None) not in known_ids):
                    scenario_containers.append(container)
        except DockerException as error:
            failures.append(f"container inventory: {_docker_error_message(error)}")
        for container in reversed(scenario_containers):
            try:
                container.remove(force=True)
            except DockerException as error:
                failures.append(
                    f"container '{getattr(container, 'name', 'unknown')}': {_docker_error_message(error)}"
                )
        scenario_networks = list(networks)
        try:
            discovered = self.client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"}
            )
            known_ids = {getattr(item, "id", None) for item in scenario_networks}
            for network in discovered:
                labels = getattr(network, "attrs", {}).get("Labels") or {}
                if (labels.get("anvil.managed") == "true"
                        and labels.get("anvil.scenario_id") == scenario_id
                        and getattr(network, "id", None) not in known_ids):
                    scenario_networks.append(network)
        except DockerException as error:
            failures.append(f"network inventory: {_docker_error_message(error)}")
        for network in reversed(scenario_networks):
            try:
                network.remove()
            except DockerException as error:
                failures.append(
                    f"network '{getattr(network, 'name', 'unknown')}': {_docker_error_message(error)}"
                )
        return failures
