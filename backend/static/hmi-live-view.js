const editedHmiInputs = new WeakSet();

export function markHmiInputEdited(input) {
  if (input instanceof HTMLInputElement) editedHmiInputs.add(input);
}

export function clearHmiInputEdited(input) {
  if (input instanceof HTMLInputElement) editedHmiInputs.delete(input);
}

function hmiLiveKey(element) {
  if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
  if (element.dataset.hmiLiveKey) return element.dataset.hmiLiveKey;
  if (element.matches(".hmi-synoptic")) return "synoptic";
  if (element.dataset.hmiObject) return `field:${element.dataset.hmiObject}`;
  if (element.dataset.hmiTargetNode && element.dataset.hmiAddress) {
    return `${element.dataset.hmiKind || "point"}:${element.dataset.hmiTargetNode}:${element.dataset.hmiAddress}`;
  }
  return null;
}

function syncHmiNode(current, next) {
  if (current.nodeType === Node.TEXT_NODE) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return current;
  }
  if (current.nodeType !== Node.ELEMENT_NODE || current.tagName !== next.tagName) return next.cloneNode(true);
  const edited = current.matches("[data-hmi-value], [data-scada-value]") && editedHmiInputs.has(current);
  const value = current.value;
  const checked = current.checked;
  for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
  for (const attr of [...next.attributes]) current.setAttribute(attr.name, attr.value);
  if (current.matches("input[data-hmi-value], input[data-scada-value]")) {
    if (!edited) {
      current.value = next.value;
      current.checked = next.checked;
    } else {
      current.value = value;
      current.checked = checked;
    }
  } else {
    syncHmiChildren(current, next);
  }
  return current;
}

function syncHmiChildren(current, next) {
  const retained = new Set();
  const oldChildren = [...current.childNodes];
  const keyed = new Map(oldChildren.filter((node) => node.nodeType === Node.ELEMENT_NODE)
    .map((node) => [hmiLiveKey(node), node]).filter(([key]) => key));
  let cursor = current.firstChild;
  for (const incoming of [...next.childNodes]) {
    const key = incoming.nodeType === Node.ELEMENT_NODE ? hmiLiveKey(incoming) : null;
    let existing = key ? keyed.get(key) : null;
    if (!existing && !key && cursor && !retained.has(cursor)
        && !hmiLiveKey(cursor) && cursor.nodeType === incoming.nodeType
        && (cursor.nodeType !== Node.ELEMENT_NODE || cursor.tagName === incoming.tagName)) existing = cursor;
    const updated = existing ? syncHmiNode(existing, incoming) : incoming.cloneNode(true);
    if (updated !== existing && existing?.parentNode === current) existing.remove();
    if (updated !== cursor) current.insertBefore(updated, cursor);
    retained.add(updated);
    cursor = updated.nextSibling;
  }
  for (const child of [...current.childNodes]) if (!retained.has(child)) child.remove();
}

export function updateHmiLiveView(output, markup) {
  const template = document.createElement("template");
  template.innerHTML = markup;
  const nextGrid = template.content.querySelector(".hmi-live-grid");
  if (!nextGrid) return;
  const currentGrid = output.querySelector(":scope > .hmi-live-grid");
  if (!currentGrid) output.prepend(nextGrid);
  else syncHmiNode(currentGrid, nextGrid);
  // The point/alarm payload normally contains no synoptic markup. Do not pass
  // that partial payload to updateHmiSynoptic: a missing section there means
  // "not included in this update", not "remove the live process diagram".
  if (template.content.querySelector(".hmi-synoptic")) updateHmiSynoptic(output, template.innerHTML);
}

export function updateHmiSynoptic(output, markup) {
  const template = document.createElement("template");
  template.innerHTML = markup;
  const nextSynoptic = template.content.querySelector(".hmi-synoptic");
  const currentSynoptic = output.querySelector(":scope > .hmi-synoptic");
  if (nextSynoptic && currentSynoptic) syncHmiNode(currentSynoptic, nextSynoptic);
  else if (nextSynoptic) output.append(nextSynoptic);
  else currentSynoptic?.remove();
}
