"""One-shot Unix-socket controller for an Anvil TCP route gateway."""

from __future__ import annotations

import json
import os
import socket
import sys


SOCKET_PATH = "/tmp/anvil-route-control.sock"


def main() -> int:
    if len(sys.argv) not in {2, 3} or sys.argv[1] not in {"clear", "set"}:
        return 2
    action = sys.argv[1]
    request = {"token": os.environ.get("ANVIL_TCP_GATEWAY_CONTROL_TOKEN", ""), "action": action}
    if action == "set":
        if len(sys.argv) != 3:
            return 2
        try:
            request["rules"] = json.loads(sys.argv[2])
        except ValueError:
            return 2
    elif len(sys.argv) != 2:
        return 2
    payload = json.dumps(request, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(SOCKET_PATH)
        client.sendall(payload)
        response = bytearray()
        while b"\n" not in response and len(response) <= 1024:
            data = client.recv(1024)
            if not data:
                break
            response.extend(data)
    try:
        result = json.loads(bytes(response).split(b"\n", 1)[0])
    except (ValueError, IndexError):
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
