// Keep the existing controllers and element IDs; give each task its own page.
const labels = { projects: "Escenarios", editor: "Editor", catalog: "Catálogo", config: "Configuración", telemetry: "Telemetría", hmi: "HMI" };

export function showWorkspace(view, { focus = false } = {}) {
  if (!labels[view]) return;
  const changed = document.body.dataset.workspace !== view;
  document.body.dataset.workspace = view;
  document.querySelectorAll("[data-workspace-page]").forEach((page) => { page.hidden = page.dataset.workspacePage !== view; });
  document.querySelectorAll("[data-workspace-view]").forEach((button) => {
    if (button.closest(".workspace-navigation")) {
      const current = button.dataset.workspaceView === view || view === "config" && button.dataset.workspaceView === "editor";
      if (current) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    }
  });
  document.getElementById("workspace-title").textContent = labels[view];
  if (changed) document.querySelector(`[data-workspace-page="${view}"]`).scrollTop = 0;
  if (focus) document.querySelector(`[data-workspace-page="${view}"] h1`)?.focus();
  window.dispatchEvent(new Event("resize"));
}

export function initWorkspace() {
  const workspace = document.querySelector(".workspace");
  workspace.setAttribute("aria-label", "Área de trabajo de Anvil");
  const projects = document.querySelector(".scenarios-section");
  const catalog = document.querySelector(".palette-section");
  const canvas = document.querySelector(".canvas-panel");
  const heading = document.querySelector(".inspector-heading");
  const inspector = document.getElementById("inspector");
  const runtime = document.getElementById("runtime-panel");
  const activity = document.querySelector(".events-section");
  const footer = document.querySelector(".panel-footer");
  const deletion = document.querySelector(".right-panel-footer");
  const feedback = document.getElementById("editor-feedback");
  const nav = document.createElement("nav");
  nav.className = "workspace-navigation";
  nav.setAttribute("aria-label", "Navegación principal");
  nav.innerHTML = `<div class="navigation-label">LABORATORIO</div>${["projects", "editor", "catalog", "telemetry", "hmi"].map((view, index) => `<button type="button" data-workspace-view="${view}" aria-controls="page-${view}"${view === "hmi" ? ' hidden data-hmi-navigation' : ""}><span class="navigation-icon" aria-hidden="true">${["▦", "◇", "+", "∿", "▤"][index]}</span>${labels[view]}</button>`).join("")}`;
  nav.append(footer);
  workspace.prepend(nav);
  function page(view, title, description, actions = "") {
    const section = document.createElement("section");
    section.id = `page-${view}`;
    section.dataset.workspacePage = view;
    section.className = `workspace-page page-${view}`;
    section.setAttribute("aria-labelledby", `heading-${view}`);
    section.innerHTML = `<header class="page-heading"><div><h1 id="heading-${view}" tabindex="-1">${title}</h1><p>${description}</p></div><div class="page-actions">${actions}</div></header>`;
    workspace.append(section);
    return section;
  }
  const projectPage = page("projects", "Tus escenarios", "Crea, importa y organiza tus laboratorios OT.");
  projectPage.querySelector(".page-actions").append(document.getElementById("new-scenario-button"));
  document.getElementById("new-scenario-button").textContent = "Nuevo escenario";
  document.getElementById("new-scenario-button").className = "button button-primary";
  projects.querySelector(".section-heading").remove();
  projectPage.append(projects, deletion);
  const editorPage = page("editor", "Diseña tu laboratorio", "Coloca sensores y actuadores por libre. Añade después los PLCs y conecta sus señales.", '<button type="button" class="button button-secondary" data-workspace-view="config">Configurar selección</button><button type="button" class="button button-primary" data-workspace-view="catalog">Añadir sensor, actuador o PLC</button>');
  const network = document.querySelector(".scenario-meta label:last-child");
  network.classList.add("network-setting");
  canvas.querySelector(".canvas-toolbar").append(network);
  editorPage.append(canvas);
  const catalogPage = page("catalog", "Añade elementos y dispositivos", "Construye primero la planta con sensores y actuadores. Después añade los controladores.", '<button type="button" class="button button-secondary" data-workspace-view="editor">Volver al editor</button>');
  catalog.querySelector(".section-heading").remove();
  catalog.querySelector(".section-help").remove();
  catalogPage.append(catalog);
  const configPage = page("config", "Configura el dispositivo", "Selecciona sus señales, permisos y conexiones.", '<button type="button" class="button button-secondary" data-workspace-view="editor">Volver al editor</button>');
  heading.querySelector(".eyebrow").remove();
  heading.querySelector("h2").remove();
  configPage.append(heading, inspector);
  const telemetryPage = page("telemetry", "Observa tu escenario", "Valores, cambios y comunicaciones del laboratorio en ejecución.", '<button type="button" class="button button-secondary" data-workspace-view="editor">Volver al editor</button>');
  telemetryPage.append(runtime);
  const hmiPage = page("hmi", "Operación HMI", "Vista de operador para consultar señales, actuar sobre salidas autorizadas y reconocer alarmas del SCADA asociado.", '<button type="button" class="button button-secondary" data-workspace-view="config" data-hmi-configure>Configurar HMI</button>');
  hmiPage.insertAdjacentHTML("beforeend", `<section class="hmi-console" aria-labelledby="hmi-console-title"><header class="hmi-console-toolbar"><div><h2 id="hmi-console-title">Panel de operación</h2><p>La HMI lee únicamente los destinos configurados en el escenario. Las escrituras y alarmas usan las políticas de Anvil.</p></div><label>HMI<select data-hmi-console-device aria-label="Seleccionar HMI"></select></label><button type="button" class="button button-secondary" data-hmi-console-refresh>Actualizar</button></header><p class="hmi-console-status" data-hmi-console-status role="status" aria-live="polite">Selecciona o configura una HMI.</p><div data-hmi-console-values class="hmi-console-values" aria-live="off"></div></section>`);
  const filter = document.createElement("label");
  filter.className = "signal-filter";
  filter.innerHTML = '<span>Dispositivo</span><select data-telemetry-device><option value="">Todos los dispositivos</option></select>';
  runtime.querySelector("#value-status").after(filter);
  const activityTab = document.createElement("button");
  activityTab.className = "runtime-tab";
  activityTab.type = "button";
  activityTab.dataset.runtimeTab = "activity";
  activityTab.textContent = "Actividad";
  runtime.querySelector(".runtime-tabs").append(activityTab);
  activity.id = "runtime-activity";
  activity.classList.add("runtime-view");
  activity.dataset.runtimeView = "activity";
  activity.hidden = true;
  activity.querySelector(".eyebrow").remove();
  runtime.append(activity);
  document.querySelector(".left-panel").remove();
  document.querySelector(".right-panel").remove();
  const title = document.createElement("span");
  title.id = "workspace-title";
  title.className = "workspace-title";
  document.querySelector(".topbar").insertBefore(title, document.querySelector(".scenario-meta"));
  // Feedback remains visible across pages, independently of the inspector.
  document.querySelector(".app-shell").append(feedback);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-workspace-view]");
    if (button) showWorkspace(button.dataset.workspaceView, { focus: true });
  });
  const skip = document.querySelector(".skip-link");
  skip.href = "#workspace-navigation";
  nav.id = "workspace-navigation";
  nav.tabIndex = -1;
  skip.textContent = "Saltar a la navegación";
  showWorkspace("projects");
}
