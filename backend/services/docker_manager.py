"""Thin docker-py wrapper for scenario networks and containers."""

from __future__ import annotations

from typing import Any

import docker
from docker.errors import DockerException
from docker.types import IPAMConfig, IPAMPool

from devices import DEVICE_REGISTRY


class DockerManagerError(RuntimeError):
    pass


class DockerManager:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client or docker.from_env()
        try:
            self.client.ping()
        except DockerException as error:
            raise DockerManagerError(f"Docker daemon is unavailable: {error}") from error

    @staticmethod
    def _network_name(scenario_id: str) -> str:
        return f"anvil-{scenario_id[:12]}"

    def deploy(
        self,
        scenario_id: str,
        subnet: str,
        runtime_devices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._scenario_containers(scenario_id):
            raise DockerManagerError("Scenario already has managed containers")

        network_name = self._network_name(scenario_id)
        for stale_network in self.client.networks.list(names=[network_name]):
            stale_network.remove()

        network = self.client.networks.create(
            network_name,
            driver="bridge",
            labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id},
            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
        )
        created_containers = []

        try:
            for runtime_device in runtime_devices:
                device_class = DEVICE_REGISTRY.get(runtime_device["device_type"])
                if device_class is None:
                    raise DockerManagerError(
                        f"Unsupported device type '{runtime_device['device_type']}'"
                    )
                container_config = device_class().build_container_config(
                    scenario_id,
                    runtime_device,
                )
                container = self.client.containers.create(**container_config)
                created_containers.append(container)
                network.connect(container, ipv4_address=runtime_device["ip"])
                container.start()
        except Exception as error:
            for container in reversed(created_containers):
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
            try:
                network.remove()
            except DockerException:
                pass
            raise DockerManagerError(f"Unable to deploy scenario: {error}") from error

        return self.status(scenario_id)

    def stop(self, scenario_id: str) -> dict[str, Any]:
        for container in self._scenario_containers(scenario_id):
            container.remove(force=True)

        for network in self.client.networks.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"}
        ):
            network.remove()

        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def status(self, scenario_id: str) -> dict[str, Any]:
        containers = []
        for container in self._scenario_containers(scenario_id):
            container.reload()
            containers.append(
                {
                    "node_id": container.labels.get("anvil.node_id", "unknown"),
                    "name": container.name,
                    "status": container.status,
                }
            )

        statuses = {item["status"] for item in containers}
        if not containers:
            overall_status = "stopped"
        elif statuses == {"running"}:
            overall_status = "running"
        elif statuses & {"dead", "exited"}:
            overall_status = "error"
        else:
            overall_status = "partial"

        return {
            "scenario_id": scenario_id,
            "status": overall_status,
            "containers": containers,
        }

    def _scenario_containers(self, scenario_id: str) -> list[Any]:
        return self.client.containers.list(
            all=True,
            filters={"label": f"anvil.scenario_id={scenario_id}"},
        )
