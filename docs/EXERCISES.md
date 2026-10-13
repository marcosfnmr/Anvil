# Ejercicios OT

Un ejercicio es una secuencia de eventos y objetivos que se ejecuta sobre un
escenario aislado para practicar y evaluar una operación. Puede aplicar cambios
de estado y comprobar resultados observables, por ejemplo que una alarma se
active o una salida tome cierto valor. No equivale a ejecutar un ataque real ni
a desplegar código arbitrario: las acciones se limitan a los tipos y destinos
que el motor admite dentro del escenario.

El escenario define la planta simulada; el ejercicio define cuándo ocurren los
eventos y qué resultado se observa. La cronología puede pausarse y reanudarse;
los eventos y resultados relevantes quedan asociados al escenario para su
revisión en auditoría/evidencias.

Anvil permite guardar una definición de ejercicio por escenario, desplegar el
escenario y ejecutar una cronología con objetivos verificables. El motor procesa
eventos vencidos cada 250 ms en el proceso del backend. Un reinicio del backend
marca las ejecuciones activas como `interrupted`; no reanuda automáticamente
acciones de una cronología anterior.
El scheduler es local al proceso: ejecuta Anvil con un único worker backend. No se
admite aún una instalación con varios workers/réplicas.

## Desde la interfaz

Abre la pestaña **Ejercicios** del panel de runtime. Pega una definición JSON,
guárdala y despliega el escenario. Después inicia la ejecución y observa el estado,
los eventos aplicados y los objetivos alcanzados. Solo puede ejecutarse un ejercicio
a la vez por escenario. Se puede cancelar una ejecución activa.

Pausar un escenario suspende también el estado del ejercicio, el reloj de la
cronología y la duración continua acumulada de objetivos de proceso. Reanudar
continúa desde el siguiente evento pendiente sin repetir los ya ejecutados. El
run aparece como `paused` en la interfaz y puede cancelarse durante la pausa. La
parada o el borrado del escenario no se consideran una pausa.

## Formato y API

```json
{
  "name": "Abrir válvula por escritura S7",
  "description": "Demostración controlada en el escenario de laboratorio",
  "events": [
    {
      "event_id": "open-inlet",
      "at_seconds": 2,
      "kind": "write_point",
      "node_id": "plc-1",
      "address": "DB1.DBX0.0",
      "value": true
    }
  ],
  "objectives": [
    {
      "objective_id": "valve-open",
      "description": "La salida de la válvula está activa",
      "kind": "point_value",
      "node_id": "plc-1",
      "address": "DB1.DBX0.0",
      "operator": "eq",
      "value": true
    },
    {
      "objective_id": "write-audited",
      "description": "La escritura quedó registrada",
      "kind": "audit_event",
      "event_type": "operator.point_written"
    }
  ]
}
```

También se pueden evaluar variables físicas de campo mediante `kind: "field_value"`
con `object_id`, `property`, `operator` y `value`; admite nivel/volumen/presión de
tanque, estado/caudal de actuadores, apertura proporcional y medición analógica.
`field_value` y `point_value` (S7/Modbus) admiten `duration_seconds` (0–86 400,
predeterminado 0), que exige que la condición se mantenga verdadera durante ese
tiempo. El scheduler registra el comienzo y reinicia el conteo si una muestra deja
de cumplirla; las señales PLC se vuelven a leer usando el protocolo del nodo. La
precisión depende del sondeo nominal de 250 ms, latencia de protocolo y carga del host.

La propiedad booleana `fault` refleja el fallo inyectado en el runtime de campo;
no es una alarma detectada automáticamente por el PLC ni un diagnóstico de
hardware. Permite comprobar una perturbación mantenida en válvulas, válvulas
proporcionales, bombas, motores, variadores y sensores analógicos/digitales. Por
ejemplo, este objetivo solo se supera cuando el fallo
del sensor sigue activo cinco segundos continuos; si se libera antes, el
temporizador vuelve a empezar:

```json
{
  "objective_id": "sensor-fault-held",
  "description": "El fallo del sensor permanece activo cinco segundos",
  "kind": "field_value",
  "object_id": "level-sensor",
  "property": "fault",
  "operator": "eq",
  "value": true,
  "duration_seconds": 5
}
```

