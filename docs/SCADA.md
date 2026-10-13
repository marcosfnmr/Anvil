# SCADA de adquisición

## Qué significa SCADA

SCADA es la capa de supervisión y adquisición que recoge datos de controladores,
los conserva para consultar tendencias y aplica alarmas. Normalmente trabaja
por encima del ciclo rápido de cada PLC: el PLC debe poder mantener su control
local aunque la estación SCADA o la red no respondan. Un sistema SCADA puede
incluir varios servidores, redundancia, historiadores y estaciones de
operación; la sigla describe una familia de funciones, no un protocolo único.

En Anvil, el nodo `scada` es un cliente de software independiente. Dentro de su
contenedor sondea puntos S7 y Modbus seleccionados, marca cada observación con
hora y calidad, mantiene un buffer temporal y el backend persiste muestras y
alarmas en SQLite. La pantalla web consulta el histórico y permite exportarlo.
Esto reproduce un flujo acotado de adquisición, no un producto SCADA completo
ni una instalación de alta disponibilidad.

### Conceptos que aparecen en la ficha SCADA

- **Fuente/destino:** el PLC S7 o dispositivo Modbus que mantiene los datos.
- **Tag/punto:** la dirección concreta consultada en ese equipo; una fuente
  puede tener cientos de puntos y SCADA solo sondea los que se configuran.
- **Sondeo:** cada lectura la inicia SCADA como cliente. Su intervalo es el
  ritmo nominal de adquisición, no el scan del PLC ni una garantía de tiempo
  real.
- **Calidad y timestamp:** indican si la petición produjo una muestra válida y
  cuándo se observó. Una muestra obsoleta no equivale a una lectura actual.
- **Buffer e histórico:** el runtime conserva temporalmente muestras y el
  backend las persiste para tendencias y exportación. Si se detiene la
  adquisición, no aparecen muestras nuevas.
- **Alarma:** una condición evaluada sobre los puntos seleccionados; el
  reconocimiento registra que un operador la vio, pero no elimina su causa.

En una planta, SCADA puede tener redundancia, historiador dedicado, interfaces
de operador y protocolos adicionales. El nodo Anvil demuestra el recorrido de
adquisición, almacenamiento y alarma descrito aquí, con los límites indicados
en la matriz de [fidelidad](DEVICE_FIDELITY.md).

La tendencia del inspector usa la media de cada intervalo agregado que contiene
muestras `good`. Los intervalos con solo lecturas defectuosas no se dibujan y
los huecos temporales interrumpen la línea; no se interpolan ni se conectan
visualmente como si hubiera una medición continua. El eje horizontal representa
el tiempo del rango consultado, no el índice de la muestra. La cifra de
intervalos buenos y el aviso de huecos permiten distinguir ausencia de lectura
de una medida estable. La frecuencia de refresco de la pantalla tampoco es la
frecuencia física de scan del PLC: son relojes de capas distintas.

### De la adquisición al histórico

El cliente inicia una petición al PLC o dispositivo Modbus; el servidor
responde con el punto pedido. SCADA no recibe automáticamente todos los tags
por estar conectado a la misma red. En Anvil hay que configurar el destino,
la dirección, el periodo y, cuando el destino está en otra zona, una ruta
permitida. El runtime conserva muestras recientes y el backend las incorpora a
SQLite. Si el escenario o el runtime SCADA se detienen, no hay adquisición
nueva, aunque el histórico ya persistido siga disponible.

#### Buffer durable y recuperación tras una interrupción

El runtime mantiene dos buffers distintos. `history_capacity` conserva una
ventana rápida en memoria para lectura interactiva (1–10.000 muestras). Además,
`durable_history_capacity` y `durable_history_max_bytes` limitan la cola SQLite
de muestras todavía no confirmadas por el backend: por defecto 100.000
muestras/128 MiB de datos serializados, con rangos configurables de 1.000–100.000
y 16–512 MiB. El presupuesto cuenta el contenido JSON de las muestras; SQLite
añade páginas, índices y WAL, así que no es un límite exacto de bytes del
volumen. La base vive en un volumen Docker exclusivo del nodo SCADA, con nombre y etiquetas
propiedad del escenario. Se conserva cuando el contenedor se detiene o recrea;
se elimina al borrar el escenario. El gateway Docker solo permite montar ese
volumen en el runtime SCADA de ese mismo escenario.

