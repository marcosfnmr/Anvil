const assert = require('node:assert/strict');
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');
const base = process.env.ANVIL_BASE_URL || 'http://127.0.0.1:8004';

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  let scenarioId;
  let requestHeaders = { Origin: new URL(base).origin };
  page.on('pageerror', error => errors.push(error.stack || error.message));
  try {
    const email = process.env.ANVIL_TEST_EMAIL || 'range-ui@example.invalid';
    const password = process.env.ANVIL_TEST_PASSWORD || 'Range-UI-Smoke-2026!';
    const token = process.env.ANVIL_BOOTSTRAP_TOKEN || 'range-ui-temporary-bootstrap-token-2026-local';
    const auth = await (await page.request.get(`${base}/api/auth/status`)).json();
    if (auth.auth_required && !auth.authenticated) {
      const endpoint = auth.setup_required ? '/api/auth/bootstrap' : '/api/auth/login';
      const data = auth.setup_required ? { email, password, token } : { email, password };
      const response = await page.request.post(`${base}${endpoint}`, { headers: { Origin: new URL(base).origin }, data });
      assert(response.ok(), `${endpoint}: ${response.status()} ${await response.text()}`);
    }
    const authCookies = await page.context().cookies(base);
    const csrfCookie = authCookies.find(cookie => cookie.name === 'anvil_csrf');
    requestHeaders = { Origin: new URL(base).origin, ...(csrfCookie ? { 'X-CSRF-Token': csrfCookie.value } : {}) };
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#api-indicator')?.textContent === 'API conectada');
    await page.locator('#new-scenario-button').click();
    await page.locator('#scenario-name').fill('RF04 range diagnostics UI');
    await page.locator('#scenario-subnet').fill('172.30.249.0/24');
    await page.locator('[data-workspace-view="catalog"]').first().click();
    await page.locator('[data-node-type="plc-s7"]').click();
    await page.locator('[data-workspace-view="editor"]').first().click();
    await page.locator('.drawflow-node').click();
    await page.locator('[data-workspace-view="config"]:visible').first().click();
    await page.locator('[data-component-type]').selectOption('io_channel');
    await page.locator('[data-add-component="selected"]').click();
    await page.locator('[data-component-field="data_type"]').last().selectOption('REAL');
    await page.locator('[data-component-field="min_value"]').last().fill('2.5');
    await page.locator('[data-component-field="max_value"]').last().fill('75');
    await page.locator('[data-component-field="unit"]').last().fill('%');
    await page.locator('[data-component-field="name"]').last().fill('Nivel de entrada');
    await page.locator('#save-button').click();
    await page.waitForFunction(() => document.querySelector('#save-button')?.textContent === 'Guardar' && !document.querySelector('#save-button').disabled);
    const scenarios = await (await page.request.get(`${base}/api/scenarios`)).json();
    scenarioId = scenarios.find(item => item.name === 'RF04 range diagnostics UI')?.id;
    assert(scenarioId, 'Scenario was saved from the UI');
    const saved = await (await page.request.get(`${base}/api/scenarios/${scenarioId}`)).json();
    const plc = saved.devices.find(item => item.device_type === 'plc-s7');
    const channel = plc.config.components.find(item => item.name === 'Nivel de entrada');
    assert.equal(channel.min_value, 2.5);
    assert.equal(channel.max_value, 75);
    assert.equal(channel.unit, '%');
    await page.locator('[data-workspace-view="catalog"]').first().click();
    await page.locator('[data-node-type="field-variable_speed_drive"]').click();
    await page.locator('[data-field-prop="initial_speed_pct"]').fill('0');
    await page.locator('[data-field-prop="max_flow_lpm"]').fill('80');
    await page.locator('[data-port-device="command"]').selectOption(plc.node_id);
    await page.locator('[data-connect-port="command"]').click();
    await page.locator('#save-button').click();
    await page.waitForFunction(() => document.querySelector('#save-button')?.textContent === 'Guardar' && !document.querySelector('#save-button').disabled);
    const savedWithDrive = await (await page.request.get(`${base}/api/scenarios/${scenarioId}`)).json();
    const drive = savedWithDrive.field_objects.find(item => item.type === 'variable_speed_drive');
    assert.equal(drive.max_flow_lpm, 80);
    assert.equal(savedWithDrive.signal_connections.find(link => link.target.object_id === drive.object_id).target.port, 'command');
    const expanded = await (await page.request.get(`${base}/api/scenarios/${scenarioId}`)).json();
    const modbusId = 'modbus-range-ui';
    expanded.devices.push({
      node_id: modbusId, device_type: 'modbus-tcp', name: 'Modbus rango UI', ip: '172.30.249.11',
      config: { points: [{ type: 'holding_register', name: 'Presión de prueba', initial_value: 20, unit: 'kPa', min_value: 10, max_value: 40 }] },
    });
    const updated = await page.request.put(`${base}/api/scenarios/${scenarioId}`, { headers: requestHeaders, data: expanded });
    assert(updated.ok(), `Add Modbus fixture: ${updated.status()} ${await updated.text()}`);
    await page.reload();
    await page.locator(`[data-scenario-id="${scenarioId}"]`).click();
    await page.locator('[data-workspace-view="config"]:visible').first().click();
    await page.locator('#device-selector').selectOption(modbusId);
    await page.locator('[data-component-field="min_value"]').first().waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-component-field="min_value"]').count(), 1);
    assert.equal(await page.locator('[data-component-field="max_value"]').count(), 1);
    await page.locator('[data-component-field="min_value"]').fill('12');
    await page.locator('[data-component-field="max_value"]').fill('42');
    await page.locator('#save-button').click();
    await page.waitForFunction(() => document.querySelector('#save-button')?.textContent === 'Guardar' && !document.querySelector('#save-button').disabled);
    const savedWithModbus = await (await page.request.get(`${base}/api/scenarios/${scenarioId}`)).json();
    const modbus = savedWithModbus.devices.find(item => item.device_type === 'modbus-tcp');
    const register = modbus.config.points.find(item => item.name === 'Presión de prueba');
    assert.equal(register.initial_value, 20);
    assert.equal(register.min_value, 12);
    assert.equal(register.max_value, 42);
    assert.equal(register.unit, 'kPa');
    assert.deepEqual(errors, []);
    console.log('UI OK: S7 REAL/Modbus UINT16 ranges and VFD REAL setpoint can be configured and saved.');
  } finally {
    if (scenarioId) await page.request.delete(`${base}/api/scenarios/${scenarioId}`, { headers: requestHeaders });
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
