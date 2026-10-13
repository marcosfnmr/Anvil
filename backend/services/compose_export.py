"""Build a portable Compose bundle from a validated scenario runtime plan."""

from __future__ import annotations

import io
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

from devices import DEVICE_REGISTRY
from services.docker_manager import DockerManager
from services.scenario_schema import CURRENT_SCENARIO_SCHEMA_VERSION


def _compose_duration(nanoseconds: int) -> str:
    seconds = nanoseconds / 1_000_000_000
    return f"{seconds:g}s"


def _bundle_env_key(kind: str, identity: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", identity).strip("_").upper()[:32] or "DEFAULT"
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8].upper()
    return f"ANVIL_BUNDLE_{kind}_{slug}_{suffix}"


def _compose_env_reference(key: str, default: str) -> str:
    return f"${{{key}:-{default}}}"


def build_compose_bundle(scenario_id: str, scenario: Any, runtime_devices: list[dict[str, Any]]) -> bytes:
    """Return a zip containing Compose YAML, declarative JSON and usage notes."""
    network_map: dict[str, tuple[str, str]] = {}
    switch_by_zone: dict[str, str] = {}
    for device in runtime_devices:
        if device.get("network_mode") == "none":
            continue
        zone_id = device.get("zone_id", "default") if device.get("zoned") else "default"
        subnet = device.get("zone_subnet", scenario.network.subnet) if device.get("zoned") else scenario.network.subnet
        previous = network_map.get(zone_id)
        if previous and previous[1] != subnet:
            raise ValueError(f"Devices in zone '{zone_id}' have conflicting subnets")
        safe_zone = re.sub(r"[^a-zA-Z0-9_.-]+", "-", zone_id).strip("-._").lower() or "zone"
        network_name = "scenario_net" if zone_id == "default" and not device.get("zoned") else f"zone_{safe_zone}"
        if network_name in {value[0] for key, value in network_map.items() if key != zone_id}:
            network_name = f"zone_{safe_zone}_{len(network_map) + 1}"
        network_map[zone_id] = (network_name, subnet)
        adapter = DEVICE_REGISTRY.get(device.get("device_type"))
        if adapter and getattr(adapter, "network_resource", False):
            switch_by_zone[zone_id] = device["node_id"]
        if device["device_type"] == "tcp-gateway":
            target_zone = device["config"]["target_zone_id"]
            target_subnet = device["config"]["target_zone_subnet"]
            target_name = f"zone_{re.sub(r'[^a-zA-Z0-9_.-]+', '-', target_zone).strip('-._').lower() or 'zone'}"
            previous = network_map.get(target_zone)
            if previous and previous[1] != target_subnet:
                raise ValueError(f"Devices in zone '{target_zone}' have conflicting subnets")
            network_map[target_zone] = (target_name, target_subnet)
    if not network_map:
        network_map["default"] = ("scenario_net", scenario.network.subnet)

    env_defaults: dict[str, str] = {}
    zone_records: dict[str, dict[str, Any]] = {}
    subnet_env: dict[str, str] = {}
    for zone_id, (_network_name, subnet) in network_map.items():
        key = _bundle_env_key("SUBNET", zone_id)
        subnet_env[zone_id] = key
        env_defaults[key] = subnet
        zone_records[zone_id] = {
            "zone_id": zone_id,
            "subnet": subnet,
            "subnet_env": key,
            "addresses": [],
        }

    def address_reference(zone_id: str, identity: str, address: str) -> str:
        key = _bundle_env_key("IP", f"{zone_id}/{identity}")
        env_defaults[key] = address
        zone_records[zone_id]["addresses"].append({"node_id": identity, "ip": address, "env": key})
        return _compose_env_reference(key, address)

    networks: dict[str, Any] = {
        name: {"driver": "bridge", "internal": True,
               "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
               "ipam": {"config": [{"subnet": _compose_env_reference(subnet_env[zone_id], subnet)}]},
               **({"labels": {"anvil.switch_node_id": switch_by_zone[zone_id]}} if zone_id in switch_by_zone else {})}
        for zone_id, (name, subnet) in network_map.items()
    }
    if any(device.get("device_type") in {"egress-gateway", "protocol-ingress"} for device in runtime_devices):
        # Compose needs a user-defined bridge so its service aliases are valid;
        # unlike Docker's special `bridge`, this project-owned network is removed
        # by `docker compose down` and gets a gateway selected by gw_priority.
        networks["host_bridge"] = {"driver": "bridge"}
    services: dict[str, Any] = {}
    used_service_names: set[str] = set()
    for device in runtime_devices:
        adapter_type = device["device_type"]
        adapter = DEVICE_REGISTRY.get(adapter_type)
        if adapter is None:
            raise ValueError(f"Unsupported device type '{adapter_type}'")
        if getattr(adapter, "network_resource", False):
            continue
        config = adapter().build_container_config(scenario_id, device)
        if device.get("image_pin"):
            config["image"] = device["image_pin"]
        DockerManager._apply_container_security(config, device)
        base = re.sub(r"[^a-zA-Z0-9_-]+", "_", device["node_id"]).strip("_").lower() or "node"
        service_name = base
        suffix = 2
        while service_name in used_service_names:
            service_name = f"{base}_{suffix}"
            suffix += 1
        used_service_names.add(service_name)
        # A standalone bundle may run beside the Anvil-managed scenario on the
        # same Docker host. Do not reuse Anvil's ownership labels: lifecycle and
        # reconciliation code intentionally select those labels for cleanup.
        labels = {key: value for key, value in config.get("labels", {}).items()
                  if key not in {"anvil.managed", "anvil.scenario_id"}}
        labels["anvil.bundle"] = "true"
        labels["anvil.bundle_scenario_id"] = scenario_id
        labels["anvil.bundle_node_id"] = device["node_id"]
        service: dict[str, Any] = {
            "image": config["image"],
            # A standalone bundle must use the image supplied by the operator.
            # Never let Compose resolve a mutable tag from a registry on deploy.
            "pull_policy": "never",
            "hostname": device.get("hostname") or base,
            "environment": {key: value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
                            for key, value in config.get("environment", {}).items()},
            "labels": labels,
            "user": config["user"],
            "read_only": config["read_only"],
            "cap_drop": config["cap_drop"],
            "cap_add": config["cap_add"],
            "security_opt": config["security_opt"],
            "pids_limit": config["pids_limit"],
            "mem_limit": config["mem_limit"],
            "cpus": config["nano_cpus"] / 1_000_000_000,
            "tmpfs": [f"{path}:{options}" for path, options in config["tmpfs"].items()],
            "restart": "no",
        }
        if config.get("log_config"):
            docker_log_config = dict(config["log_config"])
            service["logging"] = {
                "driver": docker_log_config["Type"],
                "options": dict(docker_log_config["Config"]),
            }
        if config.get("command") is not None:
            service["command"] = config["command"]
        if config.get("entrypoint") is not None:
            service["entrypoint"] = config["entrypoint"]
        if config.get("healthcheck"):
            health = config["healthcheck"]
            if health.get("test") == ["NONE"]:
                service["healthcheck"] = {"test": ["NONE"]}
            else:
                service["healthcheck"] = {
                    "test": health["test"],
                    "interval": _compose_duration(health["interval"]),
                    "timeout": _compose_duration(health["timeout"]),
                    "retries": health["retries"],
                    "start_period": _compose_duration(health["start_period"]),
                }
        if config.get("ports"):
            service["ports"] = []
            for container_port, binding in config["ports"].items():
                host_port_key = _bundle_env_key("HOST_PORT", f"{device['node_id']}/{container_port}")
                env_defaults[host_port_key] = str(binding[1])
                service["ports"].append(
                    f"{binding[0]}:{_compose_env_reference(host_port_key, str(binding[1]))}:"
                    f"{container_port.split('/')[0]}/{container_port.split('/')[1]}"
                )
        if adapter_type == "protocol-ingress":
            # In a standalone bundle the relay is connected to a dedicated
            # user-defined uplink plus the isolated OT zone. Resolve its gateway from
            # /proc/net/route in the container and keep the source-peer filter.
            ingress_config_key = "ANVIL_INGRESS_CONFIG"
            ingress_config = json.loads(service["environment"][ingress_config_key])
            ingress_config["allowed_peer_ip"] = "__docker_default_gateway__"
            service["environment"][ingress_config_key] = json.dumps(ingress_config, separators=(",", ":"))
        network_mode = device.get("network_mode")
        if network_mode:
            service["network_mode"] = network_mode
        elif adapter_type == "protocol-ingress":
            zone_id = device.get("zone_id", "default") if device.get("zoned") else "default"
            zone_network = network_map[zone_id][0]
            service_networks = {
                # gateway priority makes Docker's default route use the same
                # bridge whose gateway is the expected DNAT peer source.
                "host_bridge": {"gw_priority": 1},
                zone_network: {
                    "ipv4_address": address_reference(zone_id, device["node_id"], device["ip"]),
                    "aliases": [device.get("hostname") or base],
                    "gw_priority": 0,
                },
            }
            service["networks"] = service_networks
        else:
            zone_id = device.get("zone_id", "default") if device.get("zoned") else "default"
            network_name = network_map[zone_id][0]
            service_networks = {network_name: {
                "ipv4_address": address_reference(zone_id, device["node_id"], device["ip"]),
                "aliases": [device.get("hostname") or base],
            }}
            if adapter_type == "tcp-gateway":
                target_zone = device["config"]["target_zone_id"]
                target_ip = device["config"]["target_interface_ip"]
                service_networks[network_map[target_zone][0]] = {
                    "ipv4_address": address_reference(target_zone, f"{device['node_id']}/target", target_ip),
                }
            elif adapter_type == "egress-gateway":
                service_networks["host_bridge"] = {"gw_priority": 1}
            service["networks"] = service_networks
        services[service_name] = service

    compose = {"name": f"anvil-{scenario_id[:8]}", "services": services, "networks": networks}
    declarative = {
        "format": "anvil-scenario", "schema_version": CURRENT_SCENARIO_SCHEMA_VERSION,
        "scenario": {
            "name": scenario.name,
            "network": scenario.network.model_dump(mode="json"),
            "devices": [item.model_dump(mode="json") for item in scenario.devices],
            "canvas": scenario.canvas,
            "field_objects": [item.model_dump(mode="json") for item in scenario.field_objects],
            "signal_connections": [item.model_dump(mode="json") for item in scenario.signal_connections],
            "process_connections": [item.model_dump(mode="json") for item in scenario.process_connections],
            "network_routes": [item.model_dump(mode="json") for item in scenario.network_routes],
            "network_connections": [item.model_dump(mode="json") for item in scenario.network_connections],
        },
    }
    notes = (
        "Anvil standalone deployment bundle\n\n"
        "Requirements: Docker Engine 28 or newer and Docker Compose v2. Engine 28 is required\n"
        "for internal isolated gateway mode, which removes host access through the bridge.\n"
        "The bundle includes docker-compose.yml, scenario.json, .env, network-addresses.json,\n"
        "readdress.py, and the runtime image list. Compose reads .env automatically.\n"
        "It does not include image layers. Run ./export-images.sh on a machine with the images;\n"
        "it creates anvil-images.tar and anvil-images.lock with immutable image IDs. Copy both\n"
        "files to the target host, then run docker load -i anvil-images.tar and ./check-images.sh.\n"
        "Start with: docker compose up -d\n"
        "Inspect with: docker compose ps\n"
        "Stop and remove this bundle's resources with: docker compose down\n\n"
        "Compose project-scoped names are used to avoid container and network name collisions.\n"
        "To run beside the original Anvil scenario, pick a free subnet on this host and run,\n"
        "for example: python3 readdress.py --zone default=10.250.0.0/24\n"
        "Repeat --zone ZONE_ID=IPv4_CIDR for each zone that needs a new subnet. The helper\n"
        "preserves device address offsets, rejects overlapping subnets/addresses, and atomically\n"
        "updates .env. Review .env before starting. Change published host ports there if needed.\n"
        "Networks are internal Docker bridges. Protocol ingress, when explicitly enabled, is "
        "implemented by a dedicated relay attached to the OT zone and Docker default bridge; "
        "the relay accepts only connections sourced from that bridge gateway. The published "
        "host IP defaults to loopback and is configurable in .env. Published OT ports, when explicitly enabled, "
        "bind to the explicitly configured host IP (127.0.0.1 by default). Images are referenced by the names configured in this Anvil "
        "instance. pull_policy: never prevents registry pulls; image IDs in the generated lock "
        "file are checked before startup. The bundle does not include "
        "image layers or the Anvil backend.\n"
    )
    output = io.BytesIO()
    image_names = sorted({service["image"] for service in services.values()})
    image_lines = "".join(f"{image}\n" for image in image_names)
    # Image names originate from configured adapter image references. Quote every
    # generated shell word so unusual but valid registry names cannot alter commands.
    shell_images = " ".join("'" + image.replace("'", "'\\''") + "'" for image in image_names)
    export_script = (
        "#!/bin/sh\nset -eu\n"
        "if [ \"$#\" -ne 0 ]; then echo 'Usage: ./export-images.sh' >&2; exit 2; fi\n"
        "lock_tmp=anvil-images.lock.tmp\n"
        ": > \"$lock_tmp\"\n"
        "for image in " + shell_images + "; do\n"
        "  image_id=$(docker image inspect --format '{{.Id}}' \"$image\")\n"
        "  printf '%s %s\\n' \"$image_id\" \"$image\" >> \"$lock_tmp\"\n"
        "done\n"
        "docker save -o anvil-images.tar " + shell_images + "\n"
        "mv \"$lock_tmp\" anvil-images.lock\n"
        "echo 'Created anvil-images.tar and anvil-images.lock; transfer both to the deployment host.'\n"
    )
    check_script = (
        "#!/bin/sh\nset -eu\n"
        "if [ ! -f anvil-images.lock ]; then echo 'Missing anvil-images.lock; transfer it with anvil-images.tar.' >&2; exit 1; fi\n"
        "while IFS=' ' read -r expected_id image; do\n"
        "  [ -n \"$expected_id\" ] && [ -n \"$image\" ] || continue\n"
        "  actual_id=$(docker image inspect --format '{{.Id}}' \"$image\" 2>/dev/null) || { echo \"Missing Docker image: $image\" >&2; exit 1; }\n"
        "  if [ \"$actual_id\" != \"$expected_id\" ]; then echo \"Image ID mismatch for $image: expected $expected_id, found $actual_id\" >&2; exit 1; fi\n"
        "done < anvil-images.lock\n"
        "echo 'All scenario image IDs match anvil-images.lock.'\n"
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("docker-compose.yml", yaml.safe_dump(compose, sort_keys=False, allow_unicode=True))
        archive.writestr("scenario.json", json.dumps(declarative, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("README.txt", notes)
        archive.writestr("network-addresses.json", json.dumps({"zones": list(zone_records.values())}, indent=2) + "\n")
        archive.writestr(".env", "# Edit with readdress.py; this file contains no credentials or secrets.\n"
                         + "".join(f"{key}={value}\n" for key, value in sorted(env_defaults.items())))
        archive.write(Path(__file__).with_name("bundle_readdress.py"), "readdress.py")
        archive.writestr("images.txt", image_lines)
        archive.writestr("IMAGE_LOCK.txt", "Run ./export-images.sh to generate anvil-images.lock. Compose uses pull_policy: never.\n")
        archive.writestr("export-images.sh", export_script)
        archive.writestr("check-images.sh", check_script)
    return output.getvalue()
