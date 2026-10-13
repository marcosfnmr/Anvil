/* Verify that a basic user can create and run a closed-loop template without code. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");

const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8004";
const templateId = process.env.ANVIL_ONBOARDING_TEMPLATE || "field-to-plc-factory";
let scenarioId;

async function requireOk(response, label) {
  if (!response.ok()) throw new Error(`${label}: HTTP ${response.status()} ${await response.text()}`);
  return response;
}

async function authenticate(request) {
  const origin = new URL(base).origin;
  const headers = { Origin: origin, "Content-Type": "application/json" };
  const statusResponse = await request.get(`${base}/api/auth/status`);
  if (!statusResponse.ok()) throw new Error(`Auth status failed: HTTP ${statusResponse.status()}`);
  const auth = await statusResponse.json();
  if (!auth.auth_required) return;
  const email = process.env.ANVIL_TEST_EMAIL;
  const password = process.env.ANVIL_TEST_PASSWORD;
  const bootstrapToken = process.env.ANVIL_BOOTSTRAP_TOKEN;
  const path = auth.setup_required ? "/api/auth/bootstrap" : "/api/auth/login";
  const credentials = auth.setup_required
    ? { email, password, token: bootstrapToken }
    : { email, password };
  if (!email || !password || auth.setup_required && !bootstrapToken) {
    throw new Error("Authenticated backend requires ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD; a fresh DB also requires ANVIL_BOOTSTRAP_TOKEN");
  }
  await requireOk(await request.post(`${base}${path}`, { headers, data: credentials }), path);
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const consoleErrors = [];
  const failedRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "failed"}`));
  page.on("dialog", (dialog) => dialog.accept());
  page.setDefaultTimeout(10000);

  try {
    await authenticate(page.request);
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator")?.textContent === "API conectada");
    const registryState = await page.evaluate(async () => {
      const appRegistry = await import("/nodes/registry.js?v=field-live-3");
      return [...appRegistry.NODE_REGISTRY.keys()];
    });
    assert(registryState.includes("field-tank") && registryState.includes("field-pump"),
      `Field node catalog is unavailable to the app: ${JSON.stringify(registryState)}`);

    await page.locator("#templates-button").click();
    const instantiate = page.locator(`[data-instantiate-template="${templateId}"]`);
    await instantiate.waitFor();
    const card = page.locator(".template-card").filter({ has: instantiate });
    assert.match(await card.innerText(), /Inicial|Intermedia/);
    const instantiateResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/api/templates/${templateId}/instantiate`) && response.request().method() === "POST");
    await instantiate.click();
    const response = await requireOk(await instantiateResponse, "Instantiate beginner template");
    const scenario = await response.json();
    scenarioId = scenario.id;

    assert.equal(scenario.devices.filter((item) => item.device_type !== "field-runtime").length, 3);
    assert.equal(scenario.field_objects.length, 5);
    assert.equal(scenario.signal_connections.length, 3);
    assert.equal(scenario.process_connections.length, 4);
    assert.equal(scenario.devices.find((item) => item.node_id === "plc-field").config.control_rules.length, 2);

    await page.waitForFunction((id) => document.querySelector(`[data-scenario-id="${id}"]`) !== null, scenarioId);
    try {
      await page.waitForFunction(() => document.body.dataset.workspace === "editor"
        && document.querySelectorAll(".drawflow-node").length === 8
        && document.querySelectorAll(".independent-field").length === 5, null, { timeout: 10000 });
    } catch (error) {
      const state = await page.evaluate(() => ({
        workspace: document.body.dataset.workspace,
        devices: document.querySelectorAll(".drawflow-node").length,
        independentFields: document.querySelectorAll(".independent-field").length,
        scenarioList: document.querySelector("#scenario-list")?.innerText,
        alerts: [...document.querySelectorAll('[role="alert"]')].map((item) => item.innerText),
        toast: document.querySelector("#toast")?.innerText,
      }));
      state.console = consoleErrors;
      state.pageErrors = errors;
      throw new Error(`${error.message}; template map state=${JSON.stringify(state)}`);
    }
    const deployResponse = page.waitForResponse((item) =>
      item.url().endsWith(`/api/scenarios/${scenarioId}/deploy`) && item.request().method() === "POST",
    { timeout: 90000 });
    const deployButton = page.locator("#deploy-button");
    const deployButtonState = await deployButton.evaluate((button) => ({ disabled: button.disabled, text: button.textContent }));
    assert.equal(deployButtonState.disabled, false, `Deploy must be enabled after opening the template: ${JSON.stringify(deployButtonState)}`);
    try {
      await deployButton.click();
    } catch (error) {
      deployResponse.catch(() => undefined);
      throw new Error(`Could not click Deploy (${JSON.stringify(deployButtonState)}): ${error.message}`);
    }
    const deployed = await requireOk(await deployResponse, "Deploy template without code");
    assert.equal((await deployed.json()).status, "running");
    await page.waitForFunction(() => document.querySelector("#scenario-status")?.textContent.includes("En ejecución"),
      null, { timeout: 45000 });

    let snapshot;
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      const fieldResponse = await page.request.get(`${base}/api/scenarios/${scenarioId}/field/values`);
      await requireOk(fieldResponse, "Read simulated process");
      snapshot = await fieldResponse.json();
      const tank = snapshot.objects.find((item) => item.object_id === "tank-main");
      const pump = snapshot.objects.find((item) => item.object_id === "pump-feed");
      if (pump?.state === false && tank?.volume_liters >= 50 && tank.inflow_lpm === 0) break;
      await page.waitForTimeout(250);
    }
    const tank = snapshot.objects.find((item) => item.object_id === "tank-main");
    const pump = snapshot.objects.find((item) => item.object_id === "pump-feed");
    const inletValve = snapshot.objects.find((item) => item.object_id === "valve-inlet");
    assert(tank.volume_liters >= 50 && tank.volume_liters <= 55,
      `The process sensor did not stop the fill at the PLC setpoint plus hysteresis: ${JSON.stringify(tank)}`);
    assert.equal(pump.state, false, `PLC did not stop the pump at the sensor setpoint: ${JSON.stringify(pump)}`);
    assert.equal(inletValve.state, false, `PLC did not close the inlet valve at the sensor setpoint: ${JSON.stringify(inletValve)}`);
    assert.equal(tank.inflow_lpm, 0, `The process kept receiving flow after the PLC stopped the pump: ${JSON.stringify(tank)}`);
    const plcResponse = await page.request.get(`${base}/api/scenarios/${scenarioId}/plcs/plc-field/values`);
    await requireOk(plcResponse, "Read PLC process input over S7");
    const plcValues = await plcResponse.json();
    const plcLevel = plcValues.values.find((item) => item.address === "DB1.DBD0");
    assert(plcLevel, `PLC level input was not returned: ${JSON.stringify(plcValues.values)}`);
    assert(Math.abs(plcLevel.value - tank.volume_liters) < 1.5,
      `The S7 input did not match the process sensor: PLC=${plcLevel.value}, tank=${tank.volume_liters}`);
    assert.deepEqual(errors, [], `Browser JavaScript errors: ${errors.join("; ")}`);
    assert.deepEqual(consoleErrors, [], `Browser console errors: ${consoleErrors.join("; ")}`);
    assert.deepEqual(failedRequests, [], `Failed browser requests: ${failedRequests.join("; ")}`);
    console.log(`PASS: created «${scenario.name}» from the beginner template in the UI, deployed without editing code, verified the process sensor's S7 value (${plcLevel.value.toFixed(2)} %) against the tank, and observed the PLC stop the pump and close the inlet valve at ${tank.volume_liters.toFixed(2)} L.`);
  } finally {
    if (scenarioId) {
      const headers = { Origin: new URL(base).origin };
      const stop = await page.request.post(`${base}/api/scenarios/${scenarioId}/stop`, { headers });
      if (![200, 404].includes(stop.status())) {
        throw new Error(`Could not stop temporary scenario during cleanup: HTTP ${stop.status()} ${await stop.text()}`);
      }
      const remove = await page.request.delete(`${base}/api/scenarios/${scenarioId}`, { headers });
      if (![204, 404].includes(remove.status())) {
        throw new Error(`Could not delete temporary scenario during cleanup: HTTP ${remove.status()} ${await remove.text()}`);
      }
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
