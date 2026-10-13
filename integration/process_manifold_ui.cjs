const assert = require('node:assert/strict');
const { chromium, request } = require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');

const base = process.env.ANVIL_BASE_URL || 'http://127.0.0.1:8003';

(async () => {
  const api = await request.newContext({ baseURL: base });
  const auth = await (await api.get('/api/auth/status')).json();
  if (auth.auth_required) {
    const email = process.env.ANVIL_TEST_EMAIL;
    const password = process.env.ANVIL_TEST_PASSWORD;
    if (!email || !password) throw new Error('Set ANVIL_TEST_EMAIL and ANVIL_TEST_PASSWORD for this authenticated UI smoke');
    const path = auth.setup_required ? '/api/auth/bootstrap' : '/api/auth/login';
    const payload = auth.setup_required
      ? { email, password, token: process.env.ANVIL_BOOTSTRAP_TOKEN }
      : { email, password };
    const login = await api.post(path, { data: payload, headers: { Origin: base } });
    if (!login.ok()) throw new Error(`${path} failed: ${login.status()} ${await login.text()}`);
  }
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
  });
  const context = await browser.newContext({ storageState: await api.storageState() });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#api-indicator')?.textContent === 'API conectada');
    await page.locator('.workspace-navigation [data-workspace-view="projects"]').click();
    await page.locator('#new-scenario-button').click();
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.locator('[data-node-type="field-flow_splitter"]').click();
    await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await page.locator('[data-node-type="field-flow_mixer"]').click();
    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    assert.equal(await page.locator('.independent-field').count(), 2);

    const splitter = page.locator('.independent-field[data-object-id]').filter({ hasText: 'Distribuidor de caudal' }).first();
    await splitter.locator('[data-open-field]').click();
    const ratio = page.locator('[data-field-prop="split_ratio_pct"]');
    assert.equal(await ratio.inputValue(), '50');
    await ratio.fill('35');
    await ratio.press('Tab');
    assert.equal(await ratio.inputValue(), '35');
    assert.match(await page.locator('#inspector').innerText(), /Rama A/);
    assert.match(await page.locator('#inspector').innerText(), /Rama B/);

    await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    const mixer = page.locator('.independent-field[data-object-id]').filter({ hasText: 'Colector de caudal' }).first();
    await mixer.locator('[data-open-field]').click();
    assert.match(await page.locator('#inspector').innerText(), /Entrada A/);
    assert.match(await page.locator('#inspector').innerText(), /Entrada B/);
    assert.deepEqual(errors, []);
    console.log('PASS: catálogo crea distribuidor y colector; el inspector permite ajustar la división y muestra los puertos físicos. No se guardó ningún escenario.');
  } finally {
    await context.close();
    await browser.close();
    await api.dispose();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