Para analizar la evolución de una variable física, `field_trend` compara el
primer y el último valor numérico válido dentro de una ventana de 2–300 segundos.
Configura `object_id`, `property`, `direction` (`rising`, `falling` o `stable`),
`window_seconds` y `minimum_delta`. Por ejemplo, `rising` con `minimum_delta: 10`
requiere que la variable termine al menos 10 unidades por encima del primer
muestreo. `stable` requiere que el cambio absoluto no supere ese umbral. El
muestreador conserva una muestra nominal por segundo y solo el historial reciente;
exige muestras que cubran casi toda la ventana. Si no hay cobertura suficiente,
el resultado no se supera: no se interpolan lecturas. Al pausar el escenario, el
motor desplaza las marcas temporales para que la pausa no consuma la ventana.
La medición procede del modelo de campo de Anvil y no garantiza adquisición en
tiempo real.

```json
{
  "objective_id": "tank-rises",
  "description": "El volumen aumenta durante la ventana",
  "kind": "field_trend",
  "object_id": "tank-1",
  "property": "volume_liters",
  "direction": "rising",
  "window_seconds": 20,
  "minimum_delta": 25
}
```

Para comprobar varias condiciones del proceso en orden, usa
`field_sequence`. Cada paso selecciona un objeto de campo, propiedad, operador y
valor; se compara un paso por muestra y el siguiente solo puede cumplirse en una
muestra posterior. Admite entre 2 y 16 condiciones BOOL o numéricas de objetos
compatibles.
La propiedad BOOL `fault` también puede formar parte de una secuencia, por
ejemplo fallo activo seguido de fallo liberado.
`max_gap_seconds` es opcional: si pasa ese tiempo entre dos pasos, la secuencia
se reinicia y vuelve a esperar el primero. El progreso y las horas de coincidencia
se guardan con el run; al pausar, las marcas se desplazan para excluir la pausa.
El muestreo es nominalmente cada 250 ms, sujeto al scheduler y al runtime de
campo; no es una condición evaluada dentro del programa de un PLC.

```json
{
  "objective_id": "high-then-valve-closed",
  "description": "El tanque supera 600 L y luego se cierra la válvula",
  "kind": "field_sequence",
  "steps": [
    {"object_id": "tank-1", "property": "volume_liters", "operator": "gte", "value": 600},
    {"object_id": "valve-1", "property": "state", "operator": "eq", "value": false}
  ],
  "max_gap_seconds": 120
}
```

En puntos S7 que exponen diagnóstico de `remote_input`, el motor solo acredita
lecturas con `communication.quality: "good"`. Lecturas `waiting`, `stale` o con
calidad desconocida no satisfacen el objetivo y reinician su duración continua,
aunque el último valor retenido coincida con el esperado. Los puntos sin campo de
diagnóstico de comunicación conservan el comportamiento anterior.

Los objetivos admiten `weight` (1–100, por defecto 1). La definición puede usar
`success_rule`: `all` (por defecto, conserva el comportamiento anterior), `any` o
`score`. En modo `score`, `passing_score` fija el umbral ponderado entre 0 y 100;
el resultado expone `score` y `passed` y queda persistido con el run. Los modos
`all` y `any` también devuelven la puntuación para mostrar cuánto se cumplió.

Los eventos deben estar ordenados por `at_seconds` (0–86.400) y su máximo es 64.
`write_point` acepta únicamente salidas S7 BOOL/REAL configuradas como escribibles,
coils Modbus o holding registers. Se aplican el mapa de E/S, rangos, configuración
del protocolo y firewall existente. `node_lifecycle` admite `start`, `stop` y
`restart` sobre un nodo del mismo escenario. Cada evento ejecutado y cada escritura
quedan en auditoría. `field_control` programa perturbaciones del runtime de proceso:

### Escribir mediante el protocolo desde una estación participante

`protocol_write` genera una petición de cliente S7 o Modbus TCP **dentro del
contenedor de una estación participante**. No llama a la ruta HTTP de escritura
operativa de Anvil: la estación abre el protocolo del equipo objetivo y envía
una escritura nativa. El caso sirve para practicar una orden protocolaria
originada desde una máquina OT del escenario y comprobar su efecto y auditoría.

La estación debe ser `attacker-station` y tener habilitado
`participant_access_enabled`. El destino debe ser un PLC S7 o servidor Modbus
TCP del mismo escenario. En una misma zona se usa la dirección interna del
destino; entre zonas se requiere una ruta TCP habilitada desde esa estación al
destino, con el puerto del servicio S7 (102) o el puerto Modbus configurado.
Solo admite salidas S7 BOOL/REAL y coils/holding registers Modbus; entradas y
registros de solo lectura se rechazan al validar el ejercicio. El evento queda
registrado como `attack.protocol_write` con estación, objetivo, dirección,
protocolo, valor y ruta cuando exista.

