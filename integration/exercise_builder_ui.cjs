/* Browser regression for the guided exercise builder against an isolated checkout. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");
const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8006";
const scenarioName = `Guided exercise UI ${Date.now()}`;
let scenarioId;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
    ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}),
  });
  const page = await browser.newPage();
  const errors = [];
  await page.addInitScript(() => { window.anvilUser = { role: "admin" }; });
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const created = await page.request.post(`${base}/api/scenarios`, { data: {
      name: scenarioName,
      network: { subnet: "172.30.246.0/24", zones: [
        { zone_id: "control", name: "Control", purdue_level: "L1", subnet: "172.30.246.0/25" },
        { zone_id: "operations", name: "Operaciones", purdue_level: "L2", subnet: "172.30.246.128/25" },
      ] },
      devices: [{
        node_id: "builder-plc", device_type: "plc-s7", name: "PLC ejercicio",
        ip: "172.30.246.10", zone_id: "control",
        config: { components: [{ type: "valve", name: "Válvula demo" }] },
      }, {
        node_id: "builder-modbus", device_type: "modbus-tcp", name: "Modbus ejercicio",
        ip: "172.30.246.11", zone_id: "control",
        config: { unit_id: 1, points: [
          { type: "coil", name: "Mando de bomba", address: 0, initial_value: false },
          { type: "holding_register", name: "Consigna", address: 0, initial_value: 0 },
        ] },
      }, {
        node_id: "builder-participant", device_type: "attacker-station", name: "Estación participante",
        ip: "172.30.246.130", zone_id: "operations",
        config: { role: "attacker", summary: "Prueba UI de ejercicio", participant_access_enabled: true },
      }],
      network_routes: [{ route_id: "participant-modbus", source_node_id: "builder-participant",
        target_node_id: "builder-modbus", protocol: "tcp", tcp_port: 502, enabled: true }],
    } });
    assert.equal(created.status(), 201, await created.text());
    const scenario = await created.json();
    scenarioId = scenario.id;
    const mapResponse = await page.request.get(`${base}/api/scenarios/${scenarioId}/io-map`);
    assert(mapResponse.ok(), await mapResponse.text());
    const point = (await mapResponse.json()).points.find((item) => item.access === "read_write" && item.data_type === "BOOL");
    assert(point, "scenario exposes a writable BOOL output");

    const deployed = await page.request.post(`${base}/api/scenarios/${scenarioId}/deploy`);
    assert(deployed.ok(), await deployed.text());
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator")?.textContent === "API conectada");
    await page.locator(`[data-scenario-id="${scenarioId}"]`).click();
    // Wait for the selected scenario's API reads to finish before switching
    // views; otherwise the exercise tab can still reflect the previous role.
    await page.waitForFunction((name) => document.querySelector("#scenario-name")?.value === name
      && document.querySelector("#scenario-status")?.textContent.includes("En ejecución"), scenario.name);
    await page.locator('.workspace-navigation [data-workspace-view="telemetry"]').click();
    await page.waitForFunction(() => document.body.dataset.workspace === "telemetry"
      && document.querySelector('[data-workspace-page="telemetry"]')?.hidden === false
      && document.querySelector('#runtime-panel')?.checkVisibility()
      && document.querySelector('[data-runtime-tab="exercise"]')?.checkVisibility());
    await page.locator('[data-runtime-tab="exercise"]').click();

    await page.locator("[data-exercise-name]").fill("Apertura de válvula desde el ejercicio");
    await page.locator("[data-exercise-add-event]").click();
    const pointValue = `${point.node_id}\u001f${point.address}`;
    await page.locator('[data-exercise-event-field="target"]').selectOption(pointValue);
    await page.locator('[data-exercise-event-field="value"]').selectOption("true");
    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-field="target"]').selectOption(pointValue);
    await page.locator('[data-exercise-objective-field="value"]').selectOption("true");
    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-field="kind"]').nth(1).selectOption("audit_event");
    await page.locator('[data-exercise-objective-index="1"][data-exercise-objective-field="event_type"]')
      .fill("operator.point_written");
    const expectedAudit = { source: "exercise", node_id: point.node_id, address: point.address };
    await page.locator('[data-exercise-objective-index="1"][data-exercise-objective-field="details_match_text"]')
      .fill(JSON.stringify(expectedAudit, null, 2));

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="1"][data-exercise-event-field="kind"]')
      .selectOption("protocol_write");
    const modbusPoint = "builder-modbus\u001fcoil:0";
    await page.locator('[data-exercise-event-index="1"][data-exercise-event-field="protocol_target"]')
      .selectOption(modbusPoint);
    await page.locator('[data-exercise-event-index="1"][data-exercise-event-field="value"]').selectOption("true");
    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-index="2"][data-exercise-objective-field="kind"]')
      .selectOption("audit_event");
    await page.locator('[data-exercise-objective-index="2"][data-exercise-objective-field="event_type"]')
      .fill("attack.protocol_write");
    const expectedProtocolAudit = { source_node_id: "builder-participant", target_node_id: "builder-modbus",
      protocol: "modbus-tcp", route_id: "participant-modbus" };
    await page.locator('[data-exercise-objective-index="2"][data-exercise-objective-field="details_match_text"]')
      .fill(JSON.stringify(expectedProtocolAudit, null, 2));

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="2"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="2"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="2"][data-exercise-event-field="interception_target"]')
      .selectOption("holding_register:0");
    await page.locator('[data-exercise-event-index="2"][data-exercise-event-field="replacement_value"]')
      .fill("65000");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="3"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="3"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="3"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="3"][data-exercise-event-field="at_seconds"]')
      .fill("1");

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="at_seconds"]')
      .fill("2");
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="interception_function"]')
      .selectOption("16");
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="interception_target"]')
      .selectOption("holding_register:0");
    await page.locator('[data-exercise-event-index="4"][data-exercise-event-field="replacement_value"]')
      .fill("52000");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="5"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="5"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="5"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="5"][data-exercise-event-field="at_seconds"]')
      .fill("3");

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="at_seconds"]')
      .fill("4");
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="interception_function"]')
      .selectOption("15");
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="interception_target"]')
      .selectOption("coil:0");
    await page.locator('[data-exercise-event-index="6"][data-exercise-event-field="replacement_value"]')
      .selectOption("true");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="7"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="7"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="7"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="7"][data-exercise-event-field="at_seconds"]')
      .fill("5");

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="at_seconds"]')
      .fill("6");
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="interception_function"]')
      .selectOption("5");
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="interception_target"]')
      .selectOption("coil:0");
    await page.locator('[data-exercise-event-index="8"][data-exercise-event-field="replacement_value"]')
      .selectOption("false");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="9"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="9"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="9"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="9"][data-exercise-event-field="at_seconds"]')
      .fill("7");

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="at_seconds"]')
      .fill("8");
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="interception_function"]')
      .selectOption("22");
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="interception_target"]')
      .selectOption("holding_register:0");
    await page.locator('[data-exercise-event-index="10"][data-exercise-event-field="replacement_value"]')
      .fill("42330");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="11"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="11"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="11"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="11"][data-exercise-event-field="at_seconds"]')
      .fill("9");

    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="at_seconds"]')
      .fill("10");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="interception_function"]')
      .selectOption("23");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="interception_phase"]')
      .selectOption("write");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="interception_target"]')
      .selectOption("holding_register:0");
    await page.locator('[data-exercise-event-index="12"][data-exercise-event-field="replacement_value"]')
      .fill("42331");
    await page.locator("[data-exercise-add-event]").click();
    await page.locator('[data-exercise-event-index="13"][data-exercise-event-field="kind"]')
      .selectOption("modbus_interception");
    await page.locator('[data-exercise-event-index="13"][data-exercise-event-field="route_id"]')
      .selectOption("participant-modbus");
    await page.locator('[data-exercise-event-index="13"][data-exercise-event-field="action"]')
      .selectOption("clear");
    await page.locator('[data-exercise-event-index="13"][data-exercise-event-field="at_seconds"]')
      .fill("11");

    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-index="3"][data-exercise-objective-field="kind"]')
      .selectOption("audit_event");
    await page.locator('[data-exercise-objective-index="3"][data-exercise-objective-field="event_type"]')
      .fill("security.modbus_interception");
    const expectedInterceptAudit = { route_id: "participant-modbus", action: "clear", source: "exercise" };
    await page.locator('[data-exercise-objective-index="3"][data-exercise-objective-field="details_match_text"]')
      .fill(JSON.stringify(expectedInterceptAudit, null, 2));

    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-index="4"][data-exercise-objective-field="kind"]')
      .selectOption("protocol_write_policy");
    await page.locator('[data-exercise-objective-index="4"][data-exercise-objective-field="target"]')
      .selectOption("builder-modbus\u001fmodbus-tcp");
    await page.locator('[data-exercise-objective-index="4"][data-exercise-objective-field="allowed_source_ips_text"]')
      .fill("172.30.246.10");

    const definitionResponse = page.waitForResponse((response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/scenarios/${scenarioId}/exercises`);
    await page.locator("[data-exercise-create]").click();
    const saved = await definitionResponse;
    assert.equal(saved.status(), 201, await saved.text());
    const exercise = await saved.json();
    assert.equal(exercise.definition.events[0].kind, "write_point");
    assert.equal(exercise.definition.events[0].value, true);
    assert.equal(exercise.definition.events[1].kind, "protocol_write");
    assert.equal(exercise.definition.events[1].source_node_id, "builder-participant");
    assert.equal(exercise.definition.events[1].target_node_id, "builder-modbus");
    assert.equal(exercise.definition.events[1].address, "coil:0");
    assert.equal(exercise.definition.events[1].route_id, "participant-modbus");
    assert.equal(exercise.definition.events[2].kind, "modbus_interception");
    assert.equal(exercise.definition.events[2].action, "set");
    assert.deepEqual(exercise.definition.events[2].rules, [{ rule_id: exercise.definition.events[2].rules[0].rule_id,
      unit_id: 1, function_code: 3, address: 0, replacement_value: 65000,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[3].action, "clear");
    assert.deepEqual(exercise.definition.events[4].rules, [{ rule_id: exercise.definition.events[4].rules[0].rule_id,
      unit_id: 1, function_code: 16, address: 0, replacement_value: 52000,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[5].action, "clear");
    assert.deepEqual(exercise.definition.events[6].rules, [{ rule_id: exercise.definition.events[6].rules[0].rule_id,
      unit_id: 1, function_code: 15, address: 0, replacement_value: 1,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[7].action, "clear");
    assert.deepEqual(exercise.definition.events[8].rules, [{ rule_id: exercise.definition.events[8].rules[0].rule_id,
      unit_id: 1, function_code: 5, address: 0, replacement_value: 0,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[9].action, "clear");
    assert.deepEqual(exercise.definition.events[10].rules, [{ rule_id: exercise.definition.events[10].rules[0].rule_id,
      unit_id: 1, function_code: 22, address: 0, replacement_value: 42330,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[11].action, "clear");
    assert.deepEqual(exercise.definition.events[12].rules, [{ rule_id: exercise.definition.events[12].rules[0].rule_id,
      unit_id: 1, function_code: 23, address: 0, replacement_value: 42331,
      exercise_id: null, event_id: null }]);
    assert.equal(exercise.definition.events[13].action, "clear");
    assert.equal(exercise.definition.objectives[0].kind, "point_value");
    assert.deepEqual(exercise.definition.objectives[1].details_match, expectedAudit);
    assert.deepEqual(exercise.definition.objectives[2].details_match, expectedProtocolAudit);
    assert.deepEqual(exercise.definition.objectives[3].details_match, expectedInterceptAudit);
    assert.equal(exercise.definition.objectives[4].kind, "protocol_write_policy");
    assert.equal(exercise.definition.objectives[4].target_node_id, "builder-modbus");
    assert.equal(exercise.definition.objectives[4].protocol, "modbus-tcp");
    assert.deepEqual(exercise.definition.objectives[4].allowed_source_ips, ["172.30.246.10"]);

    const startResponse = page.waitForResponse((response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/exercises/${exercise.id}/start`));
    await page.locator("[data-exercise-start]").click();
    const started = await startResponse;
    assert.equal(started.status(), 200, await started.text());
    const completionDeadline = Date.now() + 90_000;
    let completedRun;
    while (Date.now() < completionDeadline) {
      const response = await page.request.get(`${base}/api/scenarios/${scenarioId}/exercises/${exercise.id}`);
      if (!response.ok()) throw new Error(`exercise status lookup failed: ${response.status()} ${await response.text()}`);
      completedRun = await response.json();
      if (completedRun.status !== "running") break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.equal(completedRun?.status, "completed", "exercise scheduler completes all protocol events");
    assert.equal(completedRun.score, 100, "all exercise objectives pass");
    // Reopen the tab to make the UI load the authoritative completed run from
    // the API; this avoids coupling the test to the background poll cadence.
    await page.locator('[data-runtime-tab="map"]').click();
    await page.locator('[data-runtime-tab="exercise"]').click();
    await page.waitForFunction(() => document.querySelector("[data-exercise-results]")?.textContent.includes("Superado"), null, { timeout: 5_000 });
    assert((await page.locator("[data-exercise-results]").textContent()).includes("Superado"), "the process objective is reported as passed");
    const policyResult = await page.locator("[data-exercise-results]").textContent();
    assert(policyResult.includes("protocol_write_policy"), "the peer-IP policy objective is reported");
    assert(policyResult.includes("detection_count"), "the policy result includes observed detections");
    assert.deepEqual(errors, [], "no browser JavaScript errors");
    await page.goto("about:blank"); // stop runtime polling before teardown removes the scenario
    console.log("Guided exercise UI passed: configure and save a peer-IP policy, detect an unapproved Modbus write, define S7/Modbus writes and FC03/FC05/FC15/FC16/FC22/FC23 read/write rewrites, run, and observe objectives pass.");
  } finally {
    if (scenarioId) {
      await page.request.post(`${base}/api/scenarios/${scenarioId}/stop`).catch(() => {});
      await page.request.delete(`${base}/api/scenarios/${scenarioId}`).catch(() => {});
    }
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
