/* Real process-map smoke. Uses only its own scenario and a free subnet. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');
const base = process.env.ANVIL_BASE_URL || 'http://127.0.0.1:8004';
(async () => {
 const browser = await chromium.launch({headless:true, ...(process.env.ANVIL_BROWSER_EXECUTABLE ? {executablePath:process.env.ANVIL_BROWSER_EXECUTABLE} : process.env.ANVIL_BROWSER_CHANNEL ? {channel:process.env.ANVIL_BROWSER_CHANNEL} : {})});
 const page = await browser.newPage({viewport:{width:1600,height:1000}});
 const errors=[],dialogs=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>{dialogs.push(d.message());d.accept();});
 let id;
 async function authenticate() {
  const auth=await (await page.request.get(base+'/api/auth/status')).json();
  if(!auth.auth_required)return;
  const email=process.env.ANVIL_TEST_EMAIL,password=process.env.ANVIL_TEST_PASSWORD;
  const bootstrapToken=process.env.ANVIL_BOOTSTRAP_TOKEN;
  const endpoint=auth.setup_required?'/api/auth/bootstrap':'/api/auth/login';
  const credentials=auth.setup_required?{email,password,token:bootstrapToken}:{email,password};
  assert(email&&password&&(!auth.setup_required||bootstrapToken),'Authenticated backend requires ANVIL_TEST_EMAIL, ANVIL_TEST_PASSWORD, and on a fresh DB ANVIL_BOOTSTRAP_TOKEN');
  const response=await page.request.post(base+endpoint,{headers:{Origin:new URL(base).origin},data:credentials});
  assert(response.ok(),`${endpoint}: ${response.status()} ${await response.text()}`);
 }
 async function api(endpoint, data, method='POST') {
  const response = data===undefined ? await page.request.get(base+endpoint) : await page.request.fetch(base+endpoint,{method,data,headers:{Origin:new URL(base).origin}});
  assert(response.ok(),`${endpoint}: ${response.status()} ${await response.text()}`);return response.json();
 }
 async function waitForMapFit() {
  await page.waitForFunction(()=>{const toolbar=document.querySelector('.canvas-toolbar').getBoundingClientRect(),controls=document.querySelector('.process-map-controls').getBoundingClientRect(),bottom=Math.max(toolbar.bottom,controls.bottom);return [...document.querySelectorAll('.drawflow-node')].every(node=>node.getBoundingClientRect().top>=bottom+8);},{},{timeout:10000});
 }
 try {
  await authenticate();
  const payload=JSON.parse(fs.readFileSync(path.join(__dirname,'../examples/demo-bombeo-conectado.json'),'utf8'));
  payload.name='Process map UI smoke';payload.network.subnet='172.30.248.0/24';payload.devices.forEach(d=>d.ip=d.ip.replace('172.30.247.','172.30.248.'));
  const created=await api('/api/scenarios',payload);id=created.id;
  await page.goto(base);await page.locator(`[data-scenario-id="${id}"]`).click();
  await page.waitForSelector('.field-node');
  assert.equal(await page.locator('.drawflow-node').count(),5);
  assert.equal(await page.locator('.field-node').count(),13);
  assert.equal(await page.locator('.map-link.io').count(),13);
  assert.equal(await page.locator('.map-link.s7').count(),1);
  assert.equal(await page.locator('.map-link.hmi').count(),3);
  await page.locator('.network-link-editor summary').click();
  await page.locator('[data-network-source]').selectOption('plc-proceso');
  await page.locator('[data-network-target]').selectOption('hmi-operacion');
  await page.locator('[data-network-connect]').click();
  assert.equal(await page.locator('.map-link.ethernet').count(),1);
  assert((await page.locator('.map-link.ethernet text').textContent()).includes('Ethernet · lógico'));
  assert(await page.locator('.map-link.io').count()>0,'PLC I/O links remain visually distinct');
  assert(await page.locator('.map-link.hmi').count()>0,'HMI links remain visually distinct');
  assert.equal(await page.locator('.map-link.s7').count(),1,'S7 links remain visually distinct');
  await page.locator('[data-arrange-map]').click();
  const field=page.locator('.field-node').first();
  await field.focus();await page.keyboard.press('ArrowDown');
  const before=await field.evaluate(e=>({key:e.dataset.fieldKey,y:e.style.top}));
  await page.locator('#save-button').click();
  await page.waitForTimeout(1500);
  const saved=await api('/api/scenarios/'+id);
  assert.equal(saved.devices.length,5);
  assert.equal(saved.network_connections.length,1);
  assert.equal(saved.network_connections[0].source_node_id,'plc-proceso');
  assert.equal(saved.network_connections[0].target_node_id,'hmi-operacion');
  assert.equal(saved.canvas.field_positions[before.key].y,parseFloat(before.y));
  await page.reload();await page.locator(`[data-scenario-id="${id}"]`).click();await page.waitForSelector('.field-node');
  assert.equal(await page.locator('.field-node').first().evaluate(e=>e.style.top),before.y);
  assert.equal(await page.locator('.map-link.ethernet').count(),1,'Logical Ethernet link survives reopening the scenario');
  assert(await page.locator('.map-link.io').count()>0);
  assert(await page.locator('.map-link.hmi').count()>0);
  await page.locator('[data-show-fields]').uncheck();
  assert.equal(await page.locator('.field-node:visible').count(),0);
  assert.equal(await page.locator('.map-link.s7').count(),1);
  await page.locator('[data-show-fields]').check();
  await page.locator('.field-node button').first().click();
  assert.equal(await page.locator('body').getAttribute('data-workspace'),'config');
  assert.equal(await page.locator('#device-selector').inputValue(),'plc-proceso');
  // Preflight-warning coverage is asserted against the API in the factory scenario below.
  await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
  await page.locator('[data-fit-map]').click();
  const clipped = await page.evaluate(() => {
   const bounds=document.querySelector('#drawflow').getBoundingClientRect();
   return [...document.querySelectorAll('.field-node,.drawflow-node')].filter(e=>{const r=e.getBoundingClientRect();return r.left<bounds.left || r.top<bounds.top || r.right>bounds.right || r.bottom>bounds.bottom;}).map(e=>e.textContent);
  });
  assert.deepEqual(clipped,[], 'Centrar keeps every device and component inside the canvas');
  await page.screenshot({path:process.env.ANVIL_SCREENSHOT || path.join(require('node:os').tmpdir(),'anvil-process-map.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // The demo above exercises legacy component overlays. Use the factory template
  // to verify that explicit physical and PLC signal links coexist with Ethernet.
  await page.request.post(base+'/api/scenarios/'+id+'/stop',{headers:{Origin:new URL(base).origin}});
  await page.request.delete(base+'/api/scenarios/'+id,{headers:{Origin:new URL(base).origin}});
  id=undefined;
  await page.setViewportSize({width:1600,height:1000});
  const topologyPayload=JSON.parse(fs.readFileSync(path.join(__dirname,'../backend/templates/fabrica-campo-plc.json'),'utf8'));
  topologyPayload.name='Process map topology smoke';topologyPayload.network.subnet='172.30.248.0/24';
  topologyPayload.devices.forEach(d=>d.ip=d.ip.replace('172.30.242.','172.30.248.'));
  const topology=await api('/api/scenarios',topologyPayload);id=topology.id;
  await page.goto(base);await page.locator(`[data-scenario-id="${id}"]`).click();
  await page.waitForSelector('.device-node.independent-field');
  await waitForMapFit();
  const toolbarCollisions=await page.evaluate(()=>{const toolbar=document.querySelector('.canvas-toolbar').getBoundingClientRect(),controls=document.querySelector('.process-map-controls').getBoundingClientRect(),bottom=Math.max(toolbar.bottom,controls.bottom);return [...document.querySelectorAll('.drawflow-node')].filter(node=>node.getBoundingClientRect().top<bottom+8).map(node=>node.textContent.trim().slice(0,32));});
  assert.deepEqual(toolbarCollisions,[],'Centering should keep map nodes below the map toolbar');
  const overlaps=await page.locator('.drawflow-node').evaluateAll(nodes=>nodes.flatMap((a,i)=>nodes.slice(i+1).filter(b=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();return x.left<y.right&&x.right>y.left&&x.top<y.bottom&&x.bottom>y.top;}).map(b=>[a.textContent.trim().slice(0,32),b.textContent.trim().slice(0,32)])));
  assert.deepEqual(overlaps,[],'Factory template should keep PLCs, HMI and field objects visually separated');
  assert.equal(await page.locator('.map-link.field-process').count(),4);
  assert.equal(await page.locator('.map-link.field-signal').count(),3);
  assert.equal(await page.locator('.map-link.control').count(),2,'PLC ON/OFF rules should be drawn from sensed level to pump and inlet valve');
  const controlPaths=await page.locator('.map-link.control path').evaluateAll(paths=>paths.map(path=>{const box=path.getBoundingClientRect();return {stroke:getComputedStyle(path).stroke,width:box.width,height:box.height};}));
  assert.equal(controlPaths.length,2);
  assert(controlPaths.every(path=>path.stroke==='rgb(244, 162, 97)'&&path.width>60&&path.height>40),`Control relationships should be styled and span their visual endpoints: ${JSON.stringify(controlPaths)}`);
  assert.equal(await page.locator('.map-link.hmi').count(),2);
  assert.equal(await page.locator('.map-link.s7').count(),0);
  await page.locator('.network-link-editor summary').click();
  await page.locator('[data-network-source]').selectOption('plc-field');
  await page.locator('[data-network-target]').selectOption('hmi-field');
  await page.locator('[data-network-connect]').click();
  assert.equal(await page.locator('.map-link.ethernet').count(),1);
  assert.equal(await page.locator('.map-link.field-process').count(),4);
  assert.equal(await page.locator('.map-link.field-signal').count(),3);
  assert.equal(await page.locator('.map-link.control').count(),2);
  await page.locator('#save-button').click();await page.waitForTimeout(1000);
  const topologySaved=await api('/api/scenarios/'+id);
  assert.equal(topologySaved.network_connections.length,1);
  assert.equal(topologySaved.signal_connections.length,3);
  assert.equal(topologySaved.process_connections.length,4);
  const diagnostics=await api('/api/scenarios/'+id+'/diagnostics');
  assert(diagnostics.warnings.some(warning=>warning.code==='actuator_without_command'),
    'The API preflight should explain that actuators without PLC logic are manual only');
  assert(!diagnostics.warnings.some(warning=>warning.code==='field_process_path_incomplete'),
    'A continuous factory process path should not be flagged as incomplete');
  const fragmentedPayload={...topologyPayload,name:'Process path diagnostics smoke',
   field_objects:[...topologyPayload.field_objects,
    {object_id:'branch-a',type:'pipe',name:'Rama sin destino'},
    {object_id:'branch-b',type:'pipe',name:'Continuación sin destino'}],
   process_connections:[...topologyPayload.process_connections,
    {connection_id:'orphan-branch',source:{object_id:'branch-a',port:'process_out'},
     target:{object_id:'branch-b',port:'process_in'}}]};
  const fragmented=await api('/api/scenarios',fragmentedPayload);const fragmentedId=fragmented.id;
  try {
   const fragmentedReport=await api('/api/scenarios/'+fragmentedId+'/diagnostics');
   assert(fragmentedReport.valid,'Incomplete process paths are advisory and must remain saveable');
   assert(fragmentedReport.warnings.some(warning=>warning.code==='field_process_path_incomplete'&&warning.node_id==='branch-a'),
    'The API preflight should explain when a connected pipe branch is detached from a source and destination');
  } finally {
   await page.request.delete(base+'/api/scenarios/'+fragmentedId,{headers:{Origin:new URL(base).origin}});
  }
  await page.reload();await page.locator(`[data-scenario-id="${id}"]`).click();
  await page.waitForSelector('.device-node.independent-field');
  await waitForMapFit();
  assert.equal(await page.locator('.map-link.ethernet').count(),1);
  assert.equal(await page.locator('.map-link.field-process').count(),4);
  assert.equal(await page.locator('.map-link.field-signal').count(),3);
  assert.equal(await page.locator('.map-link.control').count(),2);
  await page.screenshot({path:process.env.ANVIL_TOPOLOGY_SCREENSHOT || path.join(require('node:os').tmpdir(),'anvil-process-topology.png')});
  assert.deepEqual(errors,[]);
  console.log('Process map OK: legacy topology and explicit process/signal/control topology; Ethernet, process, signal, control, S7 and HMI links remain distinct and persist after reopen.');
 } finally {
  if(id) {
   await page.request.post(base+'/api/scenarios/'+id+'/stop',{headers:{Origin:new URL(base).origin}});
   await page.request.delete(base+'/api/scenarios/'+id,{headers:{Origin:new URL(base).origin}});
  }
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
