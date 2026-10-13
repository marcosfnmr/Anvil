# Auditoría operativa por escenario

La **auditoría** es un registro de acciones relevantes: quién hizo una
operación, cuándo y sobre qué objeto, además de cambios de estado seleccionados.
Sirve para reconstruir actividad; no es una captura completa del tráfico ni un
registro de cada lectura periódica. Los logs describen la salida de procesos,
una PCAP contiene paquetes de red y el histórico SCADA contiene muestras de
proceso: son fuentes distintas que pueden complementar la auditoría.

Anvil conserva eventos operativos en SQLite asociados al escenario. La vista **Auditoría** del panel de runtime pagina los registros más recientes y permite descargar un JSON Lines (`.jsonl`). La tabla registra UTC, ID monotónico local, tipo, actor y detalles limitados a 16 KiB. Las peticiones autenticadas se atribuyen a `user:<id>`; los trabajos de fondo sin una identidad de petición conservan el actor `system`. Cada evento incluye el hash del evento anterior y un SHA-256 calculado sobre sus campos canónicos.

Eventos implementados:

- `scenario.created`, `scenario.topology_updated`, `scenario.status_changed`, `scenario.paused`, `scenario.resumed` y `scenario.node_{start,stop,restart,pause,resume}`.
- `scenario.node_state_changed` cuando cambia el último estado observado de un nodo (estado Docker o salud del contenedor), incluida su ausencia/cierre y recuperación. El backend conserva el estado observado por escenario/nodo para suprimir duplicados entre sondeos HTTP y WebSocket; los detalles guardan errores Docker/healthcheck limitados a 500 caracteres.
- `operator.point_written` después de escrituras S7, Modbus TCP y HMI confirmadas por el runtime.
- `operator.field_control` después de cambios de estado/fallo/forzado en objetos de campo.
- `process.field_state_changed` cuando el runtime de campo observa un cambio de orden/estado/fallo en válvulas, bombas, motores, entradas digitales y alarmas; en objetos analógicos registra cambios de consigna binaria (cero/no cero) y el paso cerrado/abierto de válvulas proporcionales y marcha/parada de VFD. Incluye, si existe, la conexión de señal PLC/Modbus que apunta al puerto `command`, para guiar la investigación sin afirmar que esa conexión fue la causa única. La primera observación establece la línea base. No duplica muestras analógicas ni niveles/caudales continuos.
- `api.request_rejected` cuando la política de firewall o el mapa de E/S deniega una operación S7/Modbus/HMI. En intentos de escritura conserva, cuando están disponibles, el tipo de cliente (`source`), su `source_node_id`, el destino (`target_node_id`), la dirección, método, plantilla de ruta y código HTTP. Nunca guarda el valor solicitado, el cuerpo ni credenciales. En una consola directa, `node_id` y `target_node_id` identifican el mismo dispositivo.
- `network.packet_capture_started` y `network.packet_capture_exported` para las capturas PCAP de enlace disponibles.
- `protocol.read_{good,degraded,failed}` en la primera lectura y cuando cambia la calidad de una relación S7/Modbus consultada por API, HMI, snapshot SCADA o el worker SCADA; no registra cada sondeo ni los valores leídos.
- `protocol.modbus_operation` para escrituras y respuestas fallidas capturadas por los runtimes Modbus TCP y RTU y recogidas desde su cola SQLite durable. Guarda metadatos de protocolo y origen, nunca el valor del punto; conserva `unit_id`, `function_code`, operación, espacio y dirección numérica (o `null` si no se pudo determinar). Esto permite que ejercicios filtren una operación concreta con tipos exactos. El cursor de ID de cola hace idempotente la entrega. Los runtimes anteriores usan logs como fallback.
- `protocol.s7_operation` para escrituras y operaciones S7 fallidas capturadas por el runtime PLC y recogidas desde su cola SQLite durable. Guarda origen, área, DB/dirección por bytes y resultado, nunca los bytes del proceso. Las lecturas S7 correctas individuales se omiten; los runtimes anteriores usan logs como fallback.
- Cambios auditados de alarmas, ejercicios, rutas de red y operaciones descritas por sus módulos respectivos.
- `participant.terminal_opened` / `participant.terminal_closed` y sus equivalentes
  de estación de ingeniería registran el ciclo de sesión. Al cerrar, el evento
  añade contadores agregados de marcos, bytes, saltos de línea y duración; no
  guarda ni hashea el texto de entrada para evitar persistir contraseñas o tokens
  escritos en la shell. Estos contadores no permiten reconstruir qué comando se
  ejecutó.

La cuenta de plataforma tiene además un registro global separado para `account.bootstrap_completed`, `account.created`, `account.updated`, `account.password_changed` y `account.password_reset`. Conserva actor, usuario objetivo, rol/estado o cantidad de sesiones revocadas; nunca almacena las contraseñas ni sus hashes.

