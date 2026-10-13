# Hoja de ruta

La matriz de cobertura del documento de requisitos Cyber Range OT está en
[`SRS_TRACEABILITY.md`](SRS_TRACEABILITY.md). Los puntos indicados como parciales
o pendientes son trabajo real de producto y no se consideran capacidad implícita.

## Completado

- **Fase 1:** simulador PLC S7 parametrizable.
- **Fase 2:** persistencia y orquestación Docker.
- **Fase 3:** editor visual de escenarios.
- **Fase 4:** estado en vivo mediante WebSocket.
- **Fase 5:** consolidación operativa: supervisor compartido, healthchecks,
  reconciliación, limpieza segura de huérfanos, dependencias reproducibles,
  prueba S7 real, CI y documentación de operación.
- **Gateway Docker (incremento SRS local):** Compose deja el socket fuera del
  backend y lo sustituye por `docker-api`, que aplica allowlist de imágenes,
  endpoints y recursos; valida etiquetas, red interna, puertos y endurecimiento.
  El gateway aún conserva el socket del host, así que RNF-09 sigue parcial.
- **RNF-09 (confinamiento del backend):** los tres Compose montan raíz de solo
  lectura, descartan todas las capabilities y activan `no-new-privileges`;
  además limitan PIDs y dan `/tmp` temporal. Un bootstrap corto prepara el
  volumen SQLite dedicado `/data` y cambia Uvicorn a UID/GID 65534 por defecto,
  sin capabilities efectivas. Solo el bootstrap recibe temporalmente
  `CHOWN/FOWNER/SETGID/SETUID` para asignar el propietario y los modos privados
  del volumen; UID/GID se pueden configurar para el host. La suite de 491
  pruebas y `integration/backend_security_smoke.py` pasaron con health 200 y
  SQLite 0600 en Docker Engine local. Esto no reduce la autoridad de alto
  impacto que el gateway mantiene sobre su daemon ni acredita Docker rootless.
- **RNF-11 (endurecimiento incremental de SQLite):** el bootstrap restringe la
  base local y sus sidecars a `0600`, el directorio dedicado a `0700`, y el
  proceso protege archivos nuevos con `umask 077`. SQLite no está cifrada; el
  UID de servicio y el administrador del host pueden acceder a los datos.
- **RF-25 (rutas de servicio explícitas):** SCADA y HMI pueden llegar a un PLC
  S7/Modbus remoto mediante un proxy TCP por destino/puerto, conectado a dos
  redes internas y sin privilegios ni puertos publicados. Un `remote_input`
  puede usar una ruta PLC receptor→PLC emisor S7, y las conexiones PLC↔campo
  pueden habilitar un gateway HTTP fijo al runtime de campo. La interfaz permite
  configurar estas rutas y muestra su estado. Docker smokes probaron operaciones
  de protocolo y señales de campo con bloqueo directo. No es routing L3 ni ACL
  generales; el runtime de campo conectado se limita a una zona por escenario.
- **RF-38 (parcial):** el gateway inline reescribe respuestas FC01/FC02/FC03/FC04/FC23 y
  peticiones FC05/FC06/FC15/FC16/FC22 en una ruta Modbus TCP explícita; FC23 deja
  intacta la parte de escritura y solo sustituye la respuesta de lectura.
  FC01/FC02 cambian un bit seleccionado de la respuesta sin modificar la coil o
  discrete input del servidor; el smoke Docker verifica el estado antes, durante
  y después de la regla. La API
  de ejercicios programa acciones set/clear,
  valida registros y Unit ID, audita activación/limpieza y limpia reglas al
  cancelar o terminar. Cada respuesta cambiada se conserva en una cola SQLite
  durable acotada en el volumen del gateway y el colector la añade a la cadena
  de auditoría como security.modbus_interception_match, enlazada a ejercicio,
  evento, ruta y regla sin guardar valores. Objetivos audit_event pueden filtrar
  esa evidencia. El Docker smoke confirma lectura alterada, evidencia durable,
  restauración, servidor intacto, bloqueo directo interzona y cero puertos
  publicados. integration/exercise_smoke.py también cubre el flujo combinado bajo
  el scheduler: lectura real desde la estación, auditoría recolectada antes de
  evaluar el objetivo y restauración. RF-38 sigue parcial por otros incidentes
  aún no implementados en el SRS y por las limitaciones anotadas en su diseño.
  Véase [intercepción protocolaria](PROTOCOL_INTERCEPTION_DESIGN.md).

## Fase 6: escenario utilizable como cyber range

Implementado y probado:

