"""Small HTTP CONNECT proxy with an exact DNS-name/TCP-port allowlist."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import select
import socket
import socketserver
import threading
from typing import Any


MAX_HEADER_BYTES = 16_384
MAX_CONNECTIONS = 32
MAX_IDLE_SECONDS = 300
_WORKERS = threading.BoundedSemaphore(MAX_CONNECTIONS)


def _is_public_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        isinstance(address, ipaddress.IPv4Address)
        and address.is_global
        and not (address.is_private or address.is_loopback or address.is_link_local
                 or address.is_multicast or address.is_reserved or address.is_unspecified)
    )


def _parse_connect_request(data: bytes) -> tuple[str, int, bytes] | None:
    boundary = data.find(b"\r\n\r\n")
    if boundary < 0 or boundary > MAX_HEADER_BYTES:
        return None
    header = data[:boundary]
    trailing = data[boundary + 4:]
    try:
        lines = header.decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        return None
    if not lines or not re.fullmatch(r"CONNECT [^ ]+ HTTP/1\.[01]", lines[0]):
        return None
    authority = lines[0].split(" ")[1]
    host, separator, port_text = authority.rpartition(":")
    if not separator or not host or not port_text.isdecimal():
        return None
    host = host.lower().rstrip(".")
    if not host.isascii() or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                  for part in host.split(".")):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    port = int(port_text)
    if len(host) > 253 or len(host.split(".")) < 2 or not 1 <= port <= 65535:
        return None
    for line in lines[1:]:
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        if key.lower() in {"content-length", "transfer-encoding"} and value.strip() not in {"", "0"}:
            return None
    return host, port, trailing


def _resolve_public_ipv4(hostname: str, port: int, resolver=socket.getaddrinfo) -> list[str]:
    results = resolver(hostname, port, socket.AF_INET, socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in results))
    if not addresses or any(not _is_public_ipv4(address) for address in addresses):
        return []
    return addresses


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    sockets = (client, upstream)
    while True:
        readable, _, _ = select.select(sockets, [], [], MAX_IDLE_SECONDS)
        if not readable:
            return
        for source in readable:
            data = source.recv(65_536)
            if not data:
                return
            target = upstream if source is client else client
            target.sendall(data)


class EgressProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(5)
        if self.client_address[0] != self.server.config["source_ip"]:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        request = bytearray()
        while b"\r\n\r\n" not in request and len(request) <= MAX_HEADER_BYTES:
            block = client.recv(2048)
            if not block:
                return
            request.extend(block)
        parsed = _parse_connect_request(bytes(request))
        if parsed is None:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return
        hostname, port, trailing = parsed
        allowed = self.server.config["allowed_destinations"]
        if (hostname, port) not in allowed:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        try:
            addresses = _resolve_public_ipv4(hostname, port)
        except OSError:
            addresses = []
        if not addresses:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return
        upstream = None
        for address in addresses:
            try:
                upstream = socket.create_connection((address, port), timeout=5)
                break
            except OSError:
                continue
        if upstream is None:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: Anvil-Egress\r\n\r\n")
            if trailing:
                upstream.sendall(trailing)
            client.settimeout(None)
            upstream.settimeout(None)
            _relay(client, upstream)
        except OSError:
            pass
        finally:
            upstream.close()


class BoundedThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = MAX_CONNECTIONS

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        _WORKERS.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            _WORKERS.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            _WORKERS.release()


def _validate_config(raw: str) -> dict[str, Any]:
    config = json.loads(raw)
    if not isinstance(config, dict) or set(config) != {"listen_ip", "source_ip", "rules"}:
        raise ValueError("egress configuration must contain only listen_ip, source_ip and rules")
    for key in ("listen_ip", "source_ip"):
        address = ipaddress.ip_address(config[key])
        if (not isinstance(address, ipaddress.IPv4Address)
                or address.is_unspecified or address.is_multicast):
            raise ValueError(f"{key} must be a specific IPv4 address")
    rules = config["rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        raise ValueError("egress proxy requires between 1 and 32 allowlist rules")
    destinations: set[tuple[str, int]] = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"hostname", "tcp_port"}:
            raise ValueError("invalid egress rule")
        hostname, port = rule["hostname"], rule["tcp_port"]
        if not isinstance(hostname, str) or not isinstance(port, int) or isinstance(port, bool):
            raise ValueError("invalid egress rule values")
        parsed = _parse_connect_request(f"CONNECT {hostname}:{port} HTTP/1.1\r\n\r\n".encode())
        if parsed is None or (parsed[0], parsed[1]) in destinations:
            raise ValueError("invalid or duplicate egress destination")
        destinations.add((parsed[0], parsed[1]))
    config["allowed_destinations"] = destinations
    return config


def main() -> None:
    config = _validate_config(os.environ.get("ANVIL_EGRESS_CONFIG", ""))
    server = BoundedThreadingTCPServer((config["listen_ip"], 3128), EgressProxyHandler)
    server.config = config
    server.serve_forever()


if __name__ == "__main__":
    main()
