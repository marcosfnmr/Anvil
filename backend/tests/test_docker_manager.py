from __future__ import annotations

import sys
import base64
import hashlib
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from docker.errors import APIError, ImageNotFound, NotFound


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.docker_manager import DockerManager, DockerManagerError, _docker_error_message  # noqa: E402


class DockerManagerTests(unittest.TestCase):
    def test_modbus_route_interception_control_uses_only_fixed_nonroot_exec(self) -> None:
        client = Mock()
        token = "t" * 43
        container = Mock()
        container.id = "a" * 64
        container.status = "running"
        container.labels = {
            "anvil.node_id": "__anvil_route_modbus-route",
            "anvil.role": "tcp-gateway", "anvil.scenario_id": "scenario-id",
            "anvil.route_inspect_protocol": "modbus-tcp",
        }
        container.attrs = {"Config": {"Env": [f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"]}}
        client.containers.list.return_value = [container]
        client.api.exec_create.return_value = {"Id": "exec-1"}
        client.api.exec_start.return_value = (b'{"ok":true,"active_rules":1}\n', b"")
        client.api.exec_inspect.return_value = {"ExitCode": 0}
        rule = {"rule_id": "pressure", "unit_id": 1, "function_code": 3,
                "address": 10, "replacement_value": 900}

        result = DockerManager(client=client).set_modbus_route_interception(
            "scenario-id", "__anvil_route_modbus-route", [rule],
        )

        self.assertEqual(result, {"ok": True, "active_rules": 1})
        client.api.exec_create.assert_called_once_with(
            "a" * 64,
            cmd=["python", "tcp_route_control.py", "set", json.dumps([rule], separators=(",", ":"))],
            stdout=True, stderr=True, stdin=False, tty=False, privileged=False,
            user="65534:65534", environment=[f"ANVIL_TCP_GATEWAY_CONTROL_TOKEN={token}"],
            workdir="/app",
        )

    def test_network_recovery_recreates_only_missing_managed_zones(self) -> None:
        client = Mock()
        client.networks.list.side_effect = lambda **kwargs: [] if "names" in kwargs else []
        runtime = [
            {"node_id": "plc-1", "device_type": "plc-s7", "ip": "172.30.1.10",
             "zone_id": "control", "zone_subnet": "172.30.1.0/24", "zoned": True,
             "config": {"components": []}},
            {"node_id": "scada-1", "device_type": "scada", "ip": "172.30.2.10",
             "zone_id": "supervision", "zone_subnet": "172.30.2.0/24", "zoned": True,
             "config": {}},
        ]

        created = DockerManager(client=client).ensure_managed_scenario_networks(
            "scenario-12345678", "172.30.0.0/16", runtime,
        )

        self.assertEqual(set(created), {"control", "supervision"})
        self.assertEqual(client.networks.create.call_count, 2)
        labels = [call.kwargs["labels"] for call in client.networks.create.call_args_list]
        self.assertTrue(all(item["anvil.managed"] == "true" for item in labels))
        self.assertTrue(all(item["anvil.scenario_id"] == "scenario-12345678" for item in labels))
        self.assertTrue(all(call.kwargs["internal"] for call in client.networks.create.call_args_list))
        self.assertTrue(all(not call.kwargs.get("ports") for call in client.networks.create.call_args_list))

    def test_network_recovery_preserves_foreign_name_collision(self) -> None:
        client = Mock()
        foreign = Mock()
        foreign.name = DockerManager._network_name("scenario-12345678")
        foreign.attrs = {"Labels": {"anvil.managed": "true", "anvil.scenario_id": "other"}}

        def list_networks(**kwargs):
            return [foreign] if "names" in kwargs else []

        client.networks.list.side_effect = list_networks
        with self.assertRaisesRegex(DockerManagerError, "outside this scenario"):
            DockerManager(client=client).ensure_managed_scenario_networks(
                "scenario-12345678", "172.30.0.0/24", [],
            )
        foreign.remove.assert_not_called()
        client.networks.create.assert_not_called()

    def test_managed_network_detection_requires_exact_scenario_labels(self) -> None:
        client = Mock()
        client.ping.return_value = True
        managed = Mock(attrs={"Labels": {"anvil.managed": "true", "anvil.scenario_id": "scenario-1"}})
        foreign = Mock(attrs={"Labels": {"anvil.managed": "false", "anvil.scenario_id": "scenario-1"}})
        client.networks.list.return_value = [foreign, managed]

        self.assertTrue(DockerManager(client=client).has_managed_scenario_network("scenario-1"))
        client.networks.list.assert_called_once_with(filters={"label": "anvil.scenario_id=scenario-1"})
        client.networks.list.return_value = [foreign]
        self.assertFalse(DockerManager(client=client).has_managed_scenario_network("scenario-1"))

    def test_retentive_s7_gets_retention_and_protocol_audit_volumes(self) -> None:
        scenario_id = "12345678-1234-4234-8234-123456789abc"
        client = Mock()
        client.volumes.get.side_effect = NotFound("missing")
        client.containers.create.return_value = Mock()
        runtime = {"node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
                   "ip": "172.30.1.10", "config": {"retain_logic_state": True, "components": []}}
        network = Mock(name="anvil-zone-default")
        network.name = "anvil-zone-default"
        DockerManager(client=client)._create_runtime_container(scenario_id, runtime, {"default": network})
        self.assertEqual(client.volumes.create.call_count, 2)
        labels_by_purpose = {
            call.kwargs["labels"]["anvil.purpose"]: call.kwargs["labels"]
            for call in client.volumes.create.call_args_list
        }
        labels = labels_by_purpose["plc-retention"]
        self.assertEqual(labels["anvil.scenario_id"], scenario_id)
        self.assertEqual(labels["anvil.node_id"], "plc-1")
        created = client.containers.create.call_args.kwargs
        self.assertEqual(created["labels"]["anvil.retain_logic_state"], "true")
        self.assertEqual(created["labels"]["anvil.protocol_audit_queue"], "true")
        mounts = {mount["Target"]: mount["Source"] for mount in created["mounts"]}
        self.assertEqual(mounts["/var/lib/anvil/retention"], labels["anvil.volume_name"])
        self.assertEqual(mounts["/var/lib/anvil/protocol-audit"],
                         labels_by_purpose["protocol-audit"]["anvil.volume_name"])
        self.assertRegex(created["environment"]["ANVIL_PROTOCOL_AUDIT_ACK_TOKEN"], r"^[A-Za-z0-9_-]{43}$")

    def test_protocol_audit_queue_reader_uses_only_bounded_unprivileged_commands(self) -> None:
        scenario_id = "12345678-1234-4234-8234-123456789abc"
        client = Mock()
        container = Mock()
        container.labels = {"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                            "anvil.node_id": "plc-1", "anvil.protocol_audit_queue": "true"}
        container.status = "running"
        container.attrs = {"Config": {"Env": ["ANVIL_PROTOCOL_AUDIT_ACK_TOKEN=" + "x" * 43]}}
        container.exec_run.return_value = Mock(
            exit_code=0,
            output=b'{"events":[{"id":1,"line":"audit"}],"cursor":1,"dropped_events":0,"pending_count":1}',
        )
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)

        batch = manager.read_protocol_audit_queue(scenario_id, "plc-1", limit=1000)
        self.assertEqual(batch["events"][0]["id"], 1)
        container.exec_run.assert_called_once_with(
            ["python", "protocol_audit.py", "pending", "1000"],
            stdout=True, stderr=True, demux=False, user="65534:65534", workdir="/app",
        )
        manager.acknowledge_protocol_audit_queue(scenario_id, "plc-1", 1)
        self.assertEqual(container.exec_run.call_args.kwargs["environment"], {
            "ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": "x" * 43,
        })
        self.assertEqual(container.exec_run.call_args.args[0],
                         ["python", "protocol_audit.py", "ack", "1"])

        container.exec_run.return_value = Mock(
            exit_code=0,
            output=b'{"events":[{"id":2,"line":"audit"}],"cursor":3,"dropped_events":0,"pending_count":1}',
        )
        with self.assertRaisesRegex(DockerManagerError, "invalid cursor"):
            manager.read_protocol_audit_queue(scenario_id, "plc-1")

    def test_scada_gets_a_private_durable_history_volume(self) -> None:
        scenario_id = "12345678-1234-4234-8234-123456789abc"
        client = Mock()
        client.volumes.get.side_effect = NotFound("missing")
        client.containers.create.return_value = Mock()
        runtime = {"node_id": "scada-1", "device_type": "scada", "name": "SCADA",
                   "ip": "172.30.1.10", "config": {
                       "target_node_ids": "plc",
                       "targets": [{"node_id": "plc", "device_type": "plc-s7", "host": "172.30.1.11",
                                    "port": 102, "rack": 0, "slot": 1, "tags": []}],
                   }}
        network = Mock(name="anvil-zone-default")
        network.name = "anvil-zone-default"
        DockerManager(client=client)._create_runtime_container(scenario_id, runtime, {"default": network})
        labels = client.volumes.create.call_args.kwargs["labels"]
        self.assertEqual(labels["anvil.purpose"], "scada-history")
        self.assertEqual(labels["anvil.scenario_id"], scenario_id)
        created = client.containers.create.call_args.kwargs
        self.assertEqual(created["labels"]["anvil.scada_history_buffer"], "true")
        self.assertEqual(created["mounts"][0]["Target"], "/var/lib/anvil/scada-history")
        self.assertEqual(created["mounts"][0]["Source"], labels["anvil.volume_name"])
        scada_config = json.loads(created["environment"]["SCADA_CONFIG"])
        self.assertEqual(scada_config["durable_history_path"], "/var/lib/anvil/scada-history/history.sqlite3")
        self.assertEqual(scada_config["durable_history_capacity"], 100_000)
        self.assertEqual(scada_config["durable_history_max_bytes"], 134_217_728)
        self.assertRegex(created["environment"]["SCADA_HISTORY_ACK_TOKEN"], r"^[A-Za-z0-9_-]{43}$")

    def test_scenario_data_cleanup_removes_only_matching_scada_history_volume(self) -> None:
        from services.docker_manager import _scada_history_volume_name

        scenario_id, node_id = "12345678-1234-4234-8234-123456789abc", "scada-1"
        name = _scada_history_volume_name(scenario_id, node_id)
        owned = Mock(attrs={"Labels": {
            "anvil.managed": "true", "anvil.purpose": "scada-history",
            "anvil.scenario_id": scenario_id, "anvil.node_id": node_id,
            "anvil.volume_name": name,
        }})
        owned.name = name
        foreign = Mock(attrs={"Labels": {
            "anvil.managed": "true", "anvil.purpose": "scada-history",
            "anvil.scenario_id": scenario_id, "anvil.node_id": node_id,
            "anvil.volume_name": "anvil-scada-history-foreign",
        }})
        foreign.name = "anvil-scada-history-foreign"
        client = Mock()
        client.volumes.list.return_value = [owned, foreign]
        DockerManager(client=client).delete_retention_volumes(scenario_id)
        owned.remove.assert_called_once_with(force=False)
        foreign.remove.assert_not_called()

    def test_default_cpu_budget_accepts_thirty_mixed_s7_modbus_nodes(self) -> None:
        from services import docker_manager

        self.assertEqual(docker_manager._SCENARIO_MAX_NANO_CPUS, 15_000_000_000)
        plan = ([{"device_type": "plc-s7"} for _ in range(15)]
                + [{"device_type": "modbus-tcp"} for _ in range(15)])

        DockerManager._validate_scenario_budget(plan)

    def test_runtime_image_tags_are_pinned_with_an_immutable_local_tag(self) -> None:
        image_id = "sha256:" + "a" * 64
        image = Mock(id=image_id, attrs={"Id": image_id, "RepoDigests": [], "RepoTags": []})
        client = Mock()
        client.ping.return_value = True
        client.images.get.side_effect = [image, ImageNotFound("missing"), image]
        devices = [{"node_id": "__anvil_field__", "device_type": "field-runtime", "config": {}}]

        DockerManager(client=client).pin_runtime_images(devices)

        self.assertEqual(client.images.get.call_args_list[0].args[0], "anvil/field:dev")
        self.assertEqual(devices[0]["image_id"], image_id)
        self.assertEqual(devices[0]["image_ref"], "anvil/field:dev")
        self.assertRegex(devices[0]["image_pin"], r"^anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}$")
        image.tag.assert_called_once_with(devices[0]["image_pin"].split(":", 1)[0], "a" * 64)

    def test_runtime_image_without_registry_digest_can_still_be_pinned_locally(self) -> None:
        image_id = "sha256:" + "a" * 64
        image = Mock(id=image_id, attrs={"Id": image_id, "RepoDigests": []})
        client = Mock()
        client.ping.return_value = True
        client.images.get.side_effect = [image, ImageNotFound("missing"), image]
        devices = [{"node_id": "__anvil_field__", "device_type": "field-runtime", "config": {}}]

        DockerManager(client=client).pin_runtime_images(devices)
        self.assertRegex(devices[0]["image_pin"], r"^anvil-pinned/[a-f0-9]{12}:[a-f0-9]{64}$")

    def test_persisted_image_digest_must_still_match_its_pinned_image_id(self) -> None:
        image_id = "sha256:" + "a" * 64
        digest_ref = "anvil/field@sha256:" + "b" * 64
        image_pin = "anvil-pinned/123456789abc:" + "a" * 64
        image = Mock(id="sha256:" + "c" * 64,
                     attrs={"Id": "sha256:" + "c" * 64, "RepoTags": [image_pin]})
        client = Mock()
        client.ping.return_value = True
        client.images.get.return_value = image
        devices = [{"node_id": "__anvil_field__", "device_type": "field-runtime", "config": {},
                    "image_ref": "anvil/field:dev", "image_id": image_id, "image_pin": image_pin}]

        with self.assertRaisesRegex(DockerManagerError, "no longer matches its stored ID"):
            DockerManager(client=client).pin_runtime_images(devices)

    def test_runtime_image_pin_collision_preserves_existing_image_tag(self) -> None:
        image_id = "sha256:" + "a" * 64
        repo_key = hashlib.sha256(b"anvil/field").hexdigest()[:12]
        pin = f"anvil-pinned/{repo_key}:" + "a" * 64
        source_image = Mock(id=image_id, attrs={"Id": image_id, "RepoTags": []})
        other_image = Mock(id="sha256:" + "c" * 64, attrs={"Id": "sha256:" + "c" * 64})
        client = Mock()
        client.ping.return_value = True
        client.images.get.side_effect = [source_image, other_image]
        devices = [{"node_id": "__anvil_field__", "device_type": "field-runtime", "config": {}}]

        with self.assertRaisesRegex(DockerManagerError, "already refers to another image"):
            DockerManager(client=client).pin_runtime_images(devices)
        source_image.tag.assert_not_called()

    def test_common_docker_deployment_errors_include_actionable_guidance(self) -> None:
        cases = {
            "No such image: anvil/plc-s7:missing": "Construye o descarga la imagen configurada",
            "Bind for 0.0.0.0:102 failed: port is already allocated": "configura otro puerto externo",
            "Pool overlaps with other one on this address space": "subred distinta",
            "Cannot connect to the Docker daemon": "daemon y el gateway Docker",
            "permission denied while connecting to docker.sock": "permisos del socket/daemon configurado",
            "network has active endpoints": "contenedores conectados",
            "endpoint with name plc-1 already exists": "elimina el recurso Anvil huérfano",
        }
        for detail, guidance in cases.items():
            with self.subTest(detail=detail):
                message = _docker_error_message(RuntimeError(detail))
                self.assertIn(guidance, message)
                self.assertIn(detail, message)

    def test_unhealthy_container_error_includes_recovery_steps_when_healthcheck_has_no_output(self) -> None:
        container = Mock()
        container.name = "anvil-scenario-plc"
        container.attrs = {"State": {"Health": {"Status": "unhealthy", "Log": []}}}

        with self.assertRaises(DockerManagerError) as raised:
            DockerManager._wait_until_healthy(container)

        message = str(raised.exception)
        self.assertIn("El healthcheck no devolvió detalle", message)
        self.assertIn("Comprueba que la imagen corresponde al tipo de dispositivo", message)
        self.assertIn("revisa los logs del contenedor", message)

    def test_unhealthy_container_error_preserves_probe_output_and_suggests_next_steps(self) -> None:
        container = Mock()
        container.name = "anvil-scenario-modbus"
        container.attrs = {"State": {"Health": {"Status": "unhealthy", "Log": [
            {"Output": "connection refused on port 1502\n"},
        ]}}}

        with self.assertRaisesRegex(DockerManagerError, "connection refused on port 1502") as raised:
            DockerManager._wait_until_healthy(container)

        self.assertIn("configuración y el puerto interno coinciden", str(raised.exception))

    def test_healthcheck_timeout_includes_probe_output_and_recovery_steps(self) -> None:
        container = Mock()
        container.name = "anvil-scenario-plc"
        container.attrs = {"State": {"Health": {"Status": "starting", "Log": [
            {"Output": "startup still pending\n"},
        ]}}}

        with patch("services.docker_manager.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(DockerManagerError, "1 segundo") as raised:
                DockerManager._wait_until_healthy(container, timeout_seconds=1)

        self.assertIn("startup still pending", str(raised.exception))
        self.assertIn("revisa los logs del contenedor", str(raised.exception))

    def test_empty_docker_error_has_nonempty_diagnostic(self) -> None:
        self.assertIn("sin detalles", _docker_error_message(RuntimeError("")))

    def test_creates_one_internal_network_per_purdue_zone_and_attaches_nodes(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        first, second = Mock(), Mock()
        first.name, second.name = "anvil-scenario-control", "anvil-scenario-operations"
        c1, c2 = Mock(), Mock()
        for container, node_id in ((c1, "plc"), (c2, "hmi")):
            container.labels = {"anvil.node_id": node_id}
            container.name = node_id
            container.status = "running"
            container.attrs = {"State": {}}
        client.containers.list.side_effect = [[], [c1, c2]]
        client.containers.create.side_effect = [c1, c2]
        client.networks.list.return_value = []
        client.networks.create.side_effect = [first, second]
        client.api.create_endpoint_config.side_effect = [
            {"IPAMConfig": {"IPv4Address": "172.30.10.10"}},
            {"IPAMConfig": {"IPv4Address": "172.30.11.10"}},
        ]
        devices = [
            {"node_id": "plc", "hostname": "control-plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.10",
             "zone_id": "control", "zone_subnet": "172.30.10.0/24", "zoned": True,
             "config": {"plc_id": "plc", "rack": 0, "slot": 1, "db_size": 32, "components": []}},
            {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
             "zone_id": "operations", "zone_subnet": "172.30.11.0/24", "zoned": True,
             "config": {"role": "hmi", "summary": "operator"}},
        ]

        result = DockerManager(client=client).deploy("scenario-id", "172.30.1.0/24", devices)

        self.assertEqual(result["status"], "running")
        self.assertGreaterEqual(result["deployment_duration_seconds"], 0)
        self.assertEqual(client.networks.create.call_count, 2)
        self.assertEqual([call.kwargs["ipam"]["Config"][0]["Subnet"] for call in client.networks.create.call_args_list],
                         ["172.30.10.0/24", "172.30.11.0/24"])
        self.assertTrue(all(call.kwargs["internal"] for call in client.networks.create.call_args_list))
        self.assertTrue(all(
            call.kwargs["options"] == {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}
            for call in client.networks.create.call_args_list
        ))
        self.assertEqual(client.containers.create.call_args_list[0].kwargs["network"], "anvil-scenario-control")
        self.assertEqual(client.containers.create.call_args_list[1].kwargs["network"], "anvil-scenario-operations")
        self.assertEqual(client.containers.create.call_args_list[0].kwargs["labels"]["anvil.zone_id"], "control")
        self.assertEqual(client.containers.create.call_args_list[0].kwargs["hostname"], "control-plc")
        self.assertEqual(client.containers.create.call_args_list[0].kwargs["labels"]["anvil.hostname"], "control-plc")
        self.assertEqual(client.api.create_endpoint_config.call_args_list[0].kwargs["aliases"], ["control-plc"])
        for call in client.containers.create.call_args_list:
            self.assertEqual(call.kwargs["user"], "65534:65534")
            self.assertTrue(call.kwargs["read_only"])
            self.assertEqual(call.kwargs["cap_drop"], ["ALL"])
            self.assertEqual(call.kwargs["security_opt"], ["no-new-privileges:true"])
            self.assertEqual(call.kwargs["pids_limit"], 32)
            self.assertIn("mem_limit", call.kwargs)
            self.assertIn("nano_cpus", call.kwargs)

    def test_container_security_allows_only_required_low_port_capability(self) -> None:
        s7 = {"device_type": "plc-s7", "config": {}}
        modbus_privileged_port = {"device_type": "modbus-tcp", "config": {"port": 502}}
        modbus_unprivileged_port = {"device_type": "modbus-tcp", "config": {"port": 1502}}
        s7_config, privileged_config, unprivileged_config, ingress_config = {}, {}, {}, {}

        DockerManager._apply_container_security(s7_config, s7)
        DockerManager._apply_container_security(privileged_config, modbus_privileged_port)
        DockerManager._apply_container_security(unprivileged_config, modbus_unprivileged_port)
        DockerManager._apply_container_security(
            ingress_config, {"device_type": "protocol-ingress", "config": {}}
        )

        self.assertEqual(s7_config["cap_add"], ["NET_BIND_SERVICE"])
        self.assertEqual(dict(s7_config["log_config"]), {
            "Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"},
        })
        self.assertEqual(privileged_config["cap_add"], ["NET_BIND_SERVICE"])
        self.assertEqual(dict(privileged_config["log_config"]), dict(s7_config["log_config"]))
        self.assertEqual(unprivileged_config["cap_add"], [])
        self.assertEqual(dict(unprivileged_config["log_config"]), dict(s7_config["log_config"]))
        self.assertEqual(dict(ingress_config["log_config"]), dict(s7_config["log_config"]))
        non_protocol_config = {}
        DockerManager._apply_container_security(non_protocol_config, {"device_type": "hmi", "config": {}})
        self.assertNotIn("log_config", non_protocol_config)

    def test_container_security_rejects_privileged_host_access(self) -> None:
        for unsafe in ({"privileged": True}, {"network_mode": "host"}, {"volumes": {"/": {"bind": "/host"}}}):
            with self.subTest(unsafe=unsafe), self.assertRaises(DockerManagerError):
                DockerManager._apply_container_security(unsafe, {"device_type": "plc-s7", "config": {}})

    def test_scenario_budgets_reject_node_and_resource_overcommit_before_network_creation(self) -> None:
        client = Mock()
        manager = DockerManager(client=client)
        two_gateways = [
            {"node_id": f"gw-{index}", "device_type": "gateway", "config": {}}
            for index in range(2)
        ]
        with patch("services.docker_manager._SCENARIO_MAX_NODES", 1):
            with self.assertRaisesRegex(DockerManagerError, "configured maximum is 1"):
                manager.deploy("scenario-id", "172.30.1.0/24", two_gateways)
        client.networks.create.assert_not_called()
        client.containers.list.assert_not_called()

        one_plc = [{"node_id": "plc", "device_type": "plc-s7", "config": {}}]
        with patch("services.docker_manager._SCENARIO_MAX_MEMORY_BYTES", 64 * 1024**2):
            with self.assertRaisesRegex(DockerManagerError, "memory limit total"):
                manager.deploy("scenario-id", "172.30.1.0/24", one_plc)
        client.networks.create.assert_not_called()
        with patch("services.docker_manager._SCENARIO_MAX_NANO_CPUS", 499_999_999):
            with self.assertRaisesRegex(DockerManagerError, "CPU limit total"):
                manager.deploy("scenario-id", "172.30.1.0/24", one_plc)
        client.networks.create.assert_not_called()

    def test_exited_container_is_not_hidden_by_previous_starting_health(self) -> None:
        container = Mock()
        container.status = "exited"
        container.attrs = {"State": {"Health": {"Status": "starting"}}}
        self.assertEqual(DockerManager._container_status(container), "exited")

    def test_paused_container_status_is_preserved(self) -> None:
        container = Mock()
        container.status = "paused"
        container.attrs = {"State": {}}
        self.assertEqual(DockerManager._container_status(container), "paused")

    def test_resume_waits_for_transient_unhealthy_healthcheck(self) -> None:
        container = Mock()
        container.name = "plc-1"
        container.status = "running"
        container.attrs = {"State": {"Health": {"Status": "unhealthy", "Log": []}}}
        reloads = 0

        def reload_container():
            nonlocal reloads
            reloads += 1
            if reloads == 2:
                container.attrs["State"]["Health"]["Status"] = "healthy"

        container.reload.side_effect = reload_container
        with patch("services.docker_manager.time.sleep"):
            DockerManager._wait_until_recovered(container)
        self.assertEqual(reloads, 2)

    def test_does_not_remove_network_owned_by_another_scenario(self) -> None:
        client = Mock()
        client.containers.list.return_value = []
        foreign = Mock(attrs={"Labels": {"anvil.managed": "true", "anvil.scenario_id": "other"}})
        foreign.name = "anvil-scenario-id"
        client.networks.list.return_value = [foreign]
        with self.assertRaisesRegex(DockerManagerError, "preserved"):
            DockerManager(client=client).deploy("scenario-id", "172.30.1.0/24", [])
        foreign.remove.assert_not_called()
        client.networks.create.assert_not_called()

    def test_rejects_duplicate_published_ports_before_creating_network(self) -> None:
        client = Mock()
        client.ping.return_value = True
        manager = DockerManager(client=client)
        devices = [
            {"external_access": True, "external_port": 1502},
            {"external_access": True, "external_port": 1502},
        ]
        with self.assertRaisesRegex(DockerManagerError, "ports must be present and unique"):
            manager.deploy("scenario-id", "172.30.1.0/24", devices)
        client.networks.create.assert_not_called()

    def test_overlapping_subnet_becomes_a_useful_domain_error(self) -> None:
        client = Mock()
        client.ping.return_value = True
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        client.networks.create.side_effect = APIError(
            "invalid pool request: Pool overlaps with other one on this address space"
        )
        manager = DockerManager(client=client)

        with self.assertRaisesRegex(
            DockerManagerError,
            "subnet overlaps an existing Docker network",
        ):
            manager.deploy("scenario-id", "172.30.100.0/24", [])

    def test_failed_container_start_removes_container_and_network(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        network = client.networks.create.return_value
        network.name = "anvil-scenario-id"
        container = client.containers.create.return_value
        container.start.side_effect = APIError("start failed")
        client.api.create_endpoint_config.return_value = {"IPAMConfig": {"IPv4Address": "172.30.110.10"}}
        manager = DockerManager(client=client)
        runtime_device = {
            "node_id": "plc-1",
            "device_type": "plc-s7",
            "name": "PLC",
            "ip": "172.30.110.10",
            "config": {"components": []},
        }

        with self.assertRaises(DockerManagerError):
            manager.deploy("scenario-id", "172.30.110.0/24", [runtime_device])

        container.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once_with()
        self.assertEqual(client.containers.create.call_args.kwargs["network"], "anvil-scenario-id")
        self.assertTrue(client.networks.create.call_args.kwargs["internal"])
        self.assertEqual(client.networks.create.call_args.kwargs["options"], {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
        })
        client.api.create_endpoint_config.assert_called_once_with(ipv4_address="172.30.110.10", aliases=["plc-1"])
        network.connect.assert_not_called()
        from docker.models.containers import _create_container_args
        arguments = _create_container_args({**client.containers.create.call_args.kwargs, "version": "1.44"})
        self.assertEqual(arguments["networking_config"]["EndpointsConfig"]["anvil-scenario-id"]["IPAMConfig"]["IPv4Address"], "172.30.110.10")

    def test_stop_attempts_remaining_resources_and_reports_cleanup_failures(self) -> None:
        client = Mock()
        first, second = Mock(), Mock()
        first.name, second.name = "anvil-one", "anvil-two"
        first.labels = second.labels = {"anvil.managed": "true", "anvil.scenario_id": "scenario-id"}
        first.remove.side_effect = APIError("container removal failed")
        unrelated = Mock()
        unrelated.labels = {"anvil.managed": "false", "anvil.scenario_id": "scenario-id"}
        network = Mock()
        network.name = "anvil-scenario-id"
        network.attrs = {"Labels": {"anvil.managed": "true", "anvil.scenario_id": "scenario-id"}}
        unrelated_network = Mock()
        unrelated_network.attrs = {"Labels": {"anvil.managed": "false", "anvil.scenario_id": "scenario-id"}}
        network.remove.side_effect = APIError("network still in use")
        client.containers.list.return_value = [first, second, unrelated]
        client.networks.list.return_value = [network, unrelated_network]

        with self.assertRaisesRegex(
            DockerManagerError,
            "cleanup incomplete: container 'anvil-one': container removal failed; "
            "network 'anvil-scenario-id': network still in use",
        ):
            DockerManager(client=client).stop("scenario-id")

        first.remove.assert_called_once_with(force=True)
        second.remove.assert_called_once_with(force=True)
        unrelated.remove.assert_not_called()
        network.remove.assert_called_once_with()
        unrelated_network.remove.assert_not_called()

    def test_failed_deploy_reports_container_and_network_rollback_failures(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        network = client.networks.create.return_value
        network.name = "anvil-scenario-id"
        network.remove.side_effect = APIError("network removal failed")
        container = client.containers.create.return_value
        container.name = "anvil-scenario-plc"
        container.start.side_effect = APIError("container start failed")
        container.remove.side_effect = APIError("container removal failed")
        client.api.create_endpoint_config.return_value = {"IPAMConfig": {"IPv4Address": "172.30.111.10"}}
        runtime_device = {
            "node_id": "plc-1",
            "device_type": "plc-s7",
            "name": "PLC",
            "ip": "172.30.111.10",
            "config": {"components": []},
        }

        with self.assertRaisesRegex(
            DockerManagerError,
            "rollback incomplete: container 'anvil-scenario-plc': container removal failed; "
            "network 'anvil-scenario-id': network removal failed",
        ):
            DockerManager(client=client).deploy("scenario-id", "172.30.111.0/24", [runtime_device])

        container.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once_with()

    def test_failed_deploy_discovers_container_created_before_docker_response_was_lost(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        orphan = Mock()
        orphan.id = "a" * 64
        orphan.name = "anvil-scenario-plc"
        orphan.labels = {
            "anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.node_id": "plc-1",
        }
        network = Mock()
        network.id = "b" * 64
        network.name = "anvil-scenario-id"
        network.attrs = {"Labels": {
            "anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.zone_id": "default",
        }}
        client.containers.list.side_effect = [[], [orphan]]
        client.containers.create.side_effect = APIError("response lost after container creation")
        client.networks.list.side_effect = [[], [network]]
        client.networks.create.return_value = Mock(name="created-network")
        client.networks.create.return_value.name = "anvil-scenario-id"
        client.api.create_endpoint_config.return_value = {"IPAMConfig": {"IPv4Address": "172.30.111.10"}}
        runtime_device = {
            "node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.111.10", "config": {"components": []},
        }

        with self.assertRaisesRegex(DockerManagerError, "response lost after container creation"):
            DockerManager(client=client).deploy("scenario-id", "172.30.111.0/24", [runtime_device])

        orphan.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once_with()

    def test_failed_deploy_preserves_other_scenarios_during_response_loss_cleanup(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        foreign = Mock()
        foreign.id = "c" * 64
        foreign.name = "anvil-other-plc"
        foreign.labels = {
            "anvil.managed": "true", "anvil.scenario_id": "other-scenario", "anvil.node_id": "plc-1",
        }
        client.containers.list.side_effect = [[], [foreign]]
        client.containers.create.side_effect = APIError("response lost after container creation")
        client.networks.list.side_effect = [[], []]
        client.networks.create.return_value = Mock(name="created-network")
        client.networks.create.return_value.name = "anvil-scenario-id"
        client.api.create_endpoint_config.return_value = {"IPAMConfig": {"IPv4Address": "172.30.111.10"}}
        runtime_device = {
            "node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.111.10", "config": {"components": []},
        }

        with self.assertRaises(DockerManagerError):
            DockerManager(client=client).deploy("scenario-id", "172.30.111.0/24", [runtime_device])

        foreign.remove.assert_not_called()

    def test_failed_deploy_discovers_network_created_before_docker_response_was_lost(self) -> None:
        client = Mock()
        client.volumes.list.return_value = []
        client.volumes.get.side_effect = NotFound("missing")
        client.ping.return_value = True
        network = Mock()
        network.id = "d" * 64
        network.name = "anvil-scenario-id"
        network.attrs = {"Labels": {
            "anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.zone_id": "default",
        }}
        client.containers.list.return_value = []
        client.networks.list.side_effect = [[], [network]]
        client.networks.create.side_effect = APIError("response lost after network creation")
        runtime_device = {
            "node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
            "ip": "172.30.111.10", "config": {"components": []},
        }

        with self.assertRaisesRegex(DockerManagerError, "response lost after network creation"):
            DockerManager(client=client).deploy("scenario-id", "172.30.111.0/24", [runtime_device])

        network.remove.assert_called_once_with()

    def test_unhealthy_container_marks_scenario_as_error(self) -> None:
        client = Mock()
        client.ping.return_value = True
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "anvil-plc-1"
        container.status = "running"
        container.attrs = {"State": {"Health": {"Status": "unhealthy", "Log": [
            {"Output": "connection refused from health probe\n"},
        ]}}}
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)

        result = manager.status("scenario-id")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["containers"][0]["status"], "unhealthy")
        self.assertEqual(result["containers"][0]["health_status"], "unhealthy")
        self.assertEqual(result["containers"][0]["health_error"], "connection refused from health probe")

    def test_status_includes_bounded_container_resource_usage(self) -> None:
        client = Mock()
        container = Mock()
        container.id = "resource-sample-test"
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "anvil-plc-1"
        container.status = "running"
        container.attrs = {"State": {}}
        container.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 150}, "system_cpu_usage": 1000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 800},
            "memory_stats": {"usage": 50, "limit": 100, "stats": {"inactive_file": 5}},
            "pids_stats": {"current": 3},
        }
        client.containers.list.return_value = [container]

        result = DockerManager(client=client).status("scenario-id")

        self.assertEqual(result["containers"][0]["cpu_percent"], 50.0)
        self.assertEqual(result["containers"][0]["memory_usage_bytes"], 45)
        self.assertEqual(result["containers"][0]["memory_limit_bytes"], 100)
        self.assertEqual(result["containers"][0]["pids"], 3)
        self.assertEqual(result["containers"][0]["resource_metrics_status"], "good")
        self.assertIsNotNone(result["containers"][0]["resource_metrics_sampled_at"])
        container.stats.assert_called_once_with(stream=False, one_shot=True)

    def test_status_distinguishes_unavailable_metrics_from_stopped_container(self) -> None:
        client = Mock()
        container = Mock()
        container.id = "resource-unavailable-test"
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "anvil-plc-1"
        container.status = "running"
        container.attrs = {"State": {}}
        container.stats.side_effect = OSError("stats unavailable")
        client.containers.list.return_value = [container]

        result = DockerManager(client=client).status("scenario-id")

        metrics = result["containers"][0]
        self.assertEqual(result["status"], "running")
        self.assertEqual(metrics["resource_metrics_status"], "unavailable")
        self.assertIsNone(metrics["resource_metrics_sampled_at"])
        self.assertIsNone(metrics["cpu_percent"])

    def test_non_running_container_metrics_are_marked_not_sampled(self) -> None:
        container = Mock(id="resource-stopped-test", status="paused")

        metrics = DockerManager._cached_resource_usage(container)

        self.assertEqual(metrics["resource_metrics_status"], "not_running")
        self.assertIsNone(metrics["resource_metrics_sampled_at"])
        container.stats.assert_not_called()

    def test_resource_usage_handles_unavailable_metrics(self) -> None:
        self.assertEqual(DockerManager._parse_resource_usage({"memory_stats": {}, "cpu_stats": {}}), {
            "cpu_percent": None, "memory_usage_bytes": None,
            "memory_limit_bytes": None, "pids": None,
        })

    def test_packet_capture_uses_only_the_selected_node_network_namespace(self) -> None:
        client = Mock()
        target = Mock()
        target.id = "a" * 64
        target.status = "running"
        target.labels = {"anvil.node_id": "plc-1", "anvil.zone_id": "control", "anvil.role": "plc-s7"}
        target.attrs = {"NetworkSettings": {"Networks": {"anvil-scenario": {}}}}
        capture = Mock(status="running")
        capture.logs.return_value = b"tcpdump: listening on any"
        client.containers.list.return_value = [target]
        client.containers.create.return_value = capture

        result = DockerManager(client=client).start_packet_capture(
            "12345678-1234-4234-8234-123456789abc", "plc-1", "12345678-1234-4234-8234-123456789abd"
        )

        self.assertEqual(result["status"], "capturing")
        args = client.containers.create.call_args.kwargs
        self.assertEqual(args["network_mode"], f"container:{target.id}")
        self.assertEqual(args["user"], "0:0")
        self.assertEqual(args["cap_add"], ["NET_RAW", "SETGID", "SETUID"])
        self.assertEqual(args["cap_drop"], ["ALL"])
        self.assertTrue(args["read_only"])
        self.assertEqual(args["tmpfs"], {"/tmp": "rw,nosuid,nodev,noexec,size=16m"})
        self.assertEqual(args["entrypoint"], ["/bin/sh", "-c"])
        self.assertIn("-c 40000", args["command"][0])

    def test_link_capture_filters_peer_ip_in_source_namespace(self) -> None:
        client = Mock()
        source = Mock(id="a" * 64, status="running")
        source.labels = {"anvil.node_id": "plc-1", "anvil.zone_id": "control", "anvil.role": "plc-s7"}
        source.attrs = {"NetworkSettings": {"Networks": {"anvil-zone": {"IPAddress": "10.0.0.2", "NetworkID": "c" * 64}}}}
        peer = Mock(id="b" * 64, status="running")
        peer.labels = {"anvil.node_id": "plc-2", "anvil.zone_id": "control", "anvil.role": "plc-s7"}
        peer.attrs = {"NetworkSettings": {"Networks": {"anvil-zone": {"IPAddress": "10.0.0.3", "NetworkID": "c" * 64}}}}
        capture = Mock(status="running")
        capture.logs.return_value = b"tcpdump: listening on any"
        client.containers.list.return_value = [source, peer]
        client.containers.create.return_value = capture

        result = DockerManager(client=client).start_packet_capture(
            "12345678-1234-4234-8234-123456789abc", "plc-1",
            "12345678-1234-4234-8234-123456789abd", peer_node_id="plc-2")

        self.assertEqual(result["scope"], "link")
        self.assertEqual(result["source_node_id"], "plc-1")
        self.assertEqual(result["target_node_id"], "plc-2")
        args = client.containers.create.call_args.kwargs
        self.assertEqual(args["network_mode"], f"container:{source.id}")
        self.assertIn("host 10.0.0.3", args["command"][0])
        self.assertEqual(args["labels"]["anvil.capture_peer_container_id"], peer.id)
        self.assertEqual(args["labels"]["anvil.capture_scope"], "link")

    def test_zone_packet_capture_uses_the_managed_bridge_and_host_raw_capability(self) -> None:
        client = Mock()
        targets = []
        for node_id in ("plc-1", "hmi-1"):
            target = Mock()
            target.id = node_id[0] * 64
            target.status = "running"
            target.status = "running"
            target.labels = {"anvil.node_id": node_id, "anvil.zone_id": "control", "anvil.role": "plc-s7"}
            target.attrs = {"NetworkSettings": {"Networks": {"anvil-scenario": {}}}}
            targets.append(target)
        network = Mock()
        network.id = "b" * 64
        network.name = "anvil-scenario-control"
        network.attrs = {"Driver": "bridge", "Internal": True, "Options": {}, "Labels": {
            "anvil.managed": "true", "anvil.scenario_id": "12345678-1234-4234-8234-123456789abc", "anvil.zone_id": "control",
        }}
        network.reload = Mock()
        client.networks.list.return_value = [network]
        capture = Mock(status="running")
        capture.logs.return_value = b"tcpdump: listening on br-bbbbbbbbbbbb"
        client.containers.list.return_value = targets
        client.containers.create.return_value = capture
        result = DockerManager(client=client).start_packet_capture(
            "12345678-1234-4234-8234-123456789abc", None,
            "12345678-1234-4234-8234-123456789abd", zone_id="control")
        self.assertEqual(result["scope"], "segment")
        self.assertEqual(client.containers.create.call_count, 1)
        args = client.containers.create.call_args.kwargs
        self.assertEqual(args["network_mode"], "host")
        self.assertEqual(args["cap_add"], ["NET_RAW", "SETGID", "SETUID"])
        self.assertIn("-i br-bbbbbbbbbbbb", args["command"][0])
        self.assertEqual(args["labels"]["anvil.capture_scope"], "segment")
        self.assertEqual(args["labels"]["anvil.capture_network_id"], network.id)

    def test_participant_terminal_exec_is_non_root_and_only_targets_attacker_role(self) -> None:
        client = Mock()
        client.api.base_url = "http://docker-api:12375"
        client.api._url.return_value = "/v1.45/exec/exec-id/start"
        client.api.exec_create.return_value = {"Id": "exec-id"}
        station = Mock()
        station.id = "a" * 64
        station.status = "running"
        station.labels = {"anvil.node_id": "attacker-1", "anvil.role": "attacker"}
        client.containers.list.return_value = [station]
        stream = Mock()
        stream.recv.return_value = b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\n\r\n"
        with patch("services.docker_manager.socket.create_connection", return_value=stream):
            exec_id, terminal = DockerManager(client=client).open_participant_terminal("scenario", "attacker-1")

        self.assertEqual(exec_id, "exec-id")
        self.assertIs(terminal, stream)
        client.api.exec_create.assert_called_once_with(
            "a" * 64, cmd=["/bin/sh", "-i"], stdout=True, stderr=True,
            stdin=True, tty=True, privileged=False, user="65534:65534",
            environment=["PS1=anvil$ "],
        )
        stream.sendall.assert_called_once()
        request = stream.sendall.call_args.args[0]
        self.assertIn(b"Upgrade: tcp", request)
        self.assertIn(b'"Tty":true', request)

    def test_engineering_terminal_targets_only_engineering_role(self) -> None:
        client = Mock()
        client.api.base_url = "http://docker-api:12375"
        client.api._url.return_value = "/v1.45/exec/exec-id/start"
        client.api.exec_create.return_value = {"Id": "exec-id"}
        station = Mock()
        station.id = "b" * 64
        station.status = "running"
        station.labels = {"anvil.node_id": "engineering-1", "anvil.role": "engineering"}
        client.containers.list.return_value = [station]
        stream = Mock()
        stream.recv.return_value = b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\n\r\n"
        with patch("services.docker_manager.socket.create_connection", return_value=stream):
            exec_id, terminal = DockerManager(client=client).open_engineering_terminal("scenario", "engineering-1")
        self.assertEqual(exec_id, "exec-id")
        self.assertIs(terminal, stream)
        client.api.exec_create.assert_called_once_with(
            "b" * 64, cmd=["/bin/sh", "-i"], stdout=True, stderr=True,
            stdin=True, tty=True, privileged=False, user="65534:65534",
            environment=["PS1=anvil$ "],
        )

    def test_participant_terminal_refuses_non_station_and_unhealthy_containers(self) -> None:
        client = Mock()
        plc = Mock()
        plc.labels = {"anvil.node_id": "plc-1", "anvil.role": "plc-s7"}
        client.containers.list.return_value = [plc]
        manager = DockerManager(client=client)
        with self.assertRaisesRegex(DockerManagerError, "missing or ambiguous"):
            manager.open_participant_terminal("scenario", "plc-1")
        client.api.exec_create.assert_not_called()

        station = Mock()
        station.labels = {"anvil.node_id": "attacker-1", "anvil.role": "attacker"}
        station.status = "exited"
        client.containers.list.return_value = [station]
        with self.assertRaisesRegex(DockerManagerError, "must be running"):
            manager.open_participant_terminal("scenario", "attacker-1")
        client.api.exec_create.assert_not_called()

    def test_route_probe_runs_protocol_specific_read_only_checks_as_non_root(self) -> None:
        for protocol, target_config, expected in (
            ("s7", {"rack": 0, "slot": 1}, "S7 setup communication"),
            ("modbus-tcp", {"unit_id": 7}, "tid=0xa71"),
            ("field-http", {}, "__anvil_diagnostic_probe__"),
            ("tcp", {}, "create_connection"),
        ):
            with self.subTest(protocol=protocol):
                client = Mock()
                station = Mock()
                station.id = "a" * 64
                station.status = "running"
                station.labels = {"anvil.node_id": "hmi-1", "anvil.role": "hmi"}
                client.containers.list.return_value = [station]
                client.api.exec_create.return_value = {"Id": "probe-id"}
                client.api.exec_start.return_value = (b"probe succeeded", None)
                client.api.exec_inspect.return_value = {"ExitCode": 0}

                result = DockerManager(client=client).probe_network_route(
                    "scenario-1", source_node_id="hmi-1", host="172.30.10.2", port=22000,
                    protocol=protocol, target_config=target_config,
                )

                self.assertEqual(result["status"], "reachable")
                call = client.api.exec_create.call_args
                self.assertEqual(call.args[0], station.id)
                self.assertEqual(call.kwargs["user"], "65534:65534")
                self.assertFalse(call.kwargs["tty"])
                self.assertIn(expected, call.kwargs["cmd"][2])
                client.api.exec_start.assert_called_once_with("probe-id", demux=True)

    def test_route_probe_reports_protocol_failure_without_hiding_probe_detail(self) -> None:
        client = Mock()
        source = Mock()
        source.id = "b" * 64
        source.status = "running"
        source.labels = {"anvil.node_id": "hmi-1", "anvil.role": "hmi"}
        client.containers.list.return_value = [source]
        client.api.exec_create.return_value = {"Id": "probe-id"}
        client.api.exec_start.return_value = (b"", b"S7 setup communication was not acknowledged")
        client.api.exec_inspect.return_value = {"ExitCode": 1}

        result = DockerManager(client=client).probe_network_route(
            "scenario-1", source_node_id="hmi-1", host="172.30.10.2", port=22000,
            protocol="s7", target_config={"rack": 0, "slot": 1},
        )

        self.assertEqual(result["status"], "unreachable")
        self.assertIn("setup communication", result["detail"])

    def test_field_binding_probe_checks_each_expected_address_and_freshness_as_non_root(self) -> None:
        bindings = [
            {"address": "DB1.DBD0", "field_id": "sensor-level", "field_port": "value"},
            {"address": "DB1.DBX4.0", "field_id": "valve-1", "field_port": "state"},
        ]
        report = {"bindings": [
            {"address": item["address"], "field_id": item["field_id"], "field_port": item["field_port"],
             "status": "good", "age_ms": 35}
            for item in bindings
        ]}
        client = Mock()
        source = Mock()
        source.id = "c" * 64
        source.status = "running"
        source.labels = {"anvil.node_id": "plc-1", "anvil.role": "plc-s7"}
        client.containers.list.return_value = [source]
        client.api.exec_create.return_value = {"Id": "binding-probe"}
        client.api.exec_start.return_value = (json.dumps(report).encode(), None)
        client.api.exec_inspect.return_value = {"ExitCode": 0}

        result = DockerManager(client=client).probe_field_bindings(
            "scenario-1", source_node_id="plc-1", bindings=bindings,
        )

        self.assertEqual(result["status"], "reachable")
        self.assertEqual([item["address"] for item in result["bindings"]], ["DB1.DBD0", "DB1.DBX4.0"])
        call = client.api.exec_create.call_args
        self.assertEqual(call.args[0], source.id)
        self.assertEqual(call.kwargs["user"], "65534:65534")
        self.assertFalse(call.kwargs["stdin"])
        self.assertIn("/tmp/anvil-field-links.json", call.kwargs["cmd"][2])
        self.assertIn("last_success", call.kwargs["cmd"][2])
        self.assertNotIn("/exchange", call.kwargs["cmd"][2])

    def test_packet_capture_export_stops_validates_and_removes_sidecar(self) -> None:
        client = Mock()
        pcap = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 128, 1)
        capture = Mock()
        capture.labels = {"anvil.role": "packet-capture", "anvil.capture_id": "capture-1"}
        capture.status = "running"
        capture.logs.return_value = base64.b64encode(pcap)
        client.containers.list.return_value = [capture]

        result = DockerManager(client=client).export_packet_capture("scenario-1", "capture-1")

        self.assertEqual(result, pcap)
        capture.stop.assert_called_once_with(timeout=5)
        capture.logs.assert_called()
        capture.remove.assert_called_once_with(force=True)

    def test_node_lifecycle_acts_only_on_exact_scenario_node(self) -> None:
        client = Mock()
        target = Mock()
        target.labels = {"anvil.node_id": "plc-1"}
        target.status = "running"
        target.attrs = {"State": {}}
        client.containers.list.return_value = [target]
        manager = DockerManager(client=client)

        manager.node_lifecycle("scenario-id", "plc-1", "restart")

        target.restart.assert_called_once_with(timeout=5)
        manager.node_lifecycle("scenario-id", "plc-1", "pause")
        target.pause.assert_called_once_with()
        manager.node_lifecycle("scenario-id", "plc-1", "resume")
        target.unpause.assert_called_once_with()
        with self.assertRaisesRegex(DockerManagerError, "Expected one managed container"):
            manager.node_lifecycle("scenario-id", "other", "stop")
        target.stop.assert_not_called()

    def test_node_restart_waits_for_recovery_and_reports_failed_health(self) -> None:
        client = Mock()
        target = Mock()
        target.labels = {"anvil.node_id": "plc-1"}
        target.name = "anvil-plc-1"
        target.status = "running"
        target.attrs = {"State": {"Health": {"Status": "healthy"}}}
        client.containers.list.return_value = [target]
        manager = DockerManager(client=client)

        with patch.object(manager, "_wait_until_recovered") as wait_recovered:
            result = manager.node_lifecycle("scenario-id", "plc-1", "restart")
        target.restart.assert_called_once_with(timeout=5)
        wait_recovered.assert_called_once_with(target)
        self.assertEqual(result["status"], "running")

        with patch.object(manager, "_wait_until_recovered", side_effect=DockerManagerError("healthcheck failed")):
            with self.assertRaisesRegex(DockerManagerError, "healthcheck failed"):
                manager.node_lifecycle("scenario-id", "plc-1", "restart")
        self.assertEqual(target.restart.call_count, 2)

    def test_scenario_restart_restarts_every_planned_container_and_waits_for_health(self) -> None:
        client = Mock()
        first, second = Mock(), Mock()
        first.labels = {"anvil.node_id": "plc-1"}
        second.labels = {"anvil.node_id": "modbus-1"}
        first.status = second.status = "running"
        first.attrs = second.attrs = {"State": {"Health": {"Status": "healthy"}}}
        client.containers.list.return_value = [second, first]
        runtime = [
            {"node_id": "plc-1", "device_type": "plc-s7"},
            {"node_id": "modbus-1", "device_type": "modbus-tcp"},
            {"node_id": "switch-1", "device_type": "ethernet-switch"},
        ]
        manager = DockerManager(client=client)
        with patch.object(manager, "_wait_until_recovered") as wait_recovered:
            result = manager.restart_scenario("scenario-id", runtime)
        first.restart.assert_called_once_with(timeout=5)
        second.restart.assert_called_once_with(timeout=5)
        self.assertEqual(wait_recovered.call_count, 2)
        self.assertEqual(result["status"], "running")

    def test_scenario_restart_refuses_to_restart_a_partial_container_set(self) -> None:
        client = Mock()
        only = Mock()
        only.labels = {"anvil.node_id": "plc-1"}
        only.status = "running"
        only.attrs = {"State": {"Health": {"Status": "healthy"}}}
        client.containers.list.return_value = [only]
        manager = DockerManager(client=client)
        runtime = [
            {"node_id": "plc-1", "device_type": "plc-s7"},
            {"node_id": "modbus-1", "device_type": "modbus-tcp"},
        ]
        with self.assertRaisesRegex(DockerManagerError, "missing: modbus-1"):
            manager.restart_scenario("scenario-id", runtime)
        only.restart.assert_not_called()

    def test_scenario_restart_stops_after_a_container_fails_healthcheck(self) -> None:
        client = Mock()
        first, second = Mock(), Mock()
        first.labels = {"anvil.node_id": "plc-1"}
        second.labels = {"anvil.node_id": "modbus-1"}
        first.attrs = second.attrs = {"State": {"Health": {"Status": "healthy"}}}
        client.containers.list.return_value = [first, second]
        manager = DockerManager(client=client)
        with patch.object(manager, "_wait_until_recovered", side_effect=DockerManagerError("healthcheck failed")):
            with self.assertRaisesRegex(DockerManagerError, "healthcheck failed"):
                manager.restart_scenario("scenario-id", [
                    {"node_id": "plc-1", "device_type": "plc-s7"},
                    {"node_id": "modbus-1", "device_type": "modbus-tcp"},
                ])
        # node_id ordering places modbus-1 before plc-1.
        second.restart.assert_called_once_with(timeout=5)
        first.restart.assert_not_called()

    def test_recreate_node_uses_only_its_managed_zone_network_and_waits_for_health(self) -> None:
        client = Mock()
        client.volumes.get.side_effect = NotFound("missing")
        network = Mock()
        network.name = "anvil-scenario-control"
        network.attrs = {"Labels": {"anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.zone_id": "control"}}
        client.networks.list.return_value = [network]
        client.containers.list.return_value = []
        recreated = Mock()
        recreated.labels = {"anvil.node_id": "plc-1"}
        recreated.name = "anvil-scenario-plc-1"
        recreated.status = "running"
        recreated.attrs = {"State": {}}
        client.containers.create.return_value = recreated
        client.api.create_endpoint_config.return_value = {"IPAMConfig": {"IPv4Address": "172.30.10.10"}}
        manager = DockerManager(client=client)
        runtime_device = {
            "node_id": "plc-1", "hostname": "control-plc", "device_type": "plc-s7",
            "name": "PLC", "ip": "172.30.10.10", "zone_id": "control",
            "zone_subnet": "172.30.10.0/24", "zoned": True, "network_mode": None,
            "config": {"plc_id": "plc-1", "rack": 0, "slot": 1, "db_size": 32, "components": []},
        }

        with patch.object(manager, "_wait_until_healthy") as wait_healthy:
            manager.recreate_node("scenario-id", runtime_device)

        recreated.start.assert_called_once_with()
        wait_healthy.assert_called_once_with(recreated)
        created_config = client.containers.create.call_args.kwargs
        self.assertEqual(created_config["network"], network.name)
        self.assertEqual(created_config["networking_config"], {network.name: client.api.create_endpoint_config.return_value})
        self.assertEqual(created_config["labels"]["anvil.scenario_id"], "scenario-id")
        self.assertEqual(created_config["labels"]["anvil.node_id"], "plc-1")

    def test_recreate_node_refuses_to_create_a_scenario_network(self) -> None:
        client = Mock()
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        manager = DockerManager(client=client)
        runtime_device = {"node_id": "plc-1", "device_type": "plc-s7", "zone_id": "control",
                          "network_mode": None, "config": {"components": []}}

        with self.assertRaisesRegex(DockerManagerError, "network.*missing"):
            manager.recreate_node("scenario-id", runtime_device)
        client.containers.create.assert_not_called()

    def test_recovery_does_not_repeat_a_completed_recreate(self) -> None:
        client = Mock()
        existing = Mock()
        existing.labels = {
            "anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.node_id": "plc-1",
        }
        client.containers.list.return_value = [existing]
        manager = DockerManager(client=client)
        observed = {"scenario_id": "scenario-id", "status": "running", "containers": [{
            "node_id": "plc-1", "status": "running", "health_status": "healthy", "runtime_error": None,
        }]}
        runtime = {"node_id": "plc-1", "device_type": "plc-s7"}
        with patch.object(manager, "status", return_value=observed), patch.object(manager, "recreate_node") as recreate:
            result = manager.recover_node_operation("scenario-id", runtime, "recreate")
        self.assertIs(result, observed)
        existing.remove.assert_not_called()
        recreate.assert_not_called()

    def test_recovery_replaces_only_an_incomplete_managed_recreated_node(self) -> None:
        client = Mock()
        existing = Mock()
        existing.labels = {
            "anvil.managed": "true", "anvil.scenario_id": "scenario-id", "anvil.node_id": "plc-1",
        }
        client.containers.list.return_value = [existing]
        manager = DockerManager(client=client)
        observed = {"scenario_id": "scenario-id", "status": "deploying", "containers": [{
            "node_id": "plc-1", "status": "starting", "health_status": "starting", "runtime_error": None,
        }]}
        recovered = {"scenario_id": "scenario-id", "status": "running", "containers": []}
        runtime = {"node_id": "plc-1", "device_type": "plc-s7"}
        with patch.object(manager, "status", return_value=observed), patch.object(
            manager, "recreate_node", return_value=recovered,
        ) as recreate:
            result = manager.recover_node_operation("scenario-id", runtime, "recreate")
        existing.remove.assert_called_once_with(force=True)
        recreate.assert_called_once_with("scenario-id", runtime)
        self.assertIs(result, recovered)

    def test_restart_recovery_replays_when_docker_still_has_pre_action_state(self) -> None:
        client = Mock()
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.id = "container-1"
        container.status = "running"
        container.attrs = {"RestartCount": 4, "State": {"StartedAt": "before"}}
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)
        observed = {"scenario_id": "scenario-id", "status": "running", "containers": [{
            "node_id": "plc-1", "status": "running", "health_status": "healthy", "runtime_error": None,
        }]}
        runtime = {"node_id": "plc-1", "device_type": "plc-s7"}
        checkpoint = {"container_id": "container-1", "restart_count": 4, "started_at": "before"}
        with patch.object(manager, "status", return_value=observed), patch.object(
            manager, "node_lifecycle", return_value=observed,
        ) as restart:
            result = manager.recover_node_operation("scenario-id", runtime, "restart", checkpoint)
        restart.assert_called_once_with("scenario-id", "plc-1", "restart")
        self.assertIs(result, observed)

    def test_restart_recovery_does_not_repeat_a_restart_seen_in_docker(self) -> None:
        client = Mock()
        container = Mock()
        container.labels = {"anvil.node_id": "plc-1"}
        container.id = "container-1"
        container.status = "running"
        container.attrs = {"RestartCount": 5, "State": {"StartedAt": "after"}}
        client.containers.list.return_value = [container]
        manager = DockerManager(client=client)
        observed = {"scenario_id": "scenario-id", "status": "running", "containers": [{
            "node_id": "plc-1", "status": "running", "health_status": "healthy", "runtime_error": None,
        }]}
        runtime = {"node_id": "plc-1", "device_type": "plc-s7"}
        checkpoint = {"container_id": "container-1", "restart_count": 4, "started_at": "before"}
        with patch.object(manager, "status", return_value=observed), patch.object(
            manager, "node_lifecycle",
        ) as restart:
            result = manager.recover_node_operation("scenario-id", runtime, "restart", checkpoint)
        restart.assert_not_called()
        self.assertIs(result, observed)

    def test_scenario_pause_and_resume_apply_to_every_container(self) -> None:
        client = Mock()
        containers = []
        for node_id in ("plc-1", "hmi-1"):
            container = Mock()
            container.labels = {"anvil.node_id": node_id}
            container.name = node_id
            container.status = "running"
            container.attrs = {"State": {}}
            container.pause.side_effect = lambda item=container: setattr(item, "status", "paused")
            container.unpause.side_effect = lambda item=container: setattr(item, "status", "running")
            containers.append(container)
        client.containers.list.return_value = containers
        manager = DockerManager(client=client)

        paused = manager.pause_scenario("scenario-id")
        self.assertEqual(paused["status"], "paused")
        self.assertTrue(all(item.pause.called for item in containers))
        resumed = manager.resume_scenario("scenario-id")
        self.assertEqual(resumed["status"], "running")
        self.assertTrue(all(item.unpause.called for item in containers))

    def test_pause_failure_rolls_back_already_paused_nodes(self) -> None:
        client = Mock()
        first, second = Mock(), Mock()
        for container, status in ((first, "running"), (second, "running")):
            container.labels = {"anvil.node_id": "plc"}
            container.status = status
            container.attrs = {"State": {}}
        first.pause.side_effect = lambda: setattr(first, "status", "paused")
        second.pause.side_effect = APIError("pause failed")
        first.unpause.side_effect = lambda: setattr(first, "status", "running")
        client.containers.list.return_value = [first, second]

        with self.assertRaisesRegex(DockerManagerError, "pause failed"):
            DockerManager(client=client).pause_scenario("scenario-id")
        first.unpause.assert_called_once_with()

    def test_cleanup_only_removes_unknown_anvil_resources(self) -> None:
        client = Mock()
        client.ping.return_value = True
        known = Mock(labels={"anvil.scenario_id": "known"})
        orphan = Mock(labels={"anvil.scenario_id": "orphan"})
        known_network = Mock(attrs={"Labels": {"anvil.scenario_id": "known"}})
        orphan_network = Mock(attrs={"Labels": {"anvil.scenario_id": "orphan"}})
        orphan.name = "orphan-container"
        orphan.remove.side_effect = APIError("container removal failed")
        client.containers.list.return_value = [known, orphan]
        client.networks.list.return_value = [known_network, orphan_network]
        client.volumes.list.return_value = []
        manager = DockerManager(client=client)

        result = manager.cleanup_orphans({"known"})

        known.remove.assert_not_called()
        known_network.remove.assert_not_called()
        orphan.remove.assert_called_once_with(force=True)
        orphan_network.remove.assert_called_once_with()
        self.assertEqual(result, {
            "containers": 0,
            "networks": 1,
            "volumes": 0,
            "errors": ["container 'orphan-container': container removal failed"],
        })

    def test_orphan_cleanup_removes_only_exactly_owned_scada_history_volumes(self) -> None:
        from services.docker_manager import _scada_history_volume_name

        known_scenario = "12345678-1234-4234-8234-123456789abc"
        orphan_scenario = "22345678-1234-4234-8234-123456789abc"
        known_name = _scada_history_volume_name(known_scenario, "scada")
        orphan_name = _scada_history_volume_name(orphan_scenario, "scada")
        known = Mock(attrs={"Labels": {"anvil.managed": "true", "anvil.purpose": "scada-history",
                                       "anvil.scenario_id": known_scenario, "anvil.node_id": "scada",
                                       "anvil.volume_name": known_name}})
        known.name = known_name
        orphan = Mock(attrs={"Labels": {"anvil.managed": "true", "anvil.purpose": "scada-history",
                                        "anvil.scenario_id": orphan_scenario, "anvil.node_id": "scada",
                                        "anvil.volume_name": orphan_name}})
        orphan.name = orphan_name
        malformed = Mock(attrs={"Labels": {"anvil.managed": "true", "anvil.purpose": "scada-history",
                                           "anvil.scenario_id": orphan_scenario, "anvil.node_id": "other",
                                           "anvil.volume_name": orphan_name}})
        malformed.name = orphan_name
        client = Mock()
        client.containers.list.return_value = []
        client.networks.list.return_value = []
        client.volumes.list.return_value = [known, orphan, malformed]
        result = DockerManager(client=client).cleanup_orphans({known_scenario})
        known.remove.assert_not_called()
        orphan.remove.assert_called_once_with(force=False)
        malformed.remove.assert_not_called()
        self.assertEqual(result["volumes"], 1)


if __name__ == "__main__":
    unittest.main()