- Mapa S7 para DB, M, I y Q. Los canales `io_channel` asignan offsets automáticamente por área si se dejan vacíos, o permiten fijar byte y bit BOOL explícitos; el esquema valida solapes, alineación REAL y tamaño de memoria. Los componentes heredados y bloques ON/OFF mantienen su asignación automática en DB.
- Logs por dispositivo mediante API y WebSocket, con un lector compartido por escenario.
- Consola de valores en vivo: lectura de válvulas BOOL y sensores REAL.
- Escritura controlada de válvulas; sensores y direcciones ajenas al mapa son de solo lectura.
- Ciclo nominal S7 ajustable de 10 a 5000 ms, aplicado al bucle PLC y al puente de E/S de campo.
- Retardo fijo y jitter de respuesta (0–10 000 ms cada uno) configurables en S7 y Modbus TCP. El jitter admite secuencia determinista (por defecto), distribución uniforme o normal, con semilla reproducible; no ofrece garantías de tiempo real.
- Perfil de fallo S7 `timeout`: el servidor omite respuestas a lecturas/escrituras de datos sin detener el PLC ni cerrar la sesión TCP. No simula desconexión ni tiene probabilidad configurable.
- Historial reciente de cambios observado en la sesión del navegador.
- Vista de operaciones S7 atendidas por el simulador: cliente, área DB/M/I/Q, bytes, dato y resultado. La captura Snap7 se conserva como historial de logs acotado y alimenta la API y el panel; no es auditoría durable ni captura de paquetes.
- Smoke integral con despliegue real, lectura, escritura y verificación de la traza.
- Dispositivo Modbus TCP: coils/holding/input registers, direcciones automáticas, mapa de E/S, panel y API con permisos por tipo.
- Modbus discrete inputs BOOL con función 02, direcciones automáticas en espacio independiente y permisos de solo lectura; HMI/SCADA los adquieren y las señales BOOL de campo se enlazan a este espacio.
- Persistencia de posiciones y conexiones del canvas; escenarios previos siguen abriendo con layout de respaldo.
- Imagen Modbus aislada, sin puertos publicados en el host; guía de construcción y prueba con Docker CLI.

La vista es una traza semántica generada por eventos del servidor Snap7; no es una
captura de paquetes ni incluye la trama S7 completa. El histórico de señales se
mantiene en memoria del navegador y se reinicia al cambiar de escenario/recargar.

## Biblioteca de plantillas (incremento local)

La interfaz ofrece cinco plantillas categorizadas para comunicaciones, procesos
con lazo físico e instrumentación independiente. Admin/instructor pueden publicar
un escenario detenido como plantilla compartida desde el diálogo de biblioteca;
las plantillas se guardan en SQLite y se validan con el esquema normal al
instanciarlas. Quien publica puede retirarla y admin puede retirar cualquiera.
Cualquier usuario autenticado crea su propia copia aislada. Esto completa la
gestión compartida básica de plantillas de RF-43. Admin/instructor también pueden
publicar presets de los tipos de componente integrados desde el inspector; todos
los roles pueden añadirlos a dispositivos compatibles. Los datos se validan contra
los modelos de adaptador y no cargan código ejecutable. Las plantillas de escenario
compartidas admiten edición por el publicador o admin, revisiones inmutables,
restauración como nueva revisión y control optimista de versiones concurrentes;
la migración de bases existentes crea una revisión base idempotente. Los presets
de componentes también admiten edición versionada/restauración; validan cada
versión con el esquema del adaptador y rechazan escrituras obsoletas. Sus
migraciones iniciales son aditivas e idempotentes. Sigue pendiente migrar
automáticamente a futuros esquemas, junto con presets S7 remotos con referencias
contextuales y moderación por organización. Ver
[biblioteca de componentes](COMPONENT_LIBRARY.md). El ejemplo
de planta de agua de comunicaciones no simula hidráulica; para enseñar el balance
de nivel debe elegirse la plantilla de lazo hidráulico.

## Historial de topologías (incremento local)

Cada creación y cada cambio de configuración guardado genera una revisión
declarativa inmutable. El API expone el listado y detalle de revisiones y permite
restaurar una anterior solo con el escenario detenido; la restauración queda
registrada en auditoría y crea otra revisión en vez de borrar el historial. Los
guardados idénticos consecutivos se deduplican mediante hash del snapshot. La
interfaz permite consultar el historial y restaurar una versión. La historia está
en SQLite local y se elimina al borrar el escenario; no hay ramas ni diferencias
visuales. El intercambio migra escenarios v1→v2, pero no hay retención configurable
del historial ni migración de futuras versiones todavía no definidas.

## Perfil de comportamiento Modbus TCP (incremento local)

