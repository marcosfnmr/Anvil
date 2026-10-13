# API HTTP y eventos en vivo

Anvil publica una API FastAPI bajo `/api`. El esquema OpenAPI visible en una
instancia ejecutándose se obtiene en `/openapi.json` y la interfaz Swagger en
`/docs`. La autenticación de sesión del navegador usa cookie; la mayoría de
recursos exige una identidad válida y permisos para el escenario.

## Grupos de endpoints

Los nombres pueden crecer junto con el producto; OpenAPI de la instancia es la
referencia exacta de su build.

| Grupo | Operaciones principales |
| --- | --- |
| Salud | `GET /api/health`, `GET /api/ready` |
| Identidad | `/api/auth/status`, `bootstrap`, `login`, `me`, `logout`, `password` |
| Escenarios | CRUD en `/api/scenarios`, catálogo, import/export, clonación, revisiones y copia con migración de campo |
| Topología | mapa de E/S, inventario de servicios/puertos, diagnóstico, valores del runtime de campo |
| Despliegue | deploy, stop, pause, resume, estado y ciclo de vida de nodo |
| Protocolos | lectura/escritura de PLC, Modbus, HMI/SCADA y estado de protocolo |
| Evidencias | auditoría paginada/exportable, integridad, bundle y captura PCAP |
| Biblioteca | componentes y plantillas compartidas |
| Ejercicios | guardar, listar, iniciar, consultar y cancelar runs |

### Rutas TCP declaradas

`GET /api/scenarios/{scenario_id}/network-routes` devuelve protocolo,
extremos, puerto de destino, estado, endpoint local y `latency_ms` de cada
ruta. `latency_ms` es un entero de 0 a 10.000 ms por sentido (0 por defecto);
se configura en `network_routes[]` al crear/actualizar el escenario. El proxy
lo aplica antes de reenviar cada bloque TCP recibido. Es una aproximación de
latencia de flujo, no una garantía temporal ni demora por paquete; su alcance y
efecto en las rutas están descritos en [Zonas y rutas](NETWORK_ZONES.md).

## Ciclo CRUD de ejemplo

El nombre de los modelos es deliberado: solicita OpenAPI antes de automatizar
una integración y usa el esquema exacto de la versión instalada.

```text
POST   /api/scenarios
GET    /api/scenarios
GET    /api/scenarios/{scenario_id}
PUT    /api/scenarios/{scenario_id}
DELETE /api/scenarios/{scenario_id}
POST   /api/scenarios/{scenario_id}/deploy
POST   /api/scenarios/{scenario_id}/restart
GET    /api/scenarios/{scenario_id}/status
GET    /api/scenarios/{scenario_id}/services
POST   /api/scenarios/{scenario_id}/pause
POST   /api/scenarios/{scenario_id}/resume
POST   /api/scenarios/{scenario_id}/stop
GET    /api/scenarios/{scenario_id}/field-migration-preview
POST   /api/scenarios/{scenario_id}/field-migration-copy
GET    /api/scenarios/{scenario_id}/field/values
POST   /api/scenarios/{scenario_id}/field/{object_id}/control
```

El control de campo admite `fault`, `override`, `demand_flow_lpm` o
`leak_flow_lpm`, según el tipo del objeto. Para un tanque, demanda y fuga son
caudales finitos no negativos en L/min; `leak_flow_lpm: 0` cierra una fuga
dinámica. El cambio queda en la auditoría del escenario. Las cronologías de
ejercicio usan los mismos campos en eventos `field_control` y deben programar
explícitamente una restauración de fuga/demanda si la necesitan.

La vista previa de migración es de solo lectura. El POST crea una copia detenida
con los componentes de campo PLC S7 convertidos a objetos independientes y
canales `io_channel`, conservando las direcciones DB y la lógica. Acepta
`{"name":"nombre opcional"}`; omitirlo genera un nombre derivado. El origen no
se modifica. Antes de desplegar copia y origen simultáneamente, cambia la subred
y las IP heredadas de la copia para evitar solapes. Una topología que requiera
habilitar rutas interzona debe ajustarse manualmente; la migración no abre rutas.

`GET /api/scenarios/{scenario_id}/status` devuelve estado global y por
contenedor. Cuando Docker aporta estadísticas, cada runtime incluye
`cpu_percent`, `memory_usage_bytes`, `memory_limit_bytes`, `pids`,
`resource_metrics_status` y `resource_metrics_sampled_at` (UTC). La API limita
las consultas de Docker Stats a una muestra cada tres segundos por contenedor.
`good` indica una muestra disponible, `unavailable` indica que Docker no
devolvió métricas, `not_running` que el runtime no estaba ejecutándose,
`missing` que falta el nodo del plan y `not_applicable` que el elemento
representa una red, no un contenedor. `unknown` puede aparecer en adaptadores
externos o datos previos que no proporcionen esos campos. La hora corresponde a
la toma de métricas, no al último sondeo del navegador; ausencia de muestra no
se convierte en cero.

