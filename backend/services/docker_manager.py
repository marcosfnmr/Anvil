"""Thin docker-py wrapper for scenario networks and containers."""

from __future__ import annotations

from typing import Any

import docker
from docker.errors import APIError, DockerException
from docker.types import IPAMConfig, IPAMPool

from devices import DEVICE_REGISTRY


class DockerManagerError(RuntimeError):
    pass


def _docker_error_message(error: Exception) -> str:
    if isinstance(error, APIError) and error.explanation:
        return str(error.explanation)
    return str(error)


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
        network = None
        created_containers = []

        try:
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
        except DockerManagerError:
            self._cleanup_failed_deploy(network, created_containers)
            raise
        except Exception as error:
            self._cleanup_failed_deploy(network, created_containers)
            detail = _docker_error_message(error)
            if "overlap" in detail.lower():
                detail = (
                    "The scenario subnet overlaps an existing Docker network. "
                    "Choose a different subnet."
                )
            raise DockerManagerError(f"Unable to deploy scenario: {detail}") from error

        return self.status(scenario_id)

    def stop(self, scenario_id: str) -> dict[str, Any]:
        try:
            for container in self._scenario_containers(scenario_id):
                container.remove(force=True)

            for network in self.client.networks.list(
                filters={"label": f"anvil.scenario_id={scenario_id}"}
            ):
                network.remove()
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to stop scenario: {_docker_error_message(error)}"
            ) from error

        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def status(self, scenario_id: str) -> dict[str, Any]:
        try:
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
        except DockerException as error:
            raise DockerManagerError(
                f"Unable to inspect scenario: {_docker_error_message(error)}"
            ) from error

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

    @staticmethod
    def _cleanup_failed_deploy(network: Any | None, containers: list[Any]) -> None:
        for container in reversed(containers):
            try:
                container.remove(force=True)
            except DockerException:
                pass
        if network is not None:
            try:
                network.remove()
            except DockerException:
                pass
