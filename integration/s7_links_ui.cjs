/* Configure real S7 links through the inspector and verify their live values. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");
const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8004";

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  let ownedId;
  async function mutate(action, method, path) {
    const pending = page.waitForResponse((response) => response.request().method() === method && new URL(response.url()).pathname === path, { timeout: 90000 });
    await action();
    const response = await pending;
    assert(response.ok(), await response.text());
    const result = await response.json();
    if (method === "POST" && path === "/api/scenarios") ownedId = result.id;
    await page.waitForFunction(() => !document.querySelector("#new-scenario-button").disabled);
    return result;
  }
  try {
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator").textContent === "API conectada");
    await page.locator("#new-scenario-button").click();
    await page.locator("#scenario-subnet").fill("172.30.244.0/24");
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.locator('[data-node-type="plc-s7"]').click();
    const senderId = await page.locator("#device-selector").inputValue();
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.locator('[data-node-type="plc-s7"]').click();
    const receiverId = await page.locator("#device-selector").inputValue();
    await page.locator('select[data-component-type]').selectOption("remote_input");
    await page.locator('[data-add-component]').focus();
    await page.keyboard.press("Enter");
    await page.locator('[data-component-field="source_node_id"]').selectOption(senderId);
    await page.locator('[data-component-field="source_address"]').selectOption("DB1.DBX0.0");
    await page.locator('select[data-component-type]').selectOption("remote_input");
    await page.locator('[data-add-component]').click();
    await page.locator('[data-component-field="data_type"]').last().selectOption("REAL");
    await page.locator('[data-component-field="source_node_id"]').last().selectOption(senderId);
    await page.locator('[data-component-field="source_address"]').last().selectOption("DB1.DBD4");
    const created = await mutate(() => page.locator("#save-button").click(), "POST", "/api/scenarios");
    const configured = created.devices.find((device) => device.node_id === receiverId).config.components.filter((component) => component.type === "remote_input");
    assert.deepEqual(configured.map((item) => [item.source_node_id, item.source_address, item.source_name]), [[senderId, "DB1.DBX0.0", "valvula_1"], [senderId, "DB1.DBD4", "bomba_1"]]);
    await page.reload();
    await page.locator(`[data-scenario-id="${ownedId}"]`).click();
    await page.locator('[data-workspace-view="config"]').click();
    await page.locator("#device-selector").selectOption(receiverId);
    assert.equal(await page.locator('[data-component-field="source_address"]').last().inputValue(), "DB1.DBD4");
    if (process.env.ANVIL_AXE_PATH) {
      await page.addScriptTag({ path: process.env.ANVIL_AXE_PATH });
      const audit = await page.evaluate(() => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
      assert.deepEqual(audit.violations.map((item) => item.id), []);
    }
    await mutate(() => page.locator("#deploy-button").click(), "POST", `/api/scenarios/${ownedId}/deploy`);
    await page.locator('.workspace-navigation [data-workspace-view="telemetry"]').click();
    await page.waitForFunction(() => [...document.querySelectorAll("[data-link-status]")].length === 2 && [...document.querySelectorAll("[data-link-status]")].every((item) => item.textContent.includes("Dato vigente")));
    for (const address of ["DB1.DBX0.0", "DB1.DBX2.0"]) {
      await mutate(() => page.locator(`[data-node-id="${senderId}"][data-write-address="${address}"]`).click(), "POST", `/api/scenarios/${ownedId}/plcs/${senderId}/write`);
    }
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll(".io-point")].filter((row) => row.querySelector("[data-link-status]"));
      return rows.some((row) => row.querySelector("[data-current-value]").textContent === "ON") && rows.some((row) => row.querySelector("[data-current-value]").textContent === "60");
    });
    assert.equal(await page.locator('[data-node-id="' + receiverId + '"][data-write-address="DB1.DBX8.0"]').count(), 0);
    await page.locator('[data-telemetry-device]').selectOption(receiverId);
    assert.equal(await page.locator('.io-point:visible [data-write-address][data-node-id="' + senderId + '"]').count(), 0);
    assert.equal(await page.locator('.io-point:visible [data-link-status]').count(), 2);
    await page.locator('[data-telemetry-device]').selectOption("");
    if (process.env.ANVIL_UI_SCREENSHOT) await page.screenshot({ path: process.env.ANVIL_UI_SCREENSHOT, fullPage: true });
    assert.deepEqual(errors, []);
    console.log("S7 links UI OK: keyboard, source/type selectors, computed addresses, persistence, live BOOL/REAL, read-only remote inputs.");
  } finally {
    if (ownedId) {
      await page.request.post(`${base}/api/scenarios/${ownedId}/stop`);
      await page.request.delete(`${base}/api/scenarios/${ownedId}`);
    }
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
