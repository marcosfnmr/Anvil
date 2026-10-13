"""Internal per-scenario field simulator, managed alongside OT devices."""
import json
import os

from devices import register_device
from devices.base import Device


@register_device("field-runtime")
class FieldRuntime(Device):
    internal = True
    image = os.environ.get("ANVIL_FIELD_IMAGE", "anvil/field:dev")
    healthcheck = {
        "test": ["CMD", "python", "-c",
                 "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/capabilities',timeout=3)); assert d.get('contract')=='anvil-field-runtime' and d.get('contract_version')==1"],
        "interval": 5_000_000_000,
        "timeout": 4_000_000_000,
        "start_period": 5_000_000_000,
        "retries": 3,
    }

    def build_runtime_device(self, device):
        raise ValueError("field-runtime is generated internally")

    def build_container_config(self, scenario_id, runtime_device):
        return {"image": self.image, "name": f"anvil-{scenario_id[:8]}-field",
                "environment": {"FIELD_CONFIG": json.dumps(runtime_device["config"])},
                "healthcheck": dict(self.healthcheck),
                "detach": True, "mem_limit": "128m", "nano_cpus": 500_000_000,
                "labels": {"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                           "anvil.node_id": runtime_device["node_id"]}}
