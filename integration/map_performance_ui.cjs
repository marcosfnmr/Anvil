/* Browser smoke for the SRS 100-node editor responsiveness requirement.
 * ANVIL_BASE_URL=http://127.0.0.1:8005 node integration/map_performance_ui.cjs
 * Requires Playwright. Creates and deletes one temporary, stopped scenario.
 */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");

const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8005";
const nodeCount = Number(process.env.ANVIL_MAP_NODE_COUNT || 100);
const renderLimitMs = Number(process.env.ANVIL_MAP_RENDER_LIMIT_MS || 5000);
const renderRuns = Number(process.env.ANVIL_MAP_RENDER_RUNS || 5);
const scenarioSubnet = process.env.ANVIL_MAP_TEST_SUBNET || "192.0.2.0/24";
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
    throw new Error("Authenticated preview requires ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD; a fresh DB also requires ANVIL_BOOTSTRAP_TOKEN");
  }
  await requireOk(await request.post(`${base}${path}`, { headers, data: credentials }), path);
}

async function main() {
  if (!Number.isInteger(nodeCount) || nodeCount < 100 || nodeCount > 250) {
    throw new Error("ANVIL_MAP_NODE_COUNT must be an integer from 100 to 250");
  }
  if (!Number.isInteger(renderRuns) || renderRuns < 3 || renderRuns > 20) {
    throw new Error("ANVIL_MAP_RENDER_RUNS must be an integer from 3 to 20");
  }
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
  });
  const browserVersion = browser.version();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const consoleErrors = [];
  const failedRequests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "failed"}`));
  page.setDefaultTimeout(30000);

  try {
    const api = page.request;
    await authenticate(api);
    const devices = Array.from({ length: nodeCount }, (_, index) => {
      const node_id = `map-bench-${index.toString().padStart(3, "0")}`;
      const ip = `192.0.2.${index + 2}`;
      const common = { node_id, name: `Benchmark ${index + 1}`, ip, external_access: false, external_port: null };
      switch (index % 5) {
        case 0:
          return { ...common, device_type: "plc-s7", config: { rack: 0, slot: 1, db_size: 32, scan_cycle_ms: 100, components: [], control_rules: [], boolean_rules: [] } };
        case 1:
          return { ...common, device_type: "modbus-tcp", config: { unit_id: 1, port: 502, transport: "tcp", points: [{ type: "coil", name: "run", address: null, initial_value: false }] } };
        case 2:
          return { ...common, device_type: "hmi", config: { role: "hmi", summary: "Temporary HMI performance fixture", target_node_ids: "map-bench-000", alarm_source_node_ids: "", participant_access_enabled: false, alarms: [], firewall_allow_tcp_ports: [] } };
        case 3:
          return { ...common, device_type: "scada", config: { target_node_ids: "map-bench-000", poll_interval_ms: 1000, history_capacity: 10, alarms: [] } };
        default:
          return { ...common, device_type: "gateway", config: { role: "gateway", summary: "Temporary UI performance fixture" } };
      }
    });
    const network_connections = Array.from({ length: nodeCount - 1 }, (_, index) => ({
      connection_id: `map-link-${index.toString().padStart(3, "0")}`,
      source_node_id: devices[index].node_id,
      target_node_id: devices[index + 1].node_id,
      label: "Ethernet",
    }));
    const created = await requireOk(await api.post(`${base}/api/scenarios`, {
      data: { name: `Temporary map benchmark ${new Date().toISOString()}`, network: { subnet: scenarioSubnet }, devices, network_connections },
    }), "Create temporary map scenario");
    const scenario = await created.json();
    scenarioId = scenario.id;

    const renderSamples = [];
    for (let run = 0; run < renderRuns; run += 1) {
      await page.goto(base);
      try {
        await page.waitForFunction(() => document.querySelector("#api-indicator")?.textContent === "API conectada",
          null, { timeout: 15000 });
      } catch (error) {
        const indicator = await page.locator("#api-indicator").textContent().catch(() => "missing");
        const startup = await page.locator('[role="alert"]').allTextContents().catch(() => []);
        throw new Error(`${error.message}; API indicator=${indicator}; startup alerts=${startup.join(" | ") || "none"}; browser errors=${errors.join(" | ") || "none"}; console errors=${consoleErrors.join(" | ") || "none"}; failed requests=${failedRequests.join(" | ") || "none"}`);
      }
      const started = Date.now();
      await page.locator(`[data-scenario-id="${scenarioId}"]`).click();
      await page.waitForFunction((expected) => document.querySelectorAll(".drawflow-node").length === expected,
        nodeCount, { timeout: renderLimitMs });
      await page.waitForFunction((expected) => document.querySelectorAll(".map-link.ethernet").length === expected,
        nodeCount - 1, { timeout: renderLimitMs });
      const renderMs = Date.now() - started;
      renderSamples.push(renderMs);
      assert(renderMs <= renderLimitMs, `100-node map run ${run + 1}/${renderRuns} took ${renderMs} ms (limit ${renderLimitMs} ms)`);
    }
    const sortedSamples = [...renderSamples].sort((left, right) => left - right);
    const medianRenderMs = sortedSamples[Math.floor(sortedSamples.length / 2)];
    const p95RenderMs = sortedSamples[Math.ceil(sortedSamples.length * 0.95) - 1];

    // Verify the rendered inventory and the core navigation/selection interactions.
    for (const type of ["node-plc-s7", "node-modbus-tcp", "node-hmi", "node-scada", "node-gateway"]) {
      assert.equal(await page.locator(`.drawflow-node.${type}`).count(), Math.ceil(nodeCount / 5), `Expected rendered ${type} nodes`);
    }
    const firstNode = page.locator(".drawflow-node").first();
    const firstNodeId = await firstNode.locator(".device-node-title small").textContent();
    await firstNode.click();
    await page.waitForFunction((id) => document.querySelector("#inspector")?.textContent.includes(id), firstNodeId.trim());

    const viewport = page.locator("#drawflow");
    const zoomLabel = page.locator("[data-map-zoom]");
    const beforeZoom = await zoomLabel.textContent();
    await page.locator("[data-zoom-in]").click();
    await page.waitForFunction((previous) => document.querySelector("[data-map-zoom]")?.textContent !== previous, beforeZoom);
    const zoomed = await zoomLabel.textContent();
    assert.notEqual(zoomed, beforeZoom, "Zoom control did not update the map view");

    const viewportBox = await viewport.boundingBox();
    const beforePan = await viewport.locator(".drawflow").evaluate((element) => element.style.transform);
    await page.mouse.move(viewportBox.x + 30, viewportBox.y + 30);
    await page.keyboard.down("Space");
    await page.mouse.down();
    await page.mouse.move(viewportBox.x + 90, viewportBox.y + 75, { steps: 4 });
    await page.mouse.up();
    await page.keyboard.up("Space");
    const afterPan = await viewport.locator(".drawflow").evaluate((element) => element.style.transform);
    assert.notEqual(afterPan, beforePan, "Space+drag did not pan the map");
    assert.equal(await page.locator(".drawflow-node").count(), nodeCount, "Navigation unexpectedly changed device count");
    assert.equal(await page.locator(".map-link.ethernet").count(), nodeCount - 1, "Navigation unexpectedly changed link count");

    assert.deepEqual(consoleErrors, [], `Browser console errors: ${consoleErrors.join("; ")}`);
    assert.deepEqual(failedRequests, [], `Failed browser requests: ${failedRequests.join("; ")}`);
    assert.deepEqual(errors, [], `Browser JavaScript errors: ${errors.join("; ")}`);
    console.log(`PASS: rendered mixed inventory (${nodeCount} nodes, ${nodeCount - 1} links) ${renderRuns} times in Chromium ${browserVersion}; median ${medianRenderMs} ms, p95 ${p95RenderMs} ms, max ${Math.max(...renderSamples)} ms; selection, zoom and pan work; no browser/request errors.`);
  } finally {
    if (scenarioId) {
      try {
        await page.request.post(`${base}/api/scenarios/${scenarioId}/stop`);
      } catch {}
      try {
        await page.request.delete(`${base}/api/scenarios/${scenarioId}`);
      } catch {}
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