El inspector permite introducir un retardo fijo y jitter por solicitud
(0–10 000 ms cada uno), elegir perfil determinista, uniforme o normal y fijar
una semilla reproducible. El perfil normal está centrado en la mitad del máximo,
con desviación máximo/6 y recorte a sus límites. También permite
seleccionar el comportamiento normal, una excepción Modbus `Device Failure` o el
cierre de la conexión sin respuesta. El runtime aplica estos comportamientos al
servidor TCP real, preservando los mapas de coils/registros y sus permisos cuando
el perfil es normal. `integration/modbus_protocol_test.py` usa un cliente Pymodbus
sin modificar para verificar lecturas/escrituras, latencia observada y ambos modos
de fallo. Los perfiles estadísticos usan un generador pseudoaleatorio local al
runtime y se repiten con la misma semilla. RTU no permite estos perfiles de
respuesta TCP.

## Orden de resolución del proceso

Las conexiones físicas de topologías acíclicas se ordenan por dependencias y se propagan en una pasada, sin depender del orden en que se serializaron. Los circuitos de retorno ya admitidos (por ejemplo, tanque→válvula→tanque) conservan el bucle iterativo limitado de compatibilidad. Se detecta ese caso, pero aún no hay iteración hasta convergencia ni solver de presión de red; no se debe interpretar el resultado de ciclos complejos como solución hidráulica general.

## Modbus RTU sobre serie virtual (checkout local)

El adaptador Modbus existente acepta `config.transport: "rtu"` y conserva el
mismo mapa de coils/holding/input registers. El runtime crea un pseudo-terminal
8N1 y reenvía bytes RTU/CRC por un túnel TCP raw sin MBAP; HMI, SCADA y las
operaciones del panel usan clientes PyModbus seriales con URL `socket://`.
`integration/modbus_rtu_smoke.py` despliega el dispositivo en una red interna,
verifica lectura/escritura desde otro contenedor, confirma que no publica puertos
del host y comprueba las operaciones gestionadas de Anvil. El workflow CI añade
este smoke.

El túnel permite conectar clientes Modbus RTU sin requerir hardware, pero no
representa señal eléctrica RS-485, colisiones, multi-drop ni fallos de cable. El
pseudo-terminal limita el formato a 8N1; los baudios son nominales para las
temporizaciones software RTU. Retardo y fallos inducidos solo están disponibles
en el modo TCP/MBAP.

Completado en fase 6:

- Importar/exportar JSON y clonar escenarios preservando configuración y canvas.
- Reenvío opcional de un puerto OT a loopback o una IPv4 concreta del host; el modo sustituto requiere firewall externo con controles de origen (ver [guía de acceso](ACCESS.md)).
- Pausa y reanudación del escenario completo o de un nodo, reinicio coordinado del escenario completo y start/stop/restart por nodo, con estado Docker reflejado, controles en el topbar/inspector y eventos auditables. El reinicio completo requiere que todos los nodos planeados estén presentes y espera sus healthchecks; si Docker falla a mitad del reinicio, Anvil registra el fallo y actualiza el estado desde Docker para que la reconciliación continúe desde la condición real. La pausa completa requiere que todos los nodos estén ejecutándose; si Docker falla a mitad de operación, Anvil intenta revertir los nodos ya cambiados.

## Fase 7: catálogo OT

Implementado en el checkout local (sin publicar):

- Los adaptadores registrados proporcionan catálogo, validación de configuración, capacidades y mapa de E/S; el inspector limita los componentes S7 según el catálogo.
- Componentes S7 `motor`, `digital_input` y `alarm` con offsets automáticos y permisos R/W o R correctos.
- Bomba S7 con orden BOOL escribible y caudal REAL de solo lectura: cero parada y punto medio del rango cuando está en marcha.
- La HMI permite seleccionar Node IDs, refresca valores S7/Modbus cada dos segundos mientras está seleccionada y ofrece controles para BOOL/REAL de salida S7 y coils/holding registers escribibles.
- Las rutas de HMI limitan cada operación a los equipos seleccionados, respetan el firewall y rechazan puntos de solo lectura. La integración usa clientes reales Snap7/PyModbus dentro del escenario.
- El firewall valida los puertos de las lecturas/escrituras de consola y HMI, con respuestas 403 cuando la política los deniega.
- HMI, gateway, firewall y estación de participante aparecen en el catálogo y despliegan contenedores con límites; la estación queda aislada salvo que se habilite explícitamente su conexión a una zona interna.
- Estación de ingeniería como host genérico del escenario, con red y terminal no-root opt-in; roles diseñador/instructor/admin, imagen configurable `ANVIL_ENGINEERING_IMAGE` y cliente S7 verificado desde su zona.

