# Hoja de ruta

## Completado

- **Fase 1:** simulador PLC S7 parametrizable.
- **Fase 2:** persistencia y orquestación Docker.
- **Fase 3:** editor visual de escenarios.
- **Fase 4:** estado en vivo mediante WebSocket.
- **Fase 5:** consolidación operativa: supervisor compartido, healthchecks,
  reconciliación, limpieza segura de huérfanos, dependencias reproducibles,
  prueba S7 real, CI y documentación de operación.

## Fase 6 propuesta: escenario utilizable como cyber range

1. Guardar posiciones y conexiones del canvas.
2. Mostrar el mapa de direcciones S7 calculado sin permitir editar offsets.
3. Consultar logs y diagnóstico de cada dispositivo desde la interfaz.
4. Importar, exportar y clonar escenarios.
5. Definir una vía de acceso controlada desde el exterior a las redes OT.

## Fase 7 propuesta: catálogo OT

1. Generalizar el schema de configuración por `device_type`.
2. Añadir servidor Modbus TCP.
3. Añadir motores, bombas, entradas digitales y alarmas.
4. Incorporar HMI, gateway, firewall y una estación atacante opcional.

## Antes de exposición pública

- Autenticación y autorización.
- TLS mediante proxy inverso.
- Acceso restringido al Docker API.
- Límites de recursos y cuotas por escenario.
- Registro de auditoría y retención de eventos.
