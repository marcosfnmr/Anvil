import { registerNodeType } from './nodes/registry.js?v=field-live-3';
import { s7Signals } from './components/remote_input.js?v=field-live-3';

const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
const labels = {valve:'Válvula on/off',proportional_valve:'Válvula proporcional',variable_speed_drive:'Variador de frecuencia',motor:'Motor',pump:'Bomba',tank:'Depósito',pipe:'Tubería',flow_splitter:'Distribuidor de caudal',flow_mixer:'Colector de caudal',analog_sensor:'Sensor analógico',digital_input:'Entrada digital',alarm:'Alarma'};
const icons = {valve:'▷◁',proportional_valve:'%V',variable_speed_drive:'VFD',motor:'M',pump:'P',tank:'T',pipe:'━━',flow_splitter:'Y',flow_mixer:'∪',analog_sensor:'°',digital_input:'DI',alarm:'!'};
export function ports(field) {
  if (['pipe','flow_splitter','flow_mixer'].includes(field.type)) return [];
  if (field.type === 'analog_sensor') return [{name:'value',label:'Medida',direction:'output',data_type:'REAL',unit:field.unit || ''}];
  if (['digital_input','alarm'].includes(field.type)) return [{name:'state',label:'Señal',direction:'output',data_type:'BOOL'}];
  if (field.type === 'tank') return [{name:'level',label:'Nivel',direction:'output',data_type:'REAL',unit:'%'},{name:'pressure',label:'Presión manométrica',direction:'output',data_type:'REAL',unit:'kPa'}];
  if (field.type === 'proportional_valve') return [{name:'command',label:'Consigna de apertura',direction:'input',data_type:'REAL',unit:'%',min_value:0,max_value:100},{name:'opening',label:'Apertura real',direction:'output',data_type:'REAL',unit:'%'},{name:'fault',label:'Fallo',direction:'output',data_type:'BOOL'}];
  if (field.type === 'variable_speed_drive') return [{name:'command',label:'Consigna de velocidad',direction:'input',data_type:'REAL',unit:'%',min_value:0,max_value:100},{name:'speed',label:'Velocidad real',direction:'output',data_type:'REAL',unit:'%',min_value:0,max_value:100},{name:'flow',label:'Caudal',direction:'output',data_type:'REAL',unit:'L/min',min_value:0,max_value:field.max_flow_lpm},{name:'fault',label:'Fallo',direction:'output',data_type:'BOOL'}];
  return [{name:'command',label:'Orden',direction:'input',data_type:'BOOL'}, {name:'state',label:'Estado real',direction:'output',data_type:'BOOL'}, {name:'fault',label:'Fallo',direction:'output',data_type:'BOOL'}, ...(field.type === 'pump' ? [{name:'flow',label:'Caudal',direction:'output',data_type:'REAL',unit:field.unit}] : [])];
}
export function processPorts(field) {
  if (field.type === 'flow_splitter') return [{name:'process_in',label:'Entrada',direction:'input',unit:'L/min'},{name:'process_out_a',label:'Rama A',direction:'output',unit:'L/min'},{name:'process_out_b',label:'Rama B',direction:'output',unit:'L/min'},{name:'pressure',label:'Presión',direction:'output',unit:'kPa'}];
  if (field.type === 'flow_mixer') return [{name:'process_in_a',label:'Entrada A',direction:'input',unit:'L/min'},{name:'process_in_b',label:'Entrada B',direction:'input',unit:'L/min'},{name:'process_out',label:'Salida',direction:'output',unit:'L/min'},{name:'pressure',label:'Presión',direction:'output',unit:'kPa'}];
  if (field.type === 'pipe') return [{name:'process_in',label:'Entrada de tubería',direction:'input',unit:'L/min'},{name:'process_out',label:'Salida de tubería',direction:'output',unit:'L/min'},{name:'pressure',label:'Presión tras la tubería',direction:'output',unit:'kPa'}];
  if (field.type === 'pump') return [{name:'flow',label:'Caudal de proceso',direction:'output',unit:field.unit || 'L/min'},{name:'pressure',label:'Presión de descarga',direction:'output',unit:'kPa'}];
  if (field.type === 'variable_speed_drive') return [{name:'flow',label:'Caudal regulado',direction:'output',unit:'L/min'},{name:'pressure',label:'Presión de descarga',direction:'output',unit:'kPa'}];
  if (field.type === 'valve') return [{name:'process_in',label:'Entrada de tubería',direction:'input',unit:'L/min'},{name:'process_out',label:'Salida de tubería',direction:'output',unit:'L/min'}];
  if (field.type === 'proportional_valve') return [{name:'process_in',label:'Entrada de tubería',direction:'input',unit:'L/min'},{name:'process_out',label:'Salida de tubería',direction:'output',unit:'L/min'}];
  if (field.type === 'tank') return [{name:'inlet',label:'Entrada',direction:'input',unit:'L/min'},{name:'outlet',label:'Salida',direction:'output',unit:'L/min'},{name:'level',label:'Nivel',direction:'output',unit:'%'},{name:'pressure',label:'Presión manométrica',direction:'output',unit:'kPa'}];
  if (field.type === 'analog_sensor') return [{name:'measurement',label:'Medición física',direction:'input',unit:field.unit || ''}];
  return [];
}
for (const [type,label] of Object.entries(labels)) registerNodeType(`field-${type}`, {
  label, shortLabel:label, description:'Elemento de campo independiente · sin IP',icon:icons[type],className:'node-field-object',
  create: () => ({object_id:`field-${crypto.randomUUID()}`,type,name:label,
    ...(type === 'analog_sensor' ? {min_value:0,max_value:100,unit:'%',simulation:{mode:'constant',noise:0,seed:0}} : ['valve','motor','pump','digital_input','alarm'].includes(type) ? {initial_state:false} : {}),
    ...(type === 'proportional_valve' ? {initial_opening_pct:0,response_seconds:1,flow_rate_lpm:50} : {}),
    ...(type === 'variable_speed_drive' ? {initial_speed_pct:0,response_seconds:1,max_flow_lpm:100,max_head_kpa:100} : {}),
    ...(['valve','motor','pump'].includes(type) ? {response_seconds:1} : {}),
    ...(type === 'pump' ? {flow_min:0,flow_max:100,max_head_kpa:100,unit:'L/min'} : {}),
    ...(type === 'valve' ? {flow_rate_lpm:50} : {}),
      ...(type === 'tank' ? {capacity_liters:1000,height_m:2,initial_level_liters:500,outlet_flow_lpm:50,leak_flow_lpm:0,demand_flow_lpm:0} : {}),
      ...(type === 'pipe' ? {max_flow_lpm:100,pressure_drop_kpa:0} : {}),
      ...(type === 'flow_splitter' ? {split_ratio_pct:50} : {})}),
  render: field => `<article class="device-node independent-field" data-object-id="${esc(field.object_id)}"><header class="device-node-header"><span class="device-node-icon">${icons[type]}</span><span class="device-node-title"><strong>${esc(field.name)}</strong><small>${label} · campo</small></span></header><div class="field-live" role="status">Sin lectura · escenario detenido</div><div class="field-node-ports">${ports(field).map(p=>`<span data-node-port="${p.name}">${p.direction==='input'?'◀':'▶'} ${p.label}</span>`).join('')}${processPorts(field).map(p=>`<span class="physical-port">${p.direction==='input'?'◀':'▶'} ${p.label}</span>`).join('')}</div><button type="button" data-open-field="${esc(field.object_id)}">Configurar y conectar</button></article>`,
});