La segmentación Purdue básica ya existe en el checkout: se pueden asignar equipos a
zonas con subredes distintas y Docker crea una red `internal` por zona. Smokes reales
confirman TCP dentro de una zona, denegación entre zonas sin permiso y rutas de servicio
SCADA/HMI, PLC→PLC S7 y PLC↔campo. Todavía no hay interfaces de router L3 ni ACL generales. El inspector conserva
formularios específicos por familia; la extracción completa a plantillas declarativas
sigue pendiente. La estación de participante queda aislada por defecto. Al habilitar
su acceso, se conecta solo a la red Docker interna de su zona y puede usar clientes
Snap7/PyModbus desde el contenedor. El inspector ofrece ahora una consola WebSocket
para roles participant/instructor/admin, solo en escenarios en ejecución y estaciones
con acceso opt-in. Usa UID 65534, una sesión por estación, máximo 16 por worker y
auditoría de apertura/cierre. Un lease SQLite se renueva cada 5 s y vence a los
20 s; si el worker no puede liberar la sesión, otro worker puede reclamarla al
expirar. `test_terminal_leases.py` verifica renovación y reclamación, y los smokes
de terminal comprueban cierre/desconexión del túnel con Docker real. La cuota de
16 sigue siendo por worker, no global entre réplicas, y no hay un smoke que mate
el proceso backend durante una sesión activa.

Los nodos ahora admiten `hostname` opcional, único por zona, con valor derivado de
`node_id` cuando se omite. Docker y el ZIP Compose asignan ese hostname y su alias DNS
en la red interna; el smoke de red incluye resolución de nombres. Aún no declara
servicios industriales ni autoriza puertos o flujos.

## Tiempo de despliegue visible (incremento local)

La API de despliegue ahora informa la duración observada desde que empieza la
orquestación Docker hasta que los contenedores están listos; la interfaz muestra
el tiempo al completar y lo agrega al historial de actividad. Tres ejecuciones
locales de 20 PLC S7 con healthcheck TCP/102 tardaron entre 8,555 y 8,669 s en
WSL2/Docker. RNF-04 sigue parcial porque aún no se han caracterizado topologías
mixtas ni otros perfiles de host.

## Comunicación entre PLCs S7 (checkout local)

- Entradas remotas BOOL/REAL que leen señales locales de otro PLC del escenario.
- Cliente S7 dentro del receptor; IP, rack, slot y offsets resueltos al guardar.
- Periodo y timeout configurables, última lectura y calidad waiting/good/stale.
- Último valor conservado ante fallos y reconexión automática con espera creciente.
- Selección de emisor y señal en el inspector; entrada de solo lectura en consola/API.
- Pruebas con dos PLCs Docker y pérdida/recuperación de comunicación.

Ver [S7_LINKS.md](S7_LINKS.md). La lógica Ladder/ST completa y los enlaces Modbus
entre PLCs quedan como ampliaciones posteriores.

## Lógica PLC configurable (incremento local)

El S7 ejecuta bloques ON/OFF con histéresis (REAL→BOOL), compuertas AND/OR/XOR
(BOOL+BOOL→BOOL) con polaridad configurable de contactos normalmente abiertos o
cerrados, memorias SR con prioridad RESET, temporizadores TON (BOOL→BOOL),
contadores CTU por flanco ascendente y secuenciadores one-hot configurables de
2–16 pasos. El secuenciador avanza con flanco, mantiene el paso terminal y vuelve
al primero con RESET prioritario. Los bloques se configuran desde el inspector y
sus salidas quedan protegidas frente a escrituras HMI/API.
`integration/control_logic_smoke.py` verifica en Docker con Snap7 AND/NC, SR,
TON, CTU y secuenciador.

Retentividad configurable implementada en local: opción S7 para conservar ON/OFF,
SR, TON, CTU y secuenciador mediante volumen por PLC; parada/recreación conserva
datos y borrar escenario los elimina. `integration/retained_state_smoke.py`
comprueba la recreación del contenedor con Snap7 y la limpieza del volumen.
Pendiente: Ladder/ST, diagnóstico de calidad de entrada, pruebas de corte abrupto
del daemon/host y compilación/importación de programas IEC. Ver [lógica PLC](PLC_LOGIC.md).

## Consola HMI independiente (incremento SRS local)

