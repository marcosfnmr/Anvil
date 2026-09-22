export const COMPONENT_REGISTRY = new Map();

export function registerComponentType(type, definition) {
  if (COMPONENT_REGISTRY.has(type)) {
    throw new Error(`El componente '${type}' ya está registrado`);
  }
  COMPONENT_REGISTRY.set(type, { type, ...definition });
}

export function getComponentType(type) {
  const definition = COMPONENT_REGISTRY.get(type);
  if (!definition) {
    throw new Error(`Componente no soportado: ${type}`);
  }
  return definition;
}

export function listComponentTypes() {
  return [...COMPONENT_REGISTRY.values()];
}
