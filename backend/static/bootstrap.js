// Surface module/startup failures instead of leaving a screen with inert controls.
let failed = false;
function startupError(error) {
  if (failed) return;
  failed = true;
  const panel = document.createElement('section');
  panel.setAttribute('role', 'alert');
  panel.style.cssText = 'position:fixed;z-index:10000;inset:24px auto auto 24px;max-width:620px;padding:24px;background:#172231;color:#fff;border:2px solid #ffba68;border-radius:12px';
  const title = document.createElement('h2');
  title.textContent = 'No se pudo iniciar la interfaz';
  const detail = document.createElement('p');
  detail.textContent = error?.message || String(error);
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.textContent = 'Recargar interfaz';
  retry.className = 'button button-primary';
  retry.addEventListener('click', () => {
    const url = new URL(location.href);
    url.searchParams.set('reload', Date.now());
    location.replace(url);
  });
  panel.append(title, detail, retry);
  document.body.append(panel);
}
window.addEventListener('anvil-startup-error', (event) => startupError(event.detail));

async function installAuthenticationGate() {
  const response = await fetch('/api/auth/status', { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`No se pudo consultar el acceso (${response.status})`);
  const auth = await response.json();
  if (!auth.auth_required) return auth;
  if (auth.authenticated) {
    window.anvilUser = auth.user;
    showAccount(auth.user);
    return auth;
  }

  document.querySelector('.app-shell')?.setAttribute('inert', '');
  const gate = document.createElement('div');
  gate.className = 'auth-gate';
  gate.setAttribute('role', 'dialog');
  gate.setAttribute('aria-modal', 'true');
  gate.setAttribute('aria-labelledby', 'auth-title');
  const setup = auth.setup_required;
  gate.innerHTML = `<form class="auth-card" id="auth-form"><span class="eyebrow">ANVIL · ACCESO</span><h1 id="auth-title">${setup ? 'Configura la cuenta administradora' : 'Inicia sesión'}</h1><p>${setup ? 'Crea la primera cuenta. El token de configuración se define en el entorno del servidor.' : 'Usa tu cuenta de Anvil para continuar.'}</p><label>Correo electrónico<input name="email" type="email" autocomplete="username" required maxlength="254"></label><label>Contraseña<input name="password" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" required minlength="${setup ? '12' : '1'}" maxlength="128"></label>${setup && auth.bootstrap_enabled ? '<label>Token inicial<input name="token" type="password" autocomplete="off" required minlength="32" maxlength="256"></label>' : ''}${setup && !auth.bootstrap_enabled ? '<p class="auth-error" role="alert">El administrador debe configurar ANVIL_BOOTSTRAP_TOKEN con al menos 32 caracteres y reiniciar el servidor.</p>' : ''}<p class="auth-error" id="auth-error" role="alert" hidden></p><button class="button button-primary" type="submit" ${setup && !auth.bootstrap_enabled ? 'disabled' : ''}>${setup ? 'Crear cuenta y entrar' : 'Entrar'}</button></form>`;
  document.body.append(gate);
  const form = gate.querySelector('form');
  form.elements.email.focus();
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const error = gate.querySelector('#auth-error');
    error.hidden = true;
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form).entries());
      const endpoint = setup ? '/api/auth/bootstrap' : '/api/auth/login';
      const result = await fetch(endpoint, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
      });
      const payload = result.status === 204 ? {} : await result.json();
      if (!result.ok) throw new Error(payload.detail || `Acceso rechazado (${result.status})`);
      location.reload();
    } catch (cause) {
      error.textContent = cause?.message || String(cause);
      error.hidden = false;
      button.disabled = false;
    }
  });
  return auth;
}

function showAccount(user) {
  const actions = document.querySelector('.topbar-actions');
  if (!actions || actions.querySelector('[data-account]')) return;
  const badge = document.createElement('span');
  badge.dataset.account = 'true';
  badge.className = 'account-badge';
  badge.textContent = `${user.email} · ${user.role}`;
  const shareButton = document.getElementById('share-scenario-button');
  if (shareButton && ['admin', 'instructor'].includes(user.role)) shareButton.hidden = false;
  const logout = document.createElement('button');
  logout.type = 'button';
  logout.className = 'button button-secondary auth-action';
  logout.textContent = 'Cerrar sesión';
  logout.addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    location.reload();
  });
  actions.append(badge, logout);
  if (user.role === 'admin') installUserAdminButton(actions);
}