La navegación muestra una vista HMI de operador cuando el escenario incluye al
menos una HMI. Se pueden consultar sus señales y alarmas configuradas y usar los
controles que permiten el rol, el mapa de E/S, la ruta de red y el firewall. La
lectura se actualiza cada dos segundos mediante reconciliación incremental de
nodos DOM; conserva las referencias de tarjetas y sinóptico, el foco y las
consignas no enviadas. `integration/workspace_ui.cjs` verifica que los valores se
actualizan y el borrador local permanece durante el refresco. Las acciones
reutilizan las rutas HMI existentes, incluida auditoría y persistencia de
reconocimiento. El inspector de
ingeniería conserva la configuración de destinos y permisos. El sinóptico coloca
los objetos de campo según el plano, dibuja sus conexiones físicas y presenta
valores únicamente cuando la HMI lee la señal enlazada desde sus PLC/Modbus
seleccionados; los valores internos del simulador no sustituyen telemetría PLC.
La HMI permite configurar reglas por tag seleccionado: umbral superior/inferior
para REAL/UINT16 y estado esperado para BOOL. Evalúa únicamente las muestras que
ella misma leyó, permite reconocer alarmas y consultar hasta las últimas 50
transiciones activada/normalizada. Cada regla HMI admite `delay_ms` (0 por defecto,
hasta 24 h); la condición debe continuar en lecturas válidas y una lectura ausente
o de mala calidad reinicia el conteo pendiente. El retardo avanza con el refresco
de 2 s, no con un temporizador independiente. Estado pendiente y transiciones se
guardan en SQLite; una lectura mala conserva una alarma activa como obsoleta y no
permite reconocerla. SCADA también admite retardo en sus reglas numéricas: el
conteo usa su periodo de sondeo y se reinicia al recrear el runtime; las alarmas
ya activadas y reconocimientos sí persisten. Expresiones complejas y políticas
de retención configurable siguen pendientes. Los smokes `hmi_control_smoke.py` y
`scada_smoke.py` comprobaron en Docker el retardo de 1,5 s.

## Exportación Docker independiente (incremento local)

Cada escenario se puede descargar como ZIP con un `docker-compose.yml` equivalente
a sus contenedores y redes internas, `scenario.json` versionado y una guía breve.
El paquete añade `.env`, `network-addresses.json` y `readdress.py` para reasignar
subredes/IPs/puertos publicados conservando los offsets de cada dirección.
Se conserva la política de seguridad de contenedores y cualquier puerto OT optativo
queda enlazado a la IPv4 explícita elegida (loopback por defecto). Las imágenes no se empaquetan; deben estar disponibles en
el host destino. La importación del JSON a Anvil se mantiene aparte. No es un paquete
autosuficiente de imágenes. El smoke Docker compara dos despliegues consecutivos
de la misma topología en el mismo host (imagen efectiva, variables, puertos,
límites, endurecimiento, IP, DNS y redes). El bundle usa nombres Compose por
proyecto y labels separados de los recursos gestionados por Anvil; el smoke
`deployment_bundle_smoke.py` reasigna una subred libre, arranca el bundle mientras
Anvil continúa activo, verifica que el bundle sobrevive al borrado del escenario
fuente y limpia sus contenedores independientes. El operador elige los CIDR y
transfiere imágenes por separado.
Ninguno demuestra el estado interno completo del proceso ni equivalencia entre
hosts o arquitecturas.

## Preflight de topología (incremento local)

