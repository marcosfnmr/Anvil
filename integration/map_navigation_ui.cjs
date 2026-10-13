const assert=require('node:assert/strict');
const {chromium}=require(process.env.ANVIL_PLAYWRIGHT_PATH || 'playwright');
const base=process.env.ANVIL_BASE_URL || 'http://localhost:8003';
(async()=>{
 const b=await chromium.launch({headless:true,...(process.env.ANVIL_BROWSER_EXECUTABLE?{executablePath:process.env.ANVIL_BROWSER_EXECUTABLE}:{})});
 const p=await b.newPage({viewport:{width:1600,height:1000}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
 try {
  const scenarios=await (await p.request.get(base+'/api/scenarios')).json();
  const scenario=scenarios.find(s=>s.status==='running' && (!process.env.ANVIL_SCENARIO_ID || s.id===process.env.ANVIL_SCENARIO_ID));assert(scenario,'Needs a deployed scenario; test never changes device configuration');
  await p.goto(base);await p.locator(`[data-scenario-id="${scenario.id}"]`).click();
  await p.waitForSelector('.field-node');await p.locator('[data-fit-map]').click();
  const transform=()=>p.locator('#drawflow > .drawflow').evaluate(e=>e.style.transform);
  const positions=()=>p.locator('.drawflow-node,.field-node').evaluateAll(es=>es.map(e=>[e.style.left,e.style.top]));
  const before=await positions();
  const bounds=await p.locator('#drawflow').boundingBox();const x=bounds.x+bounds.width-50,y=bounds.y+bounds.height-45;
  let previous=await transform();
  await p.mouse.move(x,y);await p.mouse.down();await p.mouse.move(x-90,y-60,{steps:6});await p.mouse.up();
  assert.notEqual(await transform(),previous,'Background drag pans deployed map');
  await p.locator('[data-fit-map]').click();await p.locator('[data-pan-mode]').click();
  const card=await p.locator('.field-node').first().boundingBox();previous=await transform();
  await p.mouse.move(card.x+20,card.y+20);await p.mouse.down();await p.mouse.move(card.x+100,card.y+45,{steps:6});await p.mouse.up();
  assert.notEqual(await transform(),previous,'Move mode pans from a component');
  await p.locator('[data-pan-mode]').click();await p.locator('#drawflow').focus();previous=await transform();
  await p.keyboard.press('ArrowRight');assert.notEqual(await transform(),previous,'Keyboard pans map');
  await p.keyboard.press('Home');previous=await transform();
  await p.mouse.move(x,y);await p.mouse.wheel(0,-180);assert.notEqual(await transform(),previous,'Wheel zooms map');
  previous=await transform();await p.locator('[data-zoom-in]').click();assert.notEqual(await transform(),previous,'Zoom button works');
  await p.locator('[data-fit-map]').click();const device=await p.locator('.drawflow-node').first().boundingBox();previous=await transform();
  await p.mouse.move(device.x+15,device.y+15);await p.mouse.down({button:'middle'});await p.mouse.move(device.x+70,device.y+40,{steps:6});await p.mouse.up({button:'middle'});
  assert.notEqual(await transform(),previous,'Middle mouse pans over a device');
  assert.deepEqual(await positions(),before,'Navigation never moves devices or components');
  const saved=await (await p.request.get(base+'/api/scenarios/'+scenario.id)).json();assert.deepEqual(saved.canvas,scenario.canvas);
  assert.deepEqual(errors,[]);
  console.log('Map navigation OK in Brave: deployed background, move mode over components, middle mouse, wheel/buttons zoom, arrows/Home; device positions and saved scenario unchanged.');
 } finally {await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