function installUserAdminButton(actions) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'button button-secondary auth-action';
  button.textContent = 'Usuarios';
  button.addEventListener('click', async () => {
    let dialog = document.getElementById('users-dialog');
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'users-dialog';
      dialog.setAttribute('aria-labelledby', 'users-title');
      dialog.innerHTML = '<form method="dialog"><button class="button button-secondary" aria-label="Cerrar usuarios">Cerrar</button></form><h2 id="users-title">Usuarios de Anvil</h2><form id="user-create-form" class="user-create-form"><label>Correo<input name="email" type="email" required maxlength="254"></label><label>Contraseña inicial<input name="password" type="password" minlength="12" maxlength="128" required autocomplete="new-password"></label><label>Rol<select name="role"><option>participant</option><option>designer</option><option>instructor</option><option>admin</option></select></label><button class="button button-primary" type="submit">Crear usuario</button><p id="user-admin-error" role="alert" hidden></p></form><p id="user-admin-status" role="status" aria-live="polite"></p><details class="platform-audit"><summary>Auditoría administrativa</summary><button id="platform-audit-load" class="button button-secondary" type="button">Cargar actividad y verificar integridad</button><p id="platform-audit-status" role="status" aria-live="polite"></p><ol id="platform-audit-events"></ol></details><div id="user-admin-list" aria-live="polite"></div>';
      document.body.append(dialog);
      dialog.querySelector('#user-create-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const values = Object.fromEntries(new FormData(form).entries());
        const result = await fetch('/api/users', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
        if (!result.ok) { const body = await result.json(); showUserError(dialog, body.detail || 'No se pudo crear el usuario'); return; }
        form.reset();
        await renderUsers(dialog);
      });
      dialog.querySelector('#platform-audit-load').addEventListener('click', () => renderPlatformAudit(dialog));
      dialog.querySelector('#user-admin-list').addEventListener('change', async (event) => {
        const select = event.target.closest('[data-user-role]');
        if (!select) return;
        const result = await fetch(`/api/users/${encodeURIComponent(select.dataset.userId)}`, { method: 'PATCH', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: select.value, active: select.closest('[data-user-row]').querySelector('[data-user-active]').checked }) });
        if (!result.ok) { const body = await result.json(); showUserError(dialog, body.detail || 'No se pudo actualizar el usuario'); }
      });
    }
    await renderUsers(dialog);
    dialog.showModal();
  });
  actions.append(button);
}

async function renderPlatformAudit(dialog) {
  const status = dialog.querySelector('#platform-audit-status');
  const list = dialog.querySelector('#platform-audit-events');
  status.textContent = 'Cargando…';
  list.replaceChildren();
  try {
    const [eventsResponse, integrityResponse] = await Promise.all([
      fetch('/api/admin/audit-events?limit=100', { credentials: 'same-origin' }),
      fetch('/api/admin/audit-events/integrity', { credentials: 'same-origin' }),
    ]);
    if (!eventsResponse.ok || !integrityResponse.ok) throw new Error('No se pudo cargar la auditoría administrativa');
    const page = await eventsResponse.json();
    const integrity = await integrityResponse.json();
    for (const event of page.events) {
      const item = document.createElement('li');
      const when = new Date(event.timestamp).toLocaleString();
      const ids = [event.actor_user_id && `actor ${event.actor_user_id}`, event.target_user_id && `objetivo ${event.target_user_id}`].filter(Boolean).join(' · ');
      item.textContent = `${when} · ${event.event_type}${ids ? ` · ${ids}` : ''} · ${JSON.stringify(event.details)}`;
      list.append(item);
    }
    status.textContent = `Integridad ${integrity.valid ? 'válida' : 'inválida'} · ${integrity.checked_events} eventos${integrity.first_invalid_event_id ? ` · primer error #${integrity.first_invalid_event_id}` : ''}`;
  } catch (error) {
    status.textContent = error.message || 'No se pudo cargar la auditoría administrativa';
  }
}

async function renderUsers(dialog) {
  const response = await fetch('/api/users', { credentials: 'same-origin' });
  if (!response.ok) { showUserError(dialog, 'No se pudo cargar la lista de usuarios'); return; }
  const users = await response.json();
  const list = dialog.querySelector('#user-admin-list');
  list.replaceChildren();
  for (const user of users) {
    const row = document.createElement('article');
    row.dataset.userRow = 'true';
    const label = document.createElement('strong'); label.textContent = user.email;
    const role = document.createElement('select'); role.dataset.userRole = 'true'; role.dataset.userId = user.id;
    for (const name of ['participant', 'designer', 'instructor', 'admin']) { const option = document.createElement('option'); option.value = name; option.textContent = name; option.selected = name === user.role; role.append(option); }
    const activeLabel = document.createElement('label'); activeLabel.textContent = 'Activa';
    const active = document.createElement('input'); active.type = 'checkbox'; active.checked = user.active; active.dataset.userActive = 'true'; active.addEventListener('change', () => role.dispatchEvent(new Event('change', { bubbles: true })));
    activeLabel.prepend(active);
    const resetForm = document.createElement('form'); resetForm.className = 'user-password-reset';
    const passwordLabel = document.createElement('label'); passwordLabel.textContent = 'Nueva contraseña temporal';
    const password = document.createElement('input'); password.type = 'password'; password.name = 'new_password'; password.required = true; password.minLength = 12; password.maxLength = 128; password.autocomplete = 'new-password'; password.dataset.userPassword = 'true';
    const resetButton = document.createElement('button'); resetButton.type = 'submit'; resetButton.className = 'button button-secondary'; resetButton.textContent = 'Restablecer contraseña';
    passwordLabel.append(password); resetForm.append(passwordLabel, resetButton);
    resetForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const result = await fetch(`/api/users/${encodeURIComponent(user.id)}/password`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: password.value }),
      });
      if (!result.ok) { const body = await result.json(); showUserError(dialog, body.detail || 'No se pudo restablecer la contraseña'); return; }
      password.value = '';
      const error = dialog.querySelector('#user-admin-error'); error.hidden = true;
      dialog.querySelector('#user-admin-status').textContent = `Contraseña restablecida para ${user.email}. Sus sesiones anteriores se cerraron.`;
    });
    row.append(label, role, activeLabel, resetForm); list.append(row);
  }
}

function showUserError(dialog, message) {
  const error = dialog.querySelector('#user-admin-error');
  error.textContent = message;
  error.hidden = false;
}

installAuthenticationGate().then((auth) => {
  if (auth?.auth_required && !auth.authenticated) return;
  if (auth?.authenticated) window.anvilUser = auth.user;
  import('./app.js?v=canvas-node-cards-20261013').catch(startupError);
}).catch(startupError);
