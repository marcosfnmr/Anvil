// View navigation never changes scenario configuration or device positions.
export class MapViewport {
  constructor(canvas) {
    this.canvas = canvas;
    this.editor = canvas.editor;
    this.space = false;
    this.hand = false;
    const controls = document.createElement('div');
    controls.className = 'viewport-controls';
    controls.innerHTML = '<button type="button" class="button button-secondary" data-pan-mode aria-pressed="false" title="Arrastra desde cualquier punto para mover la vista">Mover</button><button type="button" class="button button-secondary" data-zoom-out aria-label="Alejar mapa">−</button><output data-map-zoom aria-label="Zoom del mapa">100 %</output><button type="button" class="button button-secondary" data-zoom-in aria-label="Acercar mapa">+</button>';
    document.querySelector('.process-map-controls').append(controls);
    this.label = controls.querySelector('output');
    controls.querySelector('[data-pan-mode]').addEventListener('click', (event) => {
      this.hand = !this.hand;
      event.currentTarget.setAttribute('aria-pressed', String(this.hand));
      canvas.element.classList.toggle('pan-mode', this.hand);
    });
    controls.querySelector('[data-zoom-out]').addEventListener('click', () => this.zoom(this.editor.zoom / 1.2));
    controls.querySelector('[data-zoom-in]').addEventListener('click', () => this.zoom(this.editor.zoom * 1.2));
    const hint = document.querySelector('.canvas-hint');
    if (hint) { hint.textContent = 'Arrastra el fondo · Rueda: zoom · Espacio + arrastrar: mover'; hint.id = 'map-navigation-hint'; canvas.element.setAttribute('aria-describedby', hint.id); }
    canvas.element.setAttribute('role', 'region');
    canvas.element.setAttribute('aria-label', 'Mapa OT. Arrastra el fondo para desplazarte; usa las flechas cuando el mapa tenga el foco.');
    canvas.element.addEventListener('pointerdown', (event) => this.start(event), true);
    for (const type of ['mousedown', 'mousemove', 'touchstart', 'touchmove']) canvas.element.addEventListener(type, (event) => {
      if (this.panning) { event.stopImmediatePropagation(); if (event.cancelable) event.preventDefault(); }
    }, { capture: true, passive: false });
    canvas.element.addEventListener('wheel', (event) => {
      event.preventDefault(); event.stopImmediatePropagation();
      if (event.shiftKey) this.pan(-event.deltaY, -event.deltaX);
      else this.zoom(this.editor.zoom * Math.exp(-event.deltaY * .002), event.clientX, event.clientY);
    }, { capture: true, passive: false });
    canvas.element.addEventListener('keydown', (event) => {
      if (event.target !== canvas.element) return;
      if (event.code === 'Space') { event.preventDefault(); this.space = true; canvas.element.classList.add('pan-mode'); }
      const step = event.shiftKey ? 100 : 40;
      const directions = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
      if (directions[event.key]) { event.preventDefault(); event.stopImmediatePropagation(); this.pan(...directions[event.key]); }
      if (['+', '=', '-'].includes(event.key)) { event.preventDefault(); this.zoom(this.editor.zoom * (event.key === '-' ? 1/1.2 : 1.2)); }
      if (event.key === 'Home') { event.preventDefault(); canvas.processMap.fit(); }
    }, true);
    window.addEventListener('keyup', (event) => { if (event.code === 'Space') this.releaseSpace(); });
    window.addEventListener('blur', () => this.releaseSpace());
    this.editor.on('zoom', () => this.refresh());
  }

  releaseSpace() { this.space = false; this.canvas.element.classList.toggle('pan-mode', this.hand); }

  refresh() { this.label.textContent = `${Math.round(this.editor.zoom * 100)} %`; }

  apply() {
    this.editor.precanvas.style.transform = `translate(${this.editor.canvas_x}px, ${this.editor.canvas_y}px) scale(${this.editor.zoom})`;
    this.editor.zoom_last_value = this.editor.zoom;
    this.refresh();
  }

  pan(dx, dy) { this.editor.canvas_x += dx; this.editor.canvas_y += dy; this.apply(); }

  zoom(value, clientX, clientY) {
    const rect = this.canvas.element.getBoundingClientRect();
    const x = clientX === undefined ? rect.width / 2 : clientX - rect.left;
    const y = clientY === undefined ? rect.height / 2 : clientY - rect.top;
    const previous = this.editor.zoom;
    const next = Math.min(this.editor.zoom_max, Math.max(this.editor.zoom_min, value));
    this.editor.canvas_x = x - (x-this.editor.canvas_x) * next/previous;
    this.editor.canvas_y = y - (y-this.editor.canvas_y) * next/previous;
    this.editor.zoom = next;
    this.apply();
  }

  start(event) {
    if (event.target.closest('button, input, select, a')) return;
    const object = event.target.closest('.drawflow-node, .field-node');
    if (event.button !== 1 && event.button !== 0) return;
    if (event.button !== 1 && !this.space && !this.hand && object && !this.canvas.locked) return;
    event.stopImmediatePropagation();
    this.canvas.element.focus({ preventScroll: true });
    const start = { x: event.clientX, y: event.clientY, px: this.editor.canvas_x, py: this.editor.canvas_y };
    this.panning = true;
    this.canvas.element.classList.add('is-panning');
    this.canvas.element.setPointerCapture(event.pointerId);
    const move = (e) => {
      this.editor.canvas_x = start.px + e.clientX-start.x;
      this.editor.canvas_y = start.py + e.clientY-start.y;
      this.apply();
    };
    const end = () => {
      this.panning = false;
      this.canvas.element.classList.remove('is-panning');
      this.canvas.element.removeEventListener('pointermove', move);
      this.canvas.element.removeEventListener('pointerup', end);
      this.canvas.element.removeEventListener('pointercancel', end);
    };
    this.canvas.element.addEventListener('pointermove', move);
    this.canvas.element.addEventListener('pointerup', end);
    this.canvas.element.addEventListener('pointercancel', end);
  }
}
