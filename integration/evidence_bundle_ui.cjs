/* Verify an authenticated scenario can export its real evidence ZIP in the browser. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");
const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8004";

async function authenticate(request) {
  const origin = new URL(base).origin;
  const headers = { Origin: origin, "Content-Type": "application/json" };
  const status = await request.get(`${base}/api/auth/status`);
  assert(status.ok(), `Auth status failed: HTTP ${status.status()}`);
  const auth = await status.json();
  if (!auth.auth_required) return { authRequired: false };
  const email = process.env.ANVIL_TEST_EMAIL;
  const password = process.env.ANVIL_TEST_PASSWORD;
  const bootstrapToken = process.env.ANVIL_BOOTSTRAP_TOKEN;
  const path = auth.setup_required ? "/api/auth/bootstrap" : "/api/auth/login";
  const data = auth.setup_required ? { email, password, token: bootstrapToken } : { email, password };
  assert(email && password && (!auth.setup_required || bootstrapToken),
    "Authenticated backend requires ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD; bootstrap on a new DB also requires ANVIL_BOOTSTRAP_TOKEN");
  const response = await request.post(`${base}${path}`, { headers, data });
  assert(response.ok(), `${path} failed: HTTP ${response.status()} ${await response.text()}`);
  return { authRequired: auth.auth_required };
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  let scenarioId;
  try {
    const authState = await authenticate(page.request);
    const created = await page.request.post(`${base}/api/scenarios`, {
      headers: { Origin: new URL(base).origin },
      data: {
        name: "Prueba descarga evidencias",
        network: {
          subnet: "172.30.0.0/16",
          zones: [
            { zone_id: "control", name: "Control", purdue_level: "L2", subnet: "172.30.247.0/24" },
            { zone_id: "operations", name: "Operations", purdue_level: "L3", subnet: "172.30.248.0/24" },
          ],
        },
        devices: [{
          node_id: "plc-evidence",
          device_type: "plc-s7",
          name: "PLC evidencia",
          ip: "172.30.247.10",
          zone_id: "control",
          config: { components: [] },
        }, {
          node_id: "plc-peer",
          device_type: "plc-s7",
          name: "PLC peer",
          ip: "172.30.247.11",
          zone_id: "control",
          config: { components: [] },
        }, {
          node_id: "plc-other-zone",
          device_type: "plc-s7",
          name: "PLC en otra zona",
          ip: "172.30.248.10",
          zone_id: "operations",
          config: { components: [] },
        }],
      },
    });
    assert.equal(created.status(), 201, await created.text());
    scenarioId = (await created.json()).id;

    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator")?.textContent === "API conectada");
    if (authState.authRequired) {
      await page.waitForFunction(() => window.anvilUser?.role === "admin");
    }
    await page.locator(`[data-scenario-id="${scenarioId}"]`).click();
    await page.locator('[data-workspace-view="telemetry"]').click();
    await page.waitForFunction(() => {
      const tab = document.querySelector('[data-runtime-tab="capture"]');
      return Boolean(tab && !tab.hidden && tab.getClientRects().length);
    });
    await page.locator('[data-runtime-tab="capture"]').click();
    await page.locator('[data-capture-scope]').selectOption("link");
    const linkSource = page.locator("[data-capture-link-source]");
    const linkTarget = page.locator("[data-capture-link-target]");
    assert.equal(await linkSource.locator("option").count(), 3, "all scenario devices can be selected as capture origin");
    assert.equal(await linkTarget.locator("option").count(), 1, "target excludes origin and devices outside its zone");
    assert.equal(await linkTarget.inputValue(), "plc-peer");
    await linkSource.selectOption("plc-peer");
    assert.equal(await linkTarget.locator("option").count(), 1);
    assert.equal(await linkTarget.inputValue(), "plc-evidence");
    assert(await page.locator("[data-capture-link-wrap]").first().isVisible());
    assert(await page.locator("[data-capture-link-wrap]").last().isVisible());

    const includeCapture = page.locator("[data-evidence-include-capture]");
    assert.equal(await includeCapture.isChecked(), false, "active capture is not included implicitly");
    assert.equal(await includeCapture.isDisabled(), true, "there is no capture to include");

    const [download, exportResponse] = await Promise.all([
      page.waitForEvent("download"),
      page.waitForResponse((response) => response.url().includes(`/api/scenarios/${scenarioId}/evidence-bundle.zip`)),
      page.locator("[data-evidence-export]").click(),
    ]);
    assert.equal(download.suggestedFilename(), `anvil-evidence-${scenarioId}.zip`);
    assert.equal(exportResponse.status(), 200, `evidence endpoint failed: ${await exportResponse.text()}`);
    assert.match(exportResponse.headers()["content-type"] || "", /application\/zip/i);
    const requestUrl = exportResponse.url();
    assert(!new URL(requestUrl).searchParams.has("capture_id"), "default export leaves active captures untouched");
    const manifestHash = exportResponse.headers()["x-anvil-manifest-sha256"] || "";
    assert.match(manifestHash, /^[a-f0-9]{64}$/i, "real export includes a manifest SHA-256 header");
    const downloadPath = await download.path();
    assert(downloadPath, "browser saved the evidence download");
    const downloadedBytes = fs.statSync(downloadPath).size;
    assert(downloadedBytes > 22, `real export contains audit/log evidence, not an empty placeholder ZIP (received ${downloadedBytes} bytes)`);
    const captureStatus = await page.locator("[data-capture-status]").textContent();
    assert(captureStatus.includes("ZIP descargado"));
    assert(!captureStatus.includes("0.0 KiB"), "UI reports a non-empty bundle size");
    console.log("Evidence bundle UI OK: same-zone selection, default excludes capture, real ZIP endpoint, manifest hash and browser download.");
  } finally {
    if (scenarioId) await page.request.delete(`${base}/api/scenarios/${scenarioId}`, {
      headers: { Origin: new URL(base).origin },
    }).catch(() => {});
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
