/* Browser + real Docker smoke. Uses only scenarios created by this run.
 * ANVIL_BASE_URL=http://127.0.0.1:8004 node integration/ui_smoke.cjs
 * Requires Playwright; optional ANVIL_AXE_PATH enables a WCAG 2 A/AA audit.
 */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");
const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8004";
const owned = new Set();
const errors = [];

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(20000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  async function api(path, method = "GET", data) {
    const response = await page.request.fetch(`${base}/api/scenarios${path}`, { method, data });
    assert(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
    return response.status() === 204 ? null : response.json();
  }
  async function mutation(action, method, path) {
    const pending = page.waitForResponse((response) => response.request().method() === method && new URL(response.url()).pathname === path, { timeout: 90000 });
    await action();
    const response = await pending;
    assert(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
    const payload = await response.json();
    if (payload.id && method === "POST") owned.add(payload.id);
    await page.waitForFunction(() => !document.querySelector("#new-scenario-button").disabled);
    return payload;
  }
  async function audit(label) {
    if (!process.env.ANVIL_AXE_PATH) return;
    await page.addScriptTag({ path: process.env.ANVIL_AXE_PATH });
    const result = await page.evaluate(() => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
    assert.deepEqual(result.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) })), [], `Accessibility: ${label}`);
    console.log(`Accessibility ${label}: no automatic WCAG A/AA violations`);
  }
  try {
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator").textContent === "API conectada");
    await audit("desktop empty");
    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await page.locator("#demo-button").click();
    assert.equal(await page.locator(".drawflow-node").count(), 3);
    assert.equal(await page.locator("[data-hmi-target]:checked").count(), 2);
    await page.locator("#scenario-subnet").fill("172.30.246.0/24");
    await page.locator('[data-workspace-view="config"]').click();
    const deviceIds = await page.locator("#device-selector option").evaluateAll((options) => options.map((item) => item.value).filter(Boolean));
    for (const [index, nodeId] of deviceIds.entries()) {
      await page.locator("#device-selector").selectOption(nodeId);
      await page.locator('[data-device-field="ip"]').fill(`172.30.246.${10 + index}`);
    }
    const first = await mutation(() => page.locator("#save-button").click(), "POST", "/api/scenarios");
    const plc = first.devices.find((item) => item.device_type === "plc-s7");
    const modbus = first.devices.find((item) => item.device_type === "modbus-tcp");
    const hmi = first.devices.find((item) => item.device_type === "hmi");
    await page.locator("#device-selector").selectOption(plc.node_id);
    await page.locator('select[data-component-type]').selectOption("analog_sensor");
    await page.locator('[data-add-component]').focus();
    await page.keyboard.press("Enter");
    await page.locator('[data-component-field="simulation.mode"]').selectOption("constant");
    await page.locator('[data-component-field="unit"]').last().fill("");
    await mutation(() => page.locator("#save-button").click(), "PUT", `/api/scenarios/${first.id}`);
    const map = await api(`/${first.id}/io-map`);
    assert.equal(await page.locator(".io-point").count(), map.points.length, "map refreshes after saving changes");
    await page.reload();
    await page.locator(`[data-scenario-id="${first.id}"]`).click();
    await page.waitForFunction(() => document.querySelectorAll(".drawflow-node").length === 3);
    assert.equal(await page.locator(".io-point").count(), map.points.length);
    await audit("desktop configured");
    await mutation(() => page.locator("#deploy-button").click(), "POST", `/api/scenarios/${first.id}/deploy`);
    await page.locator('.workspace-navigation [data-workspace-view="telemetry"]').click();
    await page.waitForFunction(() => document.querySelectorAll("[data-write-address]:not(:disabled)").length > 2);
    assert(await page.locator("#scenario-name").isDisabled(), "running topology is locked");
    const holding = page.locator('[data-write-address="holding_register:0"]');
    const register = holding.locator("..").locator("input");
    await register.fill("4321");
    // Wait through a polling cycle; the editor must retain the draft and focus.
    await page.waitForTimeout(2300);
    assert.equal(await register.inputValue(), "4321");
    await mutation(() => holding.click(), "POST", `/api/scenarios/${first.id}/modbus/${modbus.node_id}/write`);
    const readback = await api(`/${first.id}/modbus/${modbus.node_id}/values`);
    assert.equal(readback.values.find((item) => item.address === "holding_register:0").value, 4321);
    assert.equal(await page.locator('[data-write-address="input_register:0"]').count(), 0);
    const command = map.points.find((point) => point.component_type === "pump_command");
    const flow = map.points.find((point) => point.component_type === "pump_flow");
    // Build and run an exercise through the guided editor; target a Modbus
    // holding register so this check does not change the following pump test.
    await page.locator('[data-runtime-tab="exercise"]').click();
    await page.locator("[data-exercise-add-event]").click();
    const exercisePoint = map.points.find((point) => point.node_id === modbus.node_id && point.address === "holding_register:0");
    assert(exercisePoint, "guided exercise target exists in the scenario I/O map");
    const encodedPoint = `${exercisePoint.node_id}\u001f${exercisePoint.address}`;
    await page.locator('[data-exercise-event-field="target"]').selectOption(encodedPoint);
    await page.locator('[data-exercise-event-field="value"]').fill("7777");
    await page.locator("[data-exercise-add-objective]").click();
    await page.locator('[data-exercise-objective-field="target"]').selectOption(encodedPoint);
    await page.locator('[data-exercise-objective-field="value"]').fill("7777");
    const exercise = await mutation(() => page.locator("[data-exercise-create]").click(), "POST", `/api/scenarios/${first.id}/exercises`);
    await mutation(() => page.locator("[data-exercise-start]").click(), "POST", `/api/scenarios/${first.id}/exercises/${exercise.id}/start`);
    await page.waitForFunction(() => document.querySelector("[data-exercise-status]").textContent.includes("completed"), { timeout: 20000 });
    assert(await page.locator("[data-exercise-results]").textContent().then((text) => text.includes("Superado")), "exercise objective passes after its scheduled Modbus write");
    await mutation(() => page.locator(`[data-write-address="${command.address}"]`).click(), "POST", `/api/scenarios/${first.id}/plcs/${plc.node_id}/write`);
    await page.waitForFunction((address) => [...document.querySelectorAll(".io-point")].some((row) => row.querySelector(".io-point-address").textContent === address && row.querySelector("[data-current-value]").textContent === "60"), flow.address);
    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await page.locator('[data-workspace-view="config"]').click();
    await page.locator("#device-selector").selectOption(hmi.node_id);
    await page.locator("[data-hmi-read]").click();
    await page.waitForFunction(() => document.querySelector("[data-hmi-values]").textContent.includes("4321"));
    await audit("desktop live");
    // Reproduce a denied device without hiding readings from the other protocol.
    await page.route("**/plcs/*/values", (route) => route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "Denied for browser regression" }) }));
    await page.waitForFunction(() => [...document.querySelectorAll("[data-point-error]")].some((item) => item.textContent.includes("Denied for browser regression")));
    const commandRow = page.locator(`[data-write-address="${command.address}"]`).locator("../..");
    assert.equal(await commandRow.locator("[data-current-value]").textContent(), "—");
    assert.equal(await holding.locator("../..").locator("[data-current-value]").textContent(), "4321");
    await page.unroute("**/plcs/*/values");
    await mutation(() => page.locator("#stop-button").click(), "POST", `/api/scenarios/${first.id}/stop`);
    assert.equal(await page.locator("[data-write-address]:not(:disabled)").count(), 0);
    await page.locator('.workspace-navigation [data-workspace-view="projects"]').click();
    await page.locator("#new-scenario-button").click();
    await page.locator("#scenario-subnet").fill("172.30.241.0/24");
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.getByRole("button", { name: "Añadir Dispositivo Modbus TCP", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.getByRole("button", { name: "Añadir HMI", exact: true }).click();
    await page.locator("[data-hmi-target]").check();
    const custom = await mutation(() => page.locator("#save-button").click(), "POST", "/api/scenarios");
    assert.deepEqual(custom.devices.map((item) => item.ip), ["172.30.241.10", "172.30.241.11"]);
    await page.locator("#device-selector").selectOption(custom.devices[0].node_id);
    await page.locator("[data-remove-node]").click();
    const detached = await mutation(() => page.locator("#save-button").click(), "PUT", `/api/scenarios/${custom.id}`);
    assert.equal(detached.devices.length, 1);
    assert.equal(detached.devices[0].config.target_node_ids, "", "deleting a target clears its HMI reference");
    // Import with stale/hostile canvas data; authoritative devices must still load.
    const exported = await api(`/${first.id}/export`);
    exported.name = "Browser regression import";
    exported.canvas = { data: { "1": { data: { node_id: plc.node_id, device_type: "unsupported", name: "Ignore this" }, pos_x: 90, pos_y: 120 } } };
    await mutation(() => page.locator("#import-scenario-file").setInputFiles({ name: "scenario.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(exported)) }), "POST", "/api/scenarios/import");
    await page.waitForFunction(() => document.querySelectorAll(".drawflow-node").length === 3);
    assert.equal(await page.getByText("Ignore this", { exact: true }).count(), 0);
    assert.equal(await page.locator(".node-plc-s7").count(), 1);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "mobile should not overflow horizontally");
    await audit("mobile configured");
    await page.locator("#help-button").click();
    await audit("mobile help");
    await page.keyboard.press("Escape");
    assert(!(await page.locator("#help-dialog").isVisible()));
    assert.deepEqual(errors, [], "no browser JavaScript errors");
    console.log("UI smoke passed: keyboard, HMI, save/reload, S7 pump, Modbus write, partial failure, import, mobile, help.");
  } finally {
    for (const id of owned) {
      await api(`/${id}/stop`, "POST");
      await api(`/${id}`, "DELETE");
    }
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