```json
{
  "event_id": "participant-opens-valve",
  "at_seconds": 5,
  "kind": "protocol_write",
  "source_node_id": "station-1",
  "target_node_id": "plc-1",
  "route_id": "station-to-plc-s7",
  "address": "DB1.DBX0.0",
  "value": true
}
```

El evento requiere que el ejercicio lo defina un usuario autorizado. Al
ejecutarse, la estación participa en el tráfico del escenario con los permisos
de red y protocolo de ese runtime; los roles/API de Anvil no autentican S7 ni
Modbus. Esta función **no es MITM**: no intercepta ni modifica las peticiones
de otro cliente, y no altera paquetes en tránsito. Para rutas entre zonas,
el gateway restringe la conexión a la estación declarada y a un único puerto.

Las diferencias respecto a `write_point` son observables: `write_point` usa la
operación gestionada de Anvil; `protocol_write` ejecuta el cliente Snap7 o
PyModbus en la estación y deja evidencia de una petición protocolaria desde esa
IP. Ninguno de los dos eventos ejecuta código arbitrario.

```json
{
  "event_id": "inject-pump-fault",
  "at_seconds": 10,
  "kind": "field_control",
  "object_id": "pump-1",
  "fault": true
}
```

El mismo evento acepta `demand_flow_lpm` y `leak_flow_lpm` en un tanque, un
`override` numérico en un sensor analógico o un `override` booleano en una entrada
digital/alarma. La tasa de fuga modifica el balance de volumen en ejecución; usa
`leak_flow_lpm: 0` para cerrar la fuga. La demanda y la fuga no se restauran solas:
programa explícitamente sus valores anteriores o cero. Para sensores y fallos,
programa otro evento con `fault: false` o `override: null`. El validador comprueba
que cada control corresponde al tipo del objeto y respeta los límites de sensor.
Las acciones generan `operator.field_control` con origen `exercise` y no ejecutan
código arbitrario. Una caída/reinicio del backend marca el run `interrupted` y no
repite eventos ya aplicados.

Los objetivos admiten `point_value` con operadores `eq`, `ne`,
`gt`, `gte`, `lt` y `lte`, o `audit_event` para comprobar un evento desde el
inicio del run. `expectation: "occurred"` (predeterminado, compatible con
definiciones anteriores) exige que ocurra; `expectation: "absent"` exige que no
aparezca hasta que termina la cronología. El run termina como `completed` si se satisface la regla
configurada; si no, termina como `failed`. Las definiciones existentes mantienen
la regla `all`, y la migración de base de datos añade las columnas del resultado
sin descartar runs anteriores.

Un objetivo `audit_event` puede acotar la coincidencia con `details_match`. Cada
campo indicado debe existir en los detalles del evento y coincidir con el valor
configurado. Así, una escritura sobre otro PLC o punto no cuenta como la acción
que el ejercicio pretende observar. Se admiten estos campos: `source`,
`node_id`, `address`, `route_id`, `object_id`, `action`, `value`, `exercise_id`,
`event_id`, `fault`, `override`, `demand_flow_lpm`, `status`, `reason`,
`protocol`, `target_node_id` y `source_node_id`; se pueden combinar hasta ocho
condiciones escalares. Cadenas y booleanos deben coincidir exactamente; los
números se comparan por valor. Las definiciones antiguas sin `details_match`
siguen comprobando solo el tipo de evento. El filtro también se aplica a
`expectation: "absent"` y `fail_fast`, por lo que un evento ajeno no termina el
run. La evaluación usa registros de auditoría de Anvil; no inspecciona ni
atribuye paquetes de red.

Además de las operaciones gestionadas por la API, los runtimes S7 y Modbus
registran metadatos de operaciones que llegan directamente por el protocolo.
Puedes crear objetivos para `protocol.s7_operation` y
`protocol.modbus_operation` y filtrar las escrituras por `operation`, `result`
y los datos de dirección. En S7 están disponibles `area`, `db`, `byte_offset`,
`byte_count` y `result_code`; en Modbus, `unit_id`, `function_code`, `space`,
`address` y `count`. Ejemplo de detección de una escritura Modbus FC06 sobre un
holding register concreto:

```json
{
  "objective_id": "detect-setpoint-write",
  "description": "Detectar la escritura observada en la consigna",
  "kind": "audit_event",
  "event_type": "protocol.modbus_operation",
  "details_match": {
    "node_id": "modbus-1",
    "function_code": 6,
    "operation": "write_holding_register",
    "space": "holding_register",
    "address": 4,
    "result": "ok"
  }
}
```

