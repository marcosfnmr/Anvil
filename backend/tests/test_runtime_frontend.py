from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402


class RuntimeFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = TestClient(create_app(initialize_database=False))
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)

    def test_runtime_panel_and_modules_are_served(self) -> None:
        root = self.client.get("/")
        logs = self.client.get("/logs.js")
        runtime = self.client.get("/runtime.js")
        canvas = self.client.get("/canvas.js")
        styles = self.client.get("/runtime.css")
        self.assertIn('id="runtime-panel"', root.text)
        self.assertIn('id="io-map"', root.text)
        self.assertIn('id="signal-history"', root.text)
        self.assertIn('id="runtime-protocol"', root.text)
        self.assertIn('id="runtime-logs"', root.text)
        self.assertIn('id="runtime-audit"', root.text)
        self.assertIn('data-runtime-tab="audit"', root.text)
        self.assertIn('data-runtime-tab="exercise"', root.text)
        self.assertIn('data-evidence-export', root.text)
        self.assertIn('data-evidence-include-capture', root.text)
        self.assertIn('data-capture-scope', root.text)
        self.assertIn('data-capture-zone', root.text)
        self.assertIn('value="link">Enlace entre dos dispositivos', root.text)
        self.assertIn('data-capture-link-source', root.text)
        self.assertIn('data-capture-link-target', root.text)
        self.assertIn("Al incluirla, se detendrá y consumirá", root.text)
        self.assertIn("data-exercise-definition", root.text)
        self.assertIn("data-exercise-start", root.text)
        self.assertIn('data-runtime-tab="protocol"', root.text)
        self.assertEqual(logs.status_code, 200)
        self.assertIn("ScenarioLogStream", logs.text)
        self.assertEqual(runtime.status_code, 200)
        self.assertIn("RuntimePanel", runtime.text)
        self.assertIn("data-write-address", runtime.text)
        self.assertIn("audit-events/export", runtime.text)
        self.assertIn("/exercises/${this.exerciseRun.id}/start", runtime.text)
        self.assertIn("Puntuación ${this.exerciseRun.score}/100", runtime.text)
        self.assertIn("this.exerciseRun.passed", runtime.text)
        self.assertIn("evidence-bundle.zip", runtime.text)
        self.assertIn("this.capabilities.evidenceExport", runtime.text)
        self.assertIn("link_source_node_id", runtime.text)
        self.assertIn('X-Anvil-Manifest-SHA256', runtime.text)
        self.assertIn('guárdalo aparte', runtime.text)
        root = self.client.get("/")
        self.assertIn('id="import-scenario-button"', root.text)
        self.assertIn('id="export-scenario-button"', root.text)
        self.assertIn('id="clone-scenario-button"', root.text)
        nodes = self.client.get("/nodes/modbus-tcp.js")
        inspector = self.client.get("/inspector.js")
        self.assertIn("external_access", nodes.text)
        self.assertIn("Habilitar acceso desde el host/red", inspector.text)
        self.assertIn("proxy de ingreso", inspector.text)
        self.assertIn('...(!isRtu ? [', inspector.text)
        self.assertIn("El túnel RTU solo está disponible dentro de la red Docker del escenario", inspector.text)
        self.assertIn("renderNetworkServices", inspector.text)
        self.assertIn("Servicios y puertos", inspector.text)
        self.assertIn("Inventario del último guardado", inspector.text)
        self.assertIn("scenarioApi.services", self.client.get("/app.js").text)
        self.assertIn("services: (scenarioId)", self.client.get("/api.js").text)
        discrete = self.client.get("/components/discrete.js")
        self.assertEqual(discrete.status_code, 200)
        self.assertIn('"digital_input"', discrete.text)
        self.assertIn('"motor"', discrete.text)
        virtual_nodes = self.client.get("/nodes/virtual.js")
        self.assertEqual(virtual_nodes.status_code, 200)
        for type_name in ("hmi", "gateway", "firewall", "attacker-station", "engineering-station"):
            self.assertIn(type_name, virtual_nodes.text)
        self.assertIn("Lee valores de PLC S7 y Modbus configurados", virtual_nodes.text)
        self.assertIn("Host de ingeniería con acceso opcional", virtual_nodes.text)
        self.assertIn("engineering_access_enabled", inspector.text)
        self.assertIn("data-hmi-read", inspector.text)
        app = self.client.get("/app.js")
        self.assertIn("/hmi/", app.text)
        self.assertIn("syncHmiConsole", app.text)
        workspace = self.client.get("/workspace.js")
        self.assertEqual(workspace.status_code, 200)
        self.assertIn('data-hmi-console-values', workspace.text)
        self.assertIn('data-hmi-console-device', workspace.text)
        self.assertIn('data-hmi-configure', workspace.text)
        synoptic = self.client.get("/hmi-synoptic.js")
        self.assertEqual(synoptic.status_code, 200)
        self.assertIn("buildHmiSynoptic", synoptic.text)
        self.assertIn("selectedTargets", synoptic.text)
        catalog = self.client.get("/api/scenarios/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertIn("firewall", {item["type"] for item in catalog.json()})
        self.assertEqual(canvas.status_code, 200)
        self.assertIn("exportTopology", canvas.text)
        self.assertIn("loadTopology", canvas.text)
        self.assertEqual(styles.status_code, 200)
        self.assertIn("runtime-panel", styles.text)


if __name__ == "__main__":
    unittest.main()
