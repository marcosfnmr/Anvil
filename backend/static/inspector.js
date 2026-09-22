import { getComponentType, listComponentTypes } from "./components/registry.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clone(value) {
  return globalThis.structuredClone
    ? structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

function getPath(target, path) {
  return path.split(".").reduce((value, key) => value?.[key], target);
}

function setPath(target, path, value) {
  const keys = path.split(".");
  const leaf = keys.pop();
  const parent = keys.reduce((current, key) => {
    current[key] ??= {};
    return current[key];
  }, target);
  parent[leaf] = value;
}

function parseInput(input) {
  if (input.type === "checkbox") return input.checked;
  if (input.type === "number") return Number(input.value);
  return input.value;
}

function renderField(field, value, attributes) {
  const classes = `field${field.full ? " field-full" : ""}`;
  const common = `${attributes} data-value-type="${field.type}"`;

  if (field.type === "checkbox") {
    return `
      <label class="${classes} checkbox-field">
        <span>${escapeHtml(field.label)}</span>
        <input type="checkbox" ${common} ${value ? "checked" : ""} />
      </label>
    `;
  }

  if (field.type === "select") {
    const options = field.options
      .map(
        (option) =>
          `<option value="${escapeHtml(option.value)}" ${option.value === value ? "selected" : ""}>${escapeHtml(option.label)}</option>`,
      )
      .join("");
    return `
      <label class="${classes}">
        <span>${escapeHtml(field.label)}</span>
        <select ${common}>${options}</select>
      </label>
    `;
  }

  const constraints = [
    field.min !== undefined ? `min="${field.min}"` : "",
    field.max !== undefined ? `max="${field.max}"` : "",
    field.step !== undefined ? `step="${field.step}"` : "",
  ].join(" ");
  return `
    <label class="${classes}">
      <span>${escapeHtml(field.label)}</span>
      <input type="${field.type}" value="${escapeHtml(value)}" ${constraints} ${common} />
    </label>
  `;
}

export class Inspector {
  constructor(element, { onChange, onRemove } = {}) {
    this.element = element;
    this.onChange = onChange || (() => {});
    this.onRemove = onRemove || (() => {});
    this.data = null;
    this.bindEvents();
    this.renderEmpty();
  }

  bindEvents() {
    const handleField = (event) => {
      const input = event.target.closest("[data-device-field], [data-component-field]");
      if (!input || !this.data) return;

      if (input.dataset.deviceField) {
        setPath(this.data, input.dataset.deviceField, parseInput(input));
      } else {
        const index = Number(input.dataset.componentIndex);
        setPath(
          this.data.config.components[index],
          input.dataset.componentField,
          parseInput(input),
        );
      }
      this.onChange(clone(this.data));
    };

    this.element.addEventListener("input", handleField);
    this.element.addEventListener("change", handleField);

    this.element.addEventListener("click", (event) => {
      const addButton = event.target.closest("[data-add-component]");
      if (addButton && this.data) {
        const definition = getComponentType(addButton.dataset.addComponent);
        const count = this.data.config.components.filter(
          (item) => item.type === definition.type,
        ).length;
        this.data.config.components.push(definition.create(count + 1));
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }

      const removeButton = event.target.closest("[data-remove-component]");
      if (removeButton && this.data) {
        this.data.config.components.splice(Number(removeButton.dataset.removeComponent), 1);
        this.onChange(clone(this.data));
        this.render(this.data);
        return;
      }

      if (event.target.closest("[data-remove-node]")) {
        this.onRemove();
      }
    });
  }

  render(data) {
    if (!data) {
      this.data = null;
      this.renderEmpty();
      return;
    }

    this.data = clone(data);
    const addButtons = listComponentTypes()
      .map(
        (definition) =>
          `<button class="tiny-button" type="button" data-add-component="${definition.type}">+ ${escapeHtml(definition.label)}</button>`,
      )
      .join("");

    const components = this.data.config.components
      .map((component, index) => this.renderComponent(component, index))
      .join("");

    this.element.innerHTML = `
      <div class="inspector-form">
        <div class="field-grid">
          ${renderField(
            { label: "Nombre", type: "text", full: true },
            this.data.name,
            'data-device-field="name"',
          )}
          ${renderField(
            { label: "Dirección IP", type: "text", full: true },
            this.data.ip,
            'data-device-field="ip"',
          )}
          <label class="field field-full">
            <span>Node ID</span>
            <input type="text" value="${escapeHtml(this.data.node_id)}" disabled />
          </label>
          ${renderField(
            { label: "Rack", type: "number", min: 0 },
            this.data.config.rack,
            'data-device-field="config.rack"',
          )}
          ${renderField(
            { label: "Slot", type: "number", min: 0 },
            this.data.config.slot,
            'data-device-field="config.slot"',
          )}
          ${renderField(
            { label: "Tamaño DB", type: "number", min: 1, full: true },
            this.data.config.db_size,
            'data-device-field="config.db_size"',
          )}
        </div>

        <section class="inspector-section">
          <div class="inspector-section-title">
            <strong>Componentes</strong>
            <div class="component-actions">${addButtons}</div>
          </div>
          ${components || '<div class="empty-list">Añade al menos un componente.</div>'}
        </section>

        <button class="remove-node" type="button" data-remove-node>
          Eliminar este dispositivo
        </button>
      </div>
    `;
  }

  renderComponent(component, index) {
    const definition = getComponentType(component.type);
    const fields = definition.fields
      .map((field) =>
        renderField(
          field,
          getPath(component, field.path),
          `data-component-index="${index}" data-component-field="${field.path}"`,
        ),
      )
      .join("");

    return `
      <article class="component-card">
        <header class="component-card-header">
          <strong>${escapeHtml(definition.icon)} · ${escapeHtml(definition.label)} ${index + 1}</strong>
          <button
            class="remove-component"
            type="button"
            title="Eliminar componente"
            data-remove-component="${index}"
          >×</button>
        </header>
        <div class="component-card-body">${fields}</div>
      </article>
    `;
  }

  renderEmpty() {
    this.element.innerHTML = `
      <div class="inspector-empty">
        <strong>Ningún dispositivo seleccionado</strong>
        <span>Selecciona un nodo del lienzo para editar su configuración y componentes.</span>
      </div>
    `;
  }
}
