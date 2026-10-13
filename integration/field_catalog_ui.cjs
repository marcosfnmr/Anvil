const assert = require('node:assert/strict');
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');
const base = process.env.ANVIL_BASE_URL || 'http://localhost:8003';

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}) });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  try {
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#api-indicator').textContent === 'API conectada');
    const scenarios = await (await page.request.get(base + '/api/scenarios')).json();
    const legacy = scenarios.find(s => s.status === 'stopped' && !s.field_objects.length && s.devices.some(d => d.config.components?.some(c => c.type === 'valve')));
    if (legacy) {
      await page.locator(`[data-scenario-id="${legacy.id}"]`).click();
      const card = page.locator('.field-node[data-component-type="valve"]').first();
      await card.locator('[data-configure-legacy]').click();
      assert.equal(await page.locator('body').getAttribute('data-workspace'), 'config');
      assert.equal(await page.locator('[data-field-prop]').count(), 0);
      assert.equal(await page.locator('[data-legacy-field-editor]').count(), 1);
      assert.equal(await page.locator('[data-device-field]').count(), 0);
      assert.equal(await page.locator('#inspector .component-card').count(), 1);
      assert.equal(await page.locator('#heading-config').innerText(), 'Configura válvula');
      const oldName = await page.locator('[data-component-field="name"]').inputValue();
      await page.locator('[data-component-field="name"]').fill(oldName + ' UI CHECK');
      await page.locator('[data-component-field="name"]').press('Tab');
      await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
      const renamed = page.locator('.field-node[data-component-type="valve"]').filter({hasText: oldName + ' UI CHECK'}).first();
      await renamed.locator('[data-configure-legacy]').click();
      assert.equal(await page.locator('[data-component-field="name"]').inputValue(), oldName + ' UI CHECK');
      await page.locator('[data-component-field="name"]').fill(oldName);
      await page.locator('[data-component-field="name"]').press('Tab');
      await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
      await card.locator('[data-workspace-view="catalog"]').click();
      assert.equal(await page.locator('body').getAttribute('data-workspace'), 'catalog');
    }
    await page.locator('.workspace-navigation [data-workspace-view="projects"]').click();
    await page.locator('#new-scenario-button').click();
    await page.locator('[data-workspace-view="catalog"]').first().click();
    assert.equal(await page.locator('.catalog-group').count(), 2);
    await page.locator('[data-node-type="field-valve"]').click();
    await page.locator('[data-field-prop="name"]').fill('Válvula independiente');
    await page.locator('[data-field-prop="name"]').press('Tab');
    assert.equal(await page.locator('#heading-config').innerText(), 'Configura el elemento de campo');
    assert.equal(await page.locator('[data-node-id]').count(), 0);
    assert.equal(await page.locator('.independent-field').count(), 1);
    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await page.locator('[data-open-field]').click();
    assert.equal(await page.locator('body').getAttribute('data-workspace'), 'config');
    assert.equal(await page.locator('[data-field-prop="name"]').inputValue(), 'Válvula independiente');
    assert.equal(await page.locator('[data-port]').count(), 3);
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.locator('[data-node-type="plc-s7"]').click();
    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await page.locator('[data-open-field]').click();
    assert.equal(await page.locator('[data-field-prop="name"]').inputValue(), 'Válvula independiente');
    assert.equal(await page.locator('[data-device-field]').count(), 0);
    assert.equal(await page.locator('[data-port-device="command"] option').count(), 2);
    assert.deepEqual(errors, []);
    console.log('PASS: catálogo, válvula sin PLC, botón Configurar y conectar y acceso desde componentes antiguos; ningún escenario guardado o modificado.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
