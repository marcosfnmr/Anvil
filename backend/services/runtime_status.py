"""Reconcile Docker observations with the persisted device plan."""

from __future__ import annotations

from typing import Any


def include_missing_runtime_nodes(
    runtime: dict[str, Any],
    planned_devices: list[dict[str, Any]],
    persisted_status: str,
    *,
    reconcile_deploying: bool = False,
) -> dict[str, Any]:
    """Expose planned nodes absent from Docker without treating stopped plans as failures."""
    if persisted_status == "stopped" or (persisted_status == "deploying" and not reconcile_deploying):
        return runtime

    result = {**runtime, "containers": list(runtime.get("containers", []))}
    observed = {str(item.get("node_id")) for item in result["containers"]}
    from devices import DEVICE_REGISTRY
    missing = [device for device in planned_devices if device.get("node_id") not in observed]
    if not missing:
        return result

    result["containers"].extend(
        {
            "node_id": device["node_id"],
            "name": device.get("name") or device["node_id"],
            "status": "missing",
            "health_status": None,
            "health_error": None,
            "resource_metrics_status": "missing",
            "resource_metrics_sampled_at": None,
            "runtime_kind": "network" if (DEVICE_REGISTRY.get(device.get("device_type"))
                                                and getattr(DEVICE_REGISTRY[device["device_type"]], "network_resource", False)) else "container",
            "runtime_error": "Planned zone network is missing from Docker" if (DEVICE_REGISTRY.get(device.get("device_type"))
                                                                                 and getattr(DEVICE_REGISTRY[device["device_type"]], "network_resource", False)) else "Planned device container is missing from Docker",
        }
        for device in missing
    )

    if result.get("status") not in {"error", "partial"}:
        active = any(
            item.get("status") in {"running", "paused", "starting", "restarting"}
            for item in result["containers"]
            if item.get("status") != "missing"
        )
        # A saved active plan with no Docker containers is an outage, not an
        # intentional stop. The persisted `stopped` guard above preserves
        # deliberate shutdowns.
        result["status"] = "partial" if active else "error"
    return result