La detección significa que el runtime observó esa operación protocolaria; por
sí sola no determina si estaba autorizada, quién controla la IP de origen ni
si el proceso alcanzó el estado deseado. Para evaluar el efecto combina el
evento con un objetivo `point_value` o `field_value`. Las trazas no incluyen el
valor escrito; utiliza una condición de estado para comprobar el resultado sin
añadir consignas a la auditoría de protocolo.

### Detectar escrituras fuera de una política de pares

Para evaluar una defensa frente a un origen que no esté en una lista permitida,
usa `kind: "protocol_write_policy"`. El objetivo selecciona el PLC S7 o el
servidor Modbus TCP vigilado, protocolo y lista de IPv4 de pares permitidas. El
motor contrasta esa lista con la dirección `source` observada por el servidor en
cada escritura protocolaria capturada durante el ejercicio. Una lista vacía
significa que ninguna IP está permitida. La coincidencia produce el evento
durable `security.protocol_write_policy_violation`; el evento incluye protocolo,
destino, operación, dirección o área cuando se conocen, resultado y referencia
a la traza original, pero no incluye el valor escrito.

```json
{
  "objective_id": "detect-unapproved-plc-write",
  "description": "Detectar escrituras S7 desde pares fuera de política",
  "kind": "protocol_write_policy",
  "target_node_id": "plc-1",
  "protocol": "s7",
  "allowed_source_ips": ["172.30.10.20"],
  "expectation": "detected"
}
```

`expectation: "detected"` exige al menos una coincidencia de escritura cuyo
origen no esté permitido; `"none"` exige que no haya ninguna. Las IP se comparan
exactamente, no se aceptan subredes ni nombres de nodo. En una ruta entre zonas,
el PLC destino puede ver la IP del gateway y no la del cliente original; en ese
caso la política debe reflejar el peer observado. La dirección IP es un dato de
red, no una identidad autenticada: NAT, proxies o suplantación pueden cambiar
su significado. Esta función detecta y deja evidencia para el ejercicio, pero
no bloquea la conexión ni sustituye ACL, firewall o autenticación del
dispositivo. El colector recoge las trazas del runtime periódicamente y fuerza
una recogida final al terminar la cronología; por eso la detección no tiene
latencia garantizada.

```json
{
  "objective_id": "detect-specific-write",
  "description": "Detectar la escritura de la consigna de la válvula",
  "kind": "audit_event",
  "event_type": "operator.point_written",
  "details_match": {
    "node_id": "plc-1",
    "address": "holding_register:2",
    "source": "exercise"
  }
}
```

El editor guiado recibe el filtro como objeto JSON y valida su sintaxis antes de
guardar. El API vuelve a validar campos y tipos de forma independiente.

`audit_sequence` verifica que una lista de entre 2 y 16 tipos de evento auditado
ocurra en el orden declarado desde el inicio del ejercicio. Se permiten otros
eventos entre pasos. `max_gap_seconds`, si se define (1 ms–24 h), limita el
intervalo entre pasos consecutivos; al excederlo, la búsqueda vuelve al primer
paso. El editor guiado ofrece un campo por línea y muestra la secuencia encontrada
en el resultado. La evaluación usa la cadena de auditoría SQLite y no inspecciona
paquetes ni valores de proceso entre lecturas.

```json
{
  "objective_id": "detect-and-restore",
  "description": "La defensa detectó la interrupción y confirmó la restauración",
  "kind": "audit_sequence",
  "event_types": [
    "scenario.network_route_interrupted",
    "scenario.network_route_restored"
  ],
  "max_gap_seconds": 120
}
```

Para exigir que una acción prohibida no aparezca durante el ejercicio, usa
`audit_event` con `expectation: "absent"`; el valor predeterminado
`"occurred"` mantiene la semántica de las definiciones anteriores:

```json
{
  "objective_id": "no-denial",
  "description": "No se rechazaron operaciones legítimas",
  "kind": "audit_event",
  "event_type": "api.request_rejected",
  "expectation": "absent"
}
```

Los rechazos de escrituras gestionadas por la API también pueden filtrarse por
cliente y punto. Por ejemplo, este objetivo solo reconoce la denegación de una
escritura enviada desde `hmi-1` al punto S7 indicado; no coincide con un rechazo
de otra HMI o dirección:

```json
{
  "objective_id": "hmi-write-denied",
  "description": "La defensa rechazó la escritura HMI al mando de válvula",
  "kind": "audit_event",
  "event_type": "api.request_rejected",
  "details_match": {
    "source": "hmi",
    "source_node_id": "hmi-1",
    "target_node_id": "plc-1",
    "address": "DB1.DBX0.0"
  },
  "expectation": "occurred"
}
```