## API

- `GET /api/scenarios/{id}/audit-events?limit=100&before_id=123` devuelve página descendente; el cursor `next_before_id` se usa como `before_id` en la siguiente petición.
- `GET /api/scenarios/{id}/audit-events/export` descarga eventos JSONL en orden cronológico.
- `GET /api/scenarios/{id}/audit-events/integrity` valida la cadena y devuelve el primer ID inválido, si existe. No repara ni modifica eventos.
- `GET /api/admin/audit-events?limit=100&before_id=123` pagina las operaciones de cuenta; está restringido a admins.
- `GET /api/admin/audit-events/integrity` verifica la cadena global de eventos de cuenta y señala el primer ID inválido.

## Verificar auditoría de proceso en Docker

Este smoke construye la imagen de campo desde el checkout y crea un único
contenedor temporal sin red ni puertos publicados. Usa una SQLite temporal,
recoge una orden/estado de válvula y confirma que una lectura analógica cambia
sin generar eventos de proceso. El escenario, contenedor y archivo de base se
eliminan al terminar.

```bash
docker build -f plc/Dockerfile.field -t anvil/field:process-audit-local plc
.venv/bin/python integration/process_audit_smoke.py --image anvil/field:process-audit-local
```

Para comprobar la cola de protocolo de extremo a extremo, construye la imagen
Modbus del checkout y ejecuta el smoke. Este crea un contenedor y volumen con
nombres aleatorios, expone un puerto efímero solo en loopback, hace escrituras
Modbus reales, recrea el contenedor con el mismo volumen, lee los eventos sin
valores y confirma el lote. Solo borra los recursos que creó.

```bash
docker build -f plc/Dockerfile.modbus -t anvil/modbus-tcp:rf35-audit-local plc
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:rf35-audit-local \
  .venv/bin/python integration/protocol_audit_queue_smoke.py

docker build -f plc/Dockerfile -t anvil/plc-s7:rf35-audit-local plc
ANVIL_PLC_IMAGE=anvil/plc-s7:rf35-audit-local \
  .venv/bin/python integration/s7_protocol_audit_queue_smoke.py

docker build -f plc/Dockerfile.scada -t anvil/scada:rf35-rtu-audit-local plc
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:rf35-audit-local \
ANVIL_SCADA_IMAGE=anvil/scada:rf35-rtu-audit-local \
PYTHONPATH=backend .venv/bin/python integration/modbus_rtu_smoke.py
```

El endpoint `GET /api/scenarios/{id}/evidence-bundle.zip` empaqueta el manifiesto versionado, auditoría JSONL, hasta 1000 líneas recientes de logs por contenedor, CSV de todas las muestras SCADA persistidas y, si se proporciona `?capture_id=<uuid>`, una captura PCAP elegida. Está limitado a `admin` e `instructor`. El PCAP se detiene, exporta y elimina al incluirlo; sin `capture_id`, el ZIP no interactúa con capturas activas. El ZIP se transmite desde un buffer temporal con vuelco a disco para no retener el paquete completo en RAM. El generador limita el contenido descomprimido total a 1 GiB y responde HTTP 413 al excederlo; al seleccionar captura, esta ya se habrá consumido si el límite se supera durante el empaquetado.

La respuesta incluye `X-Anvil-Manifest-SHA256`, el SHA-256 exacto de `manifest.json`. La interfaz lo muestra tras descargar el ZIP para que pueda guardarse por separado. La comprobación CLI `integration/verify_evidence_bundle.py ZIP --expected-manifest-sha256 HASH` puede validar ese hash más adelante. Para que aporte autenticidad, conserva/recibe el hash por un canal de confianza separado; el header recibido junto al ZIP solo permite detectar cambios posteriores, no sustituye firma digital.

Las capturas pueden cubrir el enlace de un nodo seleccionado o el bridge interno de la zona. La captura de segmento requiere Docker Engine Linux con acceso al bridge; Docker Desktop/rootless no se han caracterizado. La descarga individual de auditoría, historial SCADA y PCAP sigue disponible.

## Retención y eliminación de escenarios

La retención es optativa y está desactivada por defecto. Configura `ANVIL_AUDIT_RETENTION_DAYS` entre `1` y `3650` para habilitar la poda diaria; `0`, un valor ausente o inválido la deja desactivada. Antes de podar, Anvil valida la cadena. Solo elimina el prefijo cronológico que ya superó el plazo y conserva los eventos desde el inicio del ejercicio activo más antiguo. Guarda el último hash eliminado en `scenario_audit_checkpoints` y añade un evento `audit.retention_applied`, de modo que la cadena retenida pueda verificarse desde ese ancla.

Al borrar un escenario detenido, Anvil conserva la auditoría y guarda un archivo con el nombre del escenario y la instantánea de propietario/membresías. El middleware permite consultar y verificar el historial archivado únicamente a admin o a usuarios que eran propietario/miembros; el resto de las rutas del escenario devuelven 404. La retención configurada también se aplica a estos historiales archivados.