La cola durable requiere desplegar el runtime SCADA construido con esta versión.
Un escenario activo que conserve una imagen antigua fijada seguirá usando su
buffer legado en memoria hasta que se actualice y recree ese runtime. La
recogida del backend mantiene compatibilidad de lectura con esa API antigua,
pero no puede recuperar muestras que ya se hayan perdido en ella.

El backend solicita la cola por lotes de hasta 10.000 muestras, inserta cada
punto en SQLite con clave idempotente y solo después confirma el cursor al
runtime. Esa confirmación exige un token efímero conocido por el backend y el
contenedor SCADA; el listener puede estar accesible por una ruta TCP declarada,
pero los demás clientes no pueden vaciar la cola. Si el backend cae entre
guardar y confirmar, el lote puede repetirse;
la restricción única evita duplicarlo. Por eso una parada del backend o una
recreación del runtime no pierde las muestras que aún quepan en la cola durable.
La adquisición sí se detiene si se detiene SCADA o se pierde la conexión al
dispositivo fuente.

La cola está acotada por número de muestras y tamaño serializado. Si se llena,
elimina las muestras pendientes más antiguas para admitir las nuevas e incrementa
`dropped_samples`; el snapshot vivo lo expone y el inspector muestra un aviso.
Una cola llena sigue implicando una brecha histórica, y debe dimensionarse según
el número de tags, intervalo de sondeo y duración de interrupción que se quiera
tolerar. Borrar el escenario elimina también las muestras que aún no se hayan
confirmado en el SQLite del backend.

| Concepto SCADA | Significado | Representación en Anvil |
| --- | --- | --- |
| Fuente | PLC/servidor que publica los datos | Nodo S7 o Modbus seleccionado |
| Tag | Medida, estado o consigna que se consulta | Protocolo, dirección y tipo del punto |
| Sondeo | Solicitud periódica del cliente | Periodo de adquisición configurado |
| Calidad | Validez/vigencia de la lectura | `good`, `waiting`, obsoleta o error según la operación |
| Buffer | Memoria temporal reciente | Buffer acotado en el runtime SCADA |
| Histórico | Observaciones consultables en el tiempo | Persistencia SQLite del backend y exportación CSV |
| Alarma | Condición que requiere atención | Reglas compatibles por umbral o estado, con reconocimiento |

La HMI y SCADA pueden compartir datos, pero tienen papeles diferentes en el
modelo Anvil: la HMI presenta operación y sinóptico; SCADA adquiere varios
destinos y conserva muestras. La [guía HMI](hmi.md) describe sus mandos; el
[modelo de proceso](process-model.md) explica de dónde proceden las medidas.

**SCADA** significa Supervisory Control and Data Acquisition: supervisión y
adquisición de datos. En una instalación suele reunir lecturas de varios PLC,
históricos, tendencias y alarmas; puede permitir órdenes supervisadas según
su diseño y permisos. No reemplaza necesariamente la lógica rápida del PLC:
el control local debe tener un comportamiento definido si la supervisión o la
red no está disponible.

En Anvil, SCADA es un cliente protocolario en contenedor separado. Sondea
explícitamente puntos S7/Modbus seleccionados, asigna timestamp y calidad,
conserva muestras y evalúa alarmas configuradas. No descubre equipos ni lee
todos los tags por estar en la misma Ethernet. La HMI de Anvil modela la vista
orientada al operador; el nodo SCADA modela el colector multi-equipo e
histórico. En productos reales ambas funciones pueden convivir.

### Qué se configura y cómo se conecta

