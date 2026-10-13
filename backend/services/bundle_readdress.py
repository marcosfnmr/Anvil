#!/usr/bin/env python3
"""Readdress the networks in an exported Anvil Compose bundle."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import tempfile
from pathlib import Path


def _parse_zone(value: str) -> tuple[str, ipaddress.IPv4Network]:
    zone_id, separator, cidr = value.partition("=")
    if not separator or not zone_id or not cidr:
        raise argparse.ArgumentTypeError("use ZONE_ID=IPv4_CIDR")
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not isinstance(network, ipaddress.IPv4Network):
        raise argparse.ArgumentTypeError("only IPv4 subnets are supported")
    if network.prefixlen > 30:
        raise argparse.ArgumentTypeError("subnets must be /30 or larger")
    return zone_id, network


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def readdress_bundle(
    *,
    zones: list[tuple[str, ipaddress.IPv4Network]],
    metadata_path: Path = Path("network-addresses.json"),
    env_path: Path = Path(".env"),
) -> dict[str, str]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    records = metadata.get("zones") if isinstance(metadata, dict) else None
    if not isinstance(records, list):
        raise ValueError("network-addresses.json has no valid zones list")
    by_id = {item.get("zone_id"): item for item in records if isinstance(item, dict)}
    if len(by_id) != len(records) or any(not isinstance(key, str) for key in by_id):
        raise ValueError("network-addresses.json contains invalid or duplicate zone IDs")

    requested: dict[str, ipaddress.IPv4Network] = {}
    for zone_id, subnet in zones:
        if zone_id not in by_id:
            raise ValueError(f"Unknown zone '{zone_id}'")
        if zone_id in requested:
            raise ValueError(f"Zone '{zone_id}' was specified more than once")
        requested[zone_id] = subnet

    original_lines, env_values = _read_env(env_path)
    networks: dict[str, ipaddress.IPv4Network] = {}
    for zone_id, record in by_id.items():
        subnet_key = record.get("subnet_env")
        current_subnet = env_values.get(subnet_key, record.get("subnet"))
        try:
            current = ipaddress.ip_network(current_subnet, strict=True)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Zone '{zone_id}' has an invalid current subnet") from error
        if not isinstance(current, ipaddress.IPv4Network):
            raise ValueError(f"Zone '{zone_id}' must use IPv4")
        target = requested.get(zone_id, current)
        if target.prefixlen > 30:
            raise ValueError(f"Zone '{zone_id}' subnet must be /30 or larger")
        networks[zone_id] = target

    for zone_id, network in networks.items():
        for other_id, other in networks.items():
            if other_id > zone_id and network.overlaps(other):
                raise ValueError(f"Zone subnets '{zone_id}' and '{other_id}' overlap")

    updates: dict[str, str] = {}
    used_addresses: dict[str, set[ipaddress.IPv4Address]] = {zone_id: set() for zone_id in by_id}
    for zone_id, record in by_id.items():
        subnet_key = record["subnet_env"]
        old_subnet = ipaddress.ip_network(env_values.get(subnet_key, record["subnet"]), strict=True)
        new_subnet = networks[zone_id]
        updates[subnet_key] = str(new_subnet)
        addresses = record.get("addresses", [])
        if not isinstance(addresses, list):
            raise ValueError(f"Zone '{zone_id}' has an invalid address list")
        for entry in addresses:
            if not isinstance(entry, dict) or not isinstance(entry.get("env"), str):
                raise ValueError(f"Zone '{zone_id}' contains an invalid address entry")
            address_key = entry["env"]
            try:
                old_address = ipaddress.ip_address(env_values.get(address_key, entry.get("ip")))
            except ValueError as error:
                raise ValueError(f"Zone '{zone_id}' contains an invalid current address") from error
            if not isinstance(old_address, ipaddress.IPv4Address) or old_address not in old_subnet:
                raise ValueError(f"Address for '{entry.get('node_id', address_key)}' is outside its current subnet")
            offset = int(old_address) - int(old_subnet.network_address)
            new_address = ipaddress.ip_address(int(new_subnet.network_address) + offset)
            if new_address not in new_subnet or new_address in {new_subnet.network_address, new_subnet.broadcast_address}:
                raise ValueError(f"Subnet '{new_subnet}' has no usable matching address for '{entry.get('node_id', address_key)}'")
            if new_address in used_addresses[zone_id]:
                raise ValueError(f"Zone '{zone_id}' maps multiple devices to {new_address}")
            used_addresses[zone_id].add(new_address)
            updates[address_key] = str(new_address)

    if not requested:
        raise ValueError("Specify at least one --zone ZONE_ID=IPv4_CIDR")

    updated_lines: list[str] = []
    seen: set[str] = set()
    for line in original_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                updated_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        updated_lines.append(line)
    updated_lines.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    payload = "\n".join(updated_lines).rstrip() + "\n"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=env_path.parent,
                                         prefix=f"{env_path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, env_path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone", action="append", type=_parse_zone, default=[], metavar="ZONE_ID=IPv4_CIDR",
                        help="replace a zone subnet; device IP offsets are preserved (repeatable)")
    args = parser.parse_args()
    try:
        updates = readdress_bundle(zones=args.zone)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f"Updated {len(updates)} Compose network/address values in .env")
    for zone_id, subnet in args.zone:
        print(f"  {zone_id}: {subnet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
