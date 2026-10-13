const assert=require('node:assert/strict');
const {chromium}=require(process.env.ANVIL_PLAYWRIGHT_PATH||'playwright');
const base=process.env.ANVIL_BASE_URL||'http://127.0.0.1:8003';
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ANVIL_BROWSER_EXECUTABLE?{executablePath:process.env.ANVIL_BROWSER_EXECUTABLE}:{})});
 const page=await browser.newPage();let sid;const errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 const get=async path=>(await page.request.get(base+path)).json();
 const add=async type=>{await page.locator('.workspace-navigation [data-workspace-view="catalog"]').click();await page.locator(`[data-node-type="${type}"]`).click();};
 const save=async()=>{await page.locator('#save-button').click();await page.waitForFunction(()=>document.querySelector('#save-button').textContent==='Guardar'&&!document.querySelector('#save-button').disabled);};
 const selectField=async oid=>{if(await page.locator('body').getAttribute('data-workspace')!=='config'){await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();await page.locator('[data-workspace-view="config"]').first().click();}await page.locator('#device-selector').selectOption(oid);};
 const connect=async(port,node)=>{await page.locator(`[data-port-device="${port}"]`).selectOption(node);await page.locator(`[data-connect-port="${port}"]`).click();};
 try{
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#api-indicator').textContent==='API conectada');
  await page.locator('#new-scenario-button').click();const name='FIELD UI '+Date.now();
  await page.locator('#scenario-name').fill(name);await page.locator('#scenario-subnet').fill('172.30.244.0/24');
  await add('field-valve');await page.locator('[data-field-prop="name"]').fill('Válvula libre');await page.locator('[data-field-prop="response_seconds"]').fill('0.4');await page.locator('[data-field-prop="response_seconds"]').press('Tab');
  await add('field-analog_sensor');await add('field-pump');await save();
  let scenario=(await get('/api/scenarios')).find(s=>s.name===name);sid=scenario.id;
  assert.equal(scenario.devices.length,0);assert.equal(scenario.field_objects.length,3);
  const valve=scenario.field_objects.find(f=>f.type==='valve'),sensor=scenario.field_objects.find(f=>f.type==='analog_sensor'),pump=scenario.field_objects.find(f=>f.type==='pump');
  await add('plc-s7');await add('modbus-tcp');await save();scenario=await get('/api/scenarios/'+sid);
  const plc=scenario.devices.find(d=>d.device_type==='plc-s7'),mb=scenario.devices.find(d=>d.device_type==='modbus-tcp');
  assert.equal(plc.config.components.length,0);
  await selectField(valve.object_id);await connect('command',plc.node_id);await connect('state',plc.node_id);await connect('fault',plc.node_id);
  await selectField(sensor.object_id);await connect('value',plc.node_id);
  await selectField(pump.object_id);await connect('command',mb.node_id);await connect('state',mb.node_id);await connect('flow',mb.node_id);
  await save();scenario=await get('/api/scenarios/'+sid);
  assert.equal(scenario.signal_connections.length,7);assert(scenario.devices.find(d=>d.node_id===plc.node_id).config.components.every(c=>c.type==='io_channel'));
  const connections=structuredClone(scenario.signal_connections),positions=scenario.field_objects.map(f=>f.position);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#api-indicator').textContent==='API conectada');
  await page.locator(`[data-scenario-id="${sid}"]`).click();await page.waitForFunction(()=>document.querySelectorAll('.independent-field').length===3);assert.equal(await page.locator('.field-signal').count(),7);
  scenario=await get('/api/scenarios/'+sid);assert.deepEqual(scenario.signal_connections,connections);assert.deepEqual(scenario.field_objects.map(f=>f.position),positions);
  const deployment=page.waitForResponse(r=>r.url().endsWith(`/api/scenarios/${sid}/deploy`)&&r.request().method()==='POST',{timeout:60000});
  await page.locator('#deploy-button').click();const deployed=await deployment;assert(deployed.ok(),await deployed.text());await page.waitForFunction(()=>document.querySelector('#scenario-status').textContent.includes('En ejecución'),{},{timeout:45000});
  const order=connections.find(l=>l.target.object_id===valve.object_id&&l.target.port==='command').source.address;
  let response=await page.request.post(`${base}/api/scenarios/${sid}/plcs/${plc.node_id}/write`,{data:{address:order,value:true}});assert(response.ok(),await response.text());
  await page.waitForFunction(oid=>document.querySelector(`[data-object-id="${oid}"] .field-live`)?.textContent.includes('Activo / abierto · orden activa'),valve.object_id,{timeout:20000});
  await selectField(valve.object_id);await page.locator('[data-field-fault="true"]').click();
  response=await page.request.post(`${base}/api/scenarios/${sid}/plcs/${plc.node_id}/write`,{data:{address:order,value:false}});assert(response.ok());
  await page.waitForFunction(oid=>document.querySelector(`[data-object-id="${oid}"] .field-live`)?.textContent.includes('orden inactiva')&&document.querySelector(`[data-object-id="${oid}"] .field-live`)?.textContent.includes('FALLO'),valve.object_id,{timeout:15000});
  await page.locator('[data-field-fault="false"]').click();
  await page.waitForFunction(oid=>document.querySelector(`[data-object-id="${oid}"] .field-live`)?.textContent.includes('Inactivo / cerrado'),valve.object_id,{timeout:15000});
  await page.setViewportSize({width:390,height:844});await page.locator('.workspace-navigation [data-workspace-view="editor"]').click();
  await page.locator('.workspace-navigation [data-workspace-view="projects"]').click();await page.locator('#new-scenario-button').click();
  assert.equal(await page.locator('.independent-field').count(),0);assert.equal(await page.locator('[data-field-prop]').count(),0);
  assert.deepEqual(errors,[]);
  console.log('Field UI OK in Brave: free objects before PLC, automatic S7/Modbus channels, 7 lines, saved positions, real valve command/feedback/fault and mobile; no JS errors.');
 }finally{
  if(sid){await page.request.post(base+'/api/scenarios/'+sid+'/stop');await page.request.delete(base+'/api/scenarios/'+sid);}
  await browser.close();
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
