"""Single-destination TCP ingress relay for an Anvil S7 or Modbus node."""

from __future__ import annotations

import json
import ipaddress
import logging
import os
import socket
import struct
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s anvil.ingress: %(message)s")
AUTO_DEFAULT_GATEWAY = "__docker_default_gateway__"
MAX_CONCURRENT_SESSIONS = 64
_SESSIONS = threading.BoundedSemaphore(MAX_CONCURRENT_SESSIONS)


def _default_gateway_ipv4(route_table: str | None = None) -> str:
    """Read the IPv4 default gateway without requiring NET_ADMIN or iproute2."""
    if route_table is None:
        with open("/proc/net/route", encoding="ascii") as route_file:
            route_table = route_file.read()
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
            gateway = socket.inet_ntoa(struct.pack("<I", int(fields[2], 16)))
        except (ValueError, OSError, struct.error):
            continue
        gateway_address = ipaddress.ip_address(gateway)
        if (flags & 0x2 and not gateway_address.is_unspecified
                and not gateway_address.is_multicast and not gateway_address.is_loopback):
            return gateway
    raise RuntimeError("No IPv4 default gateway is available for protocol-ingress peer filtering")


def _resolve_allowed_peer(config: dict, route_table: str | None = None) -> str:
    peer_ip = config.get("allowed_peer_ip")
    if peer_ip == AUTO_DEFAULT_GATEWAY:
        return _default_gateway_ipv4(route_table)
    return peer_ip


def _serve(client: socket.socket, peer_ip: str, config: dict) -> None:
    allowed = {config["allowed_peer_ip"], config["listen_ip"]}
    if peer_ip not in allowed:
        logging.warning("denied client source=%s", peer_ip)
        client.close()
        return
    target = (config["target_ip"], config["target_port"])
    try:
        upstream = socket.create_connection(target, timeout=5)
        upstream.settimeout(None)
        client.settimeout(None)
    except OSError:
        logging.exception("upstream connection failed target=%s:%s", *target)
        client.close()
        return
    logging.info("accepted target=%s:%s", *target)

    def pump(source: socket.socket, destination: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
    pump(upstream, client)
    client.close()
    upstream.close()


def _serve_and_release(client: socket.socket, peer_ip: str, config: dict,
                       slots: threading.BoundedSemaphore) -> None:
    try:
        _serve(client, peer_ip, config)
    finally:
        slots.release()


def _dispatch_connection(client: socket.socket, peer_ip: str, config: dict,
                         slots: threading.BoundedSemaphore = _SESSIONS) -> bool:
    """Start one bounded relay session, rejecting excess connections cheaply."""
    if not slots.acquire(blocking=False):
        logging.warning("connection limit reached; rejecting client=%s", peer_ip)
        client.close()
        return False
    worker = threading.Thread(target=_serve_and_release,
                              args=(client, peer_ip, config, slots), daemon=True)
    try:
        worker.start()
    except Exception:
        slots.release()
        client.close()
        raise
    return True


def main() -> None:
    config = json.loads(os.environ["ANVIL_INGRESS_CONFIG"])
    config["allowed_peer_ip"] = _resolve_allowed_peer(config)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bind_ip = config["bind_ip"]
    listener.bind((bind_ip, config["listen_port"]))
    listener.listen(32)
    logging.info("listening on %s:%s (zone address %s)", bind_ip,
                 config["listen_port"], config["listen_ip"])
    while True:
        client, peer = listener.accept()
        logging.info("accepted client=%s:%s", *peer)
        _dispatch_connection(client, peer[0], config)


if __name__ == "__main__":
    main()
