/* Verify full workspace views, drafts, keyboard navigation and responsive forms. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");
const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8004";
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  const nav = (view) => page.locator(`.workspace-navigation [data-workspace-view="${view}"]`);
  async function check(view) {
    assert.equal(await page.locator("body").getAttribute("data-workspace"), view);
    assert.equal(await page.locator("[data-workspace-page]:visible").count(), 1);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal page overflow");
    if (process.env.ANVIL_AXE_PATH) {
      await page.addScriptTag({ path: process.env.ANVIL_AXE_PATH });
      const result = await page.evaluate(() => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
      assert.deepEqual(result.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) })), [], `accessibility: ${view}`);
    }
  }
  try {
    await page.route("**/api/auth/status", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ auth_required: false, setup_required: false, authenticated: true, user: { id: "ui-admin", email: "admin@example.test", role: "admin", active: true } }),
    }));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator").textContent === "API conectada");
    const fieldError = await page.evaluate(async () => {
      const { scenarioApi } = await import("/api.js");
      const originalFetch = window.fetch;
      window.fetch = async () => new Response(JSON.stringify({ detail: [
        { loc: ["body", "devices", 0, "config", "rack"], msg: "Field required" },
        { loc: ["body", "devices", 1, "config", "slot"], msg: "Input should be greater than or equal to 0" },
      ] }), { status: 422, headers: { "Content-Type": "application/json" } });
      try {
        await scenarioApi.create({});
        return "request unexpectedly succeeded";
      } catch (error) {
        return error.message;
      } finally {
        window.fetch = originalFetch;
      }
    });
    assert.equal(fieldError, "devices[0].config.rack: Field required · devices[1].config.slot: Input should be greater than or equal to 0", "validation errors retain the field path to edit");
    await check("projects");
    await page.locator("#new-scenario-button").click();
    await page.locator("#scenario-name").fill("Borrador conservado");
    await check("editor");
    assert(!(await page.locator("#node-palette").isVisible()));
    await nav("catalog").focus();
    await page.keyboard.press("Enter");
    await check("catalog");
    await page.locator('[data-node-type="plc-s7"]').click();
    await check("config");
    assert(!(await page.locator("#runtime-panel").isVisible()));
    assert(!(await page.locator('[data-device-field="config.rack"]').isVisible()));
    await page.locator(".protocol-settings summary").click();
    await page.locator('[data-device-field="config.slot"]').fill("2");
    await page.locator("[data-component-type]").selectOption("io_channel");
    await page.locator('[data-add-component="selected"]').click();
    await page.locator('[data-component-field="data_type"]').selectOption("REAL");
    await page.locator("[data-component-type]").selectOption("io_channel");
    await page.locator('[data-add-component="selected"]').click();
    const createResponse = page.waitForResponse((response) =>
      response.url().endsWith("/api/scenarios") && response.request().method() === "POST");
    await page.locator("#save-button").click();
    const createdScenario = await createResponse;
    assert.equal(createdScenario.status(), 201, `scenario save failed: ${await createdScenario.text()}`);
    const createdScenarioId = (await createdScenario.json()).id;
    await nav("editor").click();
    const deployPath = `**/api/scenarios/${createdScenarioId}/deploy`;
    await page.route(deployPath, (route) => route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ detail: [
        { loc: ["body", "devices", 0, "config", "rack"], msg: "Invalid rack value" },
        { loc: ["body", "devices", 0, "config", "slot"], msg: "Invalid slot value" },
      ] }),
    }));
    const failedDeploy = page.waitForResponse((response) =>
      response.url().endsWith(`/api/scenarios/${createdScenarioId}/deploy`) && response.request().method() === "POST");
    await page.locator("#deploy-button").click();
    const failedDeployResponse = await failedDeploy;
    assert.equal(failedDeployResponse.status(), 422);
    await page.locator("#toast.visible").waitFor();
    const actionableError = "devices[0].config.rack: Invalid rack value · devices[0].config.slot: Invalid slot value";
    assert.equal((await page.locator("#toast").textContent()).trim(), actionableError, "deployment error toast identifies both invalid device fields");
    await nav("telemetry").click();
    await page.locator('[data-runtime-tab="activity"]').click();
    assert((await page.locator("#event-log").textContent()).includes(actionableError), "deployment error is retained in scenario activity");
    await page.unroute(deployPath);
    await nav("editor").click();
    await check("editor");
    assert.equal(await page.locator("#scenario-name").inputValue(), "Borrador conservado");
    const canvas = await page.locator(".canvas-panel").boundingBox();
    assert(canvas.height > 650 && canvas.width > 900, "editor gets the full work area");
    await page.locator('.page-editor [data-workspace-view="config"]').click();
    assert.equal(await page.locator('[data-device-field="config.slot"]').inputValue(), "2");
    await nav("telemetry").click();
    await check("telemetry");
    await page.locator('[data-runtime-tab="activity"]').click();
    assert(await page.locator("#event-log").isVisible());
    await page.locator('[data-runtime-tab="capture"]').click();
    assert(await page.locator("[data-evidence-export]").isVisible(), "evidence ZIP export is available in telemetry for instructors");
    assert.equal(await page.locator("[data-evidence-include-capture]").isChecked(), false, "active capture is excluded unless explicitly selected");
    assert.equal(await page.locator("[data-evidence-include-capture]").isDisabled(), true, "capture cannot be selected when none is active");
    assert((await page.locator(".capture-consumption-note").textContent()).includes("detendrá y consumirá"), "UI explains that bundling consumes the selected capture");
    const runtime = await page.locator("#runtime-panel").boundingBox();
    assert(runtime.height > 650 && runtime.width > 900, "telemetry gets the full work area");
    await nav("catalog").click();
    await page.locator('[data-node-type="hmi"]').click();
    assert(await nav("hmi").isVisible(), "adding an HMI exposes its operator view");
    await nav("editor").click();
    await page.locator('.page-editor [data-workspace-view="config"]').click();
    await page.locator("[data-hmi-target]").check();
    await page.locator("[data-hmi-add-alarm]").click();
    assert.equal(await page.locator("[data-hmi-alarm-rule-field='address']").first().locator("option").count(), 3, "HMI alarm editor offers the selected PLC's numeric and digital tags");
    await page.locator("[data-hmi-alarm-rule-field='address']").selectOption({ index: 1 });
    await page.locator("[data-hmi-alarm-rule-field='above']").fill("6.5");
    await page.locator("[data-hmi-add-alarm]").click();
    await page.locator("[data-hmi-alarm-rule-field='address']").nth(1).selectOption({ index: 2 });
    await page.locator("[data-hmi-alarm-rule-field='expected_state']").selectOption("true");
    assert.equal(await page.locator("[data-hmi-remove-alarm]").count(), 2, "HMI numeric and BOOL alarm rules can be edited and removed");
    await nav("hmi").click();
    await check("hmi");
    assert.equal(await page.locator("[data-hmi-console-device] option").count(), 2, "operator view selects the configured HMI");
    await page.waitForFunction(() => document.querySelector("[data-hmi-console-status]").textContent.includes("Guarda"));
    assert(await page.locator("[data-hmi-console-status]").textContent().then((value) => value.includes("Guarda")), "unsaved changes block HMI operation");
    const synoptic = await page.evaluate(async () => {
      const { buildHmiSynoptic } = await import("/hmi-synoptic.js");
      return buildHmiSynoptic({
        hmi: { config: { target_node_ids: "plc-1" } },
        fieldObjects: [
          { object_id: "pump-1", type: "pump", name: "Bomba <A>", unit: "L/min", position: { x: 10, y: 20 } },
          { object_id: "tank-1", type: "tank", name: "Depósito", capacity_liters: 1000, position: { x: 360, y: 20 } },
          { object_id: "level-sensor", type: "analog_sensor", name: "Nivel", unit: "%", position: { x: 700, y: 20 } },
          { object_id: "hidden-valve", type: "valve", name: "No seleccionada", position: { x: 720, y: 20 } },
        ],
        signalConnections: [
          { connection_id: "s1", source: { kind: "device", node_id: "plc-1", address: "DB1.DBX0.0" }, target: { kind: "field", object_id: "pump-1", port: "command" } },
          { connection_id: "s2", source: { kind: "field", object_id: "pump-1", port: "flow" }, target: { kind: "device", node_id: "plc-1", address: "DB1.DBD4" } },
          { connection_id: "s3", source: { kind: "field", object_id: "level-sensor", port: "value" }, target: { kind: "device", node_id: "plc-1", address: "DB1.DBD8" } },
        ],
        processConnections: [
          { connection_id: "p1", source: { object_id: "pump-1", port: "flow" }, target: { object_id: "tank-1", port: "inlet" } },
          { connection_id: "p2", source: { object_id: "tank-1", port: "level" }, target: { object_id: "level-sensor", port: "measurement" } },
        ],
        snapshot: { values: [
          { node_id: "plc-1", address: "DB1.DBX0.0", data_type: "BOOL", value: true },
          { node_id: "plc-1", address: "DB1.DBD4", data_type: "REAL", value: 35.5, unit: "L/min" },
          { node_id: "plc-1", address: "DB1.DBD8", data_type: "REAL", value: 55.2, unit: "%" },
        ] },
      });
    });
    assert(synoptic.includes("hmi-process-link"), "synoptic draws the physical process connection");
    assert(synoptic.includes("55,2 %") && synoptic.includes("35,5 L/min"), "synoptic displays values returned by the selected PLC");
    assert(synoptic.includes("Bomba &lt;A&gt;"), "synoptic escapes field names");
    assert(!synoptic.includes("No seleccionada"), "synoptic hides field objects outside the HMI target scope");
    await page.locator("[data-hmi-console-values]").evaluate((root, html) => { root.innerHTML = html; }, synoptic);
    assert.equal(await page.locator(".hmi-synoptic-node").count(), 3, "synoptic renders only the process objects in the selected target graph");
    assert.equal(await page.locator(".hmi-process-link").count(), 2, "synoptic renders physical connections as SVG paths");
    assert((await page.locator(".hmi-synoptic-stage").evaluate((stage) => stage.getBoundingClientRect().width)) > 600, "process diagram has a scrollable layout area");
    const hmiRefresh = await page.evaluate(async () => {
      const { clearHmiInputEdited, markHmiInputEdited, updateHmiLiveView, updateHmiSynoptic } = await import("/hmi-live-view.js");
      const output = document.createElement("div");
      document.body.append(output);
      const markup = (value, inputValue) => `<div class="hmi-live-grid"><article class="hmi-point" data-hmi-live-key="point:plc:DB1"><output>${value}</output><input data-hmi-value type="number" value="${inputValue}"></article></div>`;
      const synopticMarkup = (value) => `<section class="hmi-synoptic"><article data-hmi-object="tank"><output>${value}</output></article></section>`;
      updateHmiLiveView(output, markup("10", "1"));
      updateHmiSynoptic(output, synopticMarkup("nivel 10"));
      const grid = output.querySelector(".hmi-live-grid");
      const card = output.querySelector(".hmi-point");
      const input = output.querySelector("input[data-hmi-value]");
      const synoptic = output.querySelector(".hmi-synoptic");
      markHmiInputEdited(input);
      input.value = "7";
      input.focus();
      updateHmiLiveView(output, markup("20", "2"));
      const retainedAcrossValuesOnlyUpdate = output.querySelector(".hmi-synoptic") === synoptic;
      updateHmiSynoptic(output, synopticMarkup("nivel 20"));
      const keptDraft = input.value === "7" && document.activeElement === input;
      const retained = output.querySelector(".hmi-live-grid") === grid
        && output.querySelector(".hmi-point") === card
        && output.querySelector("input[data-hmi-value]") === input
        && output.querySelector(".hmi-synoptic") === synoptic;
      const freshValues = output.querySelector(".hmi-point output").textContent === "20"
        && output.querySelector(".hmi-synoptic output").textContent === "nivel 20";
      clearHmiInputEdited(input);
      updateHmiLiveView(output, markup("30", "3"));
      const draftUpdatesAfterWrite = input.value === "3";
      return { retained, keptDraft, freshValues, draftUpdatesAfterWrite, retainedAcrossValuesOnlyUpdate };
    });
    assert.deepEqual(hmiRefresh, { retained: true, keptDraft: true, freshValues: true, draftUpdatesAfterWrite: true, retainedAcrossValuesOnlyUpdate: true }, "HMI refresh updates values without rebuilding cards, synoptic, focus or an in-progress draft");
    await nav("catalog").click();
    await page.locator('[data-node-type="attacker-station"]').click();
    await nav("editor").click();
    await page.locator('.page-editor [data-workspace-view="config"]').click();
    await page.locator(".protocol-settings summary").click();
    const participantAccess = page.locator('[data-device-field="config.participant_access_enabled"]');
    assert.equal(await participantAccess.isChecked(), false, "participant network access is opt-in");
    await participantAccess.check();
    assert.equal(await participantAccess.isChecked(), true, "inspector enables participant access to its zone");
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ["projects", "editor", "catalog", "telemetry", "hmi"]) {
      await nav(view).click();
      await check(view);
    }
    await nav("editor").click();
    await page.locator('.page-editor [data-workspace-view="config"]').click();
    await check("config");
    const clipped = await page.locator('.page-config input, .page-config select').evaluateAll((fields) => fields.filter((field) => field.getClientRects().length && field.getBoundingClientRect().right > innerWidth).map((field) => field.outerHTML));
    assert.deepEqual(clipped, [], "mobile form controls fit the viewport");
    assert.deepEqual(errors, []);
    console.log(`Workspace UI OK: full views, HMI selection, participant station opt-in, desktop dimensions, drafts, keyboard, activity and mobile controls${process.env.ANVIL_AXE_PATH ? " plus axe accessibility audit" : ""}.`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