Antes de desplegar, Anvil vuelve a validar el escenario y presenta advertencias
para HMI sin destinos, actuadores sin mando, sensores sin consumidores, elementos
de proceso sin conexiones físicas, PLCs sin bloques automáticos y zonas aisladas.
El operador puede corregirlas o confirmar el despliegue. También comprueba la
alcanzabilidad declarada de clientes HMI/SCADA, entradas remotas S7 y enlaces
PLC↔campo entre zonas; durante la ejecución sondea las rutas habilitadas desde
el origen y comprueba los intercambios de cada binding PLC↔campo. Los enlaces
de proceso se revisan para detectar caminos incompletos. Estas comprobaciones
solo cubren relaciones que Anvil modela: una sonda verifica conectividad al
puerto, no la semántica de protocolo, la corrección de un programa PLC ni la
validez de ingeniería de la planta. Los avisos siguen siendo consultivos; los
errores de esquema sí bloquean el despliegue. La evidencia y los casos cubiertos
están en [RF-04 de la matriz SRS](SRS_TRACEABILITY.md#requisitos-funcionales)
y en `integration/process_map_ui.cjs` y `backend/tests/test_api.py`.

## Recursos de runtime por nodo (incremento local)

El estado de cada contenedor incluye CPU, memoria del cgroup y número de procesos,
muestreados como máximo cada tres segundos y visibles sobre el nodo del mapa. El
gateway solo autoriza la consulta puntual (`stream=false`, `one-shot=true`) para
contenedores etiquetados como gestionados. Algunos Docker Desktop/WSL no publican
ciertos contadores; la UI muestra `—` cuando el daemon no entrega el dato. Los
despliegues ahora suman los techos de CPU/memoria y el número de runtime nodes
frente a cuotas configurables por escenario. No reservan capacidad ni coordinan
varios escenarios concurrentes; alertas de capacidad siguen pendientes.

## Medición del editor con 100 nodos (smoke ejecutado localmente)

`integration/map_performance_ui.cjs` crea un escenario detenido temporal con 100
nodos mixtos (PLC S7, Modbus TCP, HMI, SCADA y gateway) y 99 enlaces Ethernet
lógicos. Mide la apertura hasta que se dibujan nodos y enlaces; luego comprueba
selección e inspector, zoom, paneo con espacio+arrastrar, que la topología no
cambie y que el navegador no reporte errores ni solicitudes fallidas. El límite
de render sigue siendo 5 s; el script borra al final el escenario temporal.
Ejecución:

```bash
ANVIL_BASE_URL=http://127.0.0.1:8000 node integration/map_performance_ui.cjs
```

El 2026-10-08 el smoke actualizado pasó con 100 nodos mixtos y 99 enlaces en
770 ms; selección e inspector, zoom y paneo funcionaron y no hubo errores JS,
errores de consola ni solicitudes fallidas. Se usó Chromium de Playwright 1.55.0
en contenedor contra un backend aislado en 8011, con SQLite temporal y
autenticación desactivada. Los previews 8003/8005 no se usaron ni modificaron.
Es una única medición en este host, no una garantía entre máquinas o navegadores.
El 2026-10-08 se repitió contra la API efímera en `18005`, con otra base SQLite
temporal y el mismo smoke: Chromium 140.0.7339.16 completó los cinco renders con
mediana de 912 ms y máximo/p95 de 1.266 ms; selección, zoom y paneo pasaron sin
errores de navegador ni solicitudes. El smoke creó y borró el escenario de
prueba; la API efímera se detuvo después y los previews de producto no se
utilizaron. Esta repetición reduce la dependencia de una sola ejecución, pero
sigue siendo una medición del mismo host, no una garantía multiplataforma.
Para repetirlo en un backend aislado, el ejemplo usa un puerto libre (8006):

```bash
ANVIL_DATABASE_URL=sqlite:////tmp/anvil-rnf05-100nodes-20261007.sqlite3 \
PYTHONPATH=backend .venv/bin/python -u -c 'import main; from models.db import create_db_and_tables; create_db_and_tables(); import uvicorn; uvicorn.run(main.create_app(initialize_database=False, auth_enabled=False), host="0.0.0.0", port=8006)'
```

En otra terminal, desde la raíz del repositorio:

```bash
docker run --rm --add-host host.docker.internal:host-gateway \
  -v "$PWD/integration/map_performance_ui.cjs:/work/map_performance_ui.cjs:ro" \
  -w /work mcr.microsoft.com/playwright:v1.55.0-noble bash -lc \
  'npm install --prefix /tmp/anvil-pw playwright@1.55.0 --no-save --no-package-lock && \
   ANVIL_BASE_URL=http://host.docker.internal:8006 \
   ANVIL_PLAYWRIGHT_PATH=/tmp/anvil-pw/node_modules/playwright \
   node map_performance_ui.cjs'
```

El smoke borra el escenario temporal. La medición detectó una importación duplicada
de `remote_input.js` (query strings distintos) que impedía iniciar el editor; ya se
unificó para reutilizar una sola instancia del registro de componentes.

## Captura PCAP por enlace y segmento (incremento local)

La pestaña **PCAP** permite elegir un dispositivo o una zona. El enlace de un
dispositivo se captura con un sidecar en su namespace de red. Una captura de zona
usa un único sidecar sobre la interfaz `br-<id>` del bridge Docker interno de esa
zona, por lo que registra tráfico unicast entre los nodos del segmento. El sidecar
usa `network_mode=host` y capacidades `NET_RAW`, `SETGID` y `SETUID`, pero raíz de
solo lectura, sin mounts ni puertos publicados; el gateway Docker acepta únicamente
el comando fijo para el bridge interno que coincide con escenario y zona, y bloquea
`exec` hacia cualquier capturador. El PCAP queda en tmpfs, hasta 40.000 paquetes con
snapshot de 128 bytes y exportación máxima de 8 MiB; al exportar se elimina el
sidecar. Esto requiere un Docker Engine Linux que exponga el bridge del escenario;
Docker Desktop y daemon rootless no están verificados, y el modo de dispositivo sigue
disponible si un engine no permite inspeccionar la bridge.

`integration/packet_capture_smoke.py` genera tráfico Snap7 entre PLCs, captura el
bridge del segmento y comprueba TCP/102 en el PCAP dentro del ZIP de evidencia. No
captura tráfico de interfaces físicas ajenas al segmento Docker. El uso del namespace
de red del host aumenta el privilegio del sidecar frente a la captura por dispositivo;
la política del proxy, el comando inmutable y la ausencia de `exec` limitan su alcance,
pero queda pendiente una revisión independiente de seguridad.

El panel permite además descargar un ZIP de evidencias que reúne manifiesto,
auditoría, logs recientes por contenedor e históricos SCADA CSV. Instructor y
admin pueden incluir opcionalmente una captura activa; la interfaz avisa de que
hacerlo la detiene y consume. La descarga sin esa opción no altera capturas
activas. La retención de auditoría configurable por `ANVIL_AUDIT_RETENTION_DAYS`
está implementada y preserva las ventanas de ejercicios activos (ver
[operación](OPERATIONS.md)); queda pendiente una política de retención configurable
para PCAP y una revisión independiente del capturador de segmento.

## Ejercicios OT (incremento local)

La pestaña **Ejercicios** permite construir desde un editor guiado acciones de
escritura S7/Modbus sobre salidas autorizadas, acciones de ciclo de vida de nodos,
incidentes en sensores/actuadores/depósitos y objetivos por puntos, variables
físicas o eventos de auditoría. El JSON avanzado sigue disponible para revisar o
editar el esquema completo. El backend guarda definiciones por escenario con
cronología persistente (máximo 64 eventos/24 h). RF-40 admite
reglas `all`/`any`, score ponderado con umbral, condiciones de campo que deben
mantenerse verdaderas durante un periodo configurable de hasta 24 h y tendencias
de variables físicas con muestreo nominal de 1 s y ventanas de hasta 300 s.
`field_sequence` evalúa en orden entre 2 y 16 condiciones de proceso distintas,
con timeout opcional entre pasos y progreso persistente al pausar/reanudar. Las
definiciones antiguas conservan el criterio `all`.
La ejecución queda auditada y se marca `interrupted`
tras reiniciar el backend. `integration/exercise_builder_ui.cjs` ejercita en
Edge el editor y runtime Docker con una escritura gestionada S7 y una
`protocol_write` Modbus desde estación participante, ruta seleccionada y
objetivos auditados.
RF-38/RF-40
siguen parciales: no hay MITM, tráfico arbitrario, condiciones temporales de
fallo ni scheduler multi-worker.

Los objetivos `audit_event` aceptan hasta ocho filtros escalares sobre claves
de detalle admitidas. La coincidencia se puede acotar a dispositivo, dirección,
origen, ruta u objeto y se aplica también a condiciones de ausencia y parada
inmediata. Esto permite puntuar una manipulación concreta de un registro en vez
de cualquier escritura del mismo tipo. El editor guiado valida el JSON y el
backend vuelve a comprobar claves y límites. No examina paquetes de red ni
implementa MITM; esos requisitos siguen abiertos. Ver
[trazabilidad SRS](SRS_TRACEABILITY.md) y [Ejercicios](EXERCISES.md).

## SCADA de adquisición (incremento local)

El catálogo incluye ahora un runtime SCADA independiente que sondea tags nativos
S7 y Modbus TCP dentro de la misma zona Purdue. Ofrece periodo configurable,
histórico circular en memoria, tendencias, umbrales numéricos, reconocimiento de
alarmas y rutas API auditables. La integración `integration/scada_smoke.py`
comprueba PLC S7, Modbus, adquisición, histórico y alarma en Docker.

El panel SCADA puede escribir salidas BOOL/REAL S7, coils y holding registers
autorizados. `integration/scada_control_smoke.py` cubre escrituras API hasta
contenedores S7/Modbus reales, lectura de retorno, rechazo de permisos, auditoría
y efecto de un setpoint sobre la válvula proporcional. El histórico persiste en
SQLite y se exporta a CSV. Siguen pendientes alarmas complejas, captura garantizada
durante interrupciones mayores al buffer y protocolos distintos de S7/Modbus TCP.
Ver [SCADA.md](SCADA.md).

## Elementos de campo independientes (incremento local verificado)

En el checkout local: objetos y conexiones tipados, persistencia compatible,
canales de E/S, motor en contenedor por escenario, escalas REAL ↔ UINT16,
editor de objetos y conexiones, calidad de señal y pruebas con S7/Modbus reales.
Una válvula proporcional se puede accionar con REAL S7 o con un holding register
Modbus escalado; `integration/scada_control_smoke.py` comprueba ambas rutas y el
efecto S7 sobre el caudal del depósito. El catálogo permite diseñar primero la
planta y después conectar sus señales.

La interfaz permite crear una copia de un escenario antiguo y convertir sus
componentes PLC locales en objetos/canales de campo, conservando direcciones DB
y referencias de lógica; no modifica el original. Faltan geometría, solver
nodal de presión, retornos, curvas de bomba y condiciones de contorno
avanzadas. El motor limita caudal en caminos en serie, soporta bifurcaciones
paralelas simples y bloques visuales de distribuidor porcentual/colector sumador
con balance conservativo. `integration/process_pipe_smoke.py` verifica el
recorrido completo en Docker (ver [FIELD_OBJECTS.md](FIELD_OBJECTS.md)).
Ver [FIELD_OBJECTS.md](FIELD_OBJECTS.md).

## Perfiles portables de dispositivo (RF-31)

El inspector de PLC S7 y Modbus TCP/RTU exporta e importa perfiles JSON
versionados por dispositivo. El perfil conserva el mapa de direcciones resuelto
para contrastarlo con la hoja de direccionamiento del equipo real. Al importar,
Anvil valida de nuevo la configuración, recalcula direcciones y solicita una
identidad/IP/zona para el escenario detenido. No genera proyectos de TIA Portal
ni perfiles de fabricante, y no certifica equivalencia con hardware. Ver
[perfiles de dispositivo](DEVICE_PROFILES.md).

## Requisitos para exposición pública de Anvil

- Autenticación/autorización inicial disponible en local; antes de exposición pública completar hardening del login y revisión independiente (ver [cuentas y permisos](PLATFORM_ACCESS.md)).
- TLS mediante proxy inverso.
- Acceso restringido al Docker API.
- La salida desde los escenarios sigue bloqueada por defecto. Se añadió una
  excepción optativa por FQDN/puerto TCP para estaciones de ingeniería y
  participantes, mediante un proxy dedicado validado por el gateway Docker;
  `egress_scenario_smoke.py` verifica el despliegue completo y el bloqueo de
  salidas no declaradas. Requiere conectividad saliente del host y revisión
  operativa independiente.
- Límites de recursos por nodo y cuotas básicas por escenario implementadas;
  coordinación global/multi-tenant y alertas de capacidad pendientes.
- Auditoría SQLite con actor, eventos encadenados por SHA-256, exportación JSONL,
  verificación de integridad y retención optativa ya existe. El hash no resiste a
  un administrador que pueda reescribir la base; falta anclaje externo y revisión
  independiente. La evidencia puede archivarse al eliminar el escenario.

Ver [auditoría operativa](AUDIT.md) y [trazabilidad SRS](SRS_TRACEABILITY.md).

## Interrupciones de red en ejercicios

El editor de ejercicios permite programar la interrupción y restauración de una
ruta habilitada del escenario. El scheduler detiene/inicia el gateway dedicado y
audita cada transición. La validación rechaza rutas desconocidas o deshabilitadas.
`integration/exercise_smoke.py` comprueba en Docker la pérdida y recuperación de
tráfico S7 entre zonas al detener y volver a iniciar el gateway de una ruta
declarada, y verifica los eventos auditados y el score del ejercicio. Esto modela
una caída controlada de servicio, no MITM ni modificación de paquetes.
# Continuación del SRS — 2026-10-08

- RF-04: el preflight detecta fragmentos de proceso físicamente enlazados que no
  forman un recorrido dirigido continuo; las medidas de sensores no crean
  conectividad hidráulica artificial. Acepta depósitos receptores y válvulas de
  descarga terminales. Es advertencia y no bloquea el guardado.
- RNF-03/04: el perfil mixto de 30 nodos pasa en 11,451 s con el nuevo techo
  predeterminado de 15 CPU-quota. Es una muestra puntual, sin carga de cliente y
  en un host de 12 CPU; Docker limita cada runtime a 0,5 CPU, pero no coordina
  cuotas entre escenarios.
- Evidencia RF-04: tres pruebas API focalizadas; suite backend de 354 pruebas OK y
  `process_map_ui.cjs` pasó en Edge contra backend efímero con SQLite aislada,
  validando fábrica completa, rama huérfana y persistencia del mapa.
- La guía extensa de revisión está disponible en
  `docs/GUIA_REVISION_ANVIL.html` y en el preview documental local de puerto 8007.
- Sin commit, push ni reinicio de los previews de producto 8003/8005.

## Recuperación de despliegues interrumpidos (RF-27, RNF-15/16)

Deploy y stop reclaman un lease exclusivo por escenario en SQLite compartida,
con vencimiento a los 45 s y renovación cada 10 s. Así, otro worker no interpreta
como averiado un deploy que aún está creando contenedores. El reconciliador de
arranque y un worker periódico solo limpian recursos Docker etiquetados como
gestionados por Anvil para el ID exacto de un escenario `deploying` cuyo lease
haya vencido. Conserva volúmenes de retención y reintenta si el daemon no puede
limpiar. `deployment_recovery_smoke.py` despliega un PLC real y simula la caída
antes de guardar el estado final; verifica la retirada del contenedor/red y el
estado `stopped`. El reconciliador también recrea los contenedores de un
escenario activo cuando se pierden todos. La continuación del 2026-10-08 amplió
`node_recovery_smoke.py`: además de pérdidas parciales/totales de contenedores,
eliminó la red Docker gestionada y verificó que el scheduler la reconstruye desde
el plan persistido antes de recrear los PLC; journal, auditoría, healthchecks y
puertos host cerrados quedaron verificados. Los estados `stopped` y errores de
deploy (`deploying → error`) no se relanzan automáticamente. Una subred ocupada
por otro recurso requiere resolver el conflicto. Docker y SQLite aún no forman
una transacción distribuida.