Para verificar offline el inventario de un ZIP ya descargado, usa
`python3 integration/verify_evidence_bundle.py /ruta/al/anvil-evidence.zip`.
El resultado incluye el hash SHA-256 del manifiesto. Si ese hash se guardó por
un canal confiable separado, `--expected-manifest-sha256 <hash>` también detecta
que el manifiesto incluido fue sustituido. El hash separado no es una firma ni
prueba por sí solo quién generó el bundle; la exportación queda asociada al
escenario por sus identificadores y eventos incluidos.

## Límites de confianza

El checkout incluye inicio de sesión y autorización por rol/escenario (ver
[cuentas y permisos](PLATFORM_ACCESS.md)) y atribuye los eventos de escenario
generados durante una petición autenticada a su ID de usuario. Los cambios de
cuentas se guardan en una cadena global separada. No se registran lecturas exitosas
ni todos los errores de API. SQLite no cifra estos datos y el hash de la cadena no
constituye una firma ni un ancla externa: quien controle el host o la base puede
reescribir los eventos y recalcular los hashes. El checkpoint de retención por
escenario detecta alteraciones posteriores a su ancla, pero no prueba quién creó el
historial ni detecta que se sustituya toda la base. Un fallo de escritura en SQLite
después de una orden al PLC puede hacer que la API devuelva error aunque el
dispositivo ya haya aplicado la orden.

El detalle de un rechazo permite que un ejercicio diferencie, por ejemplo, una
escritura HMI bloqueada al punto `DB1.DBX0.0` de otros rechazos de la misma API.
Es evidencia de una denegación en la ruta HTTP de Anvil. No demuestra que un
cliente conectado directamente a TCP/102 o al puerto Modbus haya sido
autenticado, atribuido a una persona o bloqueado por una ACL de usuario del
protocolo; para esos flujos se necesitan controles de red y evidencia
protocolaria separados.

Los runtimes PLC S7 y Modbus TCP/RTU guardan metadatos de operaciones de escritura y
respuestas fallidas en una cola SQLite dentro de un volumen Docker propio por
dispositivo. La cola sobrevive a la recreación del contenedor y a una caída del
backend; su límite es 100.000 eventos o 32 MiB, con líneas de hasta 4 KiB. Un
proceso que excede el límite descarta los eventos más antiguos y aumenta un
contador persistente de pérdidas.

Cada dos segundos, el backend solicita lotes de hasta 1.000 eventos usando una
operación `exec` fija y confinada: solo acepta `pending <límite>` o `ack <cursor>`,
como UID 65534, en el directorio `/app`, sobre un contenedor S7/Modbus propiedad
del escenario y con el volumen exacto de cola. Para el acuse, el gateway exige
un token efímero coincidente entre el runtime y esa petición. No ofrece una
shell en PLC/Modbus. El backend confirma primero los eventos y el cursor de lote
en SQLite; solo después acusa el lote al runtime. Si se cae entre el commit y el
acuse, la repetición se deduplica por ID durable de cola. Si el runtime informa
que hubo descartes, Anvil incorpora `protocol.audit_queue_overflow` con el
incremento observado. El contador indica pérdidas, pero no recupera el contenido
descartado.

La cola es la fuente de auditoría para contenedores que ya llevan esa etiqueta.
Para runtimes S7/Modbus TCP antiguos sin cola, Anvil conserva el recolector de logs
Docker como compatibilidad: cursor por timestamp con solapamiento de un segundo,
idempotencia por huella y `json-file` limitado a tres ficheros de 10 MB. Los logs
antiguos pueden rotar antes de recogerse; no son almacenamiento durable. Las
versiones RTU anteriores a esta cola no emitían eventos semánticos de operación.
Las operaciones
API start/stop/restart/recreate hacen vaciados best effort antes y después cuando
el runtime está disponible; no bloquean el ciclo de vida. Los eventos no se
duplican entre cola y logs para un mismo runtime.

La auditoría protocolaria incluye origen y metadatos de área/dirección S7 o
función, Unit ID, espacio, dirección y cantidad Modbus. El origen RTU se marca
`rtu-serial`; nunca guarda los valores. No registra lecturas correctas
individuales. El inspector S7 y algunos
logs Docker conservan una línea de diagnóstico que puede incluir bytes; esa
línea no se copia a la auditoría durable. Protege los logs del host según el
modelo de seguridad del despliegue. Las capturas PCAP son fuentes separadas con
su propia retención. La coordinación del cursor se ha probado con el worker
único documentado de Anvil; no se afirma deduplicación entre réplicas concurrentes.
Las transiciones de proceso se sondean cada dos segundos y pueden ocurrir varias
entre sondeos; las telemetrías continuas siguen en el histórico SCADA, no en
auditoría.
