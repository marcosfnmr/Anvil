/* Exercise shared component preset editing, stale-write handling and restore in a real browser. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || "playwright");

const base = process.env.ANVIL_BASE_URL || "http://127.0.0.1:8010";
const email = process.env.ANVIL_TEST_EMAIL;
const password = process.env.ANVIL_TEST_PASSWORD;
const bootstrapToken = process.env.ANVIL_BOOTSTRAP_TOKEN;
let presetId;

async function requireOk(response, label) {
  if (!response.ok()) throw new Error(`${label}: HTTP ${response.status()} ${await response.text()}`);
  return response;
}

async function main() {
  assert(email && password, "Set ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD for an isolated test account");
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
    ...(process.env.ANVIL_BROWSER_CHANNEL ? { channel: process.env.ANVIL_BROWSER_CHANNEL } : {}),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  page.setDefaultTimeout(10_000);
  const origin = new URL(base).origin;

  try {
    const authStatus = await requireOk(await page.request.get(`${base}/api/auth/status`), "Read auth status");
    const auth = await authStatus.json();
    if (auth.auth_required && !auth.authenticated) {
      const endpoint = auth.setup_required ? "/api/auth/bootstrap" : "/api/auth/login";
      if (auth.setup_required) assert(bootstrapToken, "A fresh isolated database requires ANVIL_BOOTSTRAP_TOKEN");
      const credentials = auth.setup_required
        ? { email, password, token: bootstrapToken }
        : { email, password };
      await requireOk(await page.request.post(`${base}${endpoint}`, {
        headers: { Origin: origin }, data: credentials,
      }), endpoint);
    }

    const created = await requireOk(await page.request.post(`${base}/api/components/templates`, {
      headers: { Origin: origin },
      data: {
        title: "Preset UI de prueba",
        description: "Válvula inicial para validar el editor de revisiones.",
        device_type: "plc-s7",
        component_type: "valve",
        config: { type: "valve", name: "valve_ui_v1", db: 1, initial_state: false },
      },
    }), "Publish temporary component preset");
    const preset = await created.json();
    presetId = preset.id;
    assert.equal(preset.revision, 1);

    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#api-indicator")?.textContent === "API conectada");
    await page.locator("#component-library-button").click();
    const card = page.locator("#component-library-list .template-card").filter({ hasText: preset.title });
    await card.waitFor();
    await card.locator("[data-edit-component-preset]").click();
    const edit = page.locator("#edit-component-preset-form");
    await edit.locator('[name="title"]').fill("Preset UI revisado");
    await edit.locator('[name="description"]').fill("Configuración revisada desde el editor web de Anvil.");
    await edit.locator('[name="config"]').fill(JSON.stringify({
      type: "valve", name: "valve_ui_v2", db: 1, initial_state: true,
    }, null, 2));
    const firstUpdate = page.waitForResponse((response) => response.request().method() === "PUT"
      && new URL(response.url()).pathname === `/api/components/templates/${presetId}`);
    await edit.locator('[type="submit"]').click();
    const revision2 = await requireOk(await firstUpdate, "Edit preset through UI");
    assert.equal((await revision2.json()).revision, 2);
    await card.waitFor({ state: "detached" });
    const revision2Card = page.locator("#component-library-list .template-card").filter({ hasText: "Preset UI revisado" });
    await revision2Card.waitFor();
    assert((await revision2Card.innerText()).includes("v2"));

    await revision2Card.locator("[data-edit-component-preset]").click();
    await edit.locator('[name="title"]').fill("Edición con revisión obsoleta");
    await edit.locator('[name="description"]').fill("Este cambio debe detectar la edición concurrente.");
    const concurrent = await requireOk(await page.request.put(`${base}/api/components/templates/${presetId}`, {
      headers: { Origin: origin },
      data: { expected_revision: 2, title: "Cambio concurrente", description: "Actualización hecha por otro editor." },
    }), "Simulate a concurrent editor");
    assert.equal((await concurrent.json()).revision, 3);
    const staleUpdate = page.waitForResponse((response) => response.request().method() === "PUT"
      && new URL(response.url()).pathname === `/api/components/templates/${presetId}`);
    await edit.locator('[type="submit"]').click();
    const conflict = await staleUpdate;
    assert.equal(conflict.status(), 409, await conflict.text());
    const error = page.locator("#edit-component-preset-error");
    await error.waitFor({ state: "visible" });
    assert((await error.textContent()).includes("current revision is 3"), "the edit form explains the stale revision conflict");
    await page.locator('#edit-component-preset-dialog [aria-label="Cerrar edición de preset"]').click();
    await page.locator('#component-library-dialog [aria-label="Cerrar componentes compartidos"]').click();
    await page.locator("#component-library-button").click();

    const latestCard = page.locator("#component-library-list .template-card").filter({ hasText: "Cambio concurrente" });
    await latestCard.locator("[data-component-history]").click();
    const history = page.locator("#component-preset-history-list");
    await page.locator("[data-restore-component-preset='1']").waitFor();
    assert.equal(await history.locator("[data-restore-component-preset]").count(), 3);
    const restore = page.waitForResponse((response) => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/components/templates/${presetId}/revisions/1/restore`);
    await page.locator("[data-restore-component-preset='1']").click();
    const revision4 = await requireOk(await restore, "Restore original preset revision through UI");
    const restored = await revision4.json();
    assert.equal(restored.revision, 4);
    assert.equal(restored.title, "Preset UI de prueba");
    assert.equal(restored.config.name, "valve_ui_v1");

    const finalHistory = await requireOk(await page.request.get(`${base}/api/components/templates/${presetId}/revisions`), "Read final revision history");
    assert.deepEqual((await finalHistory.json()).map((item) => item.version), [4, 3, 2, 1]);
    assert.deepEqual(errors, [], `Browser JavaScript errors: ${errors.join("; ")}`);
    console.log("Component preset revision UI passed: edit, detect stale write, browse history, and restore as a new revision.");
  } finally {
    if (presetId) {
      const removed = await page.request.delete(`${base}/api/components/templates/${presetId}`, {
        headers: { Origin: origin },
      });
      assert([204, 404].includes(removed.status()), `Temporary preset cleanup: HTTP ${removed.status()} ${await removed.text()}`);
    }
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
