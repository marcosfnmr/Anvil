from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402


class FrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client_context = TestClient(create_app(initialize_database=False))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    def test_root_serves_scenario_builder(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("OT SCENARIO BUILDER", response.text)
        self.assertIn("event-log", response.text)

    def test_frontend_modules_are_served(self) -> None:
        app_response = self.client.get("/app.js")
        events_response = self.client.get("/events.js")
        styles_response = self.client.get("/events.css")

        self.assertEqual(app_response.status_code, 200)
        self.assertIn("javascript", app_response.headers["content-type"])
        self.assertIn("deployScenario", app_response.text)

        self.assertEqual(events_response.status_code, 200)
        self.assertIn("javascript", events_response.headers["content-type"])
        self.assertIn("ScenarioEventStream", events_response.text)

        self.assertEqual(styles_response.status_code, 200)
        self.assertIn("text/css", styles_response.headers["content-type"])
        self.assertIn("event-connection", styles_response.text)

    def test_api_routes_take_priority_over_static_mount(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
