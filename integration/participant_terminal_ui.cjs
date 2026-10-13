const assert = require('node:assert/strict');
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');

const base = process.env.ANVIL_BASE_URL || 'http://127.0.0.1:8007';
const adminEmail = `terminal-admin-${Date.now()}@example.test`;
const adminPassword = 'terminal-admin-password-strong-1';
const participantEmail = `terminal-participant-${Date.now()}@example.test`;
const participantPassword = 'terminal-participant-password-strong-1';
const bootstrapToken = process.env.ANVIL_BOOTSTRAP_TOKEN || 'terminal-ui-bootstrap-token-20261008';

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ANVIL_BROWSER_EXECUTABLE ? { executablePath: process.env.ANVIL_BROWSER_EXECUTABLE } : {}),
  });
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  const errors = [];
  let scenarioId;
  let nodeId;
  adminPage.on('pageerror', (error) => errors.push(error.message));
  adminPage.on('dialog', (dialog) => dialog.accept());

  try {
    await adminPage.goto(base);
    await adminPage.locator('#auth-form [name="email"]').fill(adminEmail);
    await adminPage.locator('#auth-form [name="password"]').fill(adminPassword);
    await adminPage.locator('#auth-form [name="token"]').fill(bootstrapToken);
    await adminPage.locator('#auth-form button[type="submit"]').click();
    await adminPage.waitForFunction(() => document.querySelector('#api-indicator')?.textContent === 'API conectada');

    const user = await adminPage.request.post(`${base}/api/users`, {
      headers: { Origin: base },
      data: { email: participantEmail, password: participantPassword, role: 'participant' },
    });
    assert.equal(user.status(), 201, await user.text());

    const name = `Participant terminal UI ${Date.now()}`;
    await adminPage.locator('#new-scenario-button').click();
    await adminPage.locator('#scenario-name').fill(name);
    await adminPage.locator('#scenario-subnet').fill('172.30.251.0/24');
    await adminPage.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
    await adminPage.locator('[data-node-type="attacker-station"]').click();
    await adminPage.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await adminPage.locator('.page-editor [data-workspace-view="config"]').click();
    await adminPage.locator('#inspector .protocol-settings summary').click();
    await adminPage.locator('[data-device-field="config.participant_access_enabled"]').check();
    await adminPage.locator('#save-button').click();
    await adminPage.waitForFunction(() => document.querySelector('#save-button').textContent === 'Guardar'
      && !document.querySelector('#save-button').disabled);
    const scenarios = await (await adminPage.request.get(`${base}/api/scenarios`)).json();
    const scenario = scenarios.find((item) => item.name === name);
    assert(scenario, 'Scenario saved from the catalog');
    scenarioId = scenario.id;
    nodeId = scenario.devices[0].node_id;
    assert.equal(scenario.devices[0].config.participant_access_enabled, true, 'Opt-in saved from inspector');

    const membership = await adminPage.request.post(`${base}/api/scenarios/${scenarioId}/members`, {
      headers: { Origin: base },
      data: { email: participantEmail, role: 'participant' },
    });
    assert.equal(membership.status(), 201, await membership.text());
    const deployed = await adminPage.request.post(`${base}/api/scenarios/${scenarioId}/deploy`, { headers: { Origin: base } });
    assert.equal(deployed.status(), 200, await deployed.text());

    const participantContext = await browser.newContext();
    const auth = await participantContext.request.post(`${base}/api/auth/login`, {
      headers: { Origin: base },
      data: { email: participantEmail, password: participantPassword },
    });
    assert.equal(auth.status(), 200, await auth.text());
    const participantPage = await participantContext.newPage();
    participantPage.on('pageerror', (error) => errors.push(error.message));
    await participantPage.goto(base);
    await participantPage.waitForFunction(() => document.querySelector('#api-indicator')?.textContent === 'API conectada');
    await participantPage.locator(`[data-scenario-id="${scenarioId}"]`).click();
    await participantPage.waitForFunction(() => document.querySelector('#scenario-status')?.textContent.includes('En ejecución'));
    await participantPage.locator('.workspace-navigation [data-workspace-view="editor"]').click();
    await participantPage.locator('.page-editor [data-workspace-view="config"]').click();
    await participantPage.locator('#device-selector').selectOption(nodeId);
    const terminalButton = participantPage.locator(`[data-participant-terminal="${nodeId}"]`);
    await terminalButton.waitFor({ state: 'visible' });
    await participantPage.waitForFunction((id) => {
      const button = document.querySelector(`[data-participant-terminal="${CSS.escape(id)}"]`);
      return button && !button.disabled;
    }, nodeId, { timeout: 5000 }).catch(async () => {
      const diagnostic = await participantPage.evaluate(() => ({
        workspace: document.body.dataset.workspace,
        status: document.querySelector('#scenario-status')?.textContent,
        save: document.querySelector('#save-button')?.textContent,
        button: document.querySelector('[data-participant-terminal]')?.outerHTML,
      }));
      throw new Error(`Participant terminal remained disabled: ${JSON.stringify(diagnostic)}`);
    });
    await terminalButton.click({ timeout: 5000 }).catch(async () => {
      const diagnostic = await participantPage.evaluate(() => ({
        status: document.querySelector('#scenario-status')?.textContent,
        save: document.querySelector('#save-button')?.textContent,
        button: document.querySelector('[data-participant-terminal]')?.outerHTML,
        saveDisabled: document.querySelector('#save-button')?.disabled,
      }));
      throw new Error(`Terminal click no longer enabled: ${JSON.stringify(diagnostic)}`);
    });
    const dialog = participantPage.locator('#participant-terminal-dialog');
    await dialog.waitFor({ state: 'visible' });
    await participantPage.waitForFunction(() => document.querySelector('[data-terminal-status]')?.textContent.includes('Terminal activo'));
    await participantPage.locator('[data-terminal-input]').fill('id -u');
    await dialog.locator('[data-terminal-form] [type="submit"]').click();
    await participantPage.waitForFunction(() => document.querySelector('[data-terminal-output]')?.textContent.includes('65534'),
      {}, { timeout: 10000 });
    await participantPage.locator('[data-terminal-input]').fill('exit');
    await dialog.locator('[data-terminal-form] [type="submit"]').click();
    await participantPage.waitForFunction(() => document.querySelector('[data-terminal-status]')?.textContent === 'Terminal desconectado.',
      {}, { timeout: 10000 });
    assert.deepEqual(errors, []);
    await participantContext.close();
    console.log('Participant terminal UI OK: opt-in station, member login, role-visible terminal, UID 65534 and clean shell exit.');
  } finally {
    if (scenarioId) {
      await adminPage.request.post(`${base}/api/scenarios/${scenarioId}/stop`, { headers: { Origin: base } }).catch(() => {});
      await adminPage.request.delete(`${base}/api/scenarios/${scenarioId}`, { headers: { Origin: base } }).catch(() => {});
    }
    await adminContext.close();
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
