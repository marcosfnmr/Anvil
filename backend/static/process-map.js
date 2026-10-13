// Legacy components stay attached to their controller; independent objects use FieldEditor.
const escape = (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
const labels = { valve: 'Válvula', motor: 'Motor', pump: 'Bomba', analog_sensor: 'Sensor', digital_input: 'Entrada digital', alarm: 'Alarma', remote_input: 'Entrada remota S7', coil: 'Coil', discrete_input: 'Discrete input', holding_register: 'Holding register', input_register: 'Input register' };
const icons = { valve: '▷◁', motor: 'M', pump: 'P', analog_sensor: '°', digital_input: 'DI', alarm: '!', remote_input: 'GET', coil: '01', discrete_input: 'DI', holding_register: 'HR', input_register: 'IR' };

export class ProcessMap {
  constructor(canvas) {
    this.canvas = canvas;
    this.positions = {};
    this.egressRules = [];
    this.visible = true;
    this.snapshot = { points: [], values: new Map(), errors: new Map(), running: false };
    this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this.svg.classList.add('process-links');
    this.svg.setAttribute('aria-hidden', 'true');
    this.fields = document.createElement('div');
    this.fields.className = 'process-fields';
    const toolbar = document.querySelector('.canvas-toolbar');
    const controls = document.createElement('div');
    controls.className = 'process-map-controls';
    this.controls = controls;
    controls.innerHTML = '<label><input type="checkbox" data-show-fields checked> Componentes</label><button type="button" class="button button-secondary" data-arrange-map>Ordenar mapa</button><button type="button" class="button button-secondary" data-fit-map>Centrar</button><details class="network-link-editor"><summary>Enlaces y rutas</summary><div class="network-link-body"><h3>Enlace Ethernet</h3><p>Los equipos de una zona comparten el bridge Docker. Si añades un Switch Ethernet, conecta aquí cada nodo de la zona al switch: se validan sus puertos y se asocia al bridge. No hay VLAN ni filtrado por puerto.</p><label>Desde<select data-network-source></select></label><label>Hasta<select data-network-target></select></label><button type="button" class="button button-secondary" data-network-connect>Crear enlace</button><ul data-network-list></ul><hr><h3>Ruta TCP entre zonas</h3><p>Permite un único flujo TCP desde el equipo de origen a un puerto del destino. Puedes bloquear la ruta completa; los equipos de una misma zona comparten la red Docker.</p><label>Puerto del destino<input data-network-port type="number" min="1" max="65535" value="502" inputmode="numeric"></label><button type="button" class="button button-secondary" data-network-route-create>Permitir ruta TCP</button><ul data-network-route-list></ul><hr><h3>Salida a Internet controlada</h3><p>Solo las estaciones de ingeniería o participante con acceso de red habilitado. Cada regla permite un nombre DNS y un puerto TCP a través del proxy de salida.</p><label>Estación<select data-egress-source></select></label><label>Nombre DNS<input data-egress-hostname type="text" maxlength="253" placeholder="updates.example.org" autocomplete="off"></label><label>Puerto TCP<input data-egress-port type="number" min="1" max="65535" value="443" inputmode="numeric"></label><button type="button" class="button button-secondary" data-egress-create>Permitir salida</button><ul data-egress-list></ul></div></details><span class="map-legend">E/S · <span>S7</span> · HMI · <span class="control-legend">Control</span></span>';
    toolbar.append(controls);
    controls.querySelector('input').addEventListener('change', (e) => {
      this.visible = e.target.checked; this.render(); if (!this.canvas.locked) this.canvas.onChange();
    });
    controls.querySelector('[data-arrange-map]').addEventListener('click', () => this.arrange());
    controls.querySelector('[data-fit-map]').addEventListener('click', () => this.fit());
    controls.querySelector('[data-network-connect]').addEventListener('click', () => this.addNetworkConnection());
    controls.querySelector('[data-network-route-create]').addEventListener('click', () => this.addGenericTcpRoute());
    controls.querySelector('[data-egress-create]').addEventListener('click', () => this.addEgressRule());
    controls.addEventListener('click', (event) => {
      const egressRemove = event.target.closest('[data-egress-remove]');
      if (egressRemove && !this.canvas.locked) {
        this.egressRules = this.egressRules.filter((item) => item.rule_id !== egressRemove.dataset.egressRemove);
        this.canvas.element.dispatchEvent(new CustomEvent('network-egress-rules-change', { detail: this.egressRules }));
        this.renderNetworkConnections();
        return;
      }
      const remove = event.target.closest('[data-network-remove]');
      if (!remove || this.canvas.locked) return;
      this.canvas.networkConnections = this.canvas.networkConnections.filter((item) => item.connection_id !== remove.dataset.networkRemove);
      this.render(); this.canvas.onChange();
    });
    controls.addEventListener('click', (event) => {
      const remove = event.target.closest('[data-route-remove]');
      if (remove && !this.canvas.locked) {
        this.canvas.networkRoutes = this.canvas.networkRoutes.filter((item) => item.route_id !== remove.dataset.routeRemove);
        this.canvas.element.dispatchEvent(new CustomEvent('network-routes-change', { detail: this.canvas.networkRoutes }));
        this.renderNetworkConnections(); this.drawLinks();
        return;
      }
      const toggle = event.target.closest('[data-route-toggle]');
      if (!toggle || this.canvas.locked) return;
      this.canvas.networkRoutes = this.canvas.networkRoutes.map((item) => item.route_id === toggle.dataset.routeToggle
        ? { ...item, enabled: !item.enabled } : item);
      this.canvas.element.dispatchEvent(new CustomEvent('network-routes-change', { detail: this.canvas.networkRoutes }));
      this.renderNetworkConnections(); this.drawLinks();
    });
    controls.addEventListener('change', (event) => {
      const input = event.target.closest('[data-route-latency]');
      if (!input || this.canvas.locked) return;
      const latency = Number(input.value);
      if (!Number.isInteger(latency) || latency < 0 || latency > 10000) {
        input.setCustomValidity('La latencia debe ser un entero entre 0 y 10000 ms.');
        input.reportValidity();
        return;
      }
      input.setCustomValidity('');
      this.canvas.networkRoutes = this.canvas.networkRoutes.map((route) => route.route_id === input.dataset.routeLatency
        ? { ...route, latency_ms: latency } : route);
      this.canvas.element.dispatchEvent(new CustomEvent('network-routes-change', { detail: this.canvas.networkRoutes }));
      this.renderNetworkConnections(); this.drawLinks();
    });
    for (const type of ['mousedown', 'touchstart']) this.fields.addEventListener(type, (event) => event.stopPropagation());
    this.fields.addEventListener('pointerdown', (event) => this.startDrag(event));
    this.fields.addEventListener('click', (event) => {
      event.stopPropagation();
      const card = event.target.closest('[data-field-key]');
      if (card && event.target.closest('[data-configure-legacy]')) this.configure(card);
      if (card && event.target.closest('[data-workspace-view="catalog"]')) this.canvas.element.dispatchEvent(new CustomEvent('open-field-catalog'));
    });
    this.fields.addEventListener('dblclick', (event) => {
      event.stopPropagation();
      const card = event.target.closest('[data-field-key]');
      if (card) this.configure(card);
    });
    this.fields.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (!event.target.matches('[data-field-key]')) return;
      if (event.key === 'Enter') { event.preventDefault(); this.configure(event.target); }
      if (!this.canvas.locked && ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        const position = this.positions[event.target.dataset.fieldKey];
        const step = event.shiftKey ? 40 : 10;
        position.x += event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
        position.y += event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
        this.place(event.target, position); this.drawLinks(); this.canvas.onChange();
      }
    });
    if (globalThis.ResizeObserver) {
      this.resize = new ResizeObserver(() => this.drawLinks());
      this.observedLinkElements = new WeakSet();
      this.resize.observe(canvas.element);
    }
    window.addEventListener('resize', () => this.drawLinks());
  }

  configure(card) {
    this.canvas.selectDevice(card.dataset.parentId);
    this.canvas.element.dispatchEvent(new CustomEvent('configure-component', { detail: { index: Number(card.dataset.componentIndex) } }));
  }

  startDrag(event) {
    event.stopPropagation(); // Drawflow owns device dragging, not component cards.
    if (this.canvas.locked || event.button !== 0 || event.target.closest('button')) return;
    const card = event.target.closest('[data-field-key]');
    if (!card) return;
    const key = card.dataset.fieldKey;
    const position = this.positions[key];
    const start = { x: event.clientX, y: event.clientY, px: position.x, py: position.y };
    card.setPointerCapture(event.pointerId);
    const move = (e) => {
      position.x = start.px + (e.clientX - start.x) / this.canvas.editor.zoom;
      position.y = start.py + (e.clientY - start.y) / this.canvas.editor.zoom;
      this.place(card, position); this.drawLinks();
    };
    const end = () => {
      card.removeEventListener('pointermove', move); card.removeEventListener('pointerup', end); card.removeEventListener('pointercancel', end);
      this.canvas.onChange();
    };
    card.addEventListener('pointermove', move); card.addEventListener('pointerup', end); card.addEventListener('pointercancel', end);
  }

  place(card, position) { card.style.left = `${position.x}px`; card.style.top = `${position.y}px`; }

  load(topology = {}) {
    this.positions = {};
    for (const [key, p] of Object.entries(topology.field_positions || {})) {
      if (p && Number.isFinite(p.x) && Number.isFinite(p.y) && Math.abs(p.x) < 100000 && Math.abs(p.y) < 100000) this.positions[key] = { x: p.x, y: p.y };
    }
    this.visible = topology.show_components !== false;
    document.querySelector('[data-show-fields]').checked = this.visible;
    this.snapshot = { points: [], values: new Map(), errors: new Map(), running: false };
    this.render();
  }

  export() { return { field_positions: this.positions, show_components: this.visible }; }

  render() {
    const parent = this.canvas.editor.precanvas;
    if (!this.svg.isConnected) parent.prepend(this.svg);
    if (!this.fields.isConnected) parent.append(this.fields);
    this.fields.hidden = !this.visible;
    const independent = this.canvas.getFieldObjects().length > 0;
    document.querySelector('[data-show-fields]').closest('label').hidden = independent;
    const nodes = Object.entries(this.canvas.exportNodes()).filter(([, node]) => !node.data.object_id);
    this.renderNetworkConnections(nodes);
    const keys = new Set();
    const occupied = nodes.map(([, node]) => ({ x: node.pos_x, y: node.pos_y, w: 290, h: 145 }));
    this.fields.innerHTML = nodes.flatMap(([id, node]) => {
      if (independent) return []; // Physical nodes replace the legacy register/component overlay.
      const occurrences = new Map();
      return (node.data.config.components || node.data.config.points || []).map((component, index) => {
        if (component.type === 'io_channel') return '';
        const identity = JSON.stringify([node.data.node_id, component.type, component.name]);
        const occurrence = occurrences.get(identity) || 0; occurrences.set(identity, occurrence + 1);
        const key = JSON.stringify([node.data.node_id, component.type, component.name, occurrence]);
        keys.add(key);
        let position = this.positions[key];
        if (!position) {
          position = { x: node.pos_x + 300, y: node.pos_y + index * 165 };
          while (occupied.some((r) => position.x < r.x+r.w+24 && position.x+270+24 > r.x && position.y < r.y+r.h+24 && position.y+190+24 > r.y)) position.y += 210;
          this.positions[key] = position;
        }
        occupied.push({ ...position, w: 270, h: 190 });
        return `<article class="field-node" tabindex="0" data-field-key="${escape(key)}" data-parent-id="${escape(node.data.node_id)}" data-parent-drawflow="${id}" data-component-index="${index}" data-component-type="${escape(component.type)}" aria-label="${escape(labels[component.type] || component.type)} ${escape(component.name)} de ${escape(node.data.name)}"><header><span class="field-icon" aria-hidden="true">${icons[component.type] || 'IO'}</span><div><strong>${escape(component.name)}</strong><small>${escape(labels[component.type] || component.type)} · ${escape(node.data.name)}</small></div></header><div class="field-values">Sin lectura</div><footer><button type="button" data-configure-legacy>Configurar</button><small>Integrado en PLC · escenario anterior</small><button type="button" data-workspace-view="catalog">Añadir independiente</button></footer></article>`;
      });
    }).join('');
    Object.keys(this.positions).forEach((key) => { if (!keys.has(key)) delete this.positions[key]; });
    this.fields.querySelectorAll('[data-field-key]').forEach((card) => this.place(card, this.positions[card.dataset.fieldKey]));
    this.sync(this.snapshot);
    this.drawLinks();
  }

  sync(snapshot) {
    this.snapshot = snapshot;
    const pointsByNode = new Map();
    const devicesById = new Map(this.canvas.getDevices().map((device) => [device.node_id, device]));
    for (const p of snapshot.points) {
      if (!pointsByNode.has(p.node_id)) pointsByNode.set(p.node_id, []);
      pointsByNode.get(p.node_id).push(p);
    }
    for (const card of this.fields.querySelectorAll('[data-field-key]')) {
      const device = devicesById.get(card.dataset.parentId);
      const components = device?.config.components || device?.config.points || [];
      const index = Number(card.dataset.componentIndex);
      const start = components.slice(0, index).reduce((n, c) => n + (c.type === 'pump' ? 2 : 1), 0);
      const points = (pointsByNode.get(card.dataset.parentId) || []).slice(start, start + (components[index]?.type === 'pump' ? 2 : 1));
      const output = card.querySelector('.field-values');
      card.dataset.state = 'unknown';
      if (!snapshot.running) { output.textContent = 'Sin lectura · escenario detenido'; continue; }
      if (snapshot.errors.has(card.dataset.parentId)) { output.textContent = 'Sin conexión · valor no disponible'; card.dataset.state = 'error'; continue; }
      output.textContent = points.map((p) => {
        const v = snapshot.values.get(`${p.node_id}:${p.address}`);
        if (!v || v.communication?.quality === 'waiting') return `${p.address}: esperando lectura`;
        if (v.communication?.quality === 'stale') card.dataset.state = 'stale';
        else if (typeof v.value === 'boolean') card.dataset.state = v.value ? 'on' : 'off';
        const value = typeof v.value === 'boolean' ? (card.dataset.componentType === 'valve' ? (v.value ? 'Abierta' : 'Cerrada') : (v.value ? 'Activo' : 'Inactivo')) : Number(v.value).toLocaleString('es-ES', { maximumFractionDigits: 2 });
        return `${value}${typeof v.value !== 'boolean' && p.unit ? ` ${p.unit}` : ''} · ${p.address}${v.communication?.quality === 'stale' ? ' · Obsoleto' : ''}`;
      }).join('\n') || 'Esperando mapa de señales';
    }
    this.drawLinks();
  }

  drawLinks() {
    if (!this.svg.isConnected) return;
    const devices = Object.entries(this.canvas.exportNodes()).filter(([, node]) => !node.data.object_id);
    const nodesById = new Map(devices.map(([, node]) => [node.data.node_id, node.data]));
    const byId = new Map(devices.map(([id, node]) => [node.data.node_id, this.canvas.element.querySelector(`#node-${id}`)]));
    // Observe the actual endpoints as well as the canvas. A live value, font
    // load, or responsive reflow can change a card's bounds without moving it.
    if (this.resize) {
      for (const endpoint of [...byId.values(), ...this.fields.querySelectorAll('[data-field-key]')]) {
        if (endpoint && !this.observedLinkElements.has(endpoint)) {
          this.observedLinkElements.add(endpoint);
          this.resize.observe(endpoint);
        }
      }
    }
    const pairKey = (source, target) => JSON.stringify([source, target]);
    const routesByPair = new Map();
    for (const route of this.canvas.networkRoutes) {
      const key = pairKey(route.source_node_id, route.target_node_id);
      if (!routesByPair.has(key)) routesByPair.set(key, route);
    }
    const remoteValuesByPair = new Map();
    for (const value of this.snapshot.values.values()) {
      const source = value.communication?.source_node_id;
      if (!source) continue;
      const key = pairKey(value.node_id, source);
      const state = remoteValuesByPair.get(key) || { count: 0, good: 0, stale: false };
      state.count += 1;
      if (value.communication.quality === 'good') state.good += 1;
      if (value.communication.quality === 'stale') state.stale = true;
      remoteValuesByPair.set(key, state);
    }
    const paths = [];
    const nearestPorts = (from, to) => {
      const a = { left: from.offsetLeft, top: from.offsetTop,
        right: from.offsetLeft + from.offsetWidth, bottom: from.offsetTop + from.offsetHeight };
      const b = { left: to.offsetLeft, top: to.offsetTop,
        right: to.offsetLeft + to.offsetWidth, bottom: to.offsetTop + to.offsetHeight };
      const center = (r) => ({ x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 });
      const ac = center(a), bc = center(b);
      const sides = (r, other) => [
        { side: "left", normal: { x: -1, y: 0 }, point: { x: r.left, y: Math.max(r.top + 16, Math.min(r.bottom - 16, other.y)) } },
        { side: "right", normal: { x: 1, y: 0 }, point: { x: r.right, y: Math.max(r.top + 16, Math.min(r.bottom - 16, other.y)) } },
        { side: "top", normal: { x: 0, y: -1 }, point: { x: Math.max(r.left + 16, Math.min(r.right - 16, other.x)), y: r.top } },
        { side: "bottom", normal: { x: 0, y: 1 }, point: { x: Math.max(r.left + 16, Math.min(r.right - 16, other.x)), y: r.bottom } },
      ];
      let best = null;
      let closest = null;
      for (const start of sides(a, bc)) for (const end of sides(b, ac)) {
        const dx = end.point.x - start.point.x, dy = end.point.y - start.point.y;
        const distance = dx * dx + dy * dy;
        if (!closest || distance < closest.distance) closest = {
          start: start.point, end: end.point, distance,
          startSide: start.side, endSide: end.side,
        };
        // A link must leave the source and enter the destination from their
        // facing sides. This keeps its arrow attached to the nearest edge.
        if (dx * start.normal.x + dy * start.normal.y < 0
            || -dx * end.normal.x - dy * end.normal.y < 0) continue;
        if (!best || distance < best.distance) best = { start: start.point, end: end.point, distance, startSide: start.side, endSide: end.side };
      }
      best ||= closest;
      const normalFor = (side) => ({
        left: { x: -1, y: 0 }, right: { x: 1, y: 0 },
        top: { x: 0, y: -1 }, bottom: { x: 0, y: 1 },
      })[side];
      return { start: best.start, end: best.end,
        startNormal: normalFor(best.startSide), endNormal: normalFor(best.endSide) };
    };
    const linkPath = ({ start, end, startNormal, endNormal }) => {
      const distance = Math.hypot(end.x - start.x, end.y - start.y);
      const curve = Math.max(36, distance * .38);
      const c1 = { x: start.x + startNormal.x * curve, y: start.y + startNormal.y * curve };
      const c2 = { x: end.x + endNormal.x * curve, y: end.y + endNormal.y * curve };
      return `M${start.x},${start.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${end.x},${end.y}`;
    };
    const add = (from, to, label, kind, state = '') => {
      if (!from || !to) return;
      const ports = nearestPorts(from, to);
      paths.push(`<g class="map-link ${kind} ${state}"><path d="${linkPath(ports)}" marker-end="url(#map-arrow-${kind})"/><text x="${(ports.start.x+ports.end.x)/2}" y="${(ports.start.y+ports.end.y)/2-8}">${escape(label)}</text></g>`);
    };
    const addControl = (from, to, label) => {
      if (!from || !to || from === to) return;
      const ports = nearestPorts(from, to);
      const offset = paths.filter((path) => path.includes('map-link control')).length * 16;
      paths.push(`<g class="map-link control"><path d="${linkPath(ports)}" marker-end="url(#map-arrow-control)"/><text x="${(ports.start.x+ports.end.x)/2}" y="${(ports.start.y+ports.end.y)/2-8+offset}">${escape(label)}</text></g>`);
    };
    for (const link of this.canvas.networkConnections) {
      const from = byId.get(link.source_node_id), to = byId.get(link.target_node_id);
      if (!from || !to) continue;
      const ports = nearestPorts(from, to);
      paths.push(`<g class="map-link ethernet"><path d="${linkPath(ports)}"/><text x="${(ports.start.x+ports.end.x)/2}" y="${(ports.start.y+ports.end.y)/2-8}">${escape(link.label || 'Ethernet · lógico')}</text></g>`);
    }
    for (const route of this.canvas.networkRoutes.filter((item) => item.protocol === 'tcp')) {
      add(byId.get(route.source_node_id), byId.get(route.target_node_id),
        `TCP/${route.tcp_port} · ${route.enabled ? 'permitido' : 'bloqueado'}`,
        'tcp-route', route.enabled ? 'allowed' : 'blocked');
    }
    if (this.visible) this.fields.querySelectorAll('[data-field-key]').forEach((card) => add(byId.get(card.dataset.parentId), card, 'E/S', 'io'));
    const controlEndpoint = (device, componentName) => {
      const components = device.config.components || [];
      const index = components.findIndex((component) => component.name === componentName);
      if (index < 0) return null;
      if (this.visible) {
        const legacy = this.fields.querySelector(`[data-parent-id="${escape(device.node_id)}"][data-component-index="${index}"]`);
        if (legacy) return legacy;
      }
      const point = this.snapshot.points.find((item) => item.node_id === device.node_id && item.component_name === componentName);
      const component = components[index];
      const signal = this.canvas.signalConnections.find((link) => {
        const endpoint = link.source.kind === 'device' ? link.source : link.target;
        return endpoint.kind === 'device' && endpoint.node_id === device.node_id
          && (endpoint.channel_id && component.channel_id ? endpoint.channel_id === component.channel_id
            : point && endpoint.address === point.address);
      });
      if (signal) {
        const field = signal.source.kind === 'field' ? signal.source : signal.target;
        const object = this.canvas.element.querySelector(`.independent-field[data-object-id="${escape(field.object_id)}"]`);
        if (object) return object.closest('.drawflow-node');
      }
      return null;
    };
    for (const [, node] of devices) {
      if (node.data.device_type !== 'plc-s7') continue;
      const config = node.data.config || {};
      const rules = [
        ...(config.control_rules || []).map((rule) => ({ name: rule.name, inputs: [rule.input_component], output: rule.output_component })),
        ...(config.timers || []).map((rule) => ({ name: rule.name, inputs: [rule.input_component], output: rule.output_component })),
        ...(config.boolean_rules || []).map((rule) => ({ name: rule.name, inputs: [rule.input_a_component, rule.input_b_component], output: rule.output_component })),
      ];
      for (const rule of rules) {
        const target = controlEndpoint(node.data, rule.output);
        for (const input of rule.inputs) {
          addControl(controlEndpoint(node.data, input), target, rule.name);
        }
      }
    }
    for (const [, node] of devices) {
      const sources = new Set((node.data.config.components || []).filter((c) => c.type === 'remote_input').map((c) => c.source_node_id));
      for (const source of sources) {
        const sourceNode = nodesById.get(source);
        const crossZone = sourceNode && sourceNode.zone_id !== node.data.zone_id;
        const route = routesByPair.get(pairKey(node.data.node_id, source));
        const values = remoteValuesByPair.get(pairKey(node.data.node_id, source));
        const state = crossZone && !route?.enabled ? 'blocked' : this.snapshot.errors.has(node.data.node_id) ? 'stale' : values?.stale ? 'stale' : values?.count && values.good === values.count ? 'good' : '';
        const label = crossZone ? state === 'blocked' ? 'S7 · ruta bloqueada' : 'S7 · ruta permitida' : 'S7 · lectura';
        add(byId.get(source), byId.get(node.data.node_id), `${label}${state === 'stale' ? ' · fallo' : ''}`, 's7', state);
      }
      if (['hmi', 'scada'].includes(node.data.device_type)) for (const target of (node.data.config.target_node_ids || '').split(',').map((s) => s.trim()).filter(Boolean)) {
        const targetNode = nodesById.get(target);
        const crossZone = targetNode && node.data.zone_id !== targetNode.zone_id;
        const route = routesByPair.get(pairKey(node.data.node_id, target));
        const routed = crossZone && route?.enabled;
        add(byId.get(target), byId.get(node.data.node_id), crossZone ? routed ? 'TCP · ruta permitida' : 'TCP · bloqueado' : node.data.device_type === 'scada' ? 'SCADA · adquisición' : 'HMI · datos', node.data.device_type, crossZone && !routed ? 'blocked' : '');
      }
    }
    this.svg.innerHTML = `<defs>${['io','s7','hmi','scada','tcp-route','control'].map((kind) => `<marker id="map-arrow-${kind}" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" class="arrow-${kind}"/></marker>`).join('')}</defs>${paths.join('')}`;
  }

  renderNetworkConnections(nodes = Object.entries(this.canvas.exportNodes()).filter(([, node]) => !node.data.object_id)) {
    const devices = nodes.map(([, node]) => node.data);
    const networkDevices = devices.filter((device) => device.device_type !== 'attacker-station' || device.config?.participant_access_enabled === true)
      .filter((device) => device.device_type !== 'engineering-station' || device.config?.engineering_access_enabled === true);
    const option = (device) => `<option value="${escape(device.node_id)}">${escape(device.name)} · ${escape(device.zone_id || 'default')}</option>`;
    for (const selector of this.controls.querySelectorAll('[data-network-source], [data-network-target]')) {
      const previous = selector.value;
      selector.innerHTML = networkDevices.map(option).join('');
      if (networkDevices.some((device) => device.node_id === previous)) selector.value = previous;
    }
    const workstations = devices.filter((device) => ['attacker-station', 'engineering-station'].includes(device.device_type)
      && (device.device_type === 'attacker-station' ? device.config?.participant_access_enabled : device.config?.engineering_access_enabled));
    const egressSource = this.controls.querySelector('[data-egress-source]');
    const previousEgressSource = egressSource.value;
    egressSource.innerHTML = workstations.map(option).join('');
    if (workstations.some((device) => device.node_id === previousEgressSource)) egressSource.value = previousEgressSource;
    const list = document.querySelector('[data-network-list]');
    if (!list) return;
    list.innerHTML = this.canvas.networkConnections.map((link) => {
      const source = devices.find((device) => device.node_id === link.source_node_id);
      const target = devices.find((device) => device.node_id === link.target_node_id);
      return `<li>${escape(source?.name || link.source_node_id)} ↔ ${escape(target?.name || link.target_node_id)} <button type="button" data-network-remove="${escape(link.connection_id)}" aria-label="Eliminar enlace">Eliminar</button></li>`;
    }).join('') || '<li>Sin enlaces definidos.</li>';
    const button = this.controls.querySelector('[data-network-connect]');
    if (button) button.disabled = this.canvas.locked || networkDevices.length < 2;
    list.querySelectorAll('[data-network-remove]').forEach((button) => { button.disabled = this.canvas.locked; });
    const routeList = this.controls.querySelector('[data-network-route-list]');
    const endpoints = new Map((this.canvas.networkRouteEndpoints || []).map((endpoint) => [endpoint.route_id, endpoint]));
    routeList.innerHTML = this.canvas.networkRoutes.map((route) => {
      const source = devices.find((device) => device.node_id === route.source_node_id);
      const target = devices.find((device) => device.node_id === route.target_node_id);
      const endpoint = endpoints.get(route.route_id);
      const service = route.protocol === 'tcp' ? `TCP/${route.tcp_port}` : route.protocol.toUpperCase();
      const listen = endpoint?.listen_host ? ` · ${escape(endpoint.listen_host)}:${endpoint.listen_port}` : '';
      const routeState = route.enabled ? 'permitida' : 'bloqueada · proxy no desplegado';
      return `<li>${escape(source?.name || route.source_node_id)} → ${escape(target?.name || route.target_node_id)} · ${service}${listen} · ${routeState} <label>Latencia por sentido (ms)<input type="number" min="0" max="10000" step="1" value="${escape(route.latency_ms ?? 0)}" data-route-latency="${escape(route.route_id)}" aria-label="Latencia por sentido para ${escape(route.route_id)}" ${this.canvas.locked ? 'disabled' : ''}></label> <button type="button" data-route-toggle="${escape(route.route_id)}" aria-label="${route.enabled ? 'Bloquear' : 'Permitir'} esta ruta">${route.enabled ? 'Bloquear' : 'Permitir'}</button> <button type="button" data-route-remove="${escape(route.route_id)}" aria-label="Eliminar ruta">Eliminar</button></li>`;
    }).join('') || '<li>Sin rutas definidas.</li>';
    routeList.querySelectorAll('[data-route-remove]').forEach((button) => { button.disabled = this.canvas.locked; });
    routeList.querySelectorAll('[data-route-toggle]').forEach((button) => { button.disabled = this.canvas.locked; });
    const routeButton = this.controls.querySelector('[data-network-route-create]');
    if (routeButton) routeButton.disabled = this.canvas.locked || networkDevices.length < 2;
    const egressList = this.controls.querySelector('[data-egress-list]');
    egressList.innerHTML = this.egressRules.map((rule) => {
      const source = devices.find((device) => device.node_id === rule.source_node_id);
      return `<li>${escape(source?.name || rule.source_node_id)} → ${escape(rule.hostname)}:${rule.tcp_port} · ${rule.enabled ? 'permitida' : 'bloqueada'} <button type="button" data-egress-remove="${escape(rule.rule_id)}" aria-label="Eliminar regla de salida">Eliminar</button></li>`;
    }).join('') || '<li>Sin reglas de salida.</li>';
    egressList.querySelectorAll('[data-egress-remove]').forEach((button) => { button.disabled = this.canvas.locked; });
    const egressButton = this.controls.querySelector('[data-egress-create]');
    if (egressButton) egressButton.disabled = this.canvas.locked || !workstations.length;
  }

  addNetworkConnection() {
    if (this.canvas.locked) return;
    const sourceId = document.querySelector('[data-network-source]')?.value;
    const targetId = document.querySelector('[data-network-target]')?.value;
    const devices = this.canvas.getDevices();
    const source = devices.find((device) => device.node_id === sourceId);
    const target = devices.find((device) => device.node_id === targetId);
    if (!source || !target) return this.canvas.onError('Selecciona dos equipos para crear el enlace.');
    if (source.device_type === 'attacker-station' || target.device_type === 'attacker-station') return this.canvas.onError('La estación de participante se conecta a la red de su zona; no necesita un enlace Ethernet manual.');
    if (sourceId === targetId) return this.canvas.onError('Selecciona dos equipos distintos.');
    if (source.zone_id !== target.zone_id) return this.canvas.onError('Los enlaces Ethernet genéricos son de la misma zona. Para cruzar zonas configura una ruta por protocolo.');
    if (this.canvas.networkConnections.some((link) => link.source_node_id === sourceId && link.target_node_id === targetId || link.source_node_id === targetId && link.target_node_id === sourceId)) return this.canvas.onError('Ya existe un enlace entre esos equipos.');
    this.canvas.networkConnections.push({ connection_id: `net-${crypto.randomUUID()}`, source_node_id: sourceId, target_node_id: targetId, label: 'Ethernet · lógico' });
    this.render(); this.canvas.onChange();
  }

  addGenericTcpRoute() {
    if (this.canvas.locked) return;
    const sourceId = this.controls.querySelector('[data-network-source]')?.value;
    const targetId = this.controls.querySelector('[data-network-target]')?.value;
    const port = Number(this.controls.querySelector('[data-network-port]')?.value);
    const devices = this.canvas.getDevices();
    const source = devices.find((device) => device.node_id === sourceId);
    const target = devices.find((device) => device.node_id === targetId);
    if (!source || !target) return this.canvas.onError('Selecciona dos equipos para crear la ruta.');
    if (source.device_type === 'attacker-station' || target.device_type === 'attacker-station') return this.canvas.onError('La estación de participante se conecta a la red de su zona; no puede ser extremo de una ruta TCP genérica.');
    if (sourceId === targetId) return this.canvas.onError('La ruta necesita dos equipos distintos.');
    if (source.zone_id === target.zone_id) return this.canvas.onError('La ruta TCP genérica es para cruzar zonas; los equipos de una zona ya comparten su red Docker.');
    if (!Number.isInteger(port) || port < 1 || port > 65535) return this.canvas.onError('Indica un puerto TCP entre 1 y 65535.');
    if (this.canvas.networkRoutes.some((route) => route.protocol === 'tcp' && route.source_node_id === sourceId && route.target_node_id === targetId && route.tcp_port === port)) return this.canvas.onError('Ya existe una ruta TCP para ese equipo y puerto.');
    this.canvas.networkRoutes.push({ route_id: `tcp-${crypto.randomUUID()}`, source_node_id: sourceId, target_node_id: targetId, protocol: 'tcp', tcp_port: port, enabled: true });
    this.canvas.element.dispatchEvent(new CustomEvent('network-routes-change', { detail: this.canvas.networkRoutes }));
    this.renderNetworkConnections(); this.drawLinks();
  }

  addEgressRule() {
    if (this.canvas.locked) return;
    const sourceId = this.controls.querySelector('[data-egress-source]')?.value;
    const hostname = this.controls.querySelector('[data-egress-hostname]')?.value.trim().toLowerCase().replace(/\.$/, '');
    const port = Number(this.controls.querySelector('[data-egress-port]')?.value);
    const source = this.canvas.getDevices().find((device) => device.node_id === sourceId);
    if (!source) return this.canvas.onError('Añade o habilita una estación de ingeniería o participante con acceso de red.');
    if (!hostname || hostname.length > 253 || !/^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(hostname)) return this.canvas.onError('Indica un nombre DNS completo válido, sin comodines ni direcciones IP.');
    if (/^(?:localhost|local|internal|test|invalid|example|home\.arpa)$/.test(hostname) || /\.(?:localhost|local|internal|test|invalid|example|home\.arpa)$/.test(hostname)) return this.canvas.onError('No se permiten nombres locales ni dominios reservados para pruebas.');
    if (!Number.isInteger(port) || port < 1 || port > 65535) return this.canvas.onError('Indica un puerto TCP entre 1 y 65535.');
    if (this.egressRules.some((rule) => rule.source_node_id === sourceId && rule.hostname === hostname && rule.tcp_port === port)) return this.canvas.onError('Ya existe esa regla de salida para la estación.');
    if (this.egressRules.length >= 32) return this.canvas.onError('El escenario admite hasta 32 reglas de salida.');
    this.egressRules.push({ rule_id: `egress-${crypto.randomUUID()}`, source_node_id: sourceId, hostname, tcp_port: port, enabled: true });
    this.controls.querySelector('[data-egress-hostname]').value = '';
    this.canvas.element.dispatchEvent(new CustomEvent('network-egress-rules-change', { detail: this.egressRules }));
    this.renderNetworkConnections();
  }

  arrange() {
    if (this.canvas.locked) { this.canvas.onError('Detén el escenario para ordenar y guardar el mapa.'); return; }
    this.positions = {};
    let x = 70;
    for (const [id, node] of Object.entries(this.canvas.exportNodes())) {
      node.pos_x = x; node.pos_y = 90;
      const stored = this.canvas.editor.drawflow.drawflow.Home.data[id];
      stored.pos_x = x; stored.pos_y = 90;
      const element = this.canvas.element.querySelector(`#node-${id}`);
      element.style.left = `${x}px`; element.style.top = '90px';
      x += (node.data.config?.components || node.data.config?.points || []).length ? 620 : 340;
    }
    this.render(); this.fit(); this.canvas.onFieldChange?.(); this.canvas.onChange();
  }

  fit() {
    const elements = [...this.canvas.element.querySelectorAll('.drawflow-node'), ...(this.visible ? this.fields.querySelectorAll('.field-node') : [])];
    if (!elements.length) return;
    const minX = Math.min(...elements.map((e) => e.offsetLeft)), minY = Math.min(...elements.map((e) => e.offsetTop));
    const maxX = Math.max(...elements.map((e) => e.offsetLeft+e.offsetWidth)), maxY = Math.max(...elements.map((e) => e.offsetTop+e.offsetHeight));
    const editor = this.canvas.editor;
    const toolbar = document.querySelector('.canvas-toolbar');
    const canvasTop = this.canvas.element.getBoundingClientRect().top;
    const overlayBottom = Math.max(toolbar.getBoundingClientRect().bottom, this.controls.getBoundingClientRect().bottom);
    const top = Math.max(toolbar.offsetHeight + 45, overlayBottom - canvasTop + 24);
    editor.zoom = Math.min(1, Math.max(.2, Math.min((this.canvas.element.clientWidth-60)/(maxX-minX), (this.canvas.element.clientHeight-top-30)/(maxY-minY))));
    editor.zoom_last_value = editor.zoom;
    editor.canvas_x = 30-minX*editor.zoom; editor.canvas_y = top-minY*editor.zoom;
    editor.precanvas.style.transform = `translate(${editor.canvas_x}px, ${editor.canvas_y}px) scale(${editor.zoom})`;
    this.canvas.viewport?.refresh();
  }
}
