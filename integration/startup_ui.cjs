const assert=require('node:assert/strict');
const {chromium}=require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');
const base=process.env.ANVIL_BASE_URL || 'http://localhost:8003';
(async()=>{
 const b=await chromium.launch({headless:true,...(process.env.ANVIL_BROWSER_EXECUTABLE?{executablePath:process.env.ANVIL_BROWSER_EXECUTABLE}:process.env.ANVIL_BROWSER_CHANNEL?{channel:process.env.ANVIL_BROWSER_CHANNEL}:{})});
 const p=await b.newPage();const errors=[];let created;
 p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
 try {
  await p.addInitScript(()=>{window.ResizeObserver=undefined;});
  let staleCanvasRequested=false;
  await p.route(/\/canvas\.js$/,r=>{staleCanvasRequested=true;return r.fulfill({contentType:'application/javascript',body:"throw new Error('Stale unversioned canvas module loaded');"});});
  await p.route('**/api/scenarios/catalog',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Startup smoke: catalog unavailable'})}));
  const before=await (await p.request.get(base+'/api/scenarios')).json();
  await p.goto(base+'/?ui=startup-fix');
  await p.waitForFunction(()=>document.querySelector('#api-indicator').textContent==='API conectada');
  for(const s of before) assert.equal(await p.locator(`[data-scenario-id="${s.id}"]`).count(),1);
  await p.locator('#new-scenario-button').click();
  assert.equal(await p.locator('body').getAttribute('data-workspace'),'editor');
  const name='Startup smoke '+Date.now();
  await p.locator('#scenario-name').fill(name);
  await p.locator('#scenario-subnet').fill('172.30.250.0/24');
  await p.locator('.workspace-navigation [data-workspace-view="catalog"]').click();
  await p.locator('[data-node-type="plc-s7"]').click();
  await p.locator('#save-button').click();
  await p.waitForFunction(()=>document.querySelector('#save-button').textContent==='Guardar'&&!document.querySelector('#save-button').disabled);
  const after=await (await p.request.get(base+'/api/scenarios')).json();created=after.find(s=>s.name===name)?.id;
  assert(created,'New scenario saved through UI');
  await p.locator('.workspace-navigation [data-workspace-view="projects"]').click();
  await p.locator(`[data-scenario-id="${created}"]`).click();
  assert.equal(await p.locator('#scenario-name').inputValue(),name);
  assert.equal(await p.locator('.drawflow-node').count(),1);
  assert.deepEqual(errors,[]);
  assert.equal(staleCanvasRequested,false,'Old canvas URL must not be reused');
  const response=await p.request.get(base+'/app.js',{headers:{'If-None-Match':'*'}});
  assert.equal(response.status(),200);assert.equal(response.headers()['cache-control'],'no-store');
  console.log('Startup OK: existing scenarios listed; Nuevo escenario, catalog, save and reopen work; no JS errors; catalog failure and missing ResizeObserver tolerated; frontend cache disabled.');
 }finally{
  if(created)await p.request.delete(base+'/api/scenarios/'+created);
  await b.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
