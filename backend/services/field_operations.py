"""Read/control the actual managed field container, with no synthetic fallback."""
import json

from docker.errors import DockerException
from services.docker_manager import DockerManagerError
from services.field_runtime import FIELD_NODE_ID
from services.plc_operations import _managed_container

SCRIPT = '''import json,sys,urllib.request,urllib.error
path,payload=sys.argv[1:]
data=payload.encode() if payload else None
req=urllib.request.Request('http://127.0.0.1:8765/'+path, data=data, headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=2) as r: print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.read().decode()); sys.exit(2)
'''


def field_operation(manager, scenario_id, path="snapshot", payload=None):
    container = _managed_container(manager, scenario_id, FIELD_NODE_ID)
    try:
        result = container.exec_run(["python", "-c", SCRIPT, path, json.dumps(payload) if payload is not None else ""])
    except DockerException as error:
        raise DockerManagerError(f"Field runtime unavailable: {error}") from error
    try:
        value = json.loads(result.output)
    except (ValueError, TypeError) as error:
        raise DockerManagerError("Field runtime returned an invalid response") from error
    if result.exit_code == 2:
        raise ValueError(value.get("detail", "Invalid field control"))
    if result.exit_code:
        raise DockerManagerError("Field runtime operation failed")
    return value