La creación y actualización validan el esquema completo. HTTP 422 indica datos
no válidos; 404 puede indicar que el recurso no existe o que el usuario no tiene
acceso; 409 se usa para conflictos de estado en varias operaciones. Lee `detail`
como mensaje de diagnóstico, no como una autorización para saltarse validación.
El esquema también rechaza valores `external_port` duplicados dentro de un
escenario antes de guardarlo; el gestor Docker repite la comprobación en el
despliegue.

`GET /api/scenarios/{scenario_id}/services` devuelve los listeners industriales
derivados de cada adaptador, con nodo, hostname, IP de zona, protocolo,
transporte y puerto interno efectivo. Si el acceso externo está habilitado,
también incluye `host_bind_ip` y `host_port`. El inventario distingue Modbus
TCP (MBAP) del túnel TCP raw que transporta tramas RTU; no anuncia como servidor
un HMI que solo actúa como cliente. El runtime SCADA sí enumera su API HTTP
interna (scope `internal`); no es un endpoint industrial ni se publica en el
host por defecto. El endpoint describe configuración, no sustituye una sonda
de salud ni garantiza que el contenedor esté ejecutándose.

Antes de `POST /api/scenarios/{scenario_id}/deploy`, el servidor vuelve a
validar el escenario guardado. Si su esquema o topología no es válida responde
422 con `detail.message` y `detail.errors` (cada error puede incluir `path` y
`message`) sin fijar imágenes ni iniciar contenedores. El preflight de la
interfaz también muestra advertencias consultivas; no son errores de esquema y
no bloquean las llamadas directas de despliegue.

## Reglas de escrituras

Una escritura protocolaria comprueba rol, escenario, estado del runtime,
destino, punto seleccionado, dirección, tipo, permiso R/W, firewall y límites
configurados. La escritura se audita. No uses llamadas de API para eludir el
modelo de conexiones del escenario. La documentación específica de puntos está
en [Modbus](MODBUS.md), [HMI](hmi.md), [SCADA](SCADA.md) y [acceso OT](ACCESS.md).

## Sesiones y CSRF

En uso normal, inicia sesión mediante la UI/API y conserva la cookie HttpOnly.
Las mutaciones verifican el origen; no desactives el middleware en el servicio
compartido. El token de bootstrap solo es para crear la primera cuenta y no es
un token API permanente. Los límites completos están en
[PLATFORM_ACCESS.md](PLATFORM_ACCESS.md).

## Captura PCAP

La captura requiere un escenario desplegado y autorizado. `POST
/api/scenarios/{scenario_id}/captures` acepta exactamente uno de estos
alcances:

```json
{"node_id":"plc-1"}
{"link_source_node_id":"plc-1","link_target_node_id":"plc-2"}
{"zone_id":"control"}
```

Una captura `node_id` observa la interfaz del namespace de ese dispositivo; una
captura `link_*` exige dos dispositivos distintos, activos y en la misma zona
y red Docker, y aplica el filtro de la IP del destino; `zone_id` captura el
bridge interno de esa zona. La respuesta devuelve `capture_id` y `scope`
(`device`, `link` o `segment`). `GET
/api/scenarios/{scenario_id}/captures/{capture_id}/pcap` finaliza, exporta y
consume la captura. La misma exportación se puede incluir opcionalmente en el
bundle de evidencias; al incluirla también se consume. Límites de paquetes,
privilegios y compatibilidad Docker se detallan en
[Seguridad de contenedores](CONTAINER_SECURITY.md) y en la fila RF-34 de la
[matriz SRS](SRS_TRACEABILITY.md).

## WebSocket

El backend emite eventos del escenario y logs en vivo. El stream requiere
sesión/origen válidos según middleware. Eventos describen cambios observados,
no son un bus de control ni sustituyen consultas protocolarias. Si el browser
se desconecta, vuelve a consultar estado/diagnóstico para reconciliar lo último.
El stream de escenario emite `status` si cambia el estado del nodo, el estado o
salida del healthcheck, los errores runtime, las métricas o su calidad/hora de
muestra. Así un error nuevo de healthcheck no espera al heartbeat. Si nada cambia,
se envía un heartbeat periódico; el sondeo de Docker Stats sigue limitado a una
muestra cada tres segundos por contenedor.

## Cliente reproducible

Para pruebas funcionales del producto usa los scripts `integration/` y las
variables de configuración documentadas, en vez de fijar IDs de una base de
demo. `VALIDATION_LOCAL.md` enumera autenticación, despliegue y limpieza de cada
smoke. No guardes credenciales en ejemplos o logs.
