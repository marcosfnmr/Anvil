from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
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
        self.assertIn('id="restart-button"', response.text)

    def test_scenario_restart_is_available_in_api_and_operator_controls(self) -> None:
        app = self.client.get("/app.js").text
        api = self.client.get("/api.js").text
        self.assertIn("scenarioApi.restart(state.scenarioId)", app)
        self.assertIn('restart: (scenarioId) => request(`/scenarios/${scenarioId}/restart`', api)
        self.assertIn("estado volátil del proceso", app)
        self.assertIn("elements.restart.disabled = isBusy", app)

    def test_hmi_live_refresh_preserves_cards_controls_and_synoptic(self) -> None:
        app = self.client.get("/app.js").text
        live_view = self.client.get("/hmi-live-view.js").text
        workspace = self.client.get("/workspace.js").text
        synoptic = self.client.get("/hmi-synoptic.js").text
        self.assertIn('from "./hmi-live-view.js?v=hmi-map-compact-ux-20261010"', app)
        self.assertIn("export function updateHmiLiveView", live_view)
        self.assertIn("function syncHmiChildren", live_view)
        self.assertIn("if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;", live_view)
        self.assertIn("editedHmiInputs.has(current)", live_view)
        self.assertIn('if (template.content.querySelector(".hmi-synoptic")) updateHmiSynoptic', live_view)
        self.assertIn('data-hmi-live-key="point:', app)
        self.assertIn('data-hmi-live-key="alarm:', app)
        self.assertIn('data-hmi-object="${escapeHtml(field.object_id)}"', synoptic)
        self.assertIn('aria-live="off"></div></section>`)', workspace)
        self.assertNotIn('output.textContent = "Leyendo…"', app)
        self.assertNotIn('output.textContent = ""', app)
        self.assertIn("Keep the last successful snapshot visible", app)

    def test_hmi_live_view_module_is_served_as_javascript(self) -> None:
        response = self.client.get("/hmi-live-view.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertIn("markHmiInputEdited", response.text)

    def test_map_arrows_attach_to_the_nearest_object_edges(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "static/process-map.js").read_text()
        self.assertIn("const nearestPorts = (from, to)", source)
        self.assertIn("const linkPath = ({ start, end, startNormal, endNormal })", source)
        self.assertIn("const distance = dx * dx + dy * dy", source)
        self.assertIn("this.resize.observe(endpoint)", source)
        self.assertGreaterEqual(source.count("linkPath(ports)"), 3)

    def test_field_map_arrows_choose_nearest_edges_and_curve_from_them(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "static/field-editor.js").read_text()
        self.assertIn("const sides = (r, other)", source)
        self.assertIn("if(!best||distance<best.distance)", source)
        self.assertIn("const startNormal=", source)
        self.assertIn("const endNormal=", source)
        self.assertIn("C${c1.x},${c1.y} ${c2.x},${c2.y}", source)

    def test_scada_live_refresh_keeps_current_cards_and_does_not_replace_panel(self) -> None:
        app = self.client.get("/app.js").text
        live_view = self.client.get("/hmi-live-view.js").text
        self.assertIn('updateHmiLiveView(output, gridMarkup)', app)
        self.assertIn('data-hmi-live-key="scada-point:', app)
        self.assertIn('data-hmi-live-key="scada-alarm:', app)
        self.assertIn('input[data-hmi-value], input[data-scada-value]', live_view)
        self.assertNotIn('output.textContent = isLive ? "Consultando adquisición', app)
        self.assertIn('setLiveMessage(output, `Error en adquisición SCADA; se conserva la última lectura:', app)

    def test_exercise_editor_offers_fc01_fc02_boolean_response_rewrites(self) -> None:
        editor = self.client.get("/exercise-editor.js").text
        runtime = self.client.get("/runtime.js").text
        self.assertIn('"FC01 · sustituir lectura de coils"', editor)
        self.assertIn('"FC02 · sustituir lectura de discrete inputs"', editor)
        self.assertIn('discrete_input', editor)
        self.assertIn('const coilRead = [1, 2].includes(functionCode)', editor)
        self.assertIn('const isCoil = [1, 2, 5, 15].includes(item.rules[0].function_code)', editor)
        self.assertIn('rf38-fc23-write-20261013', runtime)

    def test_compact_editor_and_anvil_logo_are_included(self) -> None:
        page = self.client.get("/").text
        style = (Path(__file__).resolve().parents[1] / "static/workspace.css").read_text()
        favicon = self.client.get("/favicon.svg").text
        self.assertIn('class="brand-mark" src="/favicon.svg?', page)
        self.assertIn(".page-editor .page-heading p { display: none; }", style)
        self.assertIn(".page-editor .canvas-toolbar", style)
        self.assertIn("linearGradient id=\"anvil-steel\"", favicon)
        self.assertIn("left horn, broad face, waist, and solid base", favicon)
        self.assertIn("stop-color=\"#fff0cf\"", favicon)

    def test_legacy_field_migration_is_exposed_as_a_separate_copy_action(self) -> None:
        page = self.client.get("/")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn('id="migrate-field-copy-button"', page.text)
        self.assertIn("migrateLegacyFieldsToCopy", app.text)
        self.assertIn("el escenario original no cambiará", app.text)
        self.assertIn("field-migration-copy", api.text)

    def test_runtime_canvas_displays_container_resource_metrics(self) -> None:
        canvas = BACKEND_DIR / "static" / "canvas.js"
        source = canvas.read_text(encoding="utf-8")
        self.assertIn("node-runtime-metrics", source)
        self.assertIn("CPU ${cpu} · Mem ${memory} · Proc. ${pids}", source)
        self.assertIn("resource_metrics_sampled_at", source)
        self.assertIn('muestra hace ${sampleAge ?? "—"}', source)
        self.assertIn("métricas no disponibles", source)
        app_source = (BACKEND_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('canvas.js?v=runtime-metrics-quality-1', app_source)

    def test_network_topology_editor_explains_switch_segment_and_limits(self) -> None:
        process_map = self.client.get("/process-map.js")
        canvas = self.client.get("/canvas.js")
        app = self.client.get("/app.js")
        self.assertIn("Enlace Ethernet", process_map.text)
        self.assertIn("bridge Docker", process_map.text)
        self.assertIn("se validan sus puertos", process_map.text)
        self.assertIn("No hay VLAN ni filtrado por puerto", process_map.text)
        self.assertIn("addNetworkConnection", process_map.text)
        self.assertIn("networkConnections", canvas.text)
        self.assertIn("network_connections", app.text)
        self.assertIn("Ruta TCP entre zonas", process_map.text)
        self.assertIn("Latencia por sentido (ms)", process_map.text)
        self.assertIn("data-route-latency", process_map.text)
        self.assertIn("data-route-toggle", process_map.text)
        self.assertIn("bloqueada · proxy no desplegado", process_map.text)
        self.assertIn("los equipos de una misma zona comparten la red Docker", process_map.text)
        self.assertIn("network-routes-change", app.text)
        self.assertIn("networkRoutes:", self.client.get("/api.js").text)

    def test_workstation_egress_rules_are_editable_and_saved_in_network_config(self) -> None:
        process_map = self.client.get("/process-map.js")
        app = self.client.get("/app.js")
        self.assertIn("Salida a Internet controlada", process_map.text)
        self.assertIn("data-egress-create", process_map.text)
        self.assertIn("data-egress-remove", process_map.text)
        self.assertIn("network-egress-rules-change", app.text)
        self.assertIn("egress_rules: state.egressRules", app.text)

    def test_s7_modbus_inspector_exports_and_imports_device_profiles(self) -> None:
        inspector = self.client.get("/inspector.js")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn("Exportar perfil JSON", inspector.text)
        self.assertIn("Importar perfil JSON", inspector.text)
        self.assertIn("resolved_mapping", Path(__file__).resolve().parents[1].joinpath("services/device_profiles.py").read_text())
        self.assertIn("anvil-device-profile", Path(__file__).resolve().parents[1].joinpath("models/device_profile.py").read_text())
        self.assertIn("exportDeviceProfile", api.text)
        self.assertIn("importDeviceProfile", api.text)
        self.assertIn("importDeviceProfileFromUi", app.text)

    def test_s7_and_modbus_inspectors_allow_explicit_external_bind_ip(self) -> None:
        inspector = self.client.get("/inspector.js")
        self.assertIn('path: "external_host_ip"', inspector.text)
        self.assertIn("127.0.0.1 para local", inspector.text)
        self.assertIn("Habilitar acceso desde el host/red", inspector.text)

    def test_s7_inspector_exposes_bounded_protocol_response_delay(self):
        inspector = self.client.get("/inspector.js")
        node = self.client.get("/nodes/plc-s7.js")
        self.assertIn('config.response_delay_ms', inspector.text)
        self.assertIn('Retardo de respuesta S7 (ms)', inspector.text)
        self.assertIn('Fallo inducido S7', inspector.text)
        self.assertIn('value: "timeout", label: "Omitir respuesta de lectura/escritura (timeout)"', inspector.text)
        self.assertIn('response_delay_ms: 0', node.text)

    def test_inspectors_expose_reproducible_jitter_distributions(self):
        inspector = self.client.get("/inspector.js")
        plc = self.client.get("/nodes/plc-s7.js")
        modbus = self.client.get("/nodes/modbus-tcp.js")
        self.assertIn('label: "Distribución del jitter S7"', inspector.text)
        self.assertIn('label: "Distribución del jitter"', inspector.text)
        self.assertIn('value: "uniform", label: "Uniforme · 0 a máximo"', inspector.text)
        self.assertIn('value: "normal", label: "Normal · centrada en la mitad"', inspector.text)
        self.assertIn('response_jitter_seed: 0', plc.text)
        self.assertIn('response_jitter_seed: 0', modbus.text)

    def test_scada_alarm_delay_is_configurable_and_visible_while_pending(self):
        inspector = self.client.get("/inspector.js")
        app = self.client.get("/app.js")
        self.assertIn('data-scada-rule-field="delay_ms"', inspector.text)
        self.assertIn('Retardo para activar (ms)', inspector.text)
        self.assertIn('alarm.pending ? "Condición pendiente"', app.text)
        self.assertIn('alarm.pending ? "Esperando retardo"', app.text)

    def test_runtime_exposes_capture_controls_and_download_flow(self) -> None:
        page = self.client.get("/")
        runtime = self.client.get("/runtime.js")
        self.assertIn('data-runtime-tab="capture"', page.text)
        self.assertIn("data-capture-start", page.text)
        self.assertIn("data-capture-export", page.text)
        self.assertIn("data-capture-scope", page.text)
        self.assertIn("data-capture-zone", page.text)
        self.assertIn("bridge Docker interno", page.text)
        self.assertIn("data-capture-scope", runtime.text)
        self.assertIn("/captures/${captureId}/pcap", runtime.text)

    def test_runtime_protocol_timeline_parses_process_image_area_events(self):
        runtime = self.client.get("/runtime.js")
        self.assertIn("(DB(\\d+)|([MIQ])) bytes", runtime.text)
        self.assertIn('const area = dbRaw ? "DB" : areaRaw', runtime.text)
        self.assertIn('`${area}.BYTE${offset}`', runtime.text)

    def test_runtime_exposes_exercise_timeline_controls(self) -> None:
        page = self.client.get("/")
        runtime = self.client.get("/runtime.js")
        editor = self.client.get("/exercise-editor.js")
        self.assertIn('"network_route"', editor.text)
        self.assertIn("Interrumpir/restaurar una ruta de red", editor.text)
        self.assertIn("scenario.network_routes || []", runtime.text)
        self.assertIn('data-runtime-tab="exercise"', page.text)
        for token in ("data-exercise-definition", "data-exercise-create", "data-exercise-start", "data-exercise-cancel"):
            self.assertIn(token, page.text)
        for token in ("data-exercise-builder", "data-exercise-add-event", "data-exercise-add-objective", "Editar JSON avanzado"):
            self.assertIn(token, page.text)
        self.assertIn("/api/scenarios/${scenarioId}/exercises", runtime.text)
        self.assertEqual(editor.status_code, 200)
        self.assertIn("class ExerciseEditor", editor.text)
        self.assertIn("point_value", editor.text)
        self.assertIn("field_control", editor.text)
        self.assertIn('"protocol_write"', editor.text)
        self.assertIn("Escritura protocolaria desde participante", editor.text)
        self.assertIn('"16", "FC16 · alterar escritura de varios holding registers"', editor.text)
        self.assertIn('"15", "FC15 · alterar escritura de varias coils"', editor.text)
        self.assertIn('"5", "FC05 · alterar escritura de una coil"', editor.text)
        self.assertIn('"22", "FC22 · forzar valor en Mask Write Register"', editor.text)
        self.assertIn('"23", "FC23 · sustituir lectura de Read/Write Multiple Registers"', editor.text)
        self.assertIn("El gateway sustituye solo el rango de lectura de la respuesta FC23", editor.text)
        self.assertIn('data-exercise-event-field="interception_function"', editor.text)
        self.assertIn('data-exercise-event-field="interception_phase"', editor.text)
        self.assertIn("Petición de escritura", editor.text)
        self.assertIn('point.access === "read_write"', editor.text)
        self.assertIn("puede modificar el proceso conectado", editor.text)
        self.assertIn("participant_access_enabled", editor.text)
        self.assertIn("this.protocolRoute", editor.text)
        self.assertIn('"field_trend"', editor.text)
        self.assertIn("Ventana de muestreo", editor.text)
        self.assertIn('"field_sequence"', editor.text)
        self.assertIn("Secuencia de condiciones del proceso", editor.text)
        self.assertIn('"protocol_write_policy"', editor.text)
        self.assertIn("IP de pares permitidas", editor.text)
        self.assertIn("No autentica ni identifica", editor.text)
        self.assertIn("data-exercise-add-step", editor.text)
        self.assertIn('"fault"', editor.text)
        self.assertIn("isBooleanFieldProperty", editor.text)
        self.assertIn("exercise-sequence-step", self.client.get("/runtime.css").text)
        self.assertIn("runtime.css?v=field-sequence-1", page.text)
        self.assertIn('"audit_sequence"', editor.text)
        self.assertIn("Secuencia ordenada de eventos", editor.text)
        self.assertIn('data-exercise-objective-field="event_types"', editor.text)
        self.assertIn('data-exercise-objective-field="expectation"', editor.text)
        self.assertIn("No debe ocurrir", editor.text)
        self.assertIn('data-exercise-objective-field="fail_fast"', editor.text)
        self.assertIn("Finalizar inmediatamente si ocurre", editor.text)
        runtime = self.client.get("/runtime.js")
        self.assertIn("Ejercicio suspendido por la pausa del escenario", runtime.text)
        self.assertIn('["running", "paused"].includes(status)', runtime.text)

    def test_frontend_modules_are_served(self) -> None:
        app_response = self.client.get("/app.js")
        events_response = self.client.get("/events.js")
        styles_response = self.client.get("/events.css")

        self.assertEqual(app_response.status_code, 200)
        self.assertIn("javascript", app_response.headers["content-type"])
        self.assertIn("deployScenario", app_response.text)
        self.assertIn("Sondas de conectividad correctas", app_response.text)
        self.assertIn("runtime_route_unreachable", app_response.text)

        self.assertEqual(events_response.status_code, 200)
        self.assertIn("javascript", events_response.headers["content-type"])
        self.assertIn("ScenarioEventStream", events_response.text)

        self.assertEqual(styles_response.status_code, 200)
        self.assertIn("text/css", styles_response.headers["content-type"])
        self.assertIn("event-connection", styles_response.text)

    def test_field_editor_uses_the_shared_remote_input_module_instance(self) -> None:
        field_editor = self.client.get("/field-editor.js")
        self.assertEqual(field_editor.status_code, 200)
        self.assertIn("./components/remote_input.js?v=field-live-3", field_editor.text)
        self.assertNotIn("remote_input.js?v=field-route-1", field_editor.text)

    def test_plc_logic_editor_assets_are_served(self) -> None:
        inspector = self.client.get("/inspector.js")
        plc_node = self.client.get("/nodes/plc-s7.js")
        self.assertEqual(inspector.status_code, 200)
        self.assertIn("Programa PLC · bloques ON/OFF", inspector.text)
        self.assertIn("data-plc-logic-add", inspector.text)
        self.assertIn("Temporizadores TON", inspector.text)
        self.assertIn('data-plc-timer-field="mode"', inspector.text)
        self.assertIn("TOF · retardo al desactivar", inspector.text)
        self.assertIn("Rung BOOL · contactos y AND / OR / XOR", inspector.text)
        self.assertIn("Contacto normalmente cerrado", inspector.text)
        self.assertIn('data-plc-boolean-field="input_b_negated"', inspector.text)
        self.assertIn("Memorias SR (reset prioritario)", inspector.text)
        self.assertIn("data-plc-latch-add", inspector.text)
        self.assertIn("Contadores CTU", inspector.text)
        self.assertIn("data-plc-counter-add", inspector.text)
        self.assertIn("data-plc-counter-field=\"preset\"", inspector.text)
        self.assertIn("Secuenciadores BOOL", inspector.text)
        self.assertIn("data-plc-sequence-add", inspector.text)
        self.assertIn("data-plc-sequence-step-add", inspector.text)
        self.assertIn("data-plc-boolean-add", inspector.text)
        self.assertIn("boolean_rules", plc_node.text)
        self.assertIn("data-plc-timer-add", inspector.text)
        self.assertIn("preset_ms", inspector.text)
        self.assertEqual(plc_node.status_code, 200)
        self.assertIn("control_rules", plc_node.text)
        self.assertIn("counters: []", plc_node.text)
        self.assertIn("sequences: []", plc_node.text)

    def test_scada_view_uses_aggregated_history_for_numeric_trends(self) -> None:
        app = self.client.get("/app.js")
        self.assertEqual(app.status_code, 200)
        self.assertIn("/history/aggregate?bucket_seconds=5", app.text)
        self.assertIn("scadaTrend(series, history.samples, point, aggregate)", app.text)
        self.assertIn("snapshot.dropped_samples", app.text)
        self.assertIn("muestras por alcanzar su capacidad", app.text)
        inspector = self.client.get("/inspector.js")
        self.assertIn("config.durable_history_capacity", inspector.text)
        self.assertIn("config.durable_history_max_bytes", inspector.text)
        trend = self.client.get("/scada-trend.mjs")
        self.assertEqual(trend.status_code, 200)
        self.assertIn("bucket.good_samples", trend.text)
        self.assertIn("los huecos no se interpolan", trend.text)

    def test_fault_probability_controls_are_available_for_s7_and_modbus(self) -> None:
        inspector = self.client.get("/inspector.js")
        self.assertEqual(inspector.status_code, 200)
        self.assertIn("Probabilidad por solicitud (0–1)", inspector.text)
        self.assertIn("Probabilidad de timeout por solicitud (0–1)", inspector.text)
        self.assertIn("Semilla reproducible del fallo", inspector.text)

    def test_inspector_exposes_individual_node_lifecycle_controls(self) -> None:
        inspector = self.client.get("/inspector.js")
        api = self.client.get("/api.js")
        self.assertEqual(inspector.status_code, 200)
        for action in ("start", "stop", "restart", "pause", "resume"):
            self.assertIn(f'data-node-action="{action}"', inspector.text)
        self.assertIn("nodeLifecycle", api.text)
        self.assertIn('id="pause-button"', self.client.get("/").text)
        self.assertIn('id="resume-button"', self.client.get("/").text)
        self.assertIn("scenarioApi.pause", self.client.get("/app.js").text)
        self.assertIn('data-node-action="recreate"', inspector.text)
        self.assertIn('status === "missing"', inspector.text)
        self.assertIn("active && missing", inspector.text)
        self.assertIn("|| missing ? \"disabled\"", inspector.text)
        self.assertIn('runtime-missing .node-state', self.client.get("/style.css").text)

    def test_node_runtime_health_diagnostics_are_rendered_safely(self) -> None:
        inspector = self.client.get("/inspector.js")
        canvas = self.client.get("/canvas.js")
        self.assertIn("Healthcheck:", inspector.text)
        self.assertIn("runtime.health_error", inspector.text)
        self.assertIn("escapeHtml(runtime.health_error", inspector.text)
        self.assertIn('status === "missing"', inspector.text)
        self.assertIn('runtime && runtime.status !== "missing"', canvas.text)
        self.assertIn('indicator.setAttribute("aria-label", indicator.title)', canvas.text)

    def test_deploy_runs_preflight_and_confirms_warnings(self) -> None:
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn("scenarioApi.diagnostics(saved.id)", app.text)
        self.assertIn("¿Desplegar de todas formas?", app.text)
        self.assertIn("deployment_duration_seconds", app.text)
        self.assertIn("Despliegue completado en", app.text)
        self.assertIn('route.protocol !== "field-binding"', app.text)
        self.assertIn("binding.address", app.text)
        self.assertIn("binding.field_port", app.text)
        self.assertIn("diagnostics:", api.text)

    def test_scenario_template_library_is_available_from_ui_and_api(self) -> None:
        root = self.client.get("/")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn('id="templates-dialog"', root.text)
        self.assertIn('id="publish-template-form"', root.text)
        self.assertIn('id="edit-shared-template-form"', root.text)
        self.assertIn('id="shared-template-history-dialog"', root.text)
        self.assertIn("Las versiones anteriores no se sobrescriben", root.text)
        self.assertIn("data-open-templates", root.text)
        self.assertIn("data-instantiate-template", app.text)
        self.assertIn("data-edit-template", app.text)
        self.assertIn("data-template-history", app.text)
        self.assertIn("data-restore-template-revision", app.text)
        self.assertIn("expected_revision: Number(form.elements.expected_revision.value)", app.text)
        self.assertIn("se guardó una nueva versión", app.text)
        self.assertIn("scenarioApi.templates()", app.text)
        self.assertIn("scenarioApi.publishTemplate", app.text)
        self.assertIn("scenarioApi.deleteTemplate", app.text)
        self.assertIn("scenarioApi.updateTemplate", app.text)
        self.assertIn("scenarioApi.templateRevisions", app.text)
        self.assertIn("data-restore-template-revision", app.text)
        self.assertIn("const opened = await openScenario(scenario.id", app.text)
        self.assertIn("if (opened) showToast(`Escenario", app.text)
        self.assertIn("instantiateTemplate", api.text)

    def test_shared_component_library_is_available_from_ui_and_api(self) -> None:
        root = self.client.get("/")
        inspector = self.client.get("/inspector.js")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn('id="component-library-dialog"', root.text)
        self.assertIn('id="component-library-button"', root.text)
        self.assertIn('id="edit-component-preset-form"', root.text)
        self.assertIn('id="component-preset-history-dialog"', root.text)
        self.assertIn("data-share-component", inspector.text)
        self.assertIn("addSharedComponent", inspector.text)
        self.assertIn("scenarioApi.componentTemplates()", app.text)
        self.assertIn("scenarioApi.publishComponentTemplate", app.text)
        self.assertIn("data-edit-component-preset", app.text)
        self.assertIn("data-component-history", app.text)
        self.assertIn("data-restore-component-preset", app.text)
        self.assertIn("scenarioApi.updateComponentTemplate", app.text)
        self.assertIn("scenarioApi.componentTemplateRevisions", app.text)
        self.assertIn("scenarioApi.restoreComponentTemplateRevision", app.text)
        self.assertIn("componentTemplates:", api.text)

    def test_scenario_revision_history_is_available_from_ui_and_api(self) -> None:
        root = self.client.get("/")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn('id="history-dialog"', root.text)
        self.assertIn('id="history-button"', root.text)
        self.assertIn("data-restore-revision", app.text)
        self.assertIn("revisions: (scenarioId)", api.text)
        self.assertIn("restoreRevision", api.text)

    def test_modbus_behavior_fault_controls_are_exposed(self) -> None:
        inspector = self.client.get("/inspector.js")
        node = self.client.get("/nodes/modbus-tcp.js")
        self.assertIn("Retardo de respuesta (ms)", inspector.text)
        self.assertIn('value: "device_failure"', inspector.text)
        self.assertIn("response_delay_ms: 0", node.text)
        self.assertIn('fault_mode: "none"', node.text)

    def test_modbus_discrete_input_catalogued_as_read_only_point(self) -> None:
        component = (Path(__file__).resolve().parents[1] / "static/components/modbus.js").read_text()
        self.assertIn('registerComponentType("discrete_input"', component)
        self.assertIn('solo lectura', component)
        field_editor = (Path(__file__).resolve().parents[1] / "static/field-editor.js").read_text()
        self.assertIn("port.direction==='output'?'discrete_input':'coil'", field_editor)

    def test_modbus_register_range_fields_are_exposed_but_not_on_coils(self) -> None:
        inspector = self.client.get("/inspector.js")
        component = (Path(__file__).resolve().parents[1] / "static/components/modbus.js").read_text()
        self.assertIn('path: "min_value"', component)
        self.assertIn('path: "max_value"', component)
        self.assertIn('["coil", "discrete_input"].includes(component.type)', inspector.text)

    def test_inspector_preserves_options_for_select_device_fields(self) -> None:
        inspector = self.client.get("/inspector.js")
        self.assertIn('{ ...field, type: field.type || "number" }', inspector.text)

    def test_modbus_rtu_transport_settings_are_exposed(self) -> None:
        inspector = self.client.get("/inspector.js")
        node = self.client.get("/nodes/modbus-tcp.js")
        self.assertIn("serie virtual por túnel raw TCP", inspector.text)
        self.assertIn('path: "config.baudrate"', inspector.text)
        self.assertIn("pseudo-terminal 8N1", inspector.text)
        self.assertIn('transport: "tcp"', node.text)

    def test_inspector_exposes_a_validated_hostname_field(self) -> None:
        inspector = self.client.get("/inspector.js")
        self.assertIn('label: "Hostname DNS en su zona"', inspector.text)
        self.assertIn('data-device-field="${field.path}"', inspector.text)

    def test_field_inspector_exposes_explicit_cross_zone_signal_gateway(self) -> None:
        field_editor = self.client.get("/field-editor.js")
        self.assertEqual(field_editor.status_code, 200)
        self.assertIn("data-field-route", field_editor.text)
        self.assertIn("gateway HTTP", field_editor.text)
        self.assertIn("route-enabled", field_editor.text)

    def test_tank_inspector_exposes_live_leak_disturbance(self) -> None:
        field_editor = self.client.get("/field-editor.js")
        self.assertIn("leak_flow_lpm:0", field_editor.text)
        self.assertIn("Fuga (L/min)", field_editor.text)
        self.assertIn("state.leak_lpm", field_editor.text)
        self.assertIn("data-set-demand", field_editor.text)
        self.assertIn("data-set-leak", field_editor.text)
        self.assertIn("Demanda actual (L/min)", field_editor.text)
        self.assertIn("Fuga actual (L/min)", field_editor.text)
        self.assertIn("state.effective_demand_lpm", field_editor.text)

    def test_analog_sensor_noise_seed_is_editable_in_both_sensor_inspectors(self) -> None:
        field_editor = self.client.get("/field-editor.js")
        component = (Path(__file__).resolve().parents[1] / "static/components/analog_sensor.js").read_text()
        self.assertIn("simulationInput('seed'", field_editor.text)
        self.assertIn("simulationInput('noise'", field_editor.text)
        self.assertIn("Semilla reproducible", field_editor.text)
        self.assertIn('path: "simulation.seed"', component)
        self.assertIn("seed: 0", component)

    def test_tank_exposes_a_process_pressure_port_and_height_setting(self) -> None:
        field_editor = self.client.get("/field-editor.js")
        self.assertIn("Presión manométrica", field_editor.text)
        self.assertIn("height_m:2", field_editor.text)
        self.assertIn("Altura del depósito (m)", field_editor.text)
        self.assertIn("state.pressure_kpa", field_editor.text)

    def test_scada_can_write_only_points_marked_writable(self) -> None:
        app = self.client.get("/app.js")
        inspector = self.client.get("/inspector.js")
        self.assertIn('data-scada-write', app.text)
        self.assertIn("point.access === \"read_write\"", app.text)
        self.assertIn("onWriteScada", inspector.text)
        self.assertIn("/scada/${encodeURIComponent(nodeId)}/write", app.text)
        self.assertIn("Historial guardado", app.text)
        self.assertIn("history?limit=100", app.text)
        self.assertIn('access: "read"', app.text)

    def test_hmi_and_scada_render_real_setpoint_controls(self) -> None:
        app = self.client.get("/app.js")
        inspector = self.client.get("/inspector.js")
        channel = self.client.get("/components/io_channel.js")
        self.assertIn('data-hmi-value data-data-type="REAL"', app.text)
        self.assertIn('data-scada-value data-data-type="REAL"', app.text)
        self.assertIn("Nueva consigna", app.text)
        self.assertIn("validReal", inspector.text)
        self.assertIn("BOOL/REAL", channel.text)
        self.assertIn('Number.isFinite(item.min_value)', app.text)
        self.assertIn('Number.isFinite(point.max_value)', app.text)

    def test_hmi_can_show_and_acknowledge_alarms_from_same_zone_scada(self) -> None:
        app = self.client.get("/app.js")
        inspector = self.client.get("/inspector.js")
        self.assertIn("data-hmi-ack", app.text)
        self.assertIn("/hmi/${encodeURIComponent(nodeId)}/alarms/acknowledge", app.text)
        self.assertIn("data-hmi-alarm-source", inspector.text)
        self.assertIn("Alarmas mostradas desde SCADA", inspector.text)
        self.assertIn("Retardo para activar (ms)", inspector.text)
        self.assertIn("Condición pendiente", app.text)

    def test_s7_channel_inspector_offers_db_marker_input_and_output_areas(self):
        channel = self.client.get("/components/io_channel.js")
        signals = self.client.get("/components/remote_input.js")
        self.assertIn("Área S7", channel.text)
        self.assertIn("{value:'M',label:'M · marcas'}", channel.text)
        self.assertIn("{value:'I',label:'I · entradas'}", channel.text)
        self.assertIn("{value:'Q',label:'Q · salidas'}", channel.text)
        self.assertIn("Byte · vacío = automático", channel.text)
        self.assertIn("Bit BOOL · 0–7", channel.text)
        self.assertIn('field.path === "bit_offset"', self.client.get("/inspector.js").text)
        self.assertIn("area==='DB'?`DB${component.db}.DBX", signals.text)
        self.assertIn('path: "stale_behavior"', signals.text)
        self.assertIn('value: "force_off"', signals.text)

    def test_hmi_can_select_remote_zone_targets_and_enable_explicit_routes(self) -> None:
        inspector = self.client.get("/inspector.js")
        self.assertIn('const candidates = targets.filter(item => item.node_id !== this.data.node_id)', inspector.text)
        self.assertIn('data-scada-route', inspector.text)
        self.assertIn('Permitir ruta TCP S7/Modbus por gateway', inspector.text)

    def test_remote_s7_inspector_can_enable_peer_route_between_zones(self) -> None:
        inspector = self.client.get("/inspector.js")
        app = self.client.get("/app.js")
        process_map = self.client.get("/process-map.js")
        self.assertIn("data-s7-route", inspector.text)
        self.assertIn("Permitir enlace S7 al PLC remoto mediante gateway", inspector.text)
        self.assertIn('["scada", "hmi", "plc-s7"]', app.text)
        self.assertIn("S7 · ruta permitida", process_map.text)

    def test_field_editor_exposes_a_proportional_valve_with_s7_real_only_command(self) -> None:
        editor = self.client.get("/field-editor.js")
        channel = self.client.get("/components/io_channel.js")
        self.assertIn("proportional_valve", editor.text)
        self.assertIn("Consigna de apertura", editor.text)
        self.assertIn("holding register Modbus escalado", editor.text)
        self.assertIn("min_value", channel.text)
        self.assertIn("max_value", channel.text)

    def test_field_editor_exposes_frozen_sensor_fault_and_map_quality(self) -> None:
        editor = self.client.get("/field-editor.js")
        styles = self.client.get("/process-map.css")
        self.assertIn("Simular fallo: congelar última lectura", editor.text)
        self.assertIn("Restaurar sensor", editor.text)
        self.assertIn("FALLO: lectura congelada", editor.text)
        self.assertIn(".field-signal.fault path", styles.text)

    def test_field_editor_exposes_configurable_process_pipe(self) -> None:
        editor = self.client.get("/field-editor.js")
        self.assertIn("pipe:'Tubería'", editor.text)
        self.assertIn("max_flow_lpm", editor.text)
        self.assertIn("pressure_drop_kpa", editor.text)
        self.assertIn("max_head_kpa", editor.text)
        self.assertIn("Caudal máximo (L/min)", editor.text)

    def test_field_editor_exposes_variable_speed_drive_with_real_setpoint(self) -> None:
        editor = self.client.get("/field-editor.js")
        self.assertIn("Variador de frecuencia", editor.text)
        self.assertIn("variable_speed_drive", editor.text)
        self.assertIn("Consigna de velocidad", editor.text)
        self.assertIn("initial_speed_pct", editor.text)
        self.assertIn("Velocidad real", editor.text)
        self.assertIn("Caudal regulado", editor.text)
        self.assertIn("max_flow_lpm:100", editor.text)
        self.assertIn("this.canvas.getDevices().find(d=>d.node_id===nodeId)", editor.text)
        self.assertNotIn("if(port.data_type==='REAL') throw", editor.text)

    def test_frontend_refreshes_even_with_old_cache_validators(self) -> None:
        first = self.client.get("/app.js")
        response = self.client.get("/app.js", headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        root = self.client.get("/")
        self.assertEqual(root.headers["cache-control"], "no-store")
        self.assertIn("bootstrap.js?v=", root.text)

    def test_bootstrap_surfaces_startup_failures(self) -> None:
        response = self.client.get("/bootstrap.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Recargar interfaz", response.text)
        self.assertIn("anvil-startup-error", response.text)

    def test_scenario_access_management_is_available_from_the_editor(self) -> None:
        root = self.client.get("/")
        app = self.client.get("/app.js")
        api = self.client.get("/api.js")
        self.assertIn('id="share-scenario-button"', root.text)
        self.assertIn('id="scenario-access-dialog"', root.text)
        self.assertIn("scenarioApi.grantMember", app.text)
        self.assertIn("scenarioApi.removeMember", app.text)
        self.assertIn("grantMember:", api.text)

    def test_role_aware_controls_match_the_api_permission_matrix(self) -> None:
        app = self.client.get("/app.js")
        runtime = self.client.get("/runtime.js")
        bootstrap = self.client.get("/bootstrap.js")
        self.assertIn("function applyRoleControls()", app.text)
        self.assertIn("function canWriteScenario()", app.text)
        self.assertIn("setCapabilities(capabilities", runtime.text)
        self.assertIn("installAuthenticationGate().then", bootstrap.text)
        self.assertIn("auth-action", bootstrap.text)

    def test_admin_user_dialog_exposes_password_reset_without_rendering_passwords(self) -> None:
        response = self.client.get("/bootstrap.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Restablecer contraseña", response.text)
        self.assertIn("/password`,", response.text)
        self.assertIn("Sus sesiones anteriores se cerraron.", response.text)
        self.assertIn("password.value = ''", response.text)
        self.assertNotIn("password_hash", response.text)
        self.assertIn("Auditoría administrativa", response.text)
        self.assertIn("/api/admin/audit-events/integrity", response.text)
        self.assertIn("item.textContent", response.text)

    def test_api_routes_take_priority_over_static_mount(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_exposes_configured_instance_identity_for_isolated_preflight(self) -> None:
        with patch.dict("os.environ", {"ANVIL_INSTANCE_ID": "capacity-test-unique"}):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "instance_id": "capacity-test-unique"})


if __name__ == "__main__":
    unittest.main()