Para cada origen se seleccionan dispositivo, protocolo y dirección del punto;
además se configuran el intervalo de sondeo, buffer y reglas compatibles. El
SCADA inicia solicitudes como cliente hacia los servidores S7/Modbus. La red o
ruta debe permitir llegar al servicio, y seleccionar una zona o dibujar
Ethernet no basta para incluir un punto en la adquisición. El flujo es
servidor de protocolo → cliente SCADA → buffer/runtime → persistencia backend →
vista de histórico. Cada etapa tiene estado y límites propios documentados
abajo; no equivale a un historiador industrial de alta disponibilidad.

En términos prácticos, el **destino** contesta quién tiene el mapa (PLC S7 o
servidor Modbus); el **punto** contesta qué dirección debe consultar el
colector; el **periodo de sondeo** indica cada cuánto iniciar peticiones; el
**buffer** limita cuántas muestras recientes guarda el runtime antes de que el
backend las recoja; y el **histórico** es la persistencia SQLite consultable
después. Una zona compartida resuelve la conectividad de red, pero no configura
destinos, direcciones ni frecuencia.

## Componentes y flujo de datos

Un sistema SCADA suele incluir clientes de comunicación, configuración de tags,
adquisición, almacenamiento histórico, alarmas y pantallas. Anvil modela una
parte concreta de ese flujo: el runtime SCADA es un cliente S7/Modbus que sondea
los puntos elegidos; adjunta instante y calidad; mantiene un buffer acotado; y
el backend persiste muestras y estados de alarma en SQLite. La UI consulta ese
histórico para mostrar tabla/tendencia y permite exportar CSV. El operador puede
reconocer alarmas y, cuando el mapa y rol lo permiten, emitir escrituras
seleccionadas.

La persistencia del backend no significa adquisición continua durante una
parada del escenario: sin runtime no llegan muestras nuevas. Tampoco es un
historiador HA, un servidor OPC UA, un motor de reportes arbitrarios ni una
plataforma de alarmas certificada. El tamaño de buffer, el periodo y el límite
de retención son configurables; lee esta página y [fidelidad](DEVICE_FIDELITY.md)
para las cifras y límites exactos.

Anvil incluye un runtime SCADA en contenedor independiente. El colector sondea
por conexiones nativas S7 y Modbus TCP a PLCs/dispositivos seleccionados en la
misma zona Purdue o mediante una ruta TCP explícita a otra zona. Cada ciclo conserva
una muestra con timestamp y calidad. El runtime mantiene un buffer rápido y acotado;
el backend recoge cada cinco segundos hasta el tamaño completo del buffer configurado
(1–10.000 muestras) y las persiste en SQLite, por lo que el historial sigue disponible
tras recrear el contenedor SCADA o detener el escenario. Las lecturas repetidas se
deduplican por instante, SCADA, destino y dirección. Si el backend queda sin servicio
más tiempo del que cubre el buffer SCADA, las muestras que roten fuera de él no se
pueden recuperar. La retención se limita a 1.000.000 de observaciones por escenario y se
eliminan primero las más antiguas. La ruta de historial puede devolver datos durables
aunque Docker no esté disponible; `live` indica si se obtuvo además el buffer actual.

## Configuración

Construir la imagen junto con los demás runtimes:

```bash
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
```

Si Docker Hub no está accesible pero ya tienes la imagen Modbus local, puedes
reutilizarla como base (contiene Python y PyModbus):

```bash
docker build -t anvil/modbus-tcp:dev -f plc/Dockerfile.modbus ./plc
docker build --build-arg SCADA_BASE_IMAGE=anvil/modbus-tcp:dev \
  -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
```