La API guarda esos identificadores al rechazar escrituras HMI, SCADA y consolas
S7/Modbus, pero nunca el valor solicitado. Este objetivo demuestra un rechazo
en una ruta HTTP de Anvil. No atribuye ni bloquea por identidad una conexión
directa de un cliente a TCP/102 o Modbus; consulta [auditoría](AUDIT.md) para
distinguir esos registros de la evidencia protocolaria.

También se puede detener la ejecución al detectar el evento prohibido con
`fail_fast: true`. El scheduler comprueba esta condición cada 250 ms nominales;
la detección real puede demorarse más si el proceso está ocupado. Las demás
condiciones se marcan `not_evaluated` porque el run termina antes de llegar a su
fin normal, y el resultado se fuerza a fallido con score 0.

```json
{
  "objective_id": "stop-on-denial",
  "description": "Detener el ejercicio ante una solicitud rechazada",
  "kind": "audit_event",
  "event_type": "api.request_rejected",
  "expectation": "absent",
  "fail_fast": true
}
```

API:

- `POST /api/scenarios/{scenario_id}/exercises` guarda la definición.
- `GET /api/scenarios/{scenario_id}/exercises` lista las ejecuciones guardadas.
- `POST /api/scenarios/{scenario_id}/exercises/{exercise_id}/start` inicia un run.
- `GET /api/scenarios/{scenario_id}/exercises/{exercise_id}` consulta estado y resultados.
- `POST /api/scenarios/{scenario_id}/exercises/{exercise_id}/cancel` cancela un run activo.

La prueba Docker de extremo a extremo usa una imagen S7 local:

```sh
PYTHONPATH=backend ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
  ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev \
  ANVIL_SCADA_IMAGE=anvil/scada:dev \
  .venv/bin/python -u integration/exercise_smoke.py
```

CI también construye la imagen S7 y ejecuta este smoke contra los contenedores
S7, SCADA y de campo, con objetivos de punto estables durante 2 segundos y del
proceso físico. También inyecta y retira una avería de bomba, interrumpe y
restaura una ruta S7 entre zonas desde la cronología, y comprueba mediante
sondeos protocolarios que la comunicación falla y se recupera. Una prueba
unitaria adicional cubre la duración de una coil Modbus.

Los ejercicios son herramientas de laboratorio, no un motor de ataque general. No
se ejecuta código arbitrario ni se crean paquetes de red. El evento API
`modbus_interception` programa sustituciones FC03/FC04 de respuestas de lectura
y FC06/FC16 de peticiones de escritura en una ruta TCP interzona hacia un servidor
Modbus TCP. En FC06/FC16 el servidor almacena el valor sustituido; FC16 puede
cambiar direcciones seleccionadas de una petición múltiple y conserva los demás
registros. Una acción `set` requiere un `clear` posterior; cancelar o
terminar el run también intenta retirar las reglas activas. Los puntos, Unit ID,
funciones y rangos se validan contra el escenario, y activación/limpieza se
registran en auditoría durable. Cada mensaje modificado genera evidencia en
una cola SQLite acotada dentro del volumen del gateway; el colector la agrega a
la cadena de auditoría como security.modbus_interception_match, con ruta,
regla, ejercicio, evento, Unit ID, función, dirección request/response y punto, sin guardar valores ni
tramas. Un objetivo audit_event puede filtrar por exercise_id, event_id,
route_id o rule_id. integration/modbus_interception_smoke.py lee realmente el
valor modificado desde un cliente Docker y comprueba la evidencia durable.
integration/exercise_builder_ui.cjs verifica edición y programación desde Brave.
integration/exercise_smoke.py comprueba en Docker estos flujos: el scheduler
activa y limpia la regla, la estación participante ve una respuesta FC03
alterada y envía escrituras FC06 y FC16 cuyos valores cambian en el servidor;
la prueba FC16 modifica dos de tres registros y conserva intacto el tercero. El
colector persiste cada coincidencia de registro por separado antes de evaluar
sus objetivos. Al cerrar la ventana, la regla se
retira, pero los valores ya almacenados en el servidor permanecen.
Cronologías distribuidas y una vista de instructor con varios participantes
siguen pendientes. La API existente aplica autenticación,
permisos de escenario y restricción de creación/inicio a los roles autorizados;
esto no ofrece una vista multiusuario en tiempo real.

La intercepción se limita a FC03/FC04 de lectura y FC06/FC16 de escritura de holding
register Modbus TCP por una ruta explícita; el inicio y fin de la ventana se
declaran con eventos `set` y `clear` en la cronología. Consulta
[intercepción protocolaria](PROTOCOL_INTERCEPTION_DESIGN.md)
para el estado, formato API y criterios pendientes.