export class FieldEditor {
  constructor(canvas, inspector, {onChange,onError,getStatus,getScenarioId,isDirty,getZones,canOperate}) {
    Object.assign(this,{canvas,inspector,onChange,onError,getStatus,getScenarioId,isDirty,getZones,canOperate});
    this.svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    this.svg.classList.add('process-links'); this.svg.setAttribute('aria-hidden','true');
    this.snapshot = null; this.selected = null; this.busy = false;
    canvas.onFieldChange = () => this.drawLinks();
    window.addEventListener('resize', () => this.drawLinks());
    canvas.element.addEventListener('click', e => {
      const button = e.target.closest('[data-open-field]');
      if (button) { canvas.selectDevice(button.dataset.openField); document.querySelector('[data-workspace-view="config"]').click(); }
    });
    inspector.addEventListener('change', e => this.change(e));
    inspector.addEventListener('click', e => this.action(e));
    this.timer = setInterval(() => this.poll(),1000);
  }
  select(field) { this.selected = field; this.render(); }
  render() {
    const f = this.selected; if (!f) return;
    const running = this.getStatus() !== 'stopped';
    const devices = this.canvas.getDevices().filter(d => ['plc-s7','modbus-tcp'].includes(d.device_type));
    const input = (key,label,type='number',bounds='') => `<label class="field"><span>${label}</span><input data-field-prop="${key}" type="${type}" value="${esc(f[key])}" ${type==='number'?`step="any" ${bounds}`:''}></label>`;
    const simulationInput = (key,label,bounds) => `<label class="field"><span>${label}</span><input data-field-simulation-value="${key}" type="number" ${bounds} value="${esc(f.simulation[key] ?? '')}"></label>`;
    const zones = this.getZones?.() || [];
    const fieldRuntimeZone = zones.length ? (f.zone_id || zones[0].zone_id) : "default";
    const processBlock = ['pipe','flow_splitter','flow_mixer'].includes(f.type);
    this.inspector.innerHTML = `<div class="inspector-form"><h2>${esc(labels[f.type])}</h2><p>${processBlock?'Bloque físico del proceso. Conecta sus puertos de caudal con tuberías, distribuidores, colectores o depósitos.':'Elemento de campo independiente. Conecta cada señal a un controlador.'}</p><fieldset ${running||this.canvas.locked?'disabled':''}><legend>Propiedades</legend><div class="field-grid">${input('name','Nombre','text')}
      ${zones.length ? `<label class="field"><span>Zona Purdue</span><select data-field-zone>${zones.map(z=>`<option value="${esc(z.zone_id)}" ${(f.zone_id || zones[0].zone_id)===z.zone_id?'selected':''}>${esc(z.name)} · ${esc(z.purdue_level)}</option>`).join('')}</select></label>` : ''}
      ${f.type === 'analog_sensor' ? `${input('min_value','Mínimo')}${input('max_value','Máximo')}${input('unit','Unidad','text')}<label class="field"><span>Simulación</span><select data-field-simulation><option value="constant" ${f.simulation.mode==='constant'?'selected':''}>Constante</option><option value="sine_wave" ${f.simulation.mode==='sine_wave'?'selected':''}>Onda sinusoidal</option><option value="process" ${f.simulation.mode==='process'?'selected':''}>Proceso físico</option></select></label>${f.simulation.mode==='sine_wave'? `<label class="field"><span>Periodo (s)</span><input type="number" min="0.1" step="any" data-field-period value="${f.simulation.period_seconds || 20}"></label>`:''}${simulationInput('noise','Ruido (0–1)','min="0" max="1" step="0.01"')}${simulationInput('seed','Semilla reproducible (0–4294967295)','min="0" max="4294967295" step="1"')}` : f.type === 'tank' ? `${input('capacity_liters','Capacidad (L)')}${input('height_m','Altura del depósito (m)')}${input('initial_level_liters','Nivel inicial (L)')}${input('outlet_flow_lpm','Caudal de salida (L/min)')}${input('leak_flow_lpm','Fuga (L/min)')}${input('demand_flow_lpm','Demanda inicial (L/min)')}` : f.type === 'pipe' ? `${input('max_flow_lpm','Caudal máximo (L/min)')}${input('pressure_drop_kpa','Pérdida de presión a caudal máximo (kPa)')}` : f.type==='flow_splitter' ? input('split_ratio_pct','Caudal hacia rama A (%)','number','min="0" max="100"') : f.type==='proportional_valve' ? `${input('initial_opening_pct','Apertura inicial (%)','number','min="0" max="100"')}${input('flow_rate_lpm','Caudal máximo (L/min)')}` : f.type==='variable_speed_drive' ? `${input('initial_speed_pct','Velocidad inicial (%)','number','min="0" max="100"')}${input('max_flow_lpm','Caudal máximo (L/min)')}${input('max_head_kpa','Presión nominal de descarga (kPa)')}` : ['flow_mixer'].includes(f.type) ? '<p>Une dos caudales y conserva su suma en la salida.</p>' : `<label class="field"><span>Estado inicial</span><select data-field-initial><option value="false" ${!f.initial_state?'selected':''}>Inactivo / cerrado</option><option value="true" ${f.initial_state?'selected':''}>Activo / abierto</option></select></label>`}
      ${['valve','proportional_valve','variable_speed_drive','motor','pump'].includes(f.type) ? input('response_seconds','Tiempo de respuesta (s)') : ''}
      ${f.type==='valve'?input('flow_rate_lpm','Caudal máximo (L/min)'):''}
      ${f.type==='pump'?`${input('flow_min','Caudal mínimo')}${input('flow_max','Caudal máximo')}${input('max_head_kpa','Presión nominal de descarga (kPa)')}${input('unit','Unidad','text')}`:''}</div></fieldset>
      <fieldset ${running||this.canvas.locked?'disabled':''}><legend>Conexiones de señales PLC</legend>${ports(f).map(p => {
        const link = this.canvas.signalConnections.find(l => [l.source,l.target].some(e => e.kind==='field' && e.object_id===f.object_id && e.port===p.name));
        const endpoint = link && (link.source.kind==='device'?link.source:link.target);
        const device = devices.find(d=>d.node_id===endpoint?.node_id);
        const compatibleDevices=devices.filter(d=>!(p.direction==='input'&&p.data_type==='REAL'&&!['plc-s7','modbus-tcp'].includes(d.device_type)));
        const crossZone=Boolean(zones.length&&device&&device.zone_id!==fieldRuntimeZone);
        return `<section class="field-port" data-port="${p.name}"><strong>${p.label} · ${p.data_type} · ${p.direction==='output'?'hacia PLC':'desde PLC'}${p.min_value!==undefined?` · ${p.min_value}–${p.max_value} ${p.unit}`:''}</strong>${link?`<p>${esc(device?.name || endpoint.node_id)} · ${esc(endpoint.address)}${link.conversion?' · escala '+link.conversion.input_min+'…'+link.conversion.input_max+' → '+link.conversion.output_min+'…'+link.conversion.output_max:''}</p>${crossZone||link.route_enabled?`<label class="target-route-option"><input type="checkbox" data-field-route="${esc(link.connection_id)}" ${link.route_enabled?'checked':''}/> Permitir señal al runtime de campo mediante gateway HTTP</label>`:''}<button type="button" data-unlink="${esc(link.connection_id)}">Desconectar</button>`:`<label><span>Controlador</span><select data-port-device="${p.name}"><option value="">Selecciona controlador…</option>${compatibleDevices.map(d=>`<option value="${esc(d.node_id)}">${esc(d.name)}</option>`).join('')}</select></label><button type="button" data-connect-port="${p.name}">Conectar y crear canal</button>`}</section>`;
      }).join('')}${!devices.length?'<p>Añade los elementos de campo primero y un PLC después desde el catálogo.</p>':''}</fieldset>
      <fieldset ${running||this.canvas.locked?'disabled':''}><legend>Conexiones físicas de proceso</legend>${processPorts(f).map(p=>{
        const link=this.canvas.processConnections.find(l=>[l.source,l.target].some(e=>e.object_id===f.object_id&&e.port===p.name));
        if(link){const other=link.source.object_id===f.object_id?link.target:link.source;const object=this.canvas.getFieldObjects().find(x=>x.object_id===other.object_id);return `<section class="field-port"><strong>${p.label} · ${p.unit||'sin unidad'}</strong><p>${esc(object?.name||other.object_id)} · ${esc(other.port)}</p><button type="button" data-unlink-process="${esc(link.connection_id)}">Quitar tubería / vínculo</button></section>`;}
        const peers=this.canvas.getFieldObjects().filter(x=>x.object_id!==f.object_id).flatMap(x=>processPorts(x).filter(q=>q.direction!==p.direction&&q.unit===p.unit&&(p.direction==='output'||!this.canvas.processConnections.some(l=>l.target.object_id===x.object_id&&l.target.port===q.name))).map(q=>({field:x,port:q})));
        return `<section class="field-port"><strong>${p.label} · ${p.unit||'sin unidad'} · ${p.direction==='output'?'sale hacia':'recibe de'}</strong>${peers.length?`<label><span>Elemento conectado</span><select data-process-peer="${p.name}"><option value="">Selecciona elemento…</option>${peers.map(q=>`<option value="${esc(q.field.object_id)}|${esc(q.port.name)}">${esc(q.field.name)} · ${esc(q.port.label)}</option>`).join('')}</select></label><button type="button" data-connect-process="${p.name}">Conectar tubería</button>`:'<p>No hay puertos de proceso compatibles en el mapa.</p>'}</section>`;
      }).join('')}${!processPorts(f).length?'<p>Este elemento no tiene puertos físicos de proceso.</p>':''}</fieldset>
      ${running&&this.canOperate?.()?`<section><h3>Simulación de campo</h3><p>Estas acciones simulan una condición física; las órdenes del actuador se escriben desde el PLC.</p>${f.type==='tank'?`<label><span>Demanda actual (L/min)</span><input data-field-demand type="number" min="0" step="any" value="${esc(this.snapshot?.objects.find(item=>item.object_id===f.object_id)?.demand_lpm ?? f.demand_flow_lpm ?? 0)}"></label><button type="button" data-set-demand>Aplicar cambio de demanda</button><label><span>Fuga actual (L/min)</span><input data-field-leak type="number" min="0" step="any" value="${esc(this.snapshot?.objects.find(item=>item.object_id===f.object_id)?.leak_rate_lpm ?? f.leak_flow_lpm ?? 0)}"></label><button type="button" data-set-leak>Aplicar fuga (0 para restaurar)</button>`:['valve','proportional_valve','variable_speed_drive','motor','pump'].includes(f.type)?'<button type="button" data-field-fault="true">Simular atasco / fallo</button><button type="button" data-field-fault="false">Restaurar actuador</button>':`<button type="button" data-field-fault="true">Simular fallo: congelar última lectura</button><button type="button" data-field-fault="false">Restaurar sensor</button><label><span>Valor forzado</span><input data-field-force ${f.type==='analog_sensor'?`type="number" step="any" min="${f.min_value}" max="${f.max_value}"`:'type="checkbox"'}></label><button type="button" data-force-field>Aplicar valor forzado</button><button type="button" data-release-field>Volver a simulación</button>`}</section>`:''}
      <button type="button" class="text-danger" data-delete-field ${running||this.canvas.locked?'disabled':''}>Eliminar elemento de campo</button></div>`;
  }
  change(e) {
    if (!this.selected || this.canvas.locked) return;
    if(e.target.matches('[data-field-route]')) {
      const link=this.canvas.signalConnections.find(item=>item.connection_id===e.target.dataset.fieldRoute);
      if(!link)return;
      const deviceEndpoint=link.source.kind==='device'?link.source:link.target;
      this.canvas.signalConnections.forEach(item=>{
        const endpoint=item.source.kind==='device'?item.source:item.target;
        if(endpoint.node_id===deviceEndpoint.node_id)item.route_enabled=e.target.checked;
      });
      this.drawLinks();this.render();this.onChange();return;
    }
    const f = this.selected;
    if (e.target.matches('[data-field-prop]')) f[e.target.dataset.fieldProp] = e.target.type==='number'?Number(e.target.value):e.target.value;
    else if(e.target.matches('[data-field-initial]')) f.initial_state=e.target.value==='true';
    else if(e.target.matches('[data-field-simulation]')) {f.simulation.mode=e.target.value; if(e.target.value==='sine_wave')f.simulation.period_seconds ||=20;else delete f.simulation.period_seconds;}
    else if(e.target.matches('[data-field-simulation-value]')) {const key=e.target.dataset.fieldSimulationValue;if(e.target.value==='')delete f.simulation[key];else f.simulation[key]=Number(e.target.value);}
    else if(e.target.matches('[data-field-zone]')) f.zone_id=e.target.value;
    else if(e.target.matches('[data-field-period]')) f.simulation.period_seconds=Number(e.target.value);
    else return;
    this.canvas.updateSelected(f); this.onChange();
  }
  async action(e) {
    if(!this.selected) return;
    try {
      if(e.target.matches('[data-connect-port]') && !this.canvas.locked) this.connect(e.target.dataset.connectPort);
      if(e.target.matches('[data-connect-process]') && !this.canvas.locked) this.connectProcess(e.target.dataset.connectProcess);
      if(e.target.matches('[data-unlink]') && !this.canvas.locked) {this.canvas.signalConnections=this.canvas.signalConnections.filter(l=>l.connection_id!==e.target.dataset.unlink);this.drawLinks();this.render();this.onChange();}
      if(e.target.matches('[data-unlink-process]') && !this.canvas.locked) {const link=this.canvas.processConnections.find(l=>l.connection_id===e.target.dataset.unlinkProcess);const sensorId=link?.target.port==='measurement'?link.target.object_id:null;this.canvas.processConnections=this.canvas.processConnections.filter(l=>l.connection_id!==e.target.dataset.unlinkProcess);if(sensorId)this.setSensorMode(sensorId,'constant');this.drawLinks();this.render();this.onChange();}
      if(e.target.matches('[data-delete-field]') && !this.canvas.locked) {this.canvas.removeSelected();this.selected=null;}
      if(e.target.matches('[data-field-fault]')) await this.control({fault:e.target.dataset.fieldFault==='true'});
      if(e.target.matches('[data-force-field]')) {const input=this.inspector.querySelector('[data-field-force]');await this.control({override:input.type==='checkbox'?input.checked:Number(input.value)});}
      if(e.target.matches('[data-release-field]')) await this.control({override:null});
      if(e.target.matches('[data-set-demand]')) {const input=this.inspector.querySelector('[data-field-demand]');const value=Number(input.value);if(!Number.isFinite(value)||value<0)throw new Error('La demanda debe ser un caudal finito no negativo.');await this.control({demand_flow_lpm:value});}
      if(e.target.matches('[data-set-leak]')) {const input=this.inspector.querySelector('[data-field-leak]');const value=Number(input.value);if(!Number.isFinite(value)||value<0)throw new Error('La fuga debe ser un caudal finito no negativo.');await this.control({leak_flow_lpm:value});}
    } catch(error) {this.onError(error.message);}
  }
  connect(name) {
    const field=this.selected, port=ports(field).find(p=>p.name===name);
    const nodeId=this.inspector.querySelector(`[data-port-device="${name}"]`).value;
    if(port.direction==='input'&&port.data_type==='REAL'&&!['plc-s7','modbus-tcp'].includes(this.canvas.getDevices().find(d=>d.node_id===nodeId)?.device_type)) throw new Error('Una consigna REAL requiere una salida analógica S7 o un holding register Modbus escalado.');
    const entry=Object.entries(this.canvas.exportNodes()).find(([,n])=>n.data.node_id===nodeId);
    if(!entry) throw new Error('Selecciona un controlador.');
    const [id,node]=entry, device=structuredClone(node.data); let address, conversion, channel_id;
    if(device.device_type==='plc-s7') {
      channel_id=`channel-${crypto.randomUUID()}`;
      device.config.components.push({type:'io_channel',channel_id,name:`${field.name} · ${port.label}`,db:1,area:'DB',direction:port.direction==='output'?'input':'output',data_type:port.data_type,unit:port.unit||'',...(port.data_type==='REAL'&&port.min_value!==undefined&&port.max_value!==undefined?{min_value:port.min_value,max_value:port.max_value}:{})});
      // Validate size before editing any node. Offsets match the backend allocator.
      const signals=s7Signals(device), last=signals.at(-1); address=last.address;
      const match=address.match(/DB(?:X|D)(\d+)/); if(Number(match[1])+(port.data_type==='REAL'?4:1)>device.config.db_size) throw new Error('El DB del PLC no tiene espacio. Aumenta su tamaño en ajustes S7.');
    } else {
      const type=port.data_type==='BOOL'?(port.direction==='output'?'discrete_input':'coil'):port.direction==='input'?'holding_register':'input_register';
      const used=new Set(device.config.points.filter(p=>p.type===type && p.address!==null).map(p=>p.address));
      for(const p of device.config.points.filter(p=>p.type===type && p.address==null)){let free=0;while(used.has(free))free++;used.add(free);}
      let free=0;while(used.has(free))free++;if(free>65535)throw new Error('Espacio Modbus lleno.');
      device.config.points.push({type,name:`${field.name} · ${port.label}`,address:free,initial_value:port.data_type==='BOOL'?false:0,...(['input_register','holding_register'].includes(type)?{unit:''}:{})});
      address=`${type}:${free}`;
      if(port.data_type==='REAL'&&port.direction==='input') conversion={input_min:0,input_max:65535,output_min:port.min_value??0,output_max:port.max_value??100};
      else if(port.data_type==='REAL') conversion={input_min:port.min_value??field.min_value??0,input_max:port.max_value??(field.type==='pump'?field.flow_max:100),output_min:0,output_max:65535};
    }
    const f={kind:'field',object_id:field.object_id,port:name},d={kind:'device',node_id:nodeId,address,...(channel_id?{channel_id}:{})};
    this.canvas.editor.updateNodeDataFromId(id,device);this.canvas.refreshNode(id,device);
    const crossZone=Boolean(this.getZones?.().length&&device.zone_id!==field.zone_id);
    const priorRoute=this.canvas.signalConnections.find(link=>{
      const endpoint=link.source.kind==='device'?link.source:link.target;
      return endpoint.node_id===nodeId&&link.route_enabled!==undefined&&link.route_enabled!==null;
    });
    this.canvas.signalConnections.push({connection_id:`signal-${crypto.randomUUID()}`,source:port.direction==='output'?f:d,target:port.direction==='output'?d:f,...(conversion?{conversion}:{}),...(crossZone?{route_enabled:priorRoute?.route_enabled??false}:{})});
    this.drawLinks();this.render();this.onChange();
  }
  setSensorMode(objectId,mode) {
    const entry=Object.entries(this.canvas.exportNodes()).find(([,n])=>n.data.object_id===objectId);if(!entry)return;
    const [id,node]=entry,value=structuredClone(node.data);value.simulation.mode=mode;delete value.simulation.period_seconds;
    this.canvas.editor.updateNodeDataFromId(id,value);this.canvas.refreshNode(id,value);
  }
  connectProcess(name) {
    const current=this.selected,port=processPorts(current).find(p=>p.name===name);
    const [peerId,peerPortName]=(this.inspector.querySelector(`[data-process-peer="${name}"]`)?.value||'').split('|');
    const peer=this.canvas.getFieldObjects().find(x=>x.object_id===peerId),peerPort=peer&&processPorts(peer).find(p=>p.name===peerPortName);
    if(!peer||!peerPort)throw new Error('Selecciona un elemento con puerto físico compatible.');
    const source=port.direction==='output'?{object_id:current.object_id,port:name}:{object_id:peer.object_id,port:peerPortName};
    const target=port.direction==='input'?{object_id:current.object_id,port:name}:{object_id:peer.object_id,port:peerPortName};
    if(target.port==='measurement')this.setSensorMode(target.object_id,'process');
    this.canvas.processConnections.push({connection_id:`process-${crypto.randomUUID()}`,source,target});
    this.drawLinks();this.render();this.onChange();
  }
  drawLinks() {
    const parent=this.canvas.editor.precanvas;if(!this.svg.isConnected)parent.prepend(this.svg);
    const nodes=new Map(Object.entries(this.canvas.exportNodes()).map(([id,n])=>[n.data.object_id||n.data.node_id,this.canvas.element.querySelector(`#node-${id}`)]));
    const quality=new Map((this.snapshot?.connections||[]).map(c=>[c.connection_id,c.quality]));
    const links=[...this.canvas.signalConnections.map(link=>({link,type:'signal'})),...this.canvas.processConnections.map(link=>({link,type:'process'}))];
    this.svg.innerHTML='<defs><marker id="field-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#68d4b2"/></marker></defs>'+links.map(({link:l,type})=>{
      const a=nodes.get(l.source.object_id||l.source.node_id),b=nodes.get(l.target.object_id||l.target.node_id);if(!a||!b)return '';
      const zoom=this.canvas.editor.zoom||1;
      const fromPort=type==='process'?null:l.source.kind==='field'?a.querySelector(`[data-node-port="${l.source.port}"]`):null;
      const toPort=type==='process'?null:l.target.kind==='field'?b.querySelector(`[data-node-port="${l.target.port}"]`):null;
      const anchorY=(node,port)=>port?node.offsetTop+(port.getBoundingClientRect().top-node.getBoundingClientRect().top+port.getBoundingClientRect().height/2)/zoom:node.offsetTop+node.offsetHeight/2;
      // Pick the closest pair of facing edges for every link. The old
      // left-to-right assumption made arrows cross nodes when the user moved
      // a controller above or below a field object.
      const rect = (node) => ({ left: node.offsetLeft, top: node.offsetTop,
        right: node.offsetLeft + node.offsetWidth, bottom: node.offsetTop + node.offsetHeight });
      const sides = (r, other) => [
        { side: 'left', normal: { x: -1, y: 0 }, point: { x: r.left, y: Math.max(r.top + 16, Math.min(r.bottom - 16, other.y)) } },
        { side: 'right', normal: { x: 1, y: 0 }, point: { x: r.right, y: Math.max(r.top + 16, Math.min(r.bottom - 16, other.y)) } },
        { side: 'top', normal: { x: 0, y: -1 }, point: { x: Math.max(r.left + 16, Math.min(r.right - 16, other.x)), y: r.top } },
        { side: 'bottom', normal: { x: 0, y: 1 }, point: { x: Math.max(r.left + 16, Math.min(r.right - 16, other.x)), y: r.bottom } },
      ];
      const ar=rect(a),br=rect(b), center=(r)=>({x:(r.left+r.right)/2,y:(r.top+r.bottom)/2});
      const ac=center(ar),bc=center(br); let best=null, nearest=null;
      for(const start of sides(ar,bc)) for(const end of sides(br,ac)) {
        const dx=end.point.x-start.point.x,dy=end.point.y-start.point.y,distance=dx*dx+dy*dy;
        const candidate={start:start.point,end:end.point,startSide:start.side,endSide:end.side,distance};
        if(!nearest||distance<nearest.distance) nearest=candidate;
        if(dx*start.normal.x+dy*start.normal.y<0 || -dx*end.normal.x-dy*end.normal.y<0) continue;
        if(!best||distance<best.distance) best=candidate;
      }
      best ||= nearest;
      const x=fromPort?a.offsetLeft+(fromPort.getBoundingClientRect().left-a.getBoundingClientRect().left+fromPort.getBoundingClientRect().width/2)/zoom:best.start.x;
      const y=fromPort?anchorY(a,fromPort):best.start.y;
      const u=toPort?b.offsetLeft+(toPort.getBoundingClientRect().left-b.getBoundingClientRect().left+toPort.getBoundingClientRect().width/2)/zoom:best.end.x;
      const v=toPort?anchorY(b,toPort):best.end.y;
      const normal=(side)=>({left:{x:-1,y:0},right:{x:1,y:0},top:{x:0,y:-1},bottom:{x:0,y:1}})[side];
      const startNormal=fromPort?normal(x<=a.offsetLeft+a.offsetWidth/2?'left':'right'):normal(best.startSide);
      const endNormal=toPort?normal(u<=b.offsetLeft+b.offsetWidth/2?'left':'right'):normal(best.endSide);
      const label=type==='process'?`${l.source.port} · tubería`:l.source.kind==='field'?l.source.port:l.target.port;
      const fieldSignal=type==='signal'&&(l.source.kind==='field'||l.target.kind==='field');
      const fieldObject=fieldSignal?(l.source.kind==='field'?this.canvas.getFieldObjects().find(item=>item.object_id===l.source.object_id):this.canvas.getFieldObjects().find(item=>item.object_id===l.target.object_id)):null;
      const plc=fieldSignal?(l.source.kind==='device'?this.canvas.getDevices().find(item=>item.node_id===l.source.node_id):this.canvas.getDevices().find(item=>item.node_id===l.target.node_id)):null;
      const crosses=Boolean(this.getZones?.().length&&fieldObject&&plc&&fieldObject.zone_id!==plc.zone_id);
      const routeState=crosses?(l.route_enabled?'route-enabled':'blocked'):'';
      const routeLabel=crosses?(l.route_enabled?' · gateway HTTP':' · ruta bloqueada'):'';
      const bend=Math.max(32,Math.hypot(u-x,v-y)*.36),c1={x:x+startNormal.x*bend,y:y+startNormal.y*bend},c2={x:u+endNormal.x*bend,y:v+endNormal.y*bend};
      return `<g class="map-link ${type==='process'?'field-process':'field-signal'} ${quality.get(l.connection_id)||''} ${routeState}" data-signal-id="${esc(l.connection_id)}"><path d="M${x},${y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${u},${v}" marker-end="url(#field-arrow)"/><text x="${(x+u)/2}" y="${(y+v)/2-8}">${esc(label)}${type==='signal'?` → ${esc(l.source.address||l.target.address)}${routeLabel}`:''}</text></g>`;
    }).join('');
  }
  async control(value) {
    if(this.isDirty())throw new Error('Guarda los cambios antes de operar el escenario.');
    await this.request(`/field/${encodeURIComponent(this.selected.object_id)}/control`,value);await this.poll();
  }
  async request(path,payload) {
    const response=await fetch(`/api/scenarios/${this.getScenarioId()}${path}`,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||`HTTP ${response.status}`);return result;
  }
  async poll() {
    const sid=this.getScenarioId(),fields=this.canvas.getFieldObjects();
    if(this.busy)return;
    const running=sid && ['running','partial','error'].includes(this.getStatus()) && !this.isDirty();
    if(!fields.length)return;
    if(!running) {this.snapshot=null;this.showValues(null,'Sin lectura · escenario detenido o con cambios');this.drawLinks();return;}
    this.busy=true;
    try {const snapshot=await this.request('/field/values');if(this.getScenarioId()!==sid)return;this.snapshot=snapshot;this.showValues(snapshot);this.drawLinks();}
    catch(error) {if(this.getScenarioId()===sid){this.snapshot=null;this.showValues(null,`Sin conexión · ${error.message}`);this.drawLinks();}}
    finally{this.busy=false;}
  }
  showValues(snapshot,message) {
    for(const f of this.canvas.getFieldObjects()) {
      const output=this.canvas.element.querySelector(`[data-object-id="${f.object_id}"] .field-live`);if(!output)continue;
      const state=snapshot?.objects.find(o=>o.object_id===f.object_id);
      if(!state) {output.textContent=message||'Sin lectura';continue;}
      const links=this.canvas.signalConnections.filter(l=>[l.source,l.target].some(e=>e.object_id===f.object_id));
      const qualities=snapshot.connections.filter(q=>links.some(l=>l.connection_id===q.connection_id)).map(q=>q.quality);
      output.textContent=f.type==='analog_sensor'?`${Number(state.value).toFixed(2)} ${f.unit}`:f.type==='tank'?`${Number(state.value).toFixed(1)} % · ${Number(state.volume_liters).toFixed(1)} L · ${Number(state.pressure_kpa).toFixed(2)} kPa · entrada ${Number(state.inflow_lpm).toFixed(1)} L/min · salida ${Number(state.outflow_lpm).toFixed(1)} L/min${Number(state.effective_demand_lpm)>0?' · demanda '+Number(state.effective_demand_lpm).toFixed(1)+' L/min':''}${Number(state.leak_lpm)>0?' · fuga '+Number(state.leak_lpm).toFixed(1)+' L/min':''}`:f.type==='pipe'?`Caudal ${Number(state.flow).toFixed(1)} L/min · presión entrada ${Number(state.pressure_in_kpa).toFixed(1)} kPa · pérdida ${Number(state.pressure_drop_kpa).toFixed(1)} kPa · presión salida ${Number(state.pressure_kpa).toFixed(1)} kPa`:f.type==='flow_splitter'?`Entrada ${Number(state.process_in).toFixed(1)} L/min · rama A ${Number(state.process_out_a).toFixed(1)} L/min · rama B ${Number(state.process_out_b).toFixed(1)} L/min`:f.type==='flow_mixer'?`Entrada A ${Number(state.process_in_a).toFixed(1)} L/min · entrada B ${Number(state.process_in_b).toFixed(1)} L/min · salida ${Number(state.flow).toFixed(1)} L/min`:f.type==='proportional_valve'?`Apertura ${Number(state.opening_pct).toFixed(1)} % · consigna ${Number(state.command).toFixed(1)} % · caudal ${Number(state.flow).toFixed(1)} L/min`:f.type==='variable_speed_drive'?`Velocidad ${Number(state.speed_pct).toFixed(1)} % · consigna ${Number(state.command).toFixed(1)} % · caudal ${Number(state.flow).toFixed(1)} L/min · presión ${Number(state.pressure_kpa).toFixed(1)} kPa`:`${state.state?'Activo / abierto':'Inactivo / cerrado'}${['valve','motor','pump'].includes(f.type)?' · orden '+(state.command?'activa':'inactiva'):''}${f.type==='pump'?' · '+state.flow+' '+f.unit+' · presión '+Number(state.pressure_kpa).toFixed(1)+' kPa':''}${state.fault&&!['analog_sensor','digital_input'].includes(f.type)?' · FALLO / ATASCO':''}`;
      if(state.fault&&['analog_sensor','digital_input'].includes(f.type))output.textContent+=' · FALLO: lectura congelada';
      else if(qualities.some(q=>q!=='good'))output.textContent+=' · señal pendiente / obsoleta';
    }
  }
}