La prueba de integración despliega un PLC S7, un servidor Modbus y el colector en
una red Docker aislada y valida valores, histórico y reconocimiento de alarmas:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev \
ANVIL_SCADA_IMAGE=anvil/scada:dev \
PYTHONPATH=backend .venv/bin/python integration/scada_smoke.py
```

Para verificar también las escrituras autorizadas desde la ruta API SCADA, su
lectura de retorno en los protocolos nativos, el rechazo de puntos no seleccionados
y de solo lectura, y la auditoría, ejecuta esta prueba con Docker y las imágenes
construidas. Usa una base SQLite temporal y una subred libre; detiene el escenario
de prueba al terminar:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev \
ANVIL_SCADA_IMAGE=anvil/scada:dev \
PYTHONPATH=backend .venv/bin/python integration/scada_control_smoke.py
```

La prueba HMI también cubre la lectura de alarmas de un SCADA asociado, su
reconocimiento desde HMI, el reinicio del contenedor SCADA y la persistencia del
reconocimiento en SQLite. `ANVIL_BASE_URL` selecciona la API aislada que se prueba;
`ANVIL_HTTP_TIMEOUT_SECONDS` amplía el tiempo de espera del despliegue (por defecto,
120 segundos):

```bash
ANVIL_BASE_URL=http://127.0.0.1:8004 \
ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev \
ANVIL_SCADA_IMAGE=anvil/scada:dev \
PYTHONPATH=backend .venv/bin/python integration/hmi_control_smoke.py
```

El smoke siguiente comprueba que el worker del backend persiste muestras en
SQLite y las conserva después de detener y volver a desplegar el escenario. El
script crea y elimina su propio escenario; requiere las imágenes S7 y SCADA ya
construidas y, si la API tiene autenticación, las variables de acceso de
`VALIDATION_LOCAL.md`:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
ANVIL_SCADA_IMAGE=anvil/scada:dev \
PYTHONPATH=backend .venv/bin/python integration/scada_historian_smoke.py \
  --base-url http://127.0.0.1:8004
