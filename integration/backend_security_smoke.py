#!/usr/bin/env python3
"""Verify the backend starts with a read-only root and private ephemeral SQLite."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


FAKE_DOCKER_API = '''\
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("/_ping"):
            body, content_type, status = b"OK", "text/plain", 200
        elif self.path.endswith("/version"):
            body = json.dumps({"ApiVersion": "1.45", "MinAPIVersion": "1.24",
                               "Version": "27.0.3", "GitCommit": "mock",
                               "Os": "linux", "Arch": "amd64"}).encode()
            content_type, status = "application/json", 200
        else:
            body, content_type, status = b"not found", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass

HTTPServer(("0.0.0.0", 2375), Handler).serve_forever()
'''


def docker(*args: str, check: bool = True, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, check=check, timeout=timeout,
    )


def inspect(name: str) -> dict:
    output = docker("inspect", name).stdout
    return json.loads(output)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("ANVIL_BACKEND_IMAGE", "anvil/backend:ci"))
    args = parser.parse_args()
    name = f"anvil-backend-security-{os.getpid()}"

    with tempfile.TemporaryDirectory(prefix="anvil-backend-security-") as temporary:
        fake_api = Path(temporary) / "fake_docker_api.py"
        fake_api.write_text(FAKE_DOCKER_API, encoding="utf-8")
        try:
            docker(
                "run", "--detach", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--cap-add", "CHOWN", "--cap-add", "FOWNER", "--cap-add", "SETGID", "--cap-add", "SETUID",
                "--pids-limit", "256",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m",
                "--tmpfs", "/data:rw,nosuid,nodev,noexec,size=64m",
                "--mount", f"type=bind,src={fake_api},dst=/opt/fake-docker-api.py,readonly",
                "--env", "DOCKER_HOST=tcp://127.0.0.1:2375",
                "--env", "ANVIL_DATABASE_URL=sqlite:////data/anvil.db",
                "--env", "ANVIL_CLEANUP_ORPHANS=false",
                args.image, "sh", "-c",
                "python /opt/fake-docker-api.py & exec uvicorn main:app --host 0.0.0.0 --port 8000",
            )

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = inspect(name)
                if current["State"]["Status"] != "running":
                    raise RuntimeError(
                        "Backend container exited before becoming healthy; "
                        f"exit={current['State'].get('ExitCode')} "
                        f"user={current['Config'].get('User')} "
                        f"cap_add={current['HostConfig'].get('CapAdd')} "
                        f"tmpfs={current['HostConfig'].get('Tmpfs')}"
                    )
                probe = docker(
                    "exec", name, "python", "-c",
                    "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8000/api/health', timeout=2).status)",
                    check=False, timeout=5,
                )
                if probe.returncode == 0 and probe.stdout.strip() == "200":
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Backend health endpoint did not become ready")

            config = current["HostConfig"]
            assert config["ReadonlyRootfs"] is True
            assert "ALL" in (config.get("CapDrop") or [])
            assert set(config.get("CapAdd") or []) == {
                "CAP_CHOWN", "CAP_FOWNER", "CAP_SETGID", "CAP_SETUID",
            }
            assert "no-new-privileges:true" in (config.get("SecurityOpt") or [])
            assert config["PidsLimit"] == 256
            tmpfs = config.get("Tmpfs") or {}
            assert "/tmp" in tmpfs and "noexec" in tmpfs["/tmp"]
            assert "/data" in tmpfs

            permissions = docker(
                "exec", "--user", "65534:65534", name, "python", "-c",
                "import os,stat; p=os.stat('/data/anvil.db'); print(f'{p.st_uid}:{p.st_gid} {oct(stat.S_IMODE(p.st_mode))}')",
            ).stdout.strip()
            assert permissions == "65534:65534 0o600", f"SQLite ownership/mode is {permissions}"
            identity = docker(
                "exec", name, "python", "-c",
                "s=open('/proc/1/status').read(); print(next(x.split()[1] for x in s.splitlines() if x.startswith('Uid:'))); print(next(x.split()[1] for x in s.splitlines() if x.startswith('CapEff:')))"
            ).stdout.splitlines()
            assert identity == ["65534", "0000000000000000"], f"Backend did not drop UID/capabilities: {identity}"
            print("Backend started with read-only root, reduced bootstrap capabilities, no-new-privileges and bounded processes.")
            print("/api/health returned 200; the server runs as UID 65534 without capabilities and SQLite is 0600.")
            return 0
        except Exception as error:
            log_result = docker("logs", "--tail", "40", name, check=False)
            logs = log_result.stdout + log_result.stderr
            if logs:
                print(logs, file=sys.stderr)
            print(f"Backend confinement smoke failed: {error}", file=sys.stderr)
            return 1
        finally:
            docker("rm", "--force", name, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
