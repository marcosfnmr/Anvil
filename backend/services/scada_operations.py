"""Read the live acquisition state from a managed SCADA container."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from docker.errors import DockerException

from services.docker_manager import DockerManagerError
from services.plc_operations import _managed_container


def scada_operation(manager: Any, scenario_id: str, node_id: str, path: str, payload: dict | None = None) -> dict:
    if path not in {"snapshot", "history", "history/pending", "history/acknowledge", "alarms", "alarms/acknowledge", "alarms/restore", "write"}:
        raise ValueError("unsupported SCADA operation")
    limit = min(10_000, max(1, int((payload or {}).get("limit", 100))))
    query = urlencode({"limit": limit}) if path in {"history", "history/pending"} else ""
    script = '''import json,sys,urllib.request,urllib.error
path,body=sys.argv[1:]
headers={'Content-Type':'application/json'}
if path == 'history/acknowledge':
    import os
    token=os.environ.get('SCADA_HISTORY_ACK_TOKEN')
    if not token: raise SystemExit('SCADA history acknowledgement token is unavailable')
    headers['Authorization']='Bearer '+token
request=urllib.request.Request('http://127.0.0.1:8766/'+path, data=body.encode() if body else None, headers=headers, method='POST' if body else 'GET')
try:
    with urllib.request.urlopen(request,timeout=3) as response: print(response.read().decode())
except urllib.error.HTTPError as error:
    print(error.read().decode()); sys.exit(2)
'''
    container = _managed_container(manager, scenario_id, node_id)
    try:
        result = container.exec_run(["python", "-c", script, f"{path}?{query}" if query else path,
                                    json.dumps(payload) if path in {"history/acknowledge", "alarms/acknowledge", "alarms/restore", "write"} else ""],
                                    stdout=True, stderr=True, demux=False)
    except DockerException as error:
        raise DockerManagerError(f"Unable to query SCADA collector: {error}") from error
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    try:
        value = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise DockerManagerError("SCADA collector returned an invalid response") from error
    if result.exit_code == 2:
        raise ValueError(value.get("detail", "SCADA request rejected"))
    if result.exit_code:
        raise DockerManagerError(f"SCADA request failed: {output.strip()}")
    return value