```

Añade un nodo SCADA desde el catálogo, marca uno o más PLC S7/dispositivos Modbus
y configura el periodo (250–60000 ms) y el tamaño del histórico (1–10000 muestras).
Los objetivos de la misma zona se conectan directamente. Para un objetivo remoto,
activa la ruta TCP desde el inspector; la adquisición pasa por el gateway aislado.
El inspector permite definir
umbrales `mayor que`/`menor que` para señales numéricas. Las coils no admiten
umbrales numéricos. No se ofrece routing L3 ni reglas genéricas de tránsito.

Cada regla de alarma numérica admite también **Retardo para activar (ms)** entre
0 y 86.400.000 ms; el valor por defecto 0 mantiene la activación inmediata. Con
un retardo positivo, SCADA exige que la condición siga presente en muestras con
calidad válida hasta que venza. Una muestra ausente, inválida o normal reinicia el
tiempo pendiente. El retardo se mide con el periodo de sondeo configurado, por lo
que la activación se observa en el primer sondeo posterior al vencimiento. La
interfaz muestra la condición pendiente y el retardo. El tiempo pendiente vive en
el runtime SCADA y se reinicia si se recrea ese contenedor; las alarmas que ya se
activaron y su reconocimiento mantienen la persistencia SQLite descrita abajo.

Una HMI puede seleccionar SCADA de su misma zona en **Alarmas mostradas desde
SCADA**. La vista HMI incorpora sus alarmas activas y ofrece reconocerlas desde
el panel; la operación se aplica al colector que evalúa la regla y queda auditada.
Además, el inspector HMI permite configurar reglas propias para tags numéricos
(REAL S7 o UINT16 Modbus) y digitales (BOOL S7, coil o discrete input) seleccionados
en esa HMI. Los tags numéricos admiten umbrales superior/inferior; los BOOL admiten
alarma cuando el estado leído coincide con el valor esperado. Se evalúan solo con
valores obtenidos por lectura protocolaria HMI; una regla no puede apuntar a un
destino no seleccionado, una dirección inexistente o un tipo incompatible. El
reconocimiento no necesita que SCADA esté activo.

Anvil conserva en SQLite
la hora de activación y el reconocimiento mientras la alarma siga activa, incluso
si se recrea el contenedor SCADA. Al observar que vuelve a normal, elimina ese
estado; una activación posterior es una alarma nueva. Si se elimina el escenario,
también se elimina su estado de alarma. `/alarms` informa si el colector completó
un sondeo: mientras todavía no esté listo, la HMI muestra el último estado como
obsoleto y deshabilita la acción. Un error de calidad/comunicación tampoco se
interpreta como normal.

El estado de alarmas propias HMI y sus transiciones activada/normalizada también
se guardan en SQLite, se auditan y se incluyen en `GET .../hmi/{node}/values`.
La respuesta presenta hasta 50 transiciones recientes; la base conserva como
máximo las 10.000 más recientes por HMI. Una lectura con error conserva la alarma
activa como obsoleta y bloquea el reconocimiento hasta una muestra válida.

Cada regla propia HMI admite `delay_ms` de 0 a 86.400.000 ms (24 h); si se omite,
vale 0 y el comportamiento sigue siendo inmediato. Un valor mayor que cero exige
que lecturas válidas consecutivas mantengan la condición durante el retardo. El
tiempo pendiente se guarda en SQLite para sobrevivir reinicios del backend; una
lectura ausente o con mala calidad reinicia una condición que todavía no activó
la alarma. Si la alarma ya estaba activa, una mala lectura conserva el estado como
obsoleto hasta observar una normalización válida. La medición ocurre en cada
consulta de valores HMI (el panel consulta cada 2 s), por lo que la activación se
observa en el primer ciclo posterior al vencimiento y no tiene precisión de
temporizador industrial. SCADA aplica su retardo en sus propias muestras; su
condición pendiente se reinicia al recrear el contenedor.

El inspector presenta el campo **Retardo para activar (ms)** al editar una regla
HMI. El backend valida un entero estricto dentro del rango y el estado pendiente
aparece en la vista HMI. La migración de SQLite añade el campo de persistencia a
bases existentes sin eliminar el estado actual.

## API

Las operaciones de adquisición y control requieren el runtime desplegado y se
consultan mediante `docker exec` al contenedor SCADA. El historial usa SQLite y
solo intenta `docker exec` para incorporar el buffer reciente:

- `GET /api/scenarios/{scenario}/scada/{node}/snapshot`
- `GET /api/scenarios/{scenario}/scada/{node}/history?limit=100`
- `GET /api/scenarios/{scenario}/scada/{node}/history/aggregate?bucket_seconds=5`
- `GET /api/scenarios/{scenario}/scada/{node}/history/aggregate?start=2026-10-08T10:00:00Z&end=2026-10-08T11:00:00Z&bucket_seconds=60`
- `GET /api/scenarios/{scenario}/scada/{node}/history/export.csv?limit=1000000`
- `GET /api/scenarios/{scenario}/scada/{node}/alarms`
- `POST /api/scenarios/{scenario}/scada/{node}/alarms/acknowledge` con
  `{"target_node_id":"plc-1","address":"DB1.DBD0"}`
- `POST /api/scenarios/{scenario}/scada/{node}/write` para escritura operativa,
  con `{"target_node_id":"plc-1","address":"DB1.DBX4.0","value":true}`.
- `GET /api/scenarios/{scenario}/hmi/{node}/values` devuelve además las alarmas
  de los SCADA declarados en `alarm_source_node_ids`, las alarmas propias y las
  últimas 50 transiciones de las reglas HMI.
- `POST /api/scenarios/{scenario}/hmi/{node}/alarms/acknowledge` con
  `{"scada_node_id":"scada-1","target_node_id":"plc-1","address":"DB1.DBD0"}`.
  Para reconocer una regla propia HMI omite `scada_node_id`:
  `{"target_node_id":"plc-1","address":"DB1.DBD0"}`.

El reconocimiento desde HMI solo acepta SCADA de la misma zona declarados por
esa HMI y reglas existentes en ese SCADA. Admin, instructor, diseñador y
participante pueden operar el botón según el acceso de operación del escenario;
el endpoint registra `hmi.alarm_acknowledged`.
La respuesta `GET .../alarms` incluye `ready`; cuando es falso, la HMI devuelve
el último estado guardado con `stale: true` si existe. Un valor con calidad mala
no elimina la alarma; una normalización observada en una lectura válida sí.

`GET .../history` devuelve muestras durables agrupadas por timestamp y el campo
`live`, que indica si el runtime estaba disponible para refrescar el buffer. La
ruta sigue funcionando con el escenario detenido o Docker desconectado. La HMI
SCADA del inspector deja esos valores en solo lectura y los etiqueta como
historial guardado; la lectura de alarmas y su reconocimiento requieren el runtime activo.

`GET .../history/aggregate` resume observaciones persistidas por intervalos. Si
se omiten `start` y `end`, consulta los últimos 10 minutos; si se especifican,
ambos deben incluir zona horaria y el rango usa el intervalo `[start, end)`. Los intervalos admiten 1–86.400 segundos, el
rango máximo es de siete días y la respuesta limita a 1.000 intervalos y 100.000
observaciones leídas. Por cada punto devuelve media/mínimo/máximo calculados solo
con lecturas numéricas de calidad `good`, conteos de calidad y conteos de estados
BOOL. El inspector dibuja la media de intervalos de 5 segundos para los últimos
10 minutos; las lecturas con mala calidad no afectan esa tendencia.
El inspector ofrece descargar el histórico persistido en CSV, una fila por punto
y timestamp con valor, calidad, unidad y dirección. La exportación está disponible
con el escenario detenido, admite hasta 1.000.000 de filas (el límite de retención
por escenario) y protege etiquetas frente a fórmulas de hojas de cálculo.

La ruta de escritura solo admite dispositivos seleccionados por el SCADA, equipos
S7 o Modbus desplegados y puntos `read_write` del mapa de E/S. En S7 acepta salidas
BOOL (por ejemplo válvulas/motores) y canales `io_channel` REAL finitos codificados como IEEE-754;
en Modbus acepta coils booleanas y holding registers de 0 a 65535. Input registers,
sensores/entradas y salidas bajo control
de lógica PLC permanecen de solo lectura. Las reglas de firewall configuradas se
aplican a las escrituras igual que a HMI/consola. Cada operación autorizada se
registra como `operator.point_written` con `source=scada`; rechazos por permisos se
registran como `api.request_rejected`.

El inspector muestra controles de escritura solo para puntos cuyo permiso publicado
por la API es `read_write`; los demás aparecen como solo lectura. Una operación
requiere escenario desplegado y vuelve a consultar la muestra tras escribir. La
cobertura de API valida permisos, auditoría y llamada al adaptador de protocolo;
`integration/scada_smoke.py` valida la adquisición nativa S7/Modbus, pero no es una
prueba extremo a extremo de escritura desde la pantalla SCADA.

La adquisición continúa aunque el panel no esté abierto. La persistencia la realiza
un worker del backend que consulta los buffers SCADA en intervalos de cinco segundos;
requiere una sola instancia del backend y puede omitir muestras intermedias si el
sondeo del SCADA es más rápido que ese intervalo. El historial persistido todavía no
se puede exportar como archivo independiente. La auditoría SQLite registra el
reconocimiento de alarmas y las escrituras SCADA.

Los dispositivos PLC/Modbus no publican puertos del host por defecto y la imagen
SCADA no publica su API interna. El colector se conecta a su red interna y, cuando
hay una ruta declarada, a la IP local del proxy TCP. La HMI también ejecuta sus
clientes S7/PyModbus dentro de su contenedor y puede usar estas rutas; se construye
con la misma imagen de runtime SCADA (`ANVIL_HMI_IMAGE`, por defecto
`ANVIL_SCADA_IMAGE`). Snap7/PyModbus y los tags disponibles se resuelven al validar
y desplegar el escenario.
