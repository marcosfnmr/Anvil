# Trazabilidad del SRS de Cyber Range OT

Revisión del borrador v1.0 frente al checkout local de Anvil en octubre de 2026.
Para entender la arquitectura funcional, el vocabulario de cada subsistema y
la diferencia entre implementación y alcance industrial, empieza por
[Arquitectura funcional y cobertura](SRS_ARCHITECTURE.md) y
[Fidelidad de dispositivos](DEVICE_FIDELITY.md).
El texto fuente exacto está preservado en [`SRS_REQUIREMENTS_v1.md`](SRS_REQUIREMENTS_v1.md)
y coincide byte a byte con el adjunto recibido (SHA-256
`3affc6b6be8b6cf2adb00f13aaa77d9907b16c8fb10449965d9f85ceb0d1149c`). La prueba
de trazabilidad extrae los identificadores de ese documento y comprueba que la
matriz incluya los 84: 43 RF, 16
RNF, 4 RT, 5 IF, 6 DT, 7 CA y 3 SU. La cobertura de identificadores significa que
cada requisito tiene una fila o grupo trazable; no significa que esté cumplido.
La rama es `main`, en el commit `12a51da`; hay cambios locales extensos de fases
anteriores que no están en ese commit. No se han publicado ni se debe inferir que
estén en GitHub. El preview de producto preexistente es `http://localhost:8003/`.
La imagen de trabajo SRS `anvil/backend:srs-v1` se ejecuta de forma aislada en el
contenedor local `anvil-srs-preview`, puerto WSL 8005, con una base SQLite nueva;
no sustituye 8003 ni copia o modifica sus datos.

El preview de demo del checkout actual está separado en `http://localhost:8008/`.
Lo administra el proyecto Compose `anvil-demo`, enlazado solo a loopback, con
volumen de datos propio y `ANVIL_CLEANUP_ORPHANS=false`; su configuración y los
comandos de actualización están en `docker-compose.demo.yml` y
[`DEMO_PREVIEW.md`](DEMO_PREVIEW.md). Las pruebas de este checkout no se atribuyen
a 8003 ni a 8005.

Actualización de plantillas compartidas (2026-10-08): el checkout y 8008 admiten
edición versionada, restauración como nueva revisión y control atómico de edición
obsoleta. La migración añadió la tabla de revisiones a la base existente; la
comparación de recuentos antes/después no encontró cambios en ninguna tabla
existente (el escenario detenido y los demás datos se conservaron). Pasaron 398
pruebas backend, 108 PLC y 27 del gateway; el smoke focal de migración, permisos,
restauración, conflicto de versión, UI y cobertura SRS pasó 4/4. `/api/ready`
indicó DB/Docker `ok`; 8003 y 8005 respondieron HTTP 200. La imagen del preview es
`anvil/backend:srs-rf43-template-revisions-20261008`; no se hicieron commit ni push.

Ampliación de RF-43 a presets de componentes (2026-10-08): la biblioteca permite
editar título, descripción y configuración validada, consultar historial y
restaurar una revisión como nueva, con permisos aplicados en backend y control
atómico de conflictos `409`. La migración aditiva/idempotente también crea una
revisión inicial para presets existentes. Pasaron las 399 pruebas de
`backend/tests` y la compilación de la documentación. Se actualizó únicamente
el backend del preview 8008 a
`anvil/backend:srs-rf43-component-revisions-20261008`; `/api/ready` devolvió
`ready` con DB/Docker `ok`, OpenAPI contiene las rutas de edición, historial y
restauración, y 8003, 8005 y 8009 respondieron HTTP 200. El respaldo
previo está en `/tmp/anvil-demo-pre-component-revisions-20261008.db`. El volumen
de 8008 conservó su cuenta, escenario y auditoría; no hubo escenarios en marcha
ni ejercicios activos. El gateway no se recreó. No se hicieron commit ni push.

Verificación E2E de RF-43 (2026-10-08):
[`component_preset_revisions_ui.cjs`](../integration/component_preset_revisions_ui.cjs)
pasó en Microsoft Edge contra Uvicorn local y una SQLite efímera nueva. Desde la
UI editó título, descripción y configuración; una escritura concurrente avanzó
la revisión y el formulario rechazó el guardado obsoleto con el mensaje actual;
el historial mostró las revisiones y restauró la versión inicial como versión 4.
El smoke eliminó el preset de prueba. No desplegó dispositivos Docker ni usó la
base/cuenta del preview; todavía no se ejecuta en CI.

Verificación de la continuación secuenciador (2026-10-08): backend 357/357, PLC 94/94 y
gateway 24/24 unitarios; `control_logic_smoke.py` pasó con un PLC Docker nuevo y
cliente Snap7, verificando CTU y secuenciador. El preview 8008 se reconstruyó con
la imagen backend/gateway de este checkout y `anvil/plc-s7:demo-20261008-seq1`;
`/api/health` y `/inspector.js` devuelven HTTP 200 y el inspector contiene ambos
bloques. El volumen de datos existente de 8008 se conservó. 8003 y 8005 no se reiniciaron y sus
endpoints de health siguen devolviendo 200.

Actualización de retentividad (2026-10-08): el checkout y el preview 8008 añaden
retención optativa de bloques S7 con volumen Docker por escenario/nodo. El demo
usa `anvil/backend:demo-retention-20261008`, el gateway de igual etiqueta y
`anvil/plc-s7:retention-local-20261008`; `/api/ready` confirma DB y Docker, el
volumen `anvil-demo_demo-data` se mantuvo y 8003/8005 no se recrearon. Pasaron
368 pruebas backend, 101 PLC, 26 del gateway y el smoke Snap7 de recreación y
limpieza. `origin/main` continúa en `12a51da`; estos cambios siguen solo locales.
El estado de acceso difiere por preview: 8008 ya tiene cuenta y pide login; 8005
requiere bootstrap pero no tiene token configurado; 8003 no expone la ruta
`/api/auth/status` de este checkout.

En la revisión previa, los tres puertos devolvían HTTP 200 en `/`. Sin embargo, el proceso de 8005 no representa el checkout actual aunque monte
`backend/`: su OpenAPI no contiene las rutas de autenticación presentes en el
código y una consulta de escenario omitió `network_connections`. No se ha
verificado su contenido funcional actual. Las pruebas del checkout se atribuyen
solo al código local y al preview 8008 reconstruido, no a 8003/8005.

En la continuación de seguridad RNF-08/09 se amplió la suite del gateway a 24
pruebas y se añadió una matriz de denegación por defecto para endpoints Docker
de imágenes, build/commit, volúmenes, plugins, Swarm, servicios y secretos.
`dockerd-rootless-setuptool.sh --force check` confirma que el host no cumple los
prerrequisitos: falta `newuidmap`/`newgidmap` del paquete `uidmap`. No se instaló
software ni se detuvo el daemon rootful; RNF-09 sigue parcial.

En la continuación hidráulica de RF-11/20/21, las bifurcaciones directas de
bombas y variadores conservan el caudal total y lo reparten según los límites y
pérdidas de cada rama. Se añadieron al catálogo visual bloques de distribuidor
con porcentaje configurable y colector de dos entradas; ambos conservan el
balance de caudal. `process_pipe_smoke.py` se desplegó en Docker y verificó en
runtime dos rutas paralelas (50 L/min totales, 33,33/16,67 L/min, caída común) y
un recorrido bomba→distribuidor (30/70)→dos ramas→colector→depósito (15+35=50
L/min). `process_manifold_ui.cjs` en Edge confirmó que ambos bloques aparecen en
el catálogo, que el porcentaje se edita desde el inspector y que se muestran sus
puertos, sin guardar el borrador. En RF-04 el diagnóstico ahora señala las
entradas o salidas que falten en un distribuidor/colector; pruebas de API cubren
topologías incompletas y completas. Las suites completas del checkout pasaron:
347 pruebas backend y 81 pruebas PLC. Se eliminaron escenario y stack temporales. Sigue pendiente el
solver nodal general, presión acoplada en uniones, geometría, retornos y dinámica
distribuida. Los previews 8003 y 8005 respondieron HTTP 200 y no se reiniciaron.

En la verificación posterior del runtime de divisor, `process_pipe_smoke.py`
volvió a pasar sobre una API SQLite efímera en 18004 y una imagen de campo
construida desde el checkout. Además del reparto nominal 15/35 L/min, comprobó
un distribuidor 30/70 con ramas limitadas a 10/20 L/min: el origen se limita a
30 L/min, el caudal no atendido por la rama A se redirige a B y el colector y el
depósito reciben 30 L/min. La imagen, API, base y escenario temporales se
retiraron; los previews no se tocaron. Esta prueba no amplía el modelo a un
solver nodal general.

Evidencia previa de esta revisión: el 2026-10-08 pasaron suites con 290 pruebas
de backend, 67 PLC y 16 de `docker_proxy`. En la continuación de RF-20/21 volvieron
a pasar 321 pruebas de backend, 71 PLC y 18 de `docker_proxy`. Tras endurecer
`exec` para los gateways TCP, la suite de políticas se volvió a ejecutar por
separado y pasó 19 pruebas. La implementación de RNF-14 volvió a pasar 325
backend, 71 PLC y 22 de `docker_proxy`. El smoke de
ejercicios pasó en Docker Engine real, verificando tráfico S7 antes, durante y
después de interrumpir una ruta. Se construyeron imágenes con etiquetas locales
aisladas y no quedaron contenedores ni redes Anvil gestionados recientes.
El terminal participante pasó smokes con Docker Engine/gateway real y navegador
Playwright en backend aislado. En esta revisión se corrigió la limpieza de sesiones
`exec`: conserva la asociación mientras corre, la libera al terminar, cerrar un
túnel interactivo o borrar el contenedor, y aplica expiración de inactividad a los
casos que no consulten estado terminal. El smoke real volvió a pasar con desconexión
abrupta y liberación verificada.
El mismo día `template_onboarding_ui.cjs` pasó en Edge sobre un backend/gateway
aislado: creó y desplegó la plantilla inicial desde la UI, comprobó por S7 que
el PLC leía 50,30 % cuando el tanque llegó a 50,30 L, y observó que paraba bomba
y válvula. Se corrigieron dos fallos que el flujo real descubrió: la
UI cargaba el registro de nodos de campo como otra instancia ES module y el
gateway retiraba el seguimiento de una sesión Docker `exec` antes de consultar
su resultado. Se añadió cobertura para el registro compartido, la limpieza del
smoke y las conexiones de proceso en diagnósticos. Los previews 8003 y 8005 no
se modificaron.
También pasó `process_map_ui.cjs` en Edge contra una imagen backend construida
desde este checkout y un gateway aislado. En la demo creó desde el mapa un enlace
Ethernet lógico, comprobó que se distingue de I/O, S7 y HMI, y verificó que
persiste al guardar y reabrir. Después abrió la plantilla de fábrica, confirmó
cuatro enlaces físicos, tres de señal, dos relaciones de control ON/OFF y dos
de adquisición HMI, añadió el enlace Ethernet y verificó que los tipos siguen
distintos tras guardarlo y reabrirlo. El smoke desplegó la demo original y
comprobó estados en vivo. Se
autenticó con una cuenta efímera, confirmó en el diálogo de preflight el aviso
de salidas PLC sin bloques de control y solo entonces continuó el despliegue;
limpió los escenarios, contenedores y red temporal. Los previews 8003 y 8005
respondieron HTTP 200 antes y después.
En la continuación de RNF-14, las siete imágenes locales se construyeron con
sus bases fijadas y se inventariaron desde los contenedores terminados: se
generaron y validaron siete documentos SPDX 2.3 y un manifiesto de trazabilidad
con sus IDs Docker, referencias base y hashes de entradas/SBOM. La suite backend
pasó 327 pruebas; PLC y gateway, 71 y 22 en esta misma revisión. El workflow que
archiva esos ficheros todavía no ha corrido en GitHub.
`control_logic_smoke.py`
comprobó con Snap7 en Docker la regla ON/OFF, una compuerta BOOL AND y un TON que
respeta su preset y resetea la salida al bajar la entrada. `process_pipe_smoke.py`
pasó de nuevo contra un backend aislado: verificó 40 L/min, una pérdida de
16 kPa y lectura de 84 kPa en el sensor conectado al runtime Docker.
`packet_capture_smoke.py` capturó una lectura Snap7 y
exportó un PCAP válido con TCP/102; `deployment_bundle_smoke.py` levantó el Compose
exportado y comprobó que sobrevive al borrado del escenario de origen. Son
evidencias locales puntuales, no un resultado de CI ni una validación de los
previews.

En la continuación de RF-19/RNF-02 se añadió jitter de respuesta determinista, uniforme o normal en S7 y Modbus TCP, con semilla reproducible y límites configurados; los escenarios anteriores conservan el perfil determinista por defecto. El smoke de protocolo Modbus con PyModbus estándar pasó 40 lecturas por cada perfil estadístico contra el servidor real. En RF-20, el runtime ordena redes acíclicas por dependencia y las propaga en una pasada; mantiene el ciclo tanque→válvula→tanque mediante el bucle iterativo limitado existente. El ciclo se detecta, pero no tiene criterio de convergencia ni cálculo de presión de lazo. La verificación completa actual pasó 351 pruebas backend y 90 PLC. En esa revisión aún faltaba latencia configurable de ruta, ahora cubierta al final de este documento; siguen sin modelarse latencia por paquete, variación del scheduler ni tiempo real.

**RF-09/22 · memoria SR (2026-10-08).** Se añadió una memoria BOOL SET/RESET
configurable con prioridad RESET en el mismo scan. El inspector la configura,
el esquema resuelve y valida sus señales, y el mapa/API protegen su salida frente
a escrituras externas. Pasaron 92 pruebas del runtime PLC y 74 backend enfocadas;
`integration/control_logic_smoke.py` pasó en Docker con Snap7 y verificó que
SET activa, la salida se conserva al retirar SET, RESET la desactiva y RESET
domina cuando ambas entradas están activas. El preview 8008 se actualizó desde
este checkout y los endpoints HTTP principales respondieron 200. La memoria es
volátil por defecto; `retain_logic_state` permite conservarla al recrear el PLC.
Este bloque no convierte Anvil en un editor Ladder/ST ni en runtime IEC 61131-3.

**RF-09/22 · temporizador TOF (2026-10-08).** El bloque de temporización admite
ahora TON (retardo a la conexión, predeterminado y compatible con escenarios
anteriores) y TOF (salida inmediata y retardo al bajar la entrada, cancelable si
la entrada vuelve a subir). Se configura desde el inspector y se valida en el
modelo/API; el estado del retardo puede restaurarse con la opción de retención del
PLC. Las pruebas PLC verifican transición, cancelación, retención y modo inválido;
las pruebas del adaptador confirman que el modo TOF llega a la configuración de
runtime. La temporización sigue limitada al reloj del contenedor y a cada scan.
La suite PLC completa pasó 103 pruebas y el adaptador backend pasó 35; el smoke
Docker/Snap7 verificó TON y TOF en un PLC desplegado localmente. El preview 8008
se actualizó a `anvil/plc-s7:srs-tof-20261008` y
`anvil/backend:srs-tof-20261008`, conservando su volumen de datos. 8003 y 8005
respondieron HTTP 200 antes y después; no había contenedores de escenarios
activos durante la actualización.

**RF-36 · consulta agregada SCADA (2026-10-08).** Se añadió
`GET /history/aggregate`, con rango de hasta siete días, 1.000 intervalos y
100.000 observaciones por consulta. Devuelve media/mínimo/máximo para lecturas
numéricas de calidad `good`, conteos de calidad y estados BOOL; el inspector usa
la media por intervalos de cinco segundos en una ventana de diez minutos. El
endpoint y el filtrado de permisos se cubren en `test_scada.py` y la gráfica en
`test_frontend.py`; pasaron los 385 tests backend y la prueba de matriz SRS. El
runtime actual se desplegó en 8008 como
`anvil/backend:srs-scada-aggregate2-20261008`; `/api/ready` dio `ready` con base
y Docker `ok`, se conservó `anvil-demo_demo-data` y 8003/8005 siguieron HTTP 200.
La falta de muestras durante más tiempo que el buffer SCADA sigue siendo una
limitación documentada.

**RF-09/22 · secuenciador BOOL (2026-10-08).** Se añadió al mismo runtime un
secuenciador one-hot configurable entre 2 y 16 salidas distintas. Cuenta flancos
ascendentes, sostiene el paso terminal y aplica RESET prioritario para volver al
primero; el inspector permite ordenar pasos y editar sus salidas. Las pruebas
unitarias verifican avance por flanco, salida one-hot, saturación terminal,
reset y conflicto de entradas; el smoke Docker/Snap7 también comprueba esos
casos. La posición es volátil al reiniciar por defecto; `retain_logic_state`
permite restaurarla. Ladder/ST y el diagnóstico de calidad de las entradas
siguen pendientes.

## Leyenda

- **Hecho:** hay código y una prueba o flujo operativo que lo respalda.
- **Parcial:** existe una parte, con límites indicados.
- **Pendiente:** no existe una implementación que cumpla el requisito.
- **Futuro:** el propio SRS lo excluye de esta versión.

## Requisitos funcionales

| Req. | Estado | Implementación actual / brecha |
|---|---|---|
| RF-01 | Parcial | Nodos OT y de campo editables. Se crean enlaces de señal, proceso y Ethernet. La acción **Migrar campo a copia** convierte componentes PLC S7 heredados en objetos de campo y canales `io_channel`, manteniendo direcciones y reglas sin alterar el origen; `test_preview_keeps_original_and_addresses`, `test_migration_creates_independent_copy_and_preserves_s7_addresses_and_logic` y `test_migration_preserves_both_pump_addresses_and_flow_parameters` cubren el contrato. La vista previa es de solo lectura e indica el endpoint POST disponible. La migración requiere topología compatible en una zona. Con un switch, los enlaces de cada nodo al switch se validan como miembros del segmento L2 compartido del bridge Docker; no hay filtrado por puerto. Sin switch, los enlaces Ethernet son descriptivos. |
| RF-02 | Hecho | Inspectores por tipo para PLC S7, Modbus, HMI, dispositivos virtuales y objetos de campo. |
| RF-03 | Parcial | Enlaces de señal PLC-campo, proceso físico, Ethernet, control S7 y rutas TCP se distinguen con estilos propios. Con switch, los enlaces al switch representan pertenencia al bridge L2 compartido y se validan; sin switch son descriptivos. `process_map_ui.cjs` verifica estilos y persistencia de la topología. No hay filtrado por puerto ni routing IP genérico. |
| RF-04 | Parcial | Validación de esquema/IP/canales al guardar y revalidación del modelo persistido en el endpoint de despliegue; errores almacenados o legacy devuelven HTTP 422 antes de fijar imágenes o invocar Docker (`test_deploy_revalidates_stored_topology_before_touching_docker`). Los avisos de topología siguen consultivos y la interfaz pide confirmación. Las señales estáticas comprueban tipos, unidades y compatibilidad de rangos declarados. Los canales REAL S7 y registros UINT16 Modbus admiten límites opcionales explícitos; lecturas HMI/SCADA/API marcan `out_of_range`, preservan calidad protocolaria y rechazan escrituras fuera de límites, sin alterar el proceso en las lecturas. Unitarios/API cubren límites y valor inicial, lecturas dentro/fuera, calidad mala, rangos omitidos y escritura Modbus fuera de rango. Los límites Modbus aplican al entero raw; no existe conversión de escala. El inspector define límites solo en registros y oculta esos controles para coils/entradas discretas. `range_diagnostics_ui.cjs` verifica configuración y guardado desde UI de rangos S7 REAL y Modbus UINT16. Se corrigió además un fallo del inspector que descartaba `options` de los campos select y una carga de escenario que dejaba obsoleto el selector de dispositivo. El preflight avisa de HMI sin destinos, actuadores/sensores de campo desconectados, puertos de proceso incompletos y fragmentos hidráulicos que no forman un camino continuo desde una fuente a un depósito receptor o válvula terminal; los enlaces de medida de sensores no cuentan como tubería. Los avisos son consultivos: el escenario se puede guardar y el endpoint mantiene `valid=true` si no hay errores de esquema. `process_map_ui.cjs` verifica la topología continua de fábrica y una rama fragmentada. También avisa de zonas aisladas y actuadores sin señal de mando. Para salidas PLC S7 sin lógica, contrasta individualmente el destino de cada bloque (reglas analógicas, lógica BOOL, TON, SR, CTU y pasos de secuencia) con cada actuador heredado o canal independiente enlazado a `command`; avisa explícitamente que el mando manual sigue disponible. Los avisos no bloquean el guardado ni el despliegue. `test_topology_diagnostics_warns_when_independent_actuator_has_no_plc_logic`, `test_topology_diagnostics_accepts_independent_actuator_driven_by_sequence` y `test_topology_diagnostics_checks_each_legacy_actuator_output` cubren salidas sin lógica, controladas por secuencia y actuadores heredados ajenos a otro bloque. Comprueba alcanzabilidad declarada para destinos HMI/SCADA S7/Modbus, remote_input S7 y señales PLC↔campo interzona; detecta rutas deshabilitadas y rutas TCP genéricas que no configuran un cliente de protocolo. En escenarios desplegados contrasta contenedores/healthchecks y sondea cada ruta habilitada desde su origen. Para cada binding PLC↔campo contrasta dirección, objeto y puerto con el estado del bridge del PLC, además de exigir un intercambio correcto en los últimos 2 s. La interfaz presenta resultado por binding y advertencia de rango en HMI/SCADA. Comunicaciones no modeladas siguen pendientes. |
| RF-05 | Hecho (JSON + revisiones) | CRUD, clonado, importación/exportación `schema_version: 2`, migración explícita v1→v2 para completar `network_connections` y revisiones inmutables al guardar cambios, con listado, detalle y restauración auditada. Los documentos legacy sin sobre se interpretan como v1; no hay YAML ni migración de versiones futuras aún no definidas. |
| RF-06 | Parcial | El escenario define zonas Purdue L0–L5/DMZ. Rutas S7/Modbus, PLC receptor→emisor S7, campo HTTP y TCP genéricas por origen/destino/puerto unen zonas mediante proxies por flujo explícitos, restringidos al IP del nodo origen y bloqueables. El routing se limita a esos flujos declarados, no es un router IP genérico; el motor de campo conectado vive en una zona por escenario. |
| RF-07 | Hecho (biblioteca integrada) | Cinco plantillas empaquetadas y plantillas compartidas publicadas por admin/instructor, con creación directa de escenarios detenidos desde la interfaz/API. Las plantillas compartidas se gestionan según RF-43. |
| RF-08 | Parcial | PLC S7, Modbus TCP/RTU, SCADA de adquisición, HMI básica, gateway representativo, estación de ingeniería y switch Ethernet lógico asociado al bridge L2 interno Docker de su zona, con capacidad de puertos validada. `ethernet_switch_smoke.py` prueba intercambio TCP entre dos contenedores del segmento. No simula VLAN, STP ni routing IP. El VFD no modela motor eléctrico ni curva par/velocidad. |
| RF-09 | Parcial | El runtime ejecuta ON/OFF con histéresis, lógica AND/OR/XOR con contactos NO/NC, SR con prioridad RESET, TON y TOF, CTU por flanco ascendente y secuenciador BOOL one-hot de 2–16 pasos. El inspector ofrece `retain_logic_state` por PLC: conserva memoria ON/OFF, SR, tiempo TON/TOF (pausado durante la parada), valor y flanco previo CTU y paso/flanco previo del secuenciador en un volumen Docker gestionado e individual por escenario/nodo. La huella de configuración descarta estados incompatibles; al detener/recrear el PLC se conserva el volumen y al borrar el escenario se elimina. El Docker proxy permite exclusivamente ese montaje en el PLC S7 que lo solicita y filtra volúmenes por propiedad Anvil. `plc.tests.test_control_logic`, `plc.tests.test_retained_state`, `docker_proxy.tests.test_policy`, `backend.tests.test_docker_manager` y `integration/retained_state_smoke.py` verifican comportamiento y recreación real. Es un modelo del simulador, no memoria real certificada. Entradas remotas S7 admiten `stale_behavior=hold` (por defecto) o `force_off`; esta última desenergiza las salidas de bloques dependientes durante `waiting/stale` y reinicia sus estados. No hay editor Ladder/ST ni runtime IEC 61131-3. |
| RF-10 | Parcial | DB, marcas M y áreas de proceso I/Q S7 permiten canales BOOL/REAL con offsets automáticos independientes por área o byte/bit explícitos en `io_channel`; el backend rechaza solapes, REAL desalineados, bits sin byte y canales que exceden `db_size`. El inspector permite configurar estos offsets; `test_api.py` confirma guardado, lectura del mapa M4.3/QD6 y rechazo HTTP 422 por colisión. El mapa API/UI, HMI/SCADA y enlaces de campo usan la dirección resuelta. I es de solo lectura desde HMI/SCADA; las salidas Q/M y canales DB autorizados son escribibles. Snap7 registra área y bytes de operaciones DB/M/I/Q; el endpoint y panel de protocolo muestran las cuatro áreas. Los componentes heredados y bloques ON/OFF/TON/BOOL siguen autoasignados en DB; no se emula direccionamiento simbólico TIA ni todo su catálogo de temporizadores. Los cuatro espacios Modbus (coils, discrete inputs, holding e input registers) también son configurables en TCP y RTU. |
| RF-11 | Parcial | Bomba, variador, depósito, válvulas on/off/proporcional, sensores y bloques de distribución/unión comparten el balance determinista. El mando REAL del VFD escala velocidad/caudal; bomba/VFD admiten presión nominal configurable; la pérdida cuadrática de tuberías limita el caudal. La capacidad de la válvula on/off se limita al caudal nominal y a cero cerrada; la proporcional aplica caudal nominal × apertura y limita también el flujo de origen, para conservarlo a través del actuador. `test_closed_valve_limits_upstream_pump_flow_to_zero`, `test_proportional_valve_capacity_scales_upstream_flow_and_is_conserved` y `test_parallel_closed_valve_branch_does_not_consume_pump_flow` cubren el runtime. `integration/field_valve_balance_smoke.py` pasó el 2026-10-08 contra `anvil/field:rf11-valve-flow` construido desde el checkout, en un contenedor Docker aislado (`network_mode=none`) y sin publicar puertos; verificó cierre, apertura proporcional al 40 % (24 L/min) y conservación de 50 L/min en una rama abierta y otra cerrada. Las bifurcaciones directas conservan caudal y lo reparten según capacidad; el distribuidor explícito divide por porcentaje configurable y el colector suma sus dos entradas. `process_pipe_smoke.py` verifica en Docker presión/caudal en serie, ramas paralelas y distribuidor→ramas→colector→depósito. Siguen faltando solver general de redes, curvas reales de bomba/válvula, geometría y transitorios; ver [FIELD_OBJECTS.md](FIELD_OBJECTS.md). |
| RF-12 | Parcial | HMI dispone de una vista de operador independiente del inspector, con selección de HMI del escenario, refresco cada 2 s, tarjetas de señales y alarmas, escritura de salidas S7 BOOL/REAL y coils/holding registers solo cuando el mapa y rol autorizan, reconocimiento de alarmas propias y del SCADA asociado en la misma zona y sinóptico gráfico del proceso. Alarmas propias se configuran por tag seleccionado BOOL (estado esperado) o numérico REAL/UINT16 (umbral superior/inferior); el backend las evalúa solo con valores leídos por protocolo desde esa HMI. Cada regla HMI permite configurar `delay_ms` (0 por defecto, hasta 24 h): el estado solo se activa tras observar continuamente la condición con calidad válida; una lectura inválida reinicia una condición aún pendiente. El avance del retardo depende del ciclo de sondeo de la HMI, no de un temporizador independiente. Estado, condición pendiente y reconocimiento persisten en SQLite; transiciones activada/normalizada se conservan y aparecen en el panel (últimas 50). Durante mala calidad, se conserva estado activo como obsoleto y se deshabilita reconocimiento. El sinóptico dibuja conexiones físicas y solo muestra como telemetría tags PLC/Modbus leídos por esa HMI; los objetos sin tag leído indican “Sin lectura PLC asociada”. Una salida REAL S7 o holding register Modbus con escala explícita puede accionar una válvula proporcional. La lectura se reconcilia desde `backend/static/hmi-live-view.js`: conserva los nodos de tarjetas/sinóptico, foco y consignas no enviadas entre ciclos. `integration/workspace_ui.cjs` prueba valores actualizados, identidad DOM, foco y borrador; `test_frontend.py` valida el módulo servido. Siguen faltando expresiones complejas, retención configurable y prueba física con HMI comercial. |
| RF-13 | Parcial | Contenedor SCADA sondea puntos S7/Modbus en su zona, evalúa umbrales con retardo configurable (0 por defecto; máximo 24 h) y expone lectura/tendencia/reconocimiento de alarmas. La condición debe persistir en muestras con calidad válida y una lectura normal/errónea reinicia el retardo pendiente; la precisión depende del periodo de sondeo. Los inicios de retardo pendientes se persisten en SQLite y se restauran en el runtime al consultar alarmas después de recrearlo; el siguiente sondeo vuelve a validar condición y calidad. La adquisición escribe en una cola SQLite durable, en un volumen Docker exclusivo del nodo, configurada entre 1.000–100.000 muestras y 16–512 MiB de payload JSON (100.000/128 MiB predeterminados; el archivo SQLite requiere espacio extra); el backend recoge lotes de hasta 10.000, persiste con clave idempotente y confirma el cursor tras el commit mediante token efímero. La cola sobrevive a caída del backend y recreación del contenedor; si alcanza su límite descarta las muestras pendientes más antiguas y expone el contador de pérdidas en el snapshot/UI. El historial durable del backend sigue limitado a 1.000.000 de observaciones por escenario. Alarmas activas y reconocimiento se conservan en SQLite y se limpian solo al observar una lectura válida normal. Un arranque aún sin primer sondeo o un error de calidad conserva el último estado activo marcado como obsoleto; una condición pendiente se descarta si la calidad es mala. La cola durable no evita pérdidas si se llena ni mide mientras SCADA o la fuente están detenidos. El panel/API permiten escribir salidas S7 BOOL/REAL, coils y holding registers autorizados con mapa de E/S, firewall y auditoría; un holding register escalado acciona una válvula proporcional. El histórico se descarga en CSV desde el inspector/API, incluso detenido. La HMI incluye sinóptico del circuito físico limitado a tags leídos desde sus destinos configurados. Faltan expresiones complejas de alarma y cliente OPC UA. |
| RF-14 | Hecho · mecanismo integrado documentado | [`EXTENDING_DEVICES.md`](EXTENDING_DEVICES.md) documenta cómo añadir un tipo de nodo mediante adaptador registrado, esquema/descriptores de propiedades, runtime Docker, módulo de UI, permisos/API, gateway y pruebas. El backend descubre adaptadores empaquetados y `DeviceInput` selecciona su esquema sin cambiar el modelo común. El registry rechaza en importación adaptadores abstractos, esquemas públicos que no sean Pydantic, metadata de catálogo vacía o declaraciones de capacidad mal formadas; `DeviceRegistryContractTests` verifica estos rechazos antes de mutar el registry. `test_documented_device_extension_is_visible_and_creatable_through_api` verifica catálogo y creación de un adaptador temporal por API; `test_registered_device_config_schema_is_selected_without_changing_device_input` cubre selección de esquema y construcción de runtime. El catálogo publica `protocols`/`capabilities` declarados, pero esos metadatos no prueban fidelidad. El contrato satisface el mecanismo documentado del SRS mediante código integrado y empaquetado; no hay instalación dinámica de terceros, hot reload ni sandbox de plugins. |
| RF-15 | Parcial | S7/Snap7, Modbus TCP y tramas Modbus RTU sobre pseudo-terminal 8N1 con túnel raw TCP sin MBAP; TCP y RTU atienden FC01–06, FC15/16, FC22 Mask Write Register y FC23 Read/Write Multiple Registers para coils, discrete inputs y registros. Los smokes TCP y RTU los verifican con clientes PyModbus estándar. No emula señal eléctrica RS-485, bus multi-drop ni errores de cable. |
| RF-16 | Parcial | Clientes PyModbus sin modificar comprueban TCP y RTU FC01–06, FC15/16, FC22 y FC23; excepciones 2/4, cierre y latencia se verifican en TCP. El smoke RTU usa cliente serie en otro contenedor sobre túnel `socket://` y cubre operaciones gestionadas de Anvil, HMI y adquisición SCADA. No se ha caracterizado equivalencia amplia frente a hardware real ni cliente físico serie. |
| RF-17 | Parcial | Modbus TCP permite configurar unit ID, puerto, retardo fijo y jitter (0–10 000 ms), perfiles determinista/uniforme/normal con semilla, excepción Device Failure o desconexión inducida; integración Pymodbus verifica códigos 2/4, cierre, retardo+jitter determinista y secuencia reproducible de fallos a probabilidad configurada. S7 admite perfiles de jitter, modo `timeout` y probabilidad/semilla reproducible para suprimir respuestas a lectura/escritura sin impedir el establecimiento de sesión; `s7_fault_smoke.py` en Docker con Snap7 verifica ambos. Siguen sin emularse una caída persistente de sesión S7 ni temporización de red de tiempo real. |
| RF-18 | Parcial | Los adaptadores declaran metadatos `protocols`/`capabilities` en el catálogo API para hacer visibles las capacidades implementadas; hoy incluyen S7, Modbus TCP/RTU virtual y clientes S7/Modbus de SCADA. El registro y la guía de extensiones facilitan incorporar código revisado. OPC UA, DNP3, IEC-104, EtherNet/IP, Profinet e IEC 61850 no tienen runtime ni operaciones y no se ofrecen como soportados; no existe plugin externo ni descubrimiento dinámico. |
| RF-19 | Parcial | Ciclo nominal S7 configurable por PLC entre 10 y 5000 ms; retardo fijo y jitter acotado de respuesta S7/Modbus TCP configurables entre 0 y 10 000 ms. El jitter permite secuencia determinista (predeterminada), distribución uniforme entre cero y el máximo, o normal centrada en la mitad del máximo con desviación máximo/6 recortada a sus límites; la semilla permite repetir muestras entre runtimes. Unitarios verifican límites, semilla y distribución de muestras, y los inspectores permiten configurar perfil y semilla. Cada ruta TCP explícita admite retardo fijo por sentido (0–10 000 ms, predeterminado 0), aplicado por bloque reenviado por el proxy; no representa latencia por paquete ni tiene precisión de tiempo real. El sondeo SCADA es configurable. RTU admite baudios nominales para calcular temporización de trama, sin señal eléctrica temporizada. No se modelan jitter del scheduler ni latencia general fuera de las rutas declaradas. |
| RF-20 | Parcial | Balance de volumen por caudal/tiempo, presión estática en depósitos, presión nominal de bomba/VFD, pérdidas cuadráticas en serie, reparto paralelo simple y bloques de flujo que dividen/suman caudal conservando masa. Sensores constant/sine_wave admiten `noise` y semilla reproducible por sensor, para repetir valores de instrumentación no ligada al proceso. `process_pipe_smoke.py` despliega en Docker una red con distribuidor, dos ramas, colector y depósito, verificando los caudales en vivo. `test_nested_splitters_conserve_flow_with_unordered_connections` comprueba dos niveles de distribución, dos colectores, límites de tubería, reasignación de capacidad y conservación de 50 L/min incluso si los enlaces se entregan fuera de orden. `field_noise_seed_smoke.py` comprueba que dos runtimes Docker aislados entregan la misma secuencia de seis lecturas con igual semilla. No es solver nodal general: faltan presión de red acoplada al colector y dinámica distribuida. Los retornos cíclicos mantienen propagación iterativa acotada, sin criterio de convergencia ni cálculo de presión de lazo. |
| RF-21 | Parcial | El catálogo incluye bombas, variadores, depósitos, tuberías, válvulas, sensores, distribuidor con `split_ratio_pct` y colector de dos entradas. La tubería define `max_flow_lpm` y `pressure_drop_kpa`; en serie el origen se limita por `sqrt(cabeza disponible / suma(K))`. Las bifurcaciones directas distribuyen caudal entre rutas según límites/pérdidas. El VFD conserva modelo lineal de caudal y cuadrático de presión, sin curva física. Faltan geometría, solver general, reactor y editor de ecuaciones/modelos. |
| RF-22 | Parcial | Los bloques ON/OFF, AND/OR/XOR, SR, TON, CTU y secuenciador ejecutan decisiones por scan; sus órdenes BOOL pueden controlar salidas/proceso. `integration/control_logic_smoke.py` prueba por Snap7 AND/NC, SR, TON, CTU y secuencia one-hot. El ruido analógico ahora puede repetirse entre runtimes con `simulation.seed`, mientras cada sensor mantiene una secuencia independiente; `test_seeded_sensor_noise_repeats_and_uses_independent_sensor_streams` verifica reproducibilidad y separación. `field_noise_seed_smoke.py` confirma por `/exchange` que dos contenedores de campo aislados reproducen la misma secuencia que consume un dispositivo conectado. El scheduler no es tiempo real y las señales de proceso y lógica usan ciclos software independientes, por lo que no se afirma sincronización determinista estricta entre runtimes. |
| RF-23 | Parcial | Forzado/fallo y válvula bloqueada afectan el balance; sensores analógicos/de estado pueden congelar la última medida y el mapa marca la señal con calidad `fault`; el ejercicio admite programar la inyección/restauración. El depósito admite fuga configurable y demanda inicial (L/min), y ambas tasas pueden cambiar en ejecución mediante controles auditados o programarse como incidentes de ejercicio; fijar `leak_flow_lpm: 0` cierra la fuga. El inspector ofrece la acción, el API valida depósito/caudal finito no negativo y el mapa muestra la tasa dinámica. `test_tank_leak_can_be_injected_and_restored_while_running`, `test_live_tank_demand_control_is_forwarded_and_audited` y `test_timed_field_control_events_inject_and_clear_process_incidents` cubren runtime, API/auditoría y cronología; se rechazan fugas en objetos que no sean depósitos. Nivel, presión estática, presión de descarga, pérdida configurada en tubería y caudales se actualizan y muestran en el mapa. No hay presión distribuida, golpe de ariete ni escenarios de fallo físico avanzados. |
| RF-24 | Hecho | Los adaptadores compilan dispositivos gestionados a contenedores Docker. |
| RF-25 | Parcial | Redes internas por zona con gateway `isolated` y rutas de protocolo/TCP por origen/destino/puerto crean proxies sin privilegios que enlazan dos zonas; las rutas se pueden bloquear completas. El switch identifica el bridge L2 de su zona; se validan capacidad y enlaces de cada nodo, y el smoke prueba tráfico entre contenedores. No hay VLAN, STP, aislamiento de puertos ni routing IP general. |
| RF-26 | Parcial | IP estática y hostname DNS configurable o derivado del ID, unicidad por zona y alias en la red Docker interna. `GET /api/scenarios/{id}/services` enumera listeners efectivos derivados de los adaptadores: S7 TCP/102, Modbus TCP en el puerto configurado, túnel RTU raw-TCP, API HTTP interna SCADA/8766 y binding host opcional; etiqueta scope industrial/interno y no anuncia HMI client-only como servidor. El inspector consume ese endpoint y muestra endpoint, protocolo, transporte, alcance, binding de host y el aviso de inventario potencialmente desactualizado en un escenario modificado (`test_runtime_frontend` y `test_frontend`). La validación rechaza puertos publicados duplicados antes de guardar (`test_duplicate_external_service_ports_are_rejected_during_scenario_validation`) y Docker mantiene una segunda comprobación. El inventario describe configuración, no salud; healthcheck y diagnóstico siguen siendo verificaciones separadas. Anvil aún no tiene un descriptor general para cualquier servicio arbitrario ni reglas de red arbitrarias. |
| RF-27 | Parcial | El ciclo de vida cubre deploy/start, stop, restart del escenario completo, pause/resume, delete y start/stop/restart/pause/resume/recreate por nodo. El restart completo usa lease por escenario, verifica que estén presentes todos los runtimes planeados, reinicia en orden determinista, espera healthcheck, registra auditoría y actualiza el estado observado; `test_scenario_restart_restarts_every_planned_container_and_waits_for_health`, `test_scenario_restart_refuses_to_restart_a_partial_container_set` y las pruebas API/UI cubren el contrato. El reconciliador periódico registra intenciones durables `recreate` para nodos ausentes y reconstruye redes Docker Anvil ausentes para escenarios con intención activa (`running`, `partial` o `error` tras una última transición desde un estado activo). Solo crea redes cuyo nombre requerido no esté ocupado y etiqueta el resultado con el ID/zona exactos del escenario; luego recupera nodos bajo lease y journal. `integration/node_recovery_smoke.py` pasó en Docker Engine 29.8.1: eliminó un PLC, luego ambos PLC y la red administrada, y verificó healthcheck, estado `running`, journal, auditoría, preservación del peer en pérdida parcial, red interna reconstruida y ausencia de puertos publicados. `test_network_recovery_preserves_foreign_name_collision` verifica que una red con el nombre requerido pero de otro escenario no se elimina ni se reemplaza; la reconciliación conserva el error para reintento/resolución. Un `error` precedido por `deploying` no se relanza. Los escenarios `stopped` permanecen detenidos. Las subredes que colisionan con recursos externos requieren intervención; Docker y SQLite no forman una transacción distribuida. |
| RF-28 | Parcial | Estado y salud se reflejan sobre los nodos; las razones del healthcheck fallido se muestran en el inspector/mapa. CPU, memoria del cgroup y procesos se consultan cada ~3 s desde Docker y la API devuelve `resource_metrics_sampled_at` UTC más `resource_metrics_status` (`good`, `unavailable`, `not_running`, `missing`, `not_applicable`); la UI indica antigüedad/calidad, no sustituye la ausencia por cero. Los snapshots WebSocket convierten de forma segura las marcas `datetime` a ISO-8601; `test_snapshot_encodes_docker_metric_timestamps_as_json` cubre la regresión de un error observado en runtime. Cada despliegue valida el total de nodos y los límites de CPU/memoria configurados por escenario antes de crear redes. El host puede no proporcionar algunas métricas; faltan alertas y coordinación del presupuesto entre escenarios simultáneos. |
| RF-29 | Parcial | `integration/docker_network_smoke.py` compara en dos despliegues Docker consecutivos la imagen, configuración, límites, endurecimiento, IP, DNS y redes; también modifica valores con clientes Snap7/PyModbus y comprueba tras redeploy los valores iniciales de los canales S7 DB/M/I/Q configurados y los cuatro espacios Modbus (coil, discrete input, holding register e input register). El 2026-10-08 pasó contra el backend del checkout, una API/SQLite temporal y Docker Engine 29.8.1; las imágenes S7 (`sha256:aafa9025c5c4a026e2fb244dca359fd9388e455cb6b59d52eef8e8a2166becc9`) y Modbus (`sha256:0514767322503bef160aeb3a418598490de43b7b635e3c90ba1978c4e15112e0`) se construyeron de este checkout. El workflow CI también ejecuta este smoke. No compara el estado interno completo del proceso ni equivalencia entre hosts/arquitecturas. |
| RF-30 | Parcial | Descarga ZIP con Compose, JSON declarativo, `.env`, inventario de imágenes y scripts para exportar/verificar imágenes y reasignar redes. Cada subred, IP estática y puerto host publicado tiene un override Compose en `.env`; `readdress.py` preserva offsets, verifica que las IP resultantes sean utilizables y que los segmentos no se solapen, y solo actualiza `.env` tras validar. El smoke Docker mantiene activo el escenario Anvil mientras inicia el bundle en otra subred interna, verifica el IP reasignado y confirma que el bundle sobrevive a la eliminación del original. Compose usa `pull_policy: never`, nombres de proyecto y labels independientes. Las capas se transfieren aparte y backend/SQLite/estado vivo no forman parte del bundle; el operador todavía debe elegir un CIDR libre y revisar puertos/políticas del host. |
| RF-31 | Parcial | Perfiles JSON versionados (`anvil-device-profile`, schema 1) por dispositivo, con configuración Anvil y mapa de direcciones resuelto; se pueden reimportar a un escenario detenido con una nueva identidad/IP/zona. El mapa se recalcula al importar y no se confía en la copia del archivo. [`DEVICE_PROFILES.md`](DEVICE_PROFILES.md) documenta la API y la UI. Faltan perfiles de fabricante y correspondencia validada con hardware real. |
| RF-32 | Parcial · loopback y bundle Compose protocolariamente verificados; acceso remoto pendiente | `integration/external_protocol_probe.py` pasó con Docker Engine 29.8.1 y clientes Snap7/PyModbus desde Linux del mismo host: COTP y lectura/escritura S7, más lectura/escritura coil Modbus por `127.0.0.1` y puertos efímeros. También pasó con el manager usando una instancia temporal, loopback-only, del código actual de `DockerGate`. `integration/external_bundle_smoke.py` exporta y despliega el bundle Compose con relays, realiza lectura/escritura DB S7 y coil Modbus por loopback, confirma que los destinos no publican puertos y que un peer no autorizado es rechazado. En su repetición con Python estándar de Windows, la sonda alcanzó COTP S7 y leyó/escribió coil Modbus a false/true vía forwarding WSL; no hizo lectura/escritura DB S7 desde Windows. El gateway Compose de los previews no se sustituyó ni reinició. No se verificaron otra máquina, binding en interfaz LAN ni firewall. |
| RF-33 | Parcial | [`DEVICE_FIDELITY.md`](DEVICE_FIDELITY.md) describe PLC S7, Modbus TCP, SCADA/HMI, proceso y nodos auxiliares, separa implementación, límites y evidencias, y enumera las funciones Modbus verificadas: FC01–06, FC15/16, FC22 y FC23. Falta caracterizar más servicios S7 y contrastar emuladores con herramientas/equipos industriales externos. |
| RF-34 | Parcial | Captura PCAP por dispositivo, entre una pareja de dispositivos del mismo segmento (filtrada por la IPv4 del peer) o del bridge Docker interno de un segmento. Sidecar de UID 0, raíz de solo lectura, sin mounts ni puertos, capacidades mínimas `NET_RAW`/`SETUID`/`SETGID`, máximo 40.000 paquetes con snapshot de 128 bytes y exportación de 8 MiB. La política Docker valida ambos endpoints en el mismo escenario/red compartida y limita el comando al filtro exacto del peer; `network_mode=host` solo se admite para el perfil fijo del bridge interno Anvil y el sidecar no admite `exec`. El smoke Docker con dos PLCs usa `remote_input` para generar tráfico, verifica TCP/102 y la IPv4 del peer en el PCAP, y pasó a través de un gateway temporal local con allowlist restringida. `evidence_bundle_ui.cjs` pasó en Microsoft Edge contra API/SQLite efímeras con bootstrap y sesión autenticada de administrador: verifica selección de extremos de la misma zona, exclusión predeterminada de una captura activa y descarga del ZIP real desde el endpoint con HTTP 200, hash de manifiesto y archivo no vacío. La prueba API `test_evidence_bundle_download_is_limited_to_admin_and_scenario_instructor` verifica que admin/instructor obtienen el ZIP y que designer/participant reciben 403; no convierte la prueba del navegador en prueba de las otras sesiones. El contenido e integridad del ZIP se comprueban además mediante prueba API/CLI. El run remoto [`35992883214`](https://github.com/marcosfnmr/Anvil/actions/runs/35992883214) pasó, pero corresponde al workflow publicado en `12a51da`; no incluye las pruebas de Docker proxy/runtimes/browser del workflow local modificado. Este último aún no tiene resultado remoto. Requiere Docker Engine Linux que exponga el bridge para capturar segmentos; no captura enlaces físicos externos ni se ha verificado en Docker Desktop/rootless. |
| RF-35 | Parcial | Auditoría SQLite durable para creación/edición/estado, transiciones observadas por nodo, escrituras S7/Modbus/HMI, controles de campo, denegaciones, capturas, sesiones de terminal de participante/ingeniería con contadores agregados (sin texto de entrada) y transiciones de calidad protocolaria S7/Modbus iniciadas por API, HMI, snapshot SCADA y worker SCADA. Registra primera lectura y cambios good/degraded/failed sin duplicar sondeos ni guardar valores. PLC S7 y Modbus TCP/RTU escriben metadatos de escritura y errores en una cola SQLite por dispositivo, conservada en un volumen Docker independiente: máximo 100.000 eventos/32 MiB y 4 KiB por línea. Cada 2 s el backend pide lotes mediante `exec` de comandos exactos `pending`/`ack`, restringidos por el gateway a ese runtime, volumen y UID 65534; el acuse necesita token efímero. Persiste eventos y cursor en la auditoría del backend antes de acusar el lote; el ID de cola deduplica una repetición tras una caída entre commit y acuse. La cola conserva eventos durante reinicio o indisponibilidad del backend. Al desbordarse descarta los más antiguos e incrementa un contador durable; el backend registra `protocol.audit_queue_overflow` cuando vuelve a recoger. Los runtimes antiguos sin cola mantienen el fallback a logs Docker en todos los estados que el daemon retenga, con cursor e idempotencia. La parada API y las acciones start/stop/restart/recreate vacían best effort mientras el contenedor existe; no arrancan equipos ni bloquean el ciclo de vida. El fallback legacy fija `json-file`, rotación de 10 MB × 3 ficheros, y puede perder eventos si rota antes de recoger. El mismo worker registra `process.field_state_changed` cuando observa cambios discretos de orden, estado, fallo u override en actuadores y sensores discretos; para VFD/válvulas proporcionales registra consigna cero/no cero y marcha/parada o cerrado/abierto. Incluye las señales PLC/Modbus conectadas al puerto `command` como contexto, no como causalidad probada. `integration/protocol_audit_queue_smoke.py` y `integration/s7_protocol_audit_queue_smoke.py` prueban las colas TCP y S7. `integration/modbus_rtu_smoke.py` valida RTU con cliente serie real, operaciones de Anvil, persistencia en la auditoría backend sin valores, acuse tras el commit y deduplicación al repetir la recogida; también comprueba lectura/escritura HMI y adquisición SCADA de los seis puntos. `integration/protocol_audit_queue_smoke.py` y `integration/s7_protocol_audit_queue_smoke.py` prueban además recreación de los runtimes TCP/S7 conservando sus colas. `integration/modbus_protocol_test.py` verifica que escrituras y respuestas de error reales entren a la cola y que lecturas correctas no se auditen. Los tests de gateway limitan `exec` a `pending`/`ack` y rechazan shell, token, UID y rutas inválidas. `integration/process_audit_smoke.py` se ejecutó el 2026-10-09 con imagen de campo recién construida y contenedor `network_mode=none`: comprobó en SQLite la orden/estado de válvula, el contexto de señal y la cadena de auditoría, mientras una lectura analógica sinusoidal cambiaba sin generar eventos. La primera muestra es línea base y las señales analógicas continuas siguen en histórico, no como eventos. El desbordamiento de cola deja un evento de pérdida cuantificado, pero no recupera sus datos; el fallback legacy puede tener huecos silenciosos. Modbus RTU identifica el origen como `rtu-serial`; las lecturas correctas individuales tampoco se auditan. El inspector S7 y Docker logs aún muestran una línea de diagnóstico con bytes, no replicada en auditoría. Los eventos se encadenan con SHA-256; `/audit-events/integrity` detecta cambios y la migración enlaza logs legacy. `ANVIL_AUDIT_RETENTION_DAYS` permite retención optativa (1–3650 días; 0/desarmado conserva todo), preserva eventos desde el inicio de ejercicios activos y mantiene un checkpoint de hash tras retirar el prefijo expirado. Al borrar un escenario se archivan eventos, checkpoint y acceso de propietario/membresías; solo quedan disponibles las rutas de auditoría y el resto del escenario da 404. No impide que quien controle SQLite recalcule la cadena, ni detecta truncado de cola sin ancla externa. No audita cada lectura ni todos los fallos de API. |
| RF-36 | Parcial | Telemetría de PLC/Modbus/campo en vivo; el SCADA mantiene una ventana interactiva en memoria y una cola SQLite durable de muestras aún no recogidas (hasta 100.000 muestras/512 MiB de payload JSON configurables; el archivo ocupa algo más por metadatos SQLite). El backend recoge lotes idempotentes y, con token efímero, confirma después del commit; caídas/recreaciones quedan cubiertas mientras haya capacidad durable disponible. Al desbordarse, el runtime descarta las muestras pendientes más antiguas y expone el contador de pérdidas. El backend conserva hasta 1.000.000 de observaciones por escenario. `/history/aggregate` resume por intervalos numéricos con calidad buena (media/mínimo/máximo), conteos de calidad y estado BOOL; el inspector escala el eje horizontal por timestamp y deja huecos ante intervalos defectuosos o ausentes, sin conectar ni interpolar lecturas. El histórico se exporta como CSV. Sigue parcial porque el almacenamiento de campo es acotado, se pierden muestras si se agota y no hay adquisición durante una parada de SCADA/fuente. |
| RF-37 | Hecho (v1) | Auditoría, logs recientes, histórico SCADA y PCAP seleccionado se asocian al `scenario_id` y se exportan juntos en ZIP con manifiesto e inventario SHA-256/tamaño. Admin/instructor descargan desde panel o API; se puede verificar offline el inventario y comparar un hash del manifiesto custodiado por separado. La captura se consume si se incluye. La firma digital y retención configurable de PCAP son mejoras operativas fuera de la redacción de RF-37; el alcance de segmento/enlace de captura se evalúa en RF-34. |
| RF-38 | Parcial | Se guardan y lanzan ejercicios con cronología de hasta 24 h, escrituras gestionadas, acción `protocol_write` que ejecuta Snap7/PyModbus desde una estación participante habilitada, acciones start/stop/restart, perturbaciones de proceso y corte/restauración programados de gateways de rutas explícitas. La escritura protocolaria solo apunta a salidas S7 BOOL/REAL o coils/holding registers Modbus; la estación y el target deben pertenecer al escenario, y un destino interzona requiere ruta TCP exacta a ese servicio. La auditoría `attack.protocol_write` atribuye origen, destino, punto, protocolo y ruta. Pruebas API/unidad validan origen, estación opt-in, destinos y rutas; `integration/exercise_smoke.py` verifica clientes Snap7/PyModbus desde la estación, cambios y objetivos de auditoría. Los objetivos `audit_event` admiten hasta ocho filtros escalares exactos; pausar/reanudar conserva índice y tiempo. El relay de intercepción está integrado al contenedor de ruta TCP para destinos Modbus: encuadra MBAP, correlaciona FC01/FC02/FC03/FC04/FC23 para sustituir una respuesta y reescribe solicitudes FC05, FC06, FC15, FC16 y FC22 antes de que lleguen al servidor; en FC23 conserva la petición combinada y permite reglas diferenciadas: `phase: read` altera solo el rango de lectura en la respuesta; `phase: write` altera solo registros seleccionados del rango escrito antes de enviar la petición al servidor. Ambas fases pueden coexistir en la misma dirección y se auditan como `response`/`request` sin conservar valores. FC05/FC15 alteran solo coils seleccionadas, aceptan reemplazo 0/1 y preservan el formato FC05 0x0000/0xFF00 y los bits vecinos/padding FC15; FC16 conserva registros no seleccionados. Cada coincidencia genera evidencia durable individual filtrable por rule_id. El control usa un socket Unix con token efímero y el Docker Gate solo permite `tcp_route_control.py` como comando no-root acotado. El 2026-10-09 las pruebas focales de parser, API/scheduler, auditoría, editor y Docker Gate pasaron 155/155 en la revisión FC16; tras FC15 pasaron 167 pruebas y, tras FC05, la suite combinada pasó 170/170; el smoke Docker verificó una escritura FC16 de [43, 44, 45] convertida en [51000, 53000, 45], con dos auditorías y objetivos separados aprobados. `integration/modbus_interception_smoke.py` también pasó con redes Docker internas y cero puertos publicados. El evento API `modbus_interception` programa acciones `set`/`clear`, valida estación opt-in, ruta, destino, Unit ID y registro/función declarados, y audita activación/limpieza; cancelar, completar o fallar intenta limpiar reglas activas. La duración se expresa mediante la distancia entre eventos set/clear, no mediante una propiedad dedicada. El editor permite definir FC01/FC02/FC03/FC05/FC06/FC15/FC16/FC22/FC23, incluida selección de coil y estado 0/1 para FC05/FC15. `integration/exercise_builder_ui.cjs` pasó el 2026-10-10 en Microsoft Edge con backend/SQLite efímeros y runtimes locales: configuró y guardó la política de peer IP, detectó una escritura Modbus no permitida, definió escrituras S7/Modbus y reescrituras FC03/FC05/FC15/FC16/FC22/FC23, ejecutó el ejercicio y observó los objetivos aprobados; el `finally` limpió el escenario de prueba. El parser/unitarios y `integration/modbus_interception_smoke.py` verifican FC05 y FC15; el smoke confirma reescritura de coil en una ruta Docker interzona, servidor modificado, evidencia durable y cero puertos publicados. El 2026-10-13 se añadió FC01/FC02 para sustituir bits seleccionados de lectura de coils/discrete inputs sin cambiar el servidor; 162 pruebas focales de parser, ejercicios, interfaz y Docker Gate pasaron, y el smoke Docker verificó estados antes/durante/después [1,0,1], evidencia durable, aislamiento interzona y cero puertos publicados. El 2026-10-13 se amplió FC23 con `phase: write`, validación de rango, preservación de registros vecinos y auditoría de petición; las pruebas focales pasaron y el smoke Docker actualizado está pendiente de ejecutar. Siguen pendientes otros incidentes del SRS y funciones distintas de las descritas en el diseño; tampoco hay MITM transparente. |
| RF-39 | Parcial | La estación permanece aislada por defecto. Al habilitarla, se conecta solo a una zona Docker interna, sin puertos publicados ni egress implícito. Un participant con membresía explícita abre desde el inspector una consola WebSocket de una hora, limitada a una sesión por estación/16 por worker y ejecutada como UID 65534. La auditoría registra apertura/cierre y, al cerrar, cuenta marcos de entrada, bytes, saltos de línea y duración; no guarda ni hashea el texto enviado, que puede contener contraseñas o tokens. `test_participant_terminal_is_limited_to_opted_in_station_and_relays_bytes` verifica el agregado y que no se filtra el contenido de la shell. El gateway envía EOF al `exec` al perder el WebSocket o expirar el túnel y libera su seguimiento; para `exec` no interactivos conserva la asociación mientras corren, la libera al observar `Running=false` y expira asociaciones inactivas tras cinco minutos. La aplicación reserva slots en SQLite compartido por workers, renueva cada cinco segundos y expira leases tras 20 segundos; `test_terminal_leases.py` comprueba unicidad entre workers, capacidad, renovación y caducidad. `participant_terminal_smoke.py` verifica limpieza con Docker real y `participant_terminal_ui.cjs` apertura, comando y cierre en navegador. Réplicas con bases separadas aún no coordinan cuotas. |
| RF-40 | Parcial | Se evalúan objetivos S7/Modbus, variables físicas, tendencia numérica de campo, eventos de auditoría, secuencias auditadas y secuencias ordenadas de condiciones de proceso sobre varios objetos. `field_sequence` admite 2–16 condiciones BOOL o numéricas, las muestrea en puntos de ciclo separados, persiste avance/marcas y reinicia al superar `max_gap_seconds`; pause/resume desplaza sus relojes. `field_trend` compara valores extremos en ventana de 2–300 s, exige cobertura y admite subida/bajada/estabilidad con delta. También admite `all`/`any`, score ponderado y duración mínima continua de hasta 24 h. Los objetivos `audit_event` pueden detectar operaciones directas observadas por protocolo (`protocol.s7_operation`, `protocol.modbus_operation`) y filtrar área/DB/offset S7 o función/espacio/dirección Modbus; el scheduler recoge las colas durables antes de evaluar ejercicios cortos. El objetivo `protocol_write_policy` compara escrituras nativas S7/Modbus TCP con una allowlist de IPv4 exactas por ejercicio y emite `security.protocol_write_policy_violation`; el test de unidad y `integration/exercise_smoke.py` verifican origen permitido/no permitido, idempotencia y detección real para ambos protocolos. La IP es el peer visto por el servidor (puede ser un gateway), no una identidad autenticada ni una regla que bloquee el tráfico. Se puede combinar con `point_value`/`field_value` para verificar el efecto físico modelado. El test `test_audit_event_can_detect_a_specific_native_s7_or_modbus_write` cubre filtros positivos y negativos. El 2026-10-09 `integration/exercise_smoke.py` conectó el punto S7 del PLC a una válvula de campo independiente, la coil Modbus a una bomba y ambos equipos mediante proceso al tanque; la estación escribió por Snap7/PyModbus, los objetivos confirmaron válvula abierta, bomba activa, caudal entrante y aumento del volumen del tanque, las trazas nativas fueron detectadas y el run obtuvo score 100. Es evidencia de integración software/Docker, no de equivalencia física ni autorización basada en identidad del cliente. Los objetivos `field_value` ahora pueden mantener `fault=true` durante un intervalo; `field_sequence` también observa activación y recuperación del fallo. Siguen pendientes aplicación preventiva de ACL dentro del protocolo, atribución autenticada, MITM transparente/tráfico arbitrario y mayor fidelidad de ejercicios. |
| RF-41 | Parcial | Cuentas con bootstrap admin de un solo uso, contraseña PBKDF2, sesiones opacas revocables/expirables, cookie HttpOnly/SameSite, CSRF Origin y roles admin/instructor/designer/participant. Login/bootstrap tienen límite atómico compartido por workers en SQLite con hash del origen. La UI permite gestión admin y membresías por escenario; un admin puede restablecer contraseñas desde la UI/API con revocación de sesiones. Bootstrap, creación/actualización de cuentas y cambios/reset de contraseña se escriben en una cadena de auditoría administrativa separada, paginada y verificable; nunca incluye contraseñas ni hashes. Falta recuperación de autoservicio, MFA/SSO, coordinación entre réplicas con bases distintas, ancla externa y revisión independiente. |
| RF-42 | Hecho · aislamiento por usuario | Los escenarios nuevos tienen propietario y membresías con rol; el middleware autoriza por identidad/rol y oculta escenarios ajenos como 404, mientras el listado filtra por propietario/membresía. Pruebas API cubren operaciones y exportación CSV denegadas a usuarios ajenos; `docker_zone_isolation_smoke.py` verifica bloqueo TCP entre redes que representan zonas y escenarios distintos. Los escenarios legacy sin propietario quedan visibles solo a admin. El SRS permite aislar por usuario u organización, por lo que organizaciones/tenants y cuotas no son necesarias para cumplir este requisito y siguen fuera del alcance actual. |
| RF-43 | Parcial | La biblioteca mezcla cinco plantillas empaquetadas con plantillas compartidas en SQLite. Admin/instructor publican; publicador/admin editan metadatos o sustituyen el snapshot del escenario. Cada cambio conserva una revisión inmutable; restaurar crea una revisión nueva y las escrituras condicionales por revisión esperada rechazan ediciones obsoletas con 409. Los presets también admiten edición versionada/restauración por publicador/admin; su configuración se revalida con el esquema real y los conflictos devuelven 409. Migraciones aditivas/idempotentes crean la revisión base para plantillas y presets existentes. Todos los usuarios autenticados consultan historial e instancian plantillas/aplican presets. No se carga código de usuario. Siguen pendientes migración automática entre futuras versiones de esquema, presets S7 remotos con referencias contextuales y moderación multi-organización. |

## Requisitos no funcionales y de plataforma

| Req. | Estado | Implementación actual / brecha |
|---|---|---|
| RNF-01 | Parcial | Pruebas con clientes Snap7/PyModbus sin modificar. El 2026-10-09 `integration/modbus_protocol_test.py` pasó localmente con el servidor en un proceso separado y cliente PyModbus: Modbus TCP cubrió FC01–06, FC15/16, FC22/23, direcciones límite, excepciones 2/4, desconexión y retardo+jitter fijo/uniforme/normal con semilla; esa ejecución no fue desde otra máquina ni contra hardware. El mismo día pasó `integration/modbus_rtu_smoke.py`: un cliente serie PyModbus en otro contenedor sobre el túnel RTU virtual cubrió FC01–06, FC15/16, FC22 y FC23; además verificó operaciones gestionadas, lectura/escritura HMI, adquisición SCADA, auditoría durable sin valores y cero puertos publicados. La prueba RTU no usa interfaz serie física ni bus RS-485. `integration/s7_smoke.py` prueba acceso Snap7 a DB/M/I/Q y operaciones de memoria indicadas. Falta ampliar servicios S7 y comparar interoperabilidad con equipos de terceros/hardware real. |
| RNF-02 | Parcial | Ciclo nominal S7, retardo de respuesta y jitter determinista o estadístico con semilla configurable en S7 y Modbus TCP; las muestras son reproducibles y acotadas. Las rutas TCP declaradas pueden añadir latencia fija por sentido entre 0 y 10 000 ms; el proxy la aplica por bloque de bytes reenviado, aproximación de flujo TCP que se suma al retardo/jitter del servidor y no es una simulación por paquete. El scheduler OS puede desviarse; no hay garantías de tiempo real ni latencia configurable para tráfico que no atraviesa una ruta declarada. |
| RNF-03 | Parcial | El benchmark desplegó en un host hasta 30 nodos mixtos (15 S7 + 15 Modbus TCP) con presupuesto de 15 CPU-quota y límites por contenedor intactos. El techo agregado se ajustó de 12 a 15 CPU-quota porque los límites Docker son máximos de uso, no reservas; 30 nodos suman esos 15 máximos. El host expone 12 CPU, así que escenarios con carga simultánea pueden competir y retrasar scans. CPU/memoria/número de nodos se validan antes de crear la red. La cuota es por escenario, no agregada entre escenarios. No hay evidencia de cientos de dispositivos, múltiples hosts ni carga de cliente concurrente. |
| RNF-04 | Parcial | La API devuelve el tiempo real y la interfaz lo comunica al terminar. Tres despliegues de 20 PLC S7 tardaron 8,555–8,669 s; 20 nodos mixtos tardaron 8,026 s; 22 tardaron 9,253 s; y dos muestras mixtas de 30 nodos (15 S7 + 15 Modbus) tardaron 11,416 y 11,451 s, la segunda con el nuevo presupuesto predeterminado. Todas quedan bajo el objetivo orientativo de 300 s. Se midió en un host WSL2/Docker Engine, sin clientes concurrentes ni lógica de carga. Falta caracterizar otros hosts y carga de proceso. |
| RNF-05 | Parcial · smoke local pasado | `map_performance_ui.cjs` crea 100 nodos mixtos (S7, Modbus, HMI, SCADA y gateways) y 99 enlaces, mide cinco renders independientes con límite de 5 s por render, y comprueba selección/inspector, zoom, paneo y errores de navegador/red. El 2026-10-08 pasó contra una API efímera con SQLite temporal y sin tocar previews: Chromium 140.0.7339.16 midió mediana de 912 ms y máximo/p95 de 1.266 ms; no hubo errores JS, de consola ni solicitudes fallidas. En una medición anterior del mismo día, Edge/Chromium 154.0.4258.62 registró mediana de 1.113 ms y máximo/p95 de 1.354 ms. Son mediciones de este host/WSL, no garantías de latencia ni evidencia para otros hosts, navegadores o topologías mayores; falta caracterización cruzada. |
| RNF-06 | Hecho · smoke de incorporación verificado | `template_onboarding_ui.cjs` crea desde la interfaz la plantilla inicial «Fábrica · Campo independiente, S7 y Modbus», comprueba sus 3 dispositivos, 5 objetos de campo, enlaces y reglas de PLC, la despliega sin editar código y observa el sensor llevar el tanque al 50 %, tras lo que el PLC detiene la bomba y cierra la válvula. Alcance probado: esta plantilla y el flujo de un usuario con permiso de operación; no acredita todos los escenarios posibles ni la autoría libre de lógica. |
| RNF-07 | Parcial | Los errores de esquema y el preflight muestran ubicaciones/avisos accionables. La API de interfaz conserva en cada error de validación su ruta de campo, incluidos índices de listas (`devices[0].config.rack`), en vez de mostrar solo el texto genérico. El smoke `workspace_ui.cjs` pasó en Edge con dos errores 422 inducidos y verificó las rutas en el aviso y actividad del escenario; una prueba Node cubre además el formateo simultáneo. `_docker_error_message` ofrece pasos para imagen ausente, puerto ocupado, subred solapada, daemon inaccesible, permisos y endpoint duplicado; `test_common_docker_deployment_errors_include_actionable_guidance` comprueba esas seis categorías y conserva el detalle original. Las pruebas de healthcheck verifican salida y pasos de recuperación tanto con detalle como cuando Docker no devuelve salida. Los errores de despliegue llegan al aviso y actividad del editor. Faltan clasificar más fallos específicos de arranque y validar categorías adicionales de errores con el operador. |
| RNF-08 | Parcial | Los puertos no se publican por defecto y las redes de escenario se crean `internal` con gateway `isolated`. `integration/docker_network_smoke.py` comprueba salida a `1.1.1.1:443` y conexión a un listener del host desde la estación participante; `integration/docker_zone_isolation_smoke.py` comprueba TCP dentro de la misma zona y bloqueo de conexión directa entre dos zonas y entre dos escenarios. Otros smokes de rutas verifican que un proxy solo acepta al IP de origen declarado. El gateway Docker limita `exec` a estaciones attacker/engineering con etiqueta opt-in y lo rechaza en dispositivos, proxies TCP y capturadores. La evidencia es de Docker Engine 29.8.1 en este host; no sustituye una revisión del host/daemon ni prueba todos los caminos de salida configurables por el operador. |
| RNF-09 | Parcial · brecha crítica reducida | Los contenedores de escenario usan UID no root, FS raíz de solo lectura, `cap_drop=ALL`, `no-new-privileges`, cuotas y límites de red. El gateway Docker usa allowlist, limita inspección/etiquetado de imágenes a referencias permitidas, restringe `exec` a estaciones attacker/engineering solo con `anvil.terminal_access_enabled=true`, deniega por defecto endpoints Docker de alto riesgo y tiene 27 pruebas de política. Los tres Compose endurecen también el backend: raíz de solo lectura, `cap_drop=ALL`, `no-new-privileges`, límite de 256 PIDs y `/tmp` tmpfs. El 2026-10-09 el smoke `integration/backend_security_smoke.py` pasó contra Docker Engine 29.8.1 con imagen recién construida: health 200, /data efímero, SQLite modo 0600, Uvicorn con UID 65534 y `CapEff=0`, sin socket Docker real. El bootstrap usa capabilities temporales CHOWN/FOWNER/SETGID/SETUID solo para preparar el volumen SQLite dedicado y luego las descarta. La suite backend completa pasó 491/491. Los tres perfiles Compose ejecutan por defecto el gateway como UID 65534 y GID 1001, sin capabilities, y permiten seleccionar socket/UID:GID mediante `ANVIL_DOCKER_SOCKET` y `ANVIL_DOCKER_API_USER`; el backend usa UID/GID 65534, configurable con `ANVIL_BACKEND_UID` y `ANVIL_BACKEND_GID`. El 2026-10-09 se inició un contenedor gateway temporal con UID 65534:GID 1001, `cap_drop=ALL`, FS de solo lectura y `no-new-privileges`; quedó healthy, respondió `_ping`/`version` y denegó `/info` (403) usando el socket real. Los smokes eliminaron sus contenedores; no se reiniciaron los previews. El runtime rootless no está verificado aquí: faltan `newuidmap` y `newgidmap`; no se instalaron paquetes ni se alteró el daemon. Falta una revisión independiente del gateway/host. |
| RNF-10 | Parcial · salida aislada e ingreso loopback con gateway temporal verificados; remoto pendiente | La salida también se bloquea por defecto. La allowlist optativa por estación de ingeniería/participante admite FQDN y puerto TCP exactos mediante proxy CONNECT dedicado; deniega IPs, dominios locales y DNS no global. El editor permite gestionar las reglas. `egress_proxy_smoke.py` y `egress_scenario_smoke.py` pasaron en Docker Engine 29.8.1 a través del gateway Docker. El proxy no inspecciona TLS. Para ingreso, `external_protocol_probe.py` pasó lecturas/escrituras S7 y Modbus desde el mismo host a loopback mediante el manager directo y una instancia temporal del código actual de `DockerGate`. `external_bundle_smoke.py` levantó el bundle Compose, comprobó lectura/escritura protocolaria, ausencia de publicación directa y rechazo de peer no autorizado. El gateway Compose del preview no se reinició. Windows/WSL tras el ajuste, otra máquina y LAN siguen sin verificar. |
| RNF-11 | Parcial | Contraseñas PBKDF2, token de sesión almacenado como hash, bootstrap secreto por entorno, revocación, restablecimiento administrativo con revocación de sesiones, gestión básica de cuentas, rate limit compartido por workers de una instancia SQLite y registro SHA-256 de cambios de cuenta sin secretos. Las cookies se marcan `Secure` bajo HTTPS o mediante `ANVIL_COOKIE_SECURE=true`; ambos Compose pasan el ajuste para TLS terminado en proxy y `test_secure_cookie_requires_https_or_explicit_proxy_termination_setting` verifica que una cabecera reenviada sin confiar no active el atributo. `Cache-Control: no-store` se aplica a todas las respuestas HTTP `/api/`, incluidos errores auth y ZIP de evidencia; `test_bootstrap_login_logout_and_password_storage` y `test_evidence_bundle_packages_audit_scada_csv_and_selected_pcap` lo verifican. Al iniciar el backend, los archivos SQLite existentes y sus sidecars se restringen a modo `0600`; `umask 077` protege archivos creados después por ese proceso. `test_db_security.py` comprueba el orden de endurecimiento antes de crear la base, los modos de archivos/sidecars, la máscara de nuevos archivos y la exclusión de SQLite en memoria. El 2026-10-08 la imagen backend construida desde este checkout arrancó en Docker con `/data` efímero, red `none` y API Docker simulada limitada a ping/versión: `/api/health` devolvió 200 y `/data/anvil.db` quedó en modo `0600`. Es protección de permisos local, no cifrado, y su aplicación es best-effort según el filesystem. SQLite sigue sin cifrado de aplicación; faltan vault, MFA, recuperación de autoservicio, limitación coordinada entre réplicas, retención formal y ancla externa/auditoría inmutable. |
| RNF-12 | Hecho | Backend, PLC, Modbus y proceso se empaquetan en contenedores Linux; sin hardware OT. |
| RNF-13 | Parcial | El backend, adaptadores de dispositivos y registries de UI son módulos internos con contratos documentados. El registro valida herencia e implementación concreta del adaptador, esquema Pydantic para tipos visibles y metadata/capacidades; `DeviceRegistryContractTests` verifica el rechazo temprano sin modificar el registry. El motor de proceso corre como `field-runtime` independiente, configurable por imagen (`ANVIL_FIELD_IMAGE`); el contrato HTTP interno declara versión 1 en `/capabilities`, la configuración nueva incluye esa versión, los planes antiguos sin campo se interpretan como v1 y el adaptador impone un healthcheck Docker que consulta el endpoint y cuya condición debe superar el gestor. `backend.tests.test_devices` y `plc.tests.test_field_runtime` verifican el esquema, compatibilidad legacy, versión y endpoint HTTP; `integration/process_pipe_smoke.py` y `integration/field_route_smoke.py` verifican cálculo e intercambio PLC↔campo en Docker. La sonda requiere Python en la imagen y no es una API pública estable ni un registro de motores: no hay migración de configuración, instalación dinámica/sandbox o separación completa del esquema de la implementación. Las zonas son modelos de topología/red, no un módulo de proceso sustituible. |
| RNF-14 | Parcial · pins locales/base y generación de SBOM implementada | En el primer despliegue, Anvil resuelve la imagen configurada, guarda en el plan el ID Docker `sha256:…` y crea una etiqueta local `anvil-pinned/<hash-repo>:<id>` sin mover ni borrar tags existentes. Las recreaciones usan el ID fijado; el gateway comprueba que la etiqueta apunta a ese ID y que la referencia configurada pertenece a la allowlist. Un bundle exportado después del despliegue usa la etiqueta fijada y su `export-images.sh` registra IDs exactos. Los siete Dockerfiles propios fijan su imagen base por digest OCI multi-plataforma y existe un test de regresión. El workflow ahora genera SPDX 2.3 con inventario Debian/Python observado en cada imagen y un manifiesto con commit, ID, base y hashes de entradas, y lo archiva 30 días; en Docker local se construyeron e inventariaron las siete imágenes y se validaron los ocho JSON generados; falta observar el artefacto en una ejecución de CI. El manifiesto no está firmado. Manager, API y política tienen unitarios; un smoke por el gateway Docker real inspeccionó el tag, creó el pin y desplegó/eliminó un contenedor auxiliar con el ID verificado. Faltan attestation firmada, SBOM más amplio (archivos/código fuente), reproducibilidad bit a bit entre hosts; actualizar una imagen requiere guardar de nuevo el escenario detenido para resolver el tag actual. |
| RNF-15 | Parcial | El estado y eventos muestran `exited`, `dead`, `unhealthy` y nodos `missing`, con salida de healthcheck. Bajo lease, el reconciliador repara nodos y redes internas Docker que falten usando el plan runtime persistido, y audita las zonas reconstruidas. La prueba Docker Engine 29.8.1 eliminó el runtime completo y su red; el scheduler reconstruyó la red interna y dos PLC sanos, con journal/auditoría y sin puertos publicados. `test_network_recovery_preserves_foreign_name_collision` confirma que un nombre de red ocupado por otro escenario bloquea la recuperación sin modificar ese recurso. El reintento no resuelve por sí solo conflictos persistentes y requiere intervención. Docker y SQLite no forman una transacción distribuida. |
| RNF-16 | Parcial | Despliegue fallido, parada y limpieza de huérfanos intentan retirar recursos y exponen fallos por recurso. La regresión `test_budget_rejected_deploy_can_be_cleaned_and_retried` confirma la transición API 409→error→nodos ausentes→stop→stopped→retry running al exceder cuota. El rollback de deploy vuelve a consultar contenedores y redes por el ID exacto de escenario y elimina recursos Anvil hallados aunque se perdiera la respuesta de Docker a `create`; conserva recursos de otros escenarios. Las operaciones de ciclo de vida API y los eventos temporizados de ejercicio usan leases exclusivos por escenario (TTL 45 s, renovación cada 10 s); el borrado consume su lease junto con la fila del escenario en una transacción para evitar carreras con deploy, y un evento de ejercicio vence/difiere si hay otra operación activa. Startup y scheduler periódico reclaman atómicamente leases de deploy vencidos y vuelven a intentar la limpieza si Docker falla. `test_scenario_leases.py`, `test_reconciliation.py`, `test_deploy_stop_and_delete_reject_an_active_operation_lease` y `test_timeline_event_waits_for_another_lifecycle_operation` verifican exclusión, renovación, expiración, protección de operaciones activas, diferimiento de eventos y recuperación de registros legacy sin lease. El journal durable cubre operaciones de nodo y la recuperación automática reconstruye redes de escenario faltantes con etiquetas/subredes del plan, sin tocar colisiones ajenas. `node_recovery_smoke.py` pasa pérdida parcial, total de contenedores y pérdida de la red. Ejercicios usan `exercise:{exercise_id}:{event_id}` como clave idempotente; restart compara el estado Docker previo para no repetirlo cuando ya se completó. Docker y SQLite no ofrecen transacción conjunta: una recuperación puede volver a observar o converger el estado, y errores persistentes quedan visibles como pending/failed. |
| RT-01 | Hecho | El runtime de dispositivos usa Docker. |
| RT-02 | Parcial | Snap7/PyModbus se usan para los protocolos existentes; la cobertura de fidelidad por función requiere ampliación. |
| RT-03 | Hecho (JSON versionado + migración explícita) | JSON legible y validado; API y bundle Compose exportan versión 2. La importación ejecuta la transición v1→v2 (añade `network_connections: []` si falta), trata JSON legacy sin sobre como v1, y rechaza versiones desconocidas (incluido `true`) y campos no reconocidos para evitar pérdida silenciosa. Una versión futura requerirá su paso de migración antes de aceptarse. |
| RT-04 | Hecho | No exige hardware industrial. |
| IF-01 | Parcial | La aplicación web ofrece editor, inspectores, mapa, operación y telemetría. Las guías de [interfaz](INTERFACE.md), [PLC](plc-s7.md), [HMI](hmi.md) y [SCADA](SCADA.md) describen qué vistas pertenecen a cada runtime. La consola HMI y el panel SCADA actualizan sus tarjetas por identidad estable sin vaciar la pantalla; preservan el foco y la consigna que se edita, y mantienen la última lectura ante errores. El mapa de campo elige los lados cercanos para cada enlace y vuelve a dibujar sus curvas al mover los objetos; el editor reduce cabecera y controles en pantallas de poca altura. `test_frontend.py` cubre esas regresiones. El preview local `http://localhost:8010/` sirve esta versión y `/api/ready` responde `ready`; la autenticación del endpoint de escenarios impidió comprobar datos con una sesión de participante. Sigue sin haber evaluación de usabilidad con todos los perfiles/navegadores del SRS ni medición de accesibilidad completa. |
| IF-02 | Parcial | La API REST está documentada en [API HTTP y WebSocket](api.md) y expone CRUD, despliegue y ciclo de vida de escenarios, además de WebSocket de eventos. `backend/routes/scenarios.py` implementa estas rutas y `backend/tests/test_api.py` cubre sus contratos; la automatización no cubre todas las acciones del panel y no existe compatibilidad de API declarada por versiones. |
| IF-03 | Parcial | Los endpoints industriales dependen del runtime: S7 sobre TCP/102 y Modbus TCP sobre el puerto configurado, con RTU emulado sobre pseudo-terminal/túnel. [PLC S7](plc-s7.md), [Modbus](MODBUS.md) y [fidelidad](DEVICE_FIDELITY.md) indican funciones probadas por clientes Snap7/PyModbus y los límites; no se afirma indistinguibilidad frente a hardware. |
| IF-04 | Parcial | El backend orquesta redes, contenedores, volúmenes, healthchecks y ciclo de vida a través de Docker; el [diseño](DESIGN.md) y [seguridad de contenedores](CONTAINER_SECURITY.md) describen la frontera del Docker Gate. Los smokes de integración prueban operaciones concretas, no una transacción atómica entre Docker y SQLite ni todos los entornos Docker. |
| IF-05 | Parcial | Hay exportaciones por escenario para topología JSON, configuración/Compose ZIP con inventario de imágenes, eventos de auditoría JSONL y capturas PCAP. Son descargas separadas; el límite de alcance de las capturas se describe en RF-34 y los eventos disponibles en RF-35. |
| DT-01 | Parcial | El modelo de escenario guarda nodos, zonas, redes, objetos de campo, conexiones de señal/proceso y rutas. `ScenarioPayload` y validadores de `backend/models/scenario.py` / `backend/models/field.py` definen y comprueban el grafo; [escenarios](scenarios.md) explica su ciclo de vida. El intercambio usa schema 2 y tiene una migración adyacente 1→2; faltan transiciones de versiones futuras aún no definidas. |
| DT-02 | Parcial | Los modelos por dispositivo guardan direccionamiento, protocolo, mapa de memoria, permisos, lógica y perfil; la validación y traducción a runtimes se distribuye entre modelos, adaptadores y gestor Docker. [PLC S7](plc-s7.md) y [Modbus](MODBUS.md) documentan campos, rangos y permisos. Los componentes PLC heredados no ofrecen el mismo direccionamiento explícito que `io_channel`. |
| DT-03 | Parcial | Los objetos y relaciones del proceso se declaran en `backend/models/field.py` y el motor de campo calcula la evolución del modelo soportado. [Modelo de proceso](process-model.md), [mapa](PROCESS_MAP.md) y [motor](PROCESS_ENGINE.md) explican variables, parámetros, unidades y simplificaciones; no es un lenguaje general de ecuaciones ni un solver físico universal. |
| DT-04 | Parcial | El catálogo de nodos y componentes se define en registries del backend/frontend; plantillas y presets tienen modelos versionados compartidos. [Biblioteca](COMPONENT_LIBRARY.md), [perfiles](DEVICE_PROFILES.md) y [extensión de dispositivos](EXTENDING_DEVICES.md) documentan su uso. No hay plugin público ejecutable ni carga de código arbitrario; siguen pendientes migraciones automáticas entre schemas y referencias portables para entradas S7 remotas. |
| DT-05 | Parcial | Modelos de usuario/sesión/membresía y auditoría de plataforma, bootstrap seguro, roles de plataforma y escenarios, rutas protegidas, UI admin de cuentas y consulta/verificación de cambios de cuenta. Sin MFA/SSO, ancla externa ni revisión independiente. Ver [acceso de plataforma](PLATFORM_ACCESS.md). |
| DT-06 | Parcial | Eventos con actor autenticado se persisten, paginan y exportan en JSONL con cadena SHA-256 verificable. La retención SQLite es optativa por días y protege las ventanas de ejercicios activos. Falta almacenamiento duradero común y ancla externa contra manipulación/truncado. |

## Supuestos y dependencias del SRS

| Req. | Estado | Evidencia y límite |
|---|---|---|
| SU-01 | Parcial | El despliegue local usa Docker y valida límites configurables por escenario antes de crear sus redes. No se ha validado una instalación limpia en otro host ni existe un coordinador multi-host; disponibilidad de CPU/RAM/almacenamiento y conectividad Docker siguen siendo dependencias del operador. |
| SU-02 | Parcial | Los puertos de protocolo no se publican por defecto, las redes internas bloquean salida y las exposiciones explícitas requieren configuración. El operador aún puede publicar puertos o configurar rutas externas; no existe una barrera técnica que convierta cualquier configuración en un laboratorio aislado. |
| SU-03 | Procedimiento requerido | Es una condición de uso, no una propiedad verificable del software. La plataforma no puede comprobar que el operador tenga autorización para una actividad de ciberseguridad. |

## Prioridad MoSCoW del SRS

El SRS agrupa los requisitos representativos por prioridad, pero la mayoría de los
requisitos de la especificación siguen parcialmente implementados. Esta tabla
conserva la prioridad fuente y evita interpretar la presencia de una función como
aceptación del requisito completo.

| Prioridad fuente | Requisitos | Situación en este checkout |
|---|---|---|
| Must | RF-01..06, RF-08..13, RF-15..17, RF-20..22, RF-24..29, RF-34..36, RNF-01, RNF-03, RNF-08..10 | Hay cobertura implementada para diseño, procesos, protocolos, Docker, redes y observabilidad; siguen parciales varias capacidades y límites descritos en las filas individuales. |
| Should | RF-07, RF-14, RF-18..19, RF-30..33, RF-38..40, RNF-02, RNF-13, RNF-15 | Plantillas, el mecanismo interno documentado de extensión (RF-14), temporización, ejercicios y recuperación por nodo existen en parte; protocolos avanzados, control de tiempo real y recuperación/reconciliación completa siguen pendientes. Una API pública para plugins de terceros no está exigida por RF-14 ni se implementa. |
| Could | RF-41..43, plantillas avanzadas, orquestación distribuida | Autenticación, roles, aislamiento por usuario y biblioteca compartida versionada tienen implementación local, con brechas descritas en las filas RF-41..43. El aislamiento por usuario satisface la alternativa explícita de RF-42; el aislamiento por organización, SSO/MFA y multi-host son ampliaciones fuera del mínimo escrito en ese requisito. |
| Won't (esta versión) | Protocolos avanzados completos (IEC 61850, Profinet), simulación de señal eléctrica, alta disponibilidad | Se mantienen fuera de alcance según el SRS. |

## Aceptación del SRS

| Criterio | Estado |
|---|---|
| CA-01 | Parcial. [`process_map_ui.cjs`](../integration/process_map_ui.cjs) cubre escenario de bomba, creación API/UI, dispositivos, 13 objetos y enlaces en mapa, guardado de posiciones, despliegue, telemetría de bomba/válvula/PLC remoto y ventana móvil; `scada_control_smoke.py` añade SCADA y protocolos S7/Modbus. `process_manifold_ui.cjs` verifica alta/inspector de distribuidor y colector; `process_pipe_smoke.py` verifica en Docker el balance del proceso. Faltan topologías físicas complejas y solver general. |
| CA-02 | Parcial. [`modbus_protocol_test.py`](../integration/modbus_protocol_test.py) y [`modbus_rtu_smoke.py`](../integration/modbus_rtu_smoke.py) usan PyModbus estándar para TCP y RTU; [`s7_smoke.py`](../integration/s7_smoke.py) usa Snap7. Cubren lecturas/escrituras protocolarias indicadas en RF-15; no hay cliente RS-485 eléctrico ni validación contra hardware real. |
| CA-03 | Parcial. [`process_loop_smoke.py`](../integration/process_loop_smoke.py), [`control_logic_smoke.py`](../integration/control_logic_smoke.py), [`hmi_control_smoke.py`](../integration/hmi_control_smoke.py), [`scada_smoke.py`](../integration/scada_smoke.py) y [`scada_control_smoke.py`](../integration/scada_control_smoke.py) cubren lazo físico, lógica S7, mando proporcional y control HMI/SCADA. El 2026-10-08 ambos `scada_smoke.py` y `hmi_control_smoke.py` pasaron en Docker con runtimes construidos desde este checkout y midieron retardos de alarma de 1,5 s; el smoke HMI usó API aislada, SQLite temporal y borró su escenario al finalizar. [`test_hmi_alarm_delay.py`](../backend/tests/test_hmi_alarm_delay.py) y `test_scada.py` cubren retardo HMI/SCADA y reinicio ante mala calidad. Faltan programa IEC completo y expresiones complejas. |
| CA-04 | Parcial. [`packet_capture_smoke.py`](../integration/packet_capture_smoke.py) solicita una captura de enlace entre dos PLCs, genera tráfico Snap7 por `remote_input`, comprueba la IP del peer y TCP/102 en el PCAP exportado dentro del ZIP de evidencias. Pasó contra Docker Engine Linux y un gateway temporal construido desde el checkout. El 2026-10-09 [`evidence_bundle_ui.cjs`](../integration/evidence_bundle_ui.cjs) pasó en Microsoft Edge contra API/SQLite efímeras con bootstrap y sesión autenticada de admin: confirma selección de extremos y alcance por zona, exclusión predeterminada de captura, y descarga del ZIP real (HTTP 200, hash de manifiesto y archivo no vacío) usando el rol de la sesión cargada por la UI. La prueba API `test_evidence_bundle_download_is_limited_to_admin_and_scenario_instructor` cubre además los permisos reales: admin e instructor descargan el ZIP, mientras designer y participant reciben HTTP 403. `test_evidence_bundle_packages_audit_scada_csv_and_selected_pcap` valida contenido/manifest del ZIP con captura, mientras `test_evidence_bundle_cli` valida integridad offline y rechazo de alteración. El run de Actions [`35992883214`](https://github.com/marcosfnmr/Anvil/actions/runs/35992883214) pasó para el commit publicado, pero el workflow en ese commit no incluía el smoke browser ni el gateway Docker; el workflow ampliado actual es local y no publicado. Falta verificar Docker Desktop/rootless, además de los smokes Linux Engine y la ejecución remota del workflow actual. |
| CA-05 | Parcial. [`docker_network_smoke.py`](../integration/docker_network_smoke.py) compara dos despliegues reales consecutivos: configuración/imagen de contenedores, recursos, IP/DNS, redes y valores iniciales tras modificar y redesplegar S7 DB/M/I/Q y coil/discrete input/holding/input register Modbus. La ejecución del 2026-10-08 pasó con backend y runtimes del checkout en Docker Engine 29.8.1. Aún no compara todo el estado interno del proceso ni demuestra equivalencia entre hosts/arquitecturas. |
| CA-06 | Parcial: Docker smokes verificaron conectividad dentro de zona, bloqueo directo entre zonas y entre escenarios, operaciones S7/Modbus por rutas SCADA/HMI, enlace PLC→PLC, PLC↔campo y un flujo TCP genérico permitido solo desde el nodo declarado; otro nodo de su misma zona no puede reutilizarlo. El 2026-10-08 `docker_network_smoke.py` volvió a confirmar con imágenes S7/Modbus construidas desde el checkout el bloqueo de egress a Internet y a un listener host, la red interna, ausencia de puertos publicados, DNS y acceso de protocolo dentro de zona. Esta es una prueba representativa en un host/daemon, no una auditoría exhaustiva de aislamiento. |
| CA-07 | Parcial. `external_protocol_probe.py` verificó desde Linux lectura/escritura S7 y coil Modbus por loopback con el manager directo y con una instancia temporal del código actual de `DockerGate`. `external_bundle_smoke.py` valida un bundle Compose: ambos protocolos leen/escriben desde Linux, solo el relay publica, el binding queda en loopback y un peer uplink no autorizado se rechaza. La repetición desde Python de Windows alcanzó handshake COTP S7 y lectura/escritura Modbus coil en ambos estados por forwarding WSL; no verificó lectura/escritura DB S7 en Windows. Otra máquina/LAN, firewall y perfiles de fabricante siguen sin validar. No se reiniciaron los previews. Perfiles JSON por dispositivo con mapa resuelto se exportan/reimportan en Anvil (`test_api.py`). |

## Índice de evidencias locales

Las rutas siguientes apuntan a evidencia que existe en este checkout. Los tests
unitarios demuestran contratos y validación; solo los scripts de integración que
despliegan contenedores demuestran esos flujos en Docker. La existencia de un
script no implica que se haya ejecutado en cada revisión: el resultado observado
y el entorno de imágenes deben registrarse aparte.

| Requisitos | Evidencia automatizada principal | Alcance que sí demuestra |
|---|---|---|
| RF-01..07, RF-26, RF-29..30; RT-03; DT-01..04 | [`test_api.py`](../backend/tests/test_api.py), [`test_field_objects.py`](../backend/tests/test_field_objects.py), [`test_compose_export.py`](../backend/tests/test_compose_export.py), [`test_docker_manager.py`](../backend/tests/test_docker_manager.py), [`test_frontend.py`](../backend/tests/test_frontend.py), [`process_map_ui.cjs`](../integration/process_map_ui.cjs), [`process_manifold_ui.cjs`](../integration/process_manifold_ui.cjs), [`docker_network_smoke.py`](../integration/docker_network_smoke.py), [`field_route_smoke.py`](../integration/field_route_smoke.py), [`deployment_bundle_smoke.py`](../integration/deployment_bundle_smoke.py) | CRUD, import/export/clone, revisiones, migración de escenarios v1→v2, configuración de zonas/campo/layout, validación/despliegue DNS, ruta PLC↔campo con bloqueo directo y estado fresco comprobado para cada binding; los smokes UI verifican enlaces Ethernet frente a proceso/señal/control/I/O/S7/HMI y alta/inspector de bloques de proceso; pruebas de bundle cubren variables, reasignación atómica, offsets de IP, colisiones y convivencia Docker del original y el bundle. No demuestra ejecución en un segundo host, elección automática de CIDR ni futuras transiciones de esquema aún no definidas. |
| RF-08..10, RF-14..19; RNF-01..02; RT-02; IF-03 | [`test_devices.py`](../backend/tests/test_devices.py), [`test_control_logic.py`](../plc/tests/test_control_logic.py), [`test_modbus.py`](../backend/tests/test_modbus.py), [`test_modbus_api.py`](../backend/tests/test_modbus_api.py), [`test_modbus_server.py`](../plc/tests/test_modbus_server.py), [`control_logic_smoke.py`](../integration/control_logic_smoke.py), [`engineering_station_smoke.py`](../integration/engineering_station_smoke.py), [`modbus_protocol_test.py`](../integration/modbus_protocol_test.py), [`modbus_rtu_smoke.py`](../integration/modbus_rtu_smoke.py), [`s7_smoke.py`](../integration/s7_smoke.py), [`tcp_route_smoke.py`](../integration/tcp_route_smoke.py), [`test_protocol_capture.py`](../plc/tests/test_protocol_capture.py), [`test_policy.py`](../docker_proxy/tests/test_policy.py) | Validación de los cuatro espacios y permisos; cliente PyModbus estándar verifica FC01–06/FC15/16/FC22/FC23 en TCP y RTU, además de límites, excepciones, latencia y desconexión TCP. El smoke de rutas verifica S7/Modbus TCP a través de proxies interzona, aislamiento directo y retardo ida/vuelta con latencia por sentido configurada. La estación de ingeniería se prueba en Docker aislada por defecto, con lectura S7 desde su zona cuando se habilita y shell UID 65534. El smoke S7 prueba lógica ON/OFF, AND/NC, memoria SR con prioridad de RESET y TON real. La integración mide 40 respuestas reales de jitter uniforme y normal con semilla; unitarios cubren límites y repetibilidad. No demuestra jitter del scheduler, latencia por paquete ni indistinguibilidad frente a hardware, bus RS-485 eléctrico ni un switch/router funcional. |
| RF-11, RF-20..23; CA-03 | [`test_field_objects.py`](../backend/tests/test_field_objects.py), [`test_frontend.py`](../backend/tests/test_frontend.py), [`test_control_logic.py`](../plc/tests/test_control_logic.py), [`test_field_runtime.py`](../plc/tests/test_field_runtime.py), [`field_valve_balance_smoke.py`](../integration/field_valve_balance_smoke.py), [`process_loop_smoke.py`](../integration/process_loop_smoke.py), [`process_pipe_smoke.py`](../integration/process_pipe_smoke.py), [`process_manifold_ui.cjs`](../integration/process_manifold_ui.cjs), [`field_smoke.py`](../integration/field_smoke.py), [`template_onboarding_ui.cjs`](../integration/template_onboarding_ui.cjs) | Enlaces de proceso y ciclo determinista, nivel/presión hidrostática, presión nominal, pérdidas cuadráticas en serie, bifurcación directa, bloque distribuidor de porcentaje configurable y colector de suma, control ON/OFF, escalas, fallos, fuga y demanda. Semillas opcionales para sensores con ruido se validan en modelo y UI; `test_seeded_sensor_noise_repeats_and_uses_independent_sensor_streams` comprueba secuencias reproducibles y flujos independientes. El 2026-10-08 `process_pipe_smoke.py` se ejecutó en un stack temporal con gateway Docker y runtime de campo construido desde el checkout; verificó pérdida en serie/reducción del caudal y recorrido bomba→distribuidor→dos ramas→colector→depósito con reparto 15/35 L/min y suma de 50 L/min. En la verificación posterior volvió a pasar con ramas limitadas a 10/20 L/min, probando la reducción de la fuente a 30 L/min y conservación del caudal al colector y depósito. Una prueba de runtime nueva verifica además dos niveles de distribuidores y colectores con conexiones fuera de orden, flujo de 50 L/min conservado y reasignación por capacidad. La revisión actual añade regresiones unitarias y un smoke Docker aislado de caudal de válvula cerrada/proporcional y conservación en rama paralela; este smoke no publica puertos y no modifica los previews. `process_manifold_ui.cjs` confirmó en Edge la configuración visual, sin persistir el borrador. No demuestra solver general de presión ni dinámica transitoria. Las conexiones acíclicas se ordenan por dependencia; los ciclos conservan la propagación iterativa acotada y no se resuelven por convergencia. |
| RF-24..30, RF-32, RNF-08..10, RNF-15..16; RT-01; IF-04; CA-05..06 | [`test_docker_manager.py`](../backend/tests/test_docker_manager.py), [`test_api.py`](../backend/tests/test_api.py), [`test_runtime_status.py`](../backend/tests/test_runtime_status.py), [`test_reconciliation.py`](../backend/tests/test_reconciliation.py), [`test_scenario_leases.py`](../backend/tests/test_scenario_leases.py), [`docker_proxy/tests/test_policy.py`](../docker_proxy/tests/test_policy.py), [`node_recovery_smoke.py`](../integration/node_recovery_smoke.py), [`deployment_recovery_smoke.py`](../integration/deployment_recovery_smoke.py), [`docker_network_smoke.py`](../integration/docker_network_smoke.py), [`docker_zone_isolation_smoke.py`](../integration/docker_zone_isolation_smoke.py), [`docker_security_protocol_smoke.py`](../integration/docker_security_protocol_smoke.py), [`field_route_smoke.py`](../integration/field_route_smoke.py), [`backend_security_smoke.py`](../integration/backend_security_smoke.py) | Políticas de orquestación, estado `missing` comparado con el plan persistido, evento WebSocket de nodo ausente, leases de lifecycle entre workers, recuperación de un deploy cuyo lease expiró y limpieza de recursos Docker por ID/labels exactas. `deployment_recovery_smoke.py` comprueba en Docker que una caída de despliegue limpio deja el escenario `stopped`; `node_recovery_smoke.py` elimina un PLC, ambos PLC y la red Docker del escenario, y verifica recuperación por scheduler, journal/auditoría, preservación del peer en pérdida parcial, red interna, healthchecks y puertos host cerrados. Otros smokes verifican E/S por gateway y bloqueo directo de egress, entre zonas y entre escenarios. `backend_security_smoke.py` verifica raíz de solo lectura, caída de capabilities, no-new-privileges, límite de procesos, tmpfs y arranque con SQLite efímera sin montar el socket real. No prueba router L3/ACL generales, transacción atómica entre Docker y SQLite ni aislamiento total del host; una subred en conflicto requiere resolver la colisión. |
| RF-12..13, RF-35..36; IF-01..02, IF-05; DT-06; CA-04 | [`test_runtime_frontend.py`](../backend/tests/test_runtime_frontend.py), [`test_virtual_operations_api.py`](../backend/tests/test_virtual_operations_api.py), [`test_scada.py`](../backend/tests/test_scada.py), [`test_logs_api.py`](../backend/tests/test_logs_api.py), [`test_protocol_trace_collector.py`](../backend/tests/test_protocol_trace_collector.py), [`test_process_trace_collector.py`](../backend/tests/test_process_trace_collector.py), [`test_api.py`](../backend/tests/test_api.py), [`test_auth.py`](../backend/tests/test_auth.py), [`scada_trend_test.mjs`](../integration/scada_trend_test.mjs), [`scada_history_buffer_smoke.py`](../integration/scada_history_buffer_smoke.py), [`workspace_ui.cjs`](../integration/workspace_ui.cjs), [`evidence_bundle_ui.cjs`](../integration/evidence_bundle_ui.cjs), [`process_audit_smoke.py`](../integration/process_audit_smoke.py), [`hmi-synoptic.js`](../backend/static/hmi-synoptic.js), [`hmi_control_smoke.py`](../integration/hmi_control_smoke.py), [`scada_control_smoke.py`](../integration/scada_control_smoke.py), [`packet_capture_smoke.py`](../integration/packet_capture_smoke.py) | Rutas/permisos API, adquisición SCADA, reconocimiento HMI persistente, alarmas e historial; `scada_trend_test.mjs` cubre calidad/timestamps/huecos; `test_scada.py` verifica commit antes de ack e idempotencia, y el smoke Docker real confirma persistencia en volumen tras recrear el runtime y borrado tras ack. Cadena SHA-256, detección de cambios y retención auditora están cubiertas. El smoke Docker de proceso verifica transición real en runtime aislado, conexión de señal contextual y exclusión de muestras analógicas continuas de la auditoría. PCAP por pareja de dispositivos y por bridge de segmento se verifica en Linux Engine; Docker Desktop/rootless aún no se caracterizan. El smoke de navegador cubre captura/descarga, HMI, sinóptico, filtros y adaptación móvil; no valida con HMI comercial. |
| RF-37 | [`test_api.py`](../backend/tests/test_api.py), [`test_evidence_bundle_cli.py`](../backend/tests/test_evidence_bundle_cli.py), [`verify_evidence_bundle.py`](../integration/verify_evidence_bundle.py), [`workspace_ui.cjs`](../integration/workspace_ui.cjs), [`evidence_export.py`](../backend/services/evidence_export.py), [`runtime.js`](../backend/static/runtime.js) | ZIP versionado con manifiesto, auditoría JSONL, logs, histórico SCADA y PCAP opcional por dispositivo/bridge de zona; incluir la captura la consume. Se verifica offline con Python estándar el inventario SHA-256/tamaño y opcionalmente un hash del manifiesto guardado por separado; pruebas cubren alteración y mismatch de hash. Pendiente firma digital con gestión de claves y retención configurable de PCAP. |
| RF-39 | [`test_auth.py`](../backend/tests/test_auth.py), [`test_terminal_leases.py`](../backend/tests/test_terminal_leases.py), [`test_docker_manager.py`](../backend/tests/test_docker_manager.py), [`test_policy.py`](../docker_proxy/tests/test_policy.py), [`docker_network_smoke.py`](../integration/docker_network_smoke.py), [`participant_terminal_smoke.py`](../integration/participant_terminal_smoke.py), [`participant_terminal_ui.cjs`](../integration/participant_terminal_ui.cjs) | Acceso opt-in a una zona interna, lectura Snap7, ausencia de puertos publicados, terminal WebSocket por rol y UID no-root a través del gateway Docker real. La coordinación SQLite reserva una sesión por estación y hasta 16 por worker, con renovación y expiración; el gateway libera el `exec` al cortar el socket. Réplicas con bases separadas no comparten cuota. |
| RF-38..40 | [`test_exercises.py`](../backend/tests/test_exercises.py), [`test_protocol_attack.py`](../backend/tests/test_protocol_attack.py), [`exercise_smoke.py`](../integration/exercise_smoke.py), [`exercise_builder_ui.cjs`](../integration/exercise_builder_ui.cjs), [`modbus_interception_smoke.py`](../integration/modbus_interception_smoke.py) | Cronología, acciones, objetivos de protocolo/campo/auditoría, escrituras protocolarias Snap7 y PyModbus desde estación participante a través de rutas, filtros exactos por detalle en objetivos y `fail_fast`, secuencias auditadas ordenadas, interrupción/restauración, reglas all/any, score ponderado y persistencia/migración. El test de navegador `exercise_builder_ui.cjs` crea desde la interfaz escrituras gestionadas S7 y protocolarias Modbus, selecciona estación/coil/ruta, configura reescrituras Modbus FC03, FC05, FC15, FC16, FC22 y FC23 con sus eventos `clear`, y verifica objetivos de auditoría; el 2026-10-09 pasó en Microsoft Edge para FC03/FC16 y, tras ampliarse, volvió a pasar el 2026-10-10 incluyendo selección de coil/estado y reescrituras FC05/FC15, además de guardado, ejecución, limpieza y objetivos aprobados. La intercepción inline Modbus tiene además parser/control unitario y smoke Docker separado: verifica respuesta de lectura alterada, valor original intacto, aislamiento directo, cero puertos publicados y evento durable asociado al ejercicio/regla; `exercise_smoke.py` verifica set/clear del scheduler, lectura FC03 antes/durante/después, escritura FC06 solicitada como 42 y almacenada como 52000, además de FC15 sobre coils y FC16 con tres registros: sustitución de dos direcciones, conservación de la tercera y dos auditorías/objetivos separados. El run completo obtuvo score 100 desde la estación participante. La prueba de navegador verifica configuración y ejecución FC05/FC15/FC22/FC23, pero no acredita MITM transparente: siguen sin soportarse captura universal, ARP/DNS, tráfico sin ruta explícita ni otras funciones fuera del diseño. |
| RF-41..43, RNF-11, DT-05 | [`test_auth.py`](../backend/tests/test_auth.py), [`test_db_security.py`](../backend/tests/test_db_security.py), [`test_frontend.py`](../backend/tests/test_frontend.py), [`component_preset_revisions_ui.cjs`](../integration/component_preset_revisions_ui.cjs), [`COMPONENT_LIBRARY.md`](COMPONENT_LIBRARY.md) | Bootstrap, sesiones, rate limit persistido/atómico por origen, CSRF, RBAC, aislamiento por escenario, atribución de auditoría, modos restrictivos del archivo SQLite y sidecars, permisos de archivos posteriores, exclusión de SQLite en memoria, permisos de plantillas/presets, control de revisión obsoleta, restauración, migraciones legacy idempotentes y flujo browser real de edición, conflicto y restauración de presets. El smoke browser se ejecuta localmente contra SQLite efímera, no forma parte de CI y no cubre todas las acciones de plantilla. No cubre SSO/MFA, revisión de seguridad externa, migración a futuros esquemas ni multi-tenancy. |
| RNF-03..05 | [`capacity_benchmark.py`](../integration/capacity_benchmark.py), [`map_performance_ui.cjs`](../integration/map_performance_ui.cjs), [`informe RNF-04`](benchmarks/RNF04-2026-10-07.md) | Docker midió 20 PLC S7, 20 nodos mixtos, 22 mixtos y 30 mixtos en 8,026–11,451 s. El smoke del editor midió 100 nodos mixtos e interacciones con mediana 1.113 ms/máximo 1.354 ms en Edge y, en una repetición aislada, 912 ms/1.266 ms en Chromium 140. `test_default_cpu_budget_accepts_thirty_mixed_s7_modbus_nodes` fija el presupuesto predeterminado; `test_budget_rejected_deploy_can_be_cleaned_and_retried` valida recuperación API al exceder un límite explícitamente reducido. No hay benchmark de cientos de dispositivos, carga concurrente/múltiples hosts ni caracterización del editor en varios hosts/navegadores. |
| RNF-06 | [`template_onboarding_ui.cjs`](../integration/template_onboarding_ui.cjs) | Desde la UI, un usuario crea sin código la plantilla inicial, la despliega, comprueba por S7 la lectura del sensor de proceso y observa que la lógica del PLC detiene la bomba y cierra la válvula en el umbral de histéresis. No demuestra facilidad de uso de todas las plantillas ni autoría arbitraria de programas. |

La prueba [`test_srs_traceability.py`](../backend/tests/test_srs_traceability.py)
expande los rangos compactos de las tablas, comprueba que los 84 identificadores
del SRS v1.0 tengan una fila con estado y valida el total declarado. Así se
comprueba la integridad de la matriz; no evalúa el grado de implementación.

## Orden de integración propuesto

1. **Seguridad de laboratorio:** redes `internal` con gateway `isolated`, aislamiento Docker por escenario/zona, endurecimiento por contenedor y gateway de API Docker con allowlist se verifican en Docker Engine 29.8.1 local. Engine 28+ es requisito para retirar la dirección gateway del bridge. Compose admite socket rootless, pero falta validar smokes con ese daemon; el chequeo rootless actual requiere `uidmap`. La salida optativa por allowlist de FQDN/puerto está implementada y tiene smoke de despliegue completo; quedan revisión externa y cuotas globales entre escenarios.
2. **Redes y topología:** además de los enlaces Ethernet lógicos declarativos, se pueden configurar rutas TCP de origen/destino/puerto entre zonas, bloquear cada ruta completa y consultar el endpoint local del proxy. Una futura ampliación podría añadir routing IP genérico si los escenarios lo requieren; el SRS actual pide rutas/aislamiento configurables entre segmentos y no define ACL intra-zona.
3. **Proceso físico:** modelo determinista tanque/tubería/bomba/válvula/sensor, enlaces físicos separados de señales PLC y perturbaciones que evolucionen el estado.
4. **Control y operación:** programa PLC básico o bloques funcionales, HMI operable y SCADA con sondeo, alarmas y tendencias persistidas. Las consignas S7 REAL y Modbus UINT16 escalado accionan una válvula proporcional y el caudal del proceso; HMI y SCADA admiten retardo configurable de umbrales y la HMI configura alarmas BOOL propias. Faltan expresiones complejas y garantizar muestreo sin pérdidas.
5. **Observabilidad:** auditoría JSONL con cadena SHA-256 y retención optativa por días, además de PCAP por segmento, ya están disponibles; añadir anclaje externo, almacenamiento duradero común y replay de ejecución.
6. **Cyber range:** ejercicios reproducibles, eventos programados, condiciones de éxito y estaciones de participante confinadas.
7. **Producto multiusuario:** autenticación/roles, aislamiento por propietario/membresía y bibliotecas compartidas están implementados en el checkout local. Plantillas de escenario y presets admiten edición/versiones inmutables; quedan organizaciones, cuotas, migración futura de esquemas y más controles de cuenta.
8. **Fidelidad y escala:** RTU y protocolos futuros, profiles de sustitución, benchmarks y recuperación individual.

El orden es una propuesta de ingeniería: primero se reduce el riesgo de aislamiento
antes de introducir componentes de ataque y exposición de red. Las fases se aceptan
con una prueba automatizada y documentación del límite de fidelidad de cada emulador.

## Estado de publicación

Primeros incrementos de integración realizados: las redes Docker de escenarios son
internas para bloquear salida a redes externas por defecto; la prueba de integración
comprueba que S7/Modbus siguen alcanzándose desde el mismo escenario y que la salida
TCP pública falla. El formato de exportación/importación
JSON ahora incluye `format: "anvil-scenario"` y `schema_version: 2`, también en
`scenario.json` de los bundles Compose. La importación migra sobres v1 y JSON
legacy sin envoltorio a v2; si el cliente declara una versión futura no soportada,
recibe HTTP 422 en vez de importar datos con semántica desconocida.

La verificación posterior de ejercicios pasó la suite completa (241 backend,
60 PLC y 10 docker_proxy), compilación Python, sintaxis JS, configuración Compose
y `git diff --check`. `integration/exercise_smoke.py` desplegó S7/campo en Docker,
inyectó y retiró una avería de bomba y exigió 2 s continuos de señal S7; el ejercicio
obtuvo puntuación 100. La suite comprueba también 3 s de coil Modbus y el reinicio
del conteo al perder la condición. La prueba SCADA valida descarga CSV y neutraliza
fórmulas de hoja de cálculo.

#### Estado del checkout, GitHub y previews — comprobación 2026-10-09

El checkout local está en `main`, HEAD `12a51da5a7368e1a71623924bf3940355cb89ec0`;
`git ls-remote origin refs/heads/main` devolvió el mismo SHA. Esto solo confirma
que la rama remota apunta a ese commit: el checkout tiene 300 archivos modificados
o sin seguimiento que no están publicados. No se hizo commit ni push.

La revisión de la API de GitHub devolvió el run `35992883214`, asociado a ese SHA,
con resultado `success` (2026-09-24). Sin embargo, el workflow CI de GitHub tiene
60 líneas frente a las 260 del workflow local: el remoto compila, ejecuta las
suites PLC/backend, construye las imágenes PLC/backend y ejecuta el smoke S7. No
ejecuta la suite `docker_proxy`, el smoke de navegador, las comprobaciones Compose
rootless ni los smokes Docker adicionales que aparecen en el workflow local. Por
tanto, ese run verde no valida las ampliaciones locales. Los checks locales se
describen en [VALIDATION_LOCAL.md](VALIDATION_LOCAL.md); no deben presentarse como
checks de GitHub hasta que el workflow local se publique y corra allí.

Durante esta comprobación, las páginas raíz de los previews 8003, 8005, 8008,
8009 y 8010 respondieron HTTP 200; `/api/ready` informó base de datos y Docker
correctos en 8003, 8005, 8008 y 8010. El puerto 8009 sirve la documentación
estática y respondió 404 a `/api/ready`, como corresponde. Estas comprobaciones
solo constatan disponibilidad HTTP; no prueban que cada preview tenga el código
actual del checkout ni que sus escenarios funcionen de extremo a extremo.

### Estado local de operación, SCADA y HMI

La HMI tiene vista de operador separada del inspector, refresco de señales,
escrituras permitidas por mapa de E/S/rol/ruta, alarmas propias numéricas y BOOL,
sinóptico del proceso y transiciones persistidas. SCADA adquiere por S7/Modbus,
permite operaciones autorizadas, mantiene alarmas durables y exporta el historian
CSV. Las rutas explícitas SCADA/HMI→PLC pueden cruzar zonas mediante proxies
dedicados; no existe routing IP genérico. Los smokes de HMI, SCADA, lazo de proceso
y rutas Docker están listados en la tabla de aceptación y de evidencias superior.
El historian recupera su buffer acotado; muestras ya rotadas durante una
interrupción prolongada del backend no pueden recuperarse. No se afirma soporte
OPC UA, HMI/SCADA comerciales ni expresiones complejas de alarma o retención
configurable de temporizadores pendientes.

Las últimas ejecuciones de las suites locales registran 325 pruebas backend,
71 PLC y 22 del proxy Docker (2026-10-08); se ejecutaron por componentes en
esta continuación y no en una sola orden conjunta. `docker_network_smoke.py` pasó en Docker real
con comparación de topología y estado inicial de protocolo tras redeploy. No se debe usar el conteo menor de notas
históricas como estado actual. El estado de GitHub y los previews se describe solo
con la evidencia de la fecha de revisión en la cabecera; un smoke local no prueba
que esos servicios estén actualizados.

### Exportación del histórico SCADA (checkout local)

`GET /api/scenarios/{scenario}/scada/{node}/history/export.csv` descarga las
observaciones SQLite en orden temporal, incluso si el escenario está detenido o
Docker no está disponible. Cada fila contiene timestamp, dispositivo/dirección,
nombre, tipo, unidad, valor y calidad; el límite máximo coincide con la retención
de un millón de puntos. El inspector SCADA expone la descarga. Prueba de API
verifica cabeceras, contenido y neutralización de etiquetas que podrían ejecutarse
como fórmulas en hojas de cálculo. Continúa local, sin publicar ni tocar previews.

### Escritura de consignas S7 REAL (checkout local)

Los canales `io_channel` de salida S7 admiten `BOOL` o `REAL`, con alineación DB
calculada por el adaptador. HMI y SCADA muestran un campo numérico solo si el mapa
marca el canal `read_write`; la API rechaza valores no finitos o fuera del rango
IEEE-754 de 32 bits y ejecuta la escritura mediante un cliente Snap7 en el contenedor
PLC. Auditoría conserva valor, dirección y origen. La capa de campo añade una válvula
proporcional cuya consigna REAL 0–100 % acciona el área de proceso por medio del
puente PLC-campo; límites, retardo y fallo se validan. Un holding register Modbus
se convierte explícitamente a REAL (0–1000 → 0–100 %) antes de accionar otra válvula.
`integration/scada_control_smoke.py` comprueba escrituras reales HMI/SCADA S7 y
Modbus, lectura de retorno, auditoría, cambio de apertura proporcional y caudal
hacia un depósito. Pruebas unitarias cubren
validación de escenario, respuesta proporcional, retardo, fallo y efecto sobre el
nivel. Esto sigue siendo un modelo lineal de caudal, no una representación de una
curva de válvula industrial. Los previews 8003/8005 no se reinician y los cambios
siguen solo en el checkout local.

### Lógica PLC configurable (checkout local)

Se añadió a RF-09 un bloque ON/OFF con histéresis que se resuelve a offsets S7,
evalúa una entrada REAL y conduce una salida BOOL en cada ciclo de scan. Se
validan direcciones/tipos, salida única por regla y umbrales numéricos finitos.
Las salidas controladas pasan a solo lectura en el mapa/API para que HMI y consola
no simulen escrituras que el programa sobrescribiría. El inspector permite crear
y configurar el bloque; `docs/PLC_LOGIC.md` explica la semántica y el límite.
Verificación Docker real: `integration/control_logic_smoke.py` leyó con Snap7
sensor y orden de bomba y confirmó que el programa activó la salida. Esto mejora
RF-09/22 y CA-03, pero no equivale a Ladder/ST ni a un runtime IEC 61131-3; tampoco
es lógica de seguridad funcional. El smoke se ejecutó contra la imagen local
`anvil/plc-s7:logic-srs`.

### Bloques combinacionales BOOL (checkout local)

Se añadieron bloques AND/OR/XOR de dos entradas BOOL y una salida BOOL, editables
desde el inspector. El esquema limita las entradas a señales BOOL de entrada y
valida que dos entradas sean distintas; las salidas no pueden compartirse con
otro bloque ON/OFF, booleano o TON. Las salidas controladas son de solo lectura
en consola/HMI/SCADA. Las pruebas unitarias recorren la tabla de verdad de los
tres operadores y validan la configuración. `integration/control_logic_smoke.py`
usa Snap7 dentro del contenedor para comprobar las cuatro combinaciones del AND.
Esto amplía RF-09/RF-22, pero no implementa Ladder/ST ni lógica secuencial
general: solo SR, CTU y el secuenciador one-hot acotado descrito arriba.
La política de calidad para entradas remotas S7 está cubierta por la continuación
descrita en RF-09. El diagnóstico general de calidad de otras entradas y la
ejecución en tiempo real siguen pendientes.

### Verificación adicional de confinamiento (checkout local)

El orquestador ahora impone UID no root, FS raíz de solo lectura, eliminación de
capabilities, `no-new-privileges`, límites de procesos/CPU/memoria y ausencia de
privilegios, mounts y red host en contenedores de escenarios. El smoke
`integration/docker_security_protocol_smoke.py` inspecciona esos ajustes efectivos
en Docker y prueba lecturas/escrituras S7 y Modbus más control del runtime de campo.
La suite backend pasó con 96 pruebas. Esto reduce el riesgo dentro de cada runtime,
en aquella verificación el backend aún accedía directamente al socket Docker con
autoridad equivalente a root. En la actualización local posterior, Compose retira
ese montaje del backend y lo sustituye por `docker-api`, un gateway
con allowlist de rutas, imágenes, recursos etiquetados, redes internas y política
de contenedor. El gateway sigue montando el socket y conserva autoridad potencial
de administrador; por tanto RNF-09 sigue parcial, no queda resuelto. La API tampoco
tiene autenticación/roles. `docker_proxy/tests/test_policy.py` cubre denegaciones y
un smoke de protocolo ejecutado por `DOCKER_HOST` a través del gateway comprueba el
flujo normal con Docker real.

Los previews 8003 y 8005 respondían `/api/health` correctamente antes de ejecutar
esta verificación. El smoke crea y elimina recursos propios etiquetados con UUID;
no despliega ni reinicia previews.

### Ciclo de vida por nodo (checkout local)

El inspector permite iniciar, detener y reiniciar el contenedor del dispositivo
seleccionado mientras el escenario está desplegado. La API busca el contenedor por
las etiquetas exactas del escenario y Node ID, rechaza nodos ausentes/ambiguos y
registra la acción en auditoría. Se cubre con pruebas de manager y API. No hay pausa,
ni edición de la topología durante la ejecución.

### Incidentes físicos programados en ejercicios (checkout local)

La cronología admite `field_control` para activar/desactivar fallo de actuador,
aplicar/retirar override de sensor y cambiar demanda de tanque. La validación
comprueba el tipo de objeto, el rango del sensor y valores no nulos requeridos; las
acciones se registran como `operator.field_control` con el ID del ejercicio. Las
pruebas unitarias cubren fallo, recuperación, auditoría y rechazo de controles
incompatibles. `integration/exercise_smoke.py` comprobó el estado efectivo en el
contenedor de campo entre la avería y su recuperación programadas. El evento no
admite scripts arbitrarios ni inyección de tráfico de red, que siguen pendientes.

### Objetivos temporales de puntos PLC (checkout local)

`point_value` admite `duration_seconds`, como ya hacía `field_value`: S7/Modbus se
releen por el adaptador nativo en cada ciclo del scheduler y el progreso persistido
se reinicia si la comparación deja de cumplirse. Pruebas unitarias cubren una salida
S7 interrumpida y restablecida y una coil Modbus sostenida; el smoke Docker S7+campo
comprueba una salida verdadera durante 2 s antes de puntuar. La resolución temporal
depende del ciclo de 250 ms, de los tiempos de protocolo y de la carga; no es un
muestreador de alta velocidad ni una función de captura de tendencias arbitrarias.

### Alcanzabilidad estática y sonda activa de rutas (checkout local)

Los diagnósticos ahora revisan los destinos HMI/SCADA entre zonas según el
protocolo S7/Modbus, los `remote_input` S7 y las señales PLC↔campo. Identifican
rutas específicas ausentes/deshabilitadas y explican que una ruta TCP genérica
no configura el cliente HMI/SCADA. Los escenarios siguen guardándose para no
romper topologías locales existentes; el preflight muestra el aviso con la acción
correctiva antes del despliegue. Pruebas API cubren rutas S7 bloqueadas, TCP
genérica mal aplicada, señal de campo interzona bloqueada, nodos Docker no
saludables/ausentes y el resultado de una sonda activa. En un escenario desplegado,
el endpoint ejecuta el probe como UID 65534 en el contenedor origen, hacia el
listener privado ya declarado: S7 completa COTP y setup de comunicación, Modbus
recibe una respuesta a una lectura de holding register sin escribir y TCP genérico
establece conexión. Para el bridge de campo usa un GET a una ruta inexistente
esperando HTTP 404, sin invocar `/snapshot`, `/exchange` ni `/control`; comprueba
el servidor sin avanzar el proceso ni mandar señales. Las sondas de campo se hacen
desde nodos con bindings cuyo bridge es directo dentro de su zona o usa el gateway
habilitado. `integration/tcp_route_smoke.py` verifica las cinco rutas S7/Modbus/TCP
con SCADA/HMI reales; `integration/field_route_smoke.py` verifica la ruta HTTP de
campo desde el PLC y el bloqueo de caminos directos. Esto comprueba listeners y
protocolos de transporte modelados. Cada binding PLC↔campo sí compara su dirección
con el objeto/puerto configurado y la frescura del último intercambio; todavía no
valida el valor de proceso contra un rango de ingeniería ni cubre comunicaciones
no modeladas. Si Docker no está disponible, el diagnóstico estático sigue
respondiendo.

### Temporizador TON (checkout local)

El catálogo de bloques S7 añade temporizadores TON configurables desde el inspector
o el JSON del escenario. La entrada BOOL debe permanecer activa por el preset
(10 ms–1 h); si cae, el conteo se reinicia y la salida se apaga. Validación de tipos,
direcciones, preset y exclusividad de salida está cubierta en backend y runtime; el
mapa marca la salida como solo lectura. `integration/control_logic_smoke.py` se
ejecutó contra `anvil/plc-s7:ton-srs-local` en Docker con cliente Snap7 y comprobó
que no termina antes del preset, termina después y se resetea al bajar la entrada.
El smoke conjunto también verifica una compuerta AND BOOL desde Snap7.
El estado no es retentivo por defecto; el tiempo observado depende del scan y del scheduler;
esto no equivale a un temporizador de hardware ni a un runtime IEC 61131-3.

### Contador CTU (checkout local)

El editor S7 configura un CTU BOOL con flanco ascendente, RESET prioritario,
preset entero entre 1 y 65535 y salida DONE de solo lectura. El runtime cuenta
una sola vez mientras el pulso permanece alto, satura en el preset y borra el
estado con RESET (incluso coincidente con un flanco). Los unitarios cubren
conteo, flanco, saturación, salida, reset y preset inválido; el smoke Docker con
Snap7 pasó en `anvil/plc-s7:ctu-local-20261008`. El preview 8008 usa el runtime
`anvil/plc-s7:retention-local-20261008`. El valor acumulado no está expuesto como
punto S7/HMI; se pierde al recrear el PLC por defecto y se conserva con
`retain_logic_state`.

### Secuenciador BOOL (checkout local)

El inspector permite configurar entre 2 y 16 salidas BOOL en orden, más entradas
de avance y RESET distintas. El runtime activa solo la salida del paso actual,
avanza por flanco, se mantiene en el último paso y RESET vuelve al primero con
prioridad. Las salidas son exclusivas de solo lectura para clientes; la prueba
unitaria verifica estados one-hot, flancos, final de secuencia y reset, y el
smoke Docker/Snap7 pasó contra `anvil/plc-s7:sequence-local-20261008`. El preview
8008 conserva sus datos y sirve el editor con el runtime
`anvil/plc-s7:retention-local-20261008`; las referencias S7 anteriores se
mantienen permitidas en el gateway para no romper escenarios guardados. El estado
de paso se pierde al recrear el PLC por defecto y se conserva con
`retain_logic_state`. No hay temporizador, bucle, transición condicional ni
editor Ladder/ST.

### Direccionamiento S7 explícito por canal (checkout local)

Los canales `io_channel` mantienen asignación automática si byte y bit están
vacíos. El inspector ahora permite fijar el byte y, para BOOL, el bit; los REAL
requieren byte par y no admiten bit. La validación reserva las direcciones en el
área DB/M/I/Q correspondiente, detecta solapes con E/S explícita o automática,
y comprueba el tamaño configurado. Pruebas de adaptador y API cubren M4.3, QD6,
continuación automática a M5.0, colisiones con componentes heredados y otros
canales, REAL desalineado, bit sin byte y fuera de memoria. La suite backend
completa pasó 347 pruebas. Componentes heredados y bloques de lógica conservan offsets automáticos;
no se añade direccionamiento simbólico TIA.

### Bootstrap y auditoría de cuentas

El alta inicial crea la cuenta administradora y el evento `account.bootstrap_completed`
en una misma transacción. Si falla la persistencia SQL de la auditoría, la ruta
revierte la cuenta y responde HTTP 503; el HTTP 409 queda reservado para conflictos
de unicidad/concurrencia. La regresión `test_bootstrap_rolls_back_user_when_audit_storage_fails`
verifica que no quede cuenta ni evento parcial. El 2026-10-08 pasaron las 377
pruebas de backend, incluida la trazabilidad de los 84 identificadores del SRS;
`git diff --check` también pasó. La imagen backend reconstruida para el preview
8008 incluye esta corrección y respondió healthy/ready; su volumen SQLite se
conservó. No se ha publicado en GitHub.

### Retardo de alarmas SCADA tras recrear el runtime

RF-13: los inicios de condición pendiente se guardan por escenario/SCADA/tag en
`scenario_scada_alarm_pending`. Antes de leer snapshot o alarmas, la API restaura
esos tiempos en el contenedor; el runtime vuelve a evaluar la condición en el
siguiente sondeo. Una muestra normal o de mala calidad borra el pendiente; un
estado `unknown` durante el arranque lo conserva hasta la primera muestra. Las
alarmas activas conservan su estado/acknowledgement por la tabla existente. La
tabla nueva se crea al arrancar y se limpia al borrar el escenario. `test_scada`
cubre reinicio, restauración, activación tras vencer el retardo, conservación
durante el arranque `unknown`, reinicio del pendiente por normal/mala calidad,
invocación API y limpieza; pasaron backend
377/377 y PLC 101/101 el 2026-10-08. Esto persiste el temporizador de alarma, no
las muestras que hayan rotado fuera del buffer SCADA durante una interrupción.
El 2026-10-08 se reconstruyó `anvil/scada:demo-20261008-alarm-delay1` y el
backend del preview aislado `http://localhost:8008`; el arranque creó la tabla
nueva en el volumen existente `anvil-demo_demo-data`. `/api/ready` confirmó DB y
Docker disponibles; 8003 y 8005 siguieron respondiendo HTTP 200 y no se
recrearon. No había contenedores SCADA desplegados en 8008 al verificar, así que
los próximos escenarios usarán la imagen nueva. El cambio no se ha publicado en
GitHub.

### Validación segura de bundles de evidencia

RF-37: el verificador offline rechaza nombres de entrada absolutos, con `..`,
separadores Windows o entradas de directorio; requiere inventario único y
canónico con bytes enteros no negativos y SHA-256 hexadecimal. También limita
el bundle a 10.000 archivos y 1 GiB descomprimido para acotar el trabajo con ZIPs
no confiables. El generador API impone el mismo límite durante la escritura y
responde HTTP 413 sin retener el ZIP parcial; `test_evidence_bundle_budget_is_enforced_and_api_returns_payload_too_large`
verifica el presupuesto y la respuesta. Si se eligió PCAP, ese sidecar ya se
consume antes de calcular el bundle. La prueba `test_offline_verifier_rejects_path_traversal_and_noncanonical_windows_paths`
cubre variantes de traversal y `test_offline_verifier_rejects_directories_and_invalid_inventory_sizes`
cubre directorios e inventarios inválidos. La API expone el hash del manifiesto en
`X-Anvil-Manifest-SHA256` y la interfaz lo muestra para guardarlo aparte; las
pruebas API/UI cubren ambos. Es una protección del verificador; el header recibido
junto al ZIP no autentica procedencia: hace falta conservar el hash por canal de
confianza o añadir firma digital. Pendiente validar tamaños grandes legítimos del histórico antes de fijar un límite operativo más bajo. El
2026-10-08 pasaron 380/380 pruebas backend, incluidas las de exportación API,
verificación CLI y trazabilidad SRS. El backend/UI se desplegó en el preview
aislado `http://localhost:8008`; `/api/ready` y el código de exportación confirman
la versión nueva, y el volumen SQLite se conservó. Los previews 8003/8005
respondieron HTTP 200 y no se recrearon. El CLI offline endurecido permanece en
el checkout local, y ningún cambio se ha publicado a GitHub.

### Revalidación de aislamiento RF-42 (2026-10-08)

Se corrigió la clasificación de RF-42: el texto fuente permite aislar por
usuario **o** por organización; propiedad de escenario, membresías y filtros
por usuario cubren la primera alternativa. Pasaron cinco regresiones de
propiedad/RBAC, exportación de histórico y default-deny de rutas. Además,
`docker_zone_isolation_smoke.py`, ejecutado con Docker Engine local desde un
contenedor efímero, imprimió `PASS: same-zone TCP reachable; cross-zone and
cross-scenario TCP blocked`. No quedaron contenedores/redes etiquetados como
`anvil.integration=zone-isolation`. La suite no crea organizaciones, pero esa
capacidad no es requerida por RF-42. No se modificó ni desplegó código.

Revalidación adicional: `test_unowned_scenario_is_hidden_on_every_registered_http_route`
recorre más de 50 combinaciones de método/ruta registradas bajo
`/api/scenarios/{scenario_id}` con una cuenta ajena y exige 404 en todas;
también verifica que el listado omita el escenario y que el WebSocket cierre
con 4403. Pasaron esa prueba, las regresiones de propiedad/RBAC y el test de
trazabilidad SRS (4 pruebas seleccionadas, 2026-10-08). La comprobación se hizo
en un contenedor efímero con dependencias de desarrollo, sin cambiar ni
reiniciar previews.

### RF-40 · calidad de objetivos temporizados (2026-10-08)

El evaluador de objetivos `point_value` ya no acredita entradas remotas S7 con
comunicación `waiting`, `stale` o desconocida; tampoco acumula duración durante
esas muestras. Se añadieron regresiones para dato obsoleto, ausencia del campo
`quality` y reinicio del periodo, conservando la lectura anterior para puntos sin
metadatos de comunicación. Pasaron las 17 pruebas de `test_exercises.py` y la
prueba de integridad de la matriz SRS (18 en total). Se desplegó solo el backend
en 8008 como `anvil/backend:srs-rf40-quality-20261008`; `/api/ready` indicó DB y
Docker `ok`, el contenedor cargó la nueva regla y conservó `anvil-demo_demo-data`.
Los previews 8003/8005 no se recrearon y ambos siguieron respondiendo HTTP 200.
El cambio continúa solo en el checkout local; no se publicó a GitHub.

### RF-15/16 · Modbus TCP FC22 y FC23 (2026-10-08)

Se amplió `integration/modbus_protocol_test.py` para verificar FC22 Mask Write
Register y FC23 Read/Write Multiple Registers con `ModbusTcpClient` estándar,
incluida la lectura posterior de los valores modificados. La integración pasó
contra el servidor Python del checkout montado en solo lectura y las dependencias
PyModbus fijadas de `anvil/modbus-tcp:dev`; los casos anteriores de lectura,
escritura, jitter, excepción Device Failure y desconexión también pasaron.
Después, `modbus_rtu_smoke.py` pasó en Docker Engine 29.8.1 con una imagen Modbus
construida desde este checkout y cliente serie en contenedor separado: verificó
FC22/23 por túnel RTU, además de lecturas/escrituras gestionadas, HMI y SCADA. El
smoke limpió su escenario, contenedores y redes temporales. Esto no valida la
señal eléctrica RS-485 ni hardware físico. No se actualizó ningún preview porque
el código del servidor no cambió; las pruebas y documentación permanecen locales,
sin commit/push.

### Revisión global del checkout (2026-10-08)

Se ejecutaron las suites completas usando el checkout montado en solo lectura en
contenedores efímeros: backend 382/382, PLC 101/101 y gateway Docker 26/26. El
backend terminó con código 0, al igual que PLC y gateway; las dependencias de test
se instalaron solo dentro de esos contenedores. Además pasaron el smoke de
protocolo Modbus TCP ampliado y el smoke Docker RTU con FC22/23 descritos arriba.
No se modificaron ni reiniciaron los previews para esta revisión; los resultados
son del checkout local y no de CI ni de GitHub.

### RNF-09 · alcance de terminal Docker por rol (2026-10-08)

El gateway ahora permite Docker `exec` únicamente para contenedores con rol
`attacker` o `engineering` y etiqueta `anvil.terminal_access_enabled=true`, que
el adaptador calcula desde el opt-in de configuración. La misma comprobación se
aplica al crear, iniciar e inspeccionar la sesión; PLC, Modbus, SCADA, HMI,
gateways y capturadores quedan denegados. Pasaron las 27 pruebas del gateway, las
34 de `test_devices.py`, la regresión de trazabilidad y `participant_terminal_smoke.py`
en Docker Engine 29.8.1: consola abierta con UID 65534, auditoría de ciclo de vida y cierre
del exec tras desconexión abrupta. El smoke limpió el escenario temporal. Se
corrigió su harness para traducir `DOCKER_SOCKET` Unix URL a la ruta del gateway
y comprobar `exec_inspect` a través del propio gateway.

Sin escenarios ni ejercicios activos, se desplegaron solo backend y gateway en
8008 como `anvil/backend:srs-exec-scope-20261008` y
`anvil/docker-api-gate:srs-exec-scope-20261008`; se conservó
`anvil-demo_demo-data`. `/api/ready` reportó base de datos y Docker `ok`; 8003 y
8005 no se recrearon y ambos siguen sanos. El cambio sigue local, sin commit ni
push. RNF-09 aún requiere validar el daemon rootless y revisión independiente.

### RF-23 · fallo de sensor congelado (2026-10-08)

Los sensores analógicos y entradas digitales de campo pueden congelar su última
lectura mediante el control de fallo; el PLC conserva el último valor recibido y
el mapa distingue el enlace como `fault`. La restauración reanuda la medición
derivada del proceso. El inspector permite inyectarlo y restaurarlo mientras
corre el escenario; `FieldControl` permite programarlo como evento de ejercicio.
Pasaron 389 pruebas backend y 108 PLC; `field_sensor_fault_smoke.py` verifica el
ciclo completo en un contenedor Docker temporal. La simulación representa un
sensor atascado; no modela deriva, ruido de avería ni diagnóstico eléctrico del
instrumento.

### RF-23 · fuga dinámica en depósito (2026-10-09)

`leak_flow_lpm` se puede cambiar en un tanque en ejecución desde el inspector o
el endpoint de control de campo; el nuevo caudal se integra en el balance de
volumen y `0` lo restaura. El control queda auditado. Los eventos `field_control`
de ejercicios admiten el mismo campo, validan que el destino sea un tanque y
pueden activar y cerrar la fuga en instantes programados. Pasaron pruebas de
runtime para la reducción de volumen y restauración, API/auditoría y ejercicios
programados, además del smoke `field_valve_balance_smoke.py` con imagen construida
desde el checkout en un contenedor `network_mode=none` sin puertos publicados; no
se modificó ningún preview. El modelo conserva una tasa fija
configurada mientras dura la perturbación, sin propagación hidráulica distribuida.

### RF-40 · secuencias de eventos de auditoría (2026-10-08)

Los objetivos `audit_sequence` requieren entre 2 y 16 tipos de evento observados
en ese orden desde el inicio del run; otros eventos pueden intercalarse. Un
intervalo máximo opcional (1 ms–24 h) reinicia la búsqueda cuando se excede. El
editor guiado configura un tipo por línea y el resultado expone secuencia
esperada y prefijo encontrado. Pasaron las 391 pruebas backend y
`integration/exercise_smoke.py` con Docker/Snap7: una ruta S7 cruzada se
interrumpió y restauró, la secuencia auditada se cumplió y el score terminó en
100. Solo se evalúan eventos de auditoría persistidos; existe parada intermedia
optativa para ciertos eventos prohibidos, pero no objetivos continuos de fallo
basados en variables de proceso o tendencias. Un evento de auditoría puede
evaluar la coincidencia de la intercepción Modbus acotada de RF-38; esto no
equivale a un objetivo de proceso sobre el valor manipulado ni a un MITM
transparente.

### RF-40 · criterios de ausencia de eventos auditados (2026-10-08)

El objetivo `audit_event` admite `expectation: "absent"` para penalizar una
acción prohibida ocurrida desde el inicio del run; por defecto se evalúa al
terminar la cronología. Las definiciones previas conservan `occurred` por
defecto. El editor guiado expone ambas condiciones y el resultado conserva el
valor observado. `fail_fast: true` permite detener el run al detectarlo durante
la ejecución. Siguen pendientes score progresivo y objetivos temporales basados
en tendencias o valores de proceso intermedios.

`fail_fast: true` en un objetivo `audit_event` ausente detiene el run al primer
tick del scheduler que detecta el evento, registra `exercise.failed` con su tipo
y objetivo, asigna score 0 y marca los demás objetivos `not_evaluated`. Solo se
permite con `expectation: "absent"`. El intervalo nominal del scheduler es 250
ms; no es una garantía de tiempo real. Esto cubre parada intermedia por eventos
de auditoría, no comparaciones intermedias de variables o tendencias.

La revisión del 2026-10-08 pasó 395/395 pruebas backend y
`integration/exercise_smoke.py` con Docker Engine: la prueba falló el run al
detectar `scenario.node_stop`, dejó sin aplicar el reinicio programado y
conservó el resultado normal del ejercicio anterior (score 100). `git diff
--check` y compilación Python también pasaron.

### RF-27/RF-38 · pausa de ejercicios con el escenario (2026-10-08)

Pausar un escenario suspende su run de ejercicio, conserva el índice de eventos
y acumula el tiempo detenido; reanudar desplaza los cronómetros de objetivos
continuos activos para que la pausa no se compute como tiempo de proceso, sin
alterar `started_at` ni el filtro de auditoría. La interfaz identifica el estado
pausado y permite cancelar el run. Pasaron 397 pruebas backend. El
`integration/exercise_smoke.py` con Docker verificó que los eventos no avanzan
durante la pausa y se aplican al reanudar con el tiempo restante, junto con los
ciclos previos de control de proceso, rutas, auditoría y fail-fast. La migración
añade columnas con valores por defecto y se probó sobre una base anterior.
Se desplegó en 8008 como `anvil/backend:srs-rf43-exercise-pause-20261008`.
`/api/ready` devolvió `ready` con DB y Docker OK; el runtime sirvió la UI nueva.
Los previews 8003 y 8005 respondieron HTTP 200 y no se recrearon. El volumen de
datos `anvil-demo_demo-data` se conservó y el smoke no dejó contenedores de
escenario.

### RF-30 · bundle Compose portable (2026-10-08)

El bundle de despliegue incluye variables `.env` para subredes, IP estáticas y
puertos host publicados, además de `network-addresses.json` y `readdress.py`.
El operador puede elegir un CIDR libre por zona conservando los offsets de los
nodos; el script valida direcciones utilizables y solapamientos antes de
escribir `.env` de forma atómica. `test_compose_export.py` cubre valores por
defecto, offset de varias zonas y rechazo sin modificación ante errores. El
smoke Docker `deployment_bundle_smoke.py` arrancó el bundle en una segunda
subred mientras el escenario fuente seguía desplegado, confirmó sus IP/redes y
comprobó que el bundle persistió después de detener y borrar el escenario
fuente. Pasó también la suite backend completa (403 pruebas) y el build de
documentación. Esto no transfiere las capas de imagen automáticamente ni
escoge subredes por el operador: las imágenes se exportan/cargan por separado y
se debe revisar el CIDR, los puertos y la política de host. No se hicieron
commit ni push.

### RF-27/RNF-15/16 · lease y recuperación de despliegues (2026-10-08)

Deploy, stop, delete, pause, resume, control de nodo y eventos de ejercicio se
serializan con un lease por escenario en SQLite, exclusivo entre workers, con
TTL de 45 s y renovación cada 10 s. Los eventos de ejercicio se reintentan en
el siguiente tick si otra operación posee el lease. En startup y cada 10 s, el
reconciliador omite operaciones con lease activo y reclama las vencidas para
retirar únicamente contenedores/redes que tengan `anvil.managed=true` y el ID
exacto del escenario. Si Docker no puede limpiar, el estado queda `deploying` y
se reintenta; los volúmenes de retención se conservan. El borrado consume el lease
en la misma transacción que elimina el escenario. Pasaron 411 pruebas backend,
incluidas exclusión API de deploy/stop/delete, renovación/expiración, protección
de operaciones activas, limpieza retryable y preservación de recursos sin etiqueta gestionada. El nuevo
`integration/deployment_recovery_smoke.py` pasó con Docker Engine 29.8.1 y PLC
construido desde el checkout: simuló la caída entre creación Docker y guardado
del estado, y comprobó que el contenedor/red se retiraron y el escenario quedó
`stopped`. El 2026-10-08 se desplegó el backend
`anvil/backend:srs-rf27-recovery-20261008` en 8008; el único escenario existente
estaba detenido, se respaldó SQLite en
`/tmp/anvil-demo-pre-rf27-recovery-20261008.db`, la migración creó la tabla de
leases y `/api/ready` confirmó DB/Docker OK. 8003/8005 no se recrearon. No hay
journal por recurso ni recuperación automática de nodos
ausentes en escenarios ya `running`; no se probaron caídas reales del proceso
ni multi-worker bajo carga.

Revisión de ciclo de vida (2026-10-08): además de deploy/stop/delete, pause,
resume y el control de nodo usan leases exclusivos; el borrado consume el lease
en la transacción que elimina el escenario. La adquisición rechaza escenarios
que ya no existen incluso en SQLite sin enforcement de FK. Eventos del motor
de ejercicios comparten el lease y quedan diferidos al próximo tick ante una
operación concurrente. Las pruebas API/ejercicio cubren el conflicto y el
diferimiento; `test_scenario_leases.py` cubre el rechazo de leases huérfanos.
En la suite completa posterior pasaron 412 pruebas backend; `exercise_smoke.py`
verificó con Docker real el control S7/proceso, la interrupción/restauración de
ruta, auditoría, pause/resume y fail-fast. También pasó
`deployment_recovery_smoke.py`. El preview 8008 quedó en
`anvil/backend:srs-rf27-lifecycle-lease-20261008`; antes se guardó
`/tmp/anvil-demo-pre-lifecycle-lease-20261008.db`. La comparación SQLite no
mostró cambios de recuento por tabla. Gateway y volumen no se recrearon; 8003 y
8005 permanecieron en HTTP 200. No se hizo commit ni push.

### RNF-15/16 · journal durable de operaciones por nodo (2026-10-08)

Cada acción de ciclo de vida de nodo escribe su intención en
`scenario_node_operations` antes de llamar a Docker y registra el resultado al
terminar. El reconciliador procesa entradas `pending` en startup y cada 10 s bajo
el lease SQLite del escenario; stop/pause/resume/start convergen al estado
solicitado. Para `restart`, el journal guarda ID de contenedor, RestartCount y
StartedAt previos, para que una caída antes del efecto reintente y una caída
después del efecto no reinicie el PLC por segunda vez. Los eventos de ejercicio
usan una clave idempotente `exercise:{exercise_id}:{event_id}`. Instructor/admin
pueden consultar estado, número de intentos y último error en
`GET /api/scenarios/{id}/node-operations`. La migración añade el checkpoint a
tablas journal existentes sin eliminar filas.

La suite backend pasó 420/420 pruebas, incluidas migración aditiva, autorización,
reintento, deduplicación de timeline y las dos ventanas de reinicio. El
test `test_pending_node_operation_is_durable_and_retried_to_completion` también
verifica que recuperar un PLC no marque como `running` un escenario cuyo plan
conserva otro nodo ausente. El
`integration/node_recovery_smoke.py` pasó con Docker Engine 29.8.1 y dos PLC S7
simulados: ejecutó el restart pendiente, no repitió uno que Docker ya había
completado, verificó journal `completed`, ausencia de puertos host y conservación
del otro nodo, y limpió el escenario de prueba. CI ahora ejecuta ese smoke.
Persistencia SQLite y Docker siguen sin formar una transacción distribuida; una
acción que permanezca en error queda inspeccionable/reintentable y el sistema
todavía no reconstruye por sí solo nodos ausentes que no tengan una operación
pendiente. Se guardó el respaldo
`/tmp/anvil-demo-pre-node-journal-20261008.db` y se comparó con la copia posterior:
ninguna tabla preexistente cambió su número de filas; se creó la tabla nueva con
cero entradas y la columna `precondition_json`. El backend de preview 8008 usa
`anvil/backend:srs-rnf15-node-recovery-20261008`; `/api/ready` devolvió DB/Docker
`ok` y OpenAPI incluye el endpoint. Los previews 8003, 8005 y la documentación
8009 respondieron HTTP 200; el gateway de 8008 no se recreó. No se hizo commit ni
push.

### RNF-15 · cambios de salud y métricas por WebSocket (2026-10-08)

El stream de runtime incluye en su firma de cambio `health_status`, salida del
healthcheck, error del runtime y estado/hora de la muestra de recursos. Si
cambia cualquiera de esos datos mientras el estado Docker permanece `running`,
emite un evento `status` inmediatamente; los clientes no esperan al heartbeat
periódico. `backend.tests.test_event_hub` verifica cambio de salud y cambio de
calidad de métricas. Pasaron 424 pruebas backend y el build estricto de MkDocs.
Actualización posterior del mismo día: el reconciliador periódico reconstruye
automáticamente un contenedor ausente si el escenario queda `partial` y aún hay
otro runtime activo. `node_recovery_smoke.py` verifica ese recorrido real; siguen
siendo explícitas la recuperación de un escenario completamente ausente y la de
redes de zona.


### RNF-15 · recuperación automática de un nodo parcial (2026-10-08)

`integration/node_recovery_smoke.py` se amplió para iniciar la app con el
scheduler real de 10 s, borrar espontáneamente un PLC de un escenario temporal
de dos PLC y esperar hasta 35 s a la reconciliación. En Docker Engine 29.8.1 pasó:
el nuevo contenedor quedó sano, el escenario volvió a `running`, el journal
registró `recreate` completado con origen `runtime-reconciler`, se persistió la
auditoría y el segundo PLC conservó su ID de contenedor. No hubo puertos del host
publicados. También se corrigió la serialización de resultados del journal: los
status de Docker contienen timestamps `datetime`, que ahora se convierten con
`jsonable_encoder` antes de persistir JSON. La regresión se cubre en
`test_reconciliation.py`.

En la validación actual, la suite backend pasó 429 pruebas, el build estricto de
MkDocs pasó y `git diff --check` quedó limpio. `node_recovery_smoke.py` probó
pérdida parcial y total: se recuperaron ambos PLC desde el plan persistido, con
healthcheck, journal y auditoría; en esa ejecución la red aún se conservaba.
No hubo commit ni push.

### RF-27/RNF-15/16 · reconstrucción segura de redes perdidas (2026-10-08)

El reconciliador vuelve a crear redes Docker internas que faltan a partir del
plan runtime guardado, únicamente para un escenario con intención activa y
reclamado bajo lease. La red nueva usa subnet/zona derivadas de los nodos
persistidos, opciones `internal` y gateway `isolated`, y etiquetas exactas del
escenario. Si el nombre requerido pertenece a otro escenario/usuario, se
preserva y la operación informa el error. Cuando el estado quedó `error`, se
consulta la última transición auditada para distinguir una caída desde
`running`/`partial` de un despliegue fallido (`deploying → error`); este último
no se relanza. Las zonas restauradas se registran en auditoría antes de que el
scheduler programe los nodos ausentes.

`test_docker_manager.py` verifica redes internas por zona y preservación de una
colisión ajena; `test_reconciliation.py` cubre pérdida total tras una transición
activa y confirma que un error de deploy no se relanza. Pasaron 69 pruebas
focales. `integration/node_recovery_smoke.py` pasó con Docker Engine 29.8.1:
eliminó un PLC, después ambos PLC y finalmente la red del escenario. El
scheduler recuperó la red, ambos PLC y el estado `running`, conservó el peer
durante la pérdida parcial, completó journal/auditoría y no publicó puertos del
host. No se hizo commit ni push. Docker y SQLite siguen sin una transacción
conjunta; una subred ocupada por un recurso ajeno necesita intervención. En la
misma revisión pasó la suite backend completa (437 pruebas), la compilación
estricta MkDocs y `git diff --check`.

### RF-40 · tendencias numéricas del proceso (2026-10-08)

Se añadió `field_trend` como objetivo para propiedades físicas numéricas: el
motor retiene muestras nominales cada segundo, acotadas a 302 por objetivo, y
compara los extremos de una ventana de 2–300 s para una tendencia ascendente,
descendente o estable con delta configurable. Exige cobertura temporal, limita
los huecos de muestreo a 2,5 s y no interpola lecturas. El progreso se guarda en
`objective_tracking_json`; pause/resume desplaza las marcas temporales para
excluir la pausa. El editor guiado expone objetivo, propiedad, dirección, ventana
y delta. Las pruebas `test_exercises.py` cubren modelo/rangos, muestreo,
persistencia, las tres direcciones y cobertura; `test_frontend.py` verifica el
editor. Pasaron 29 pruebas de ejercicios y 49 de frontend (440 en el backend
completo). RF-40 sigue parcial: estas tendencias
son del modelo físico de Anvil, no telemetría externa en tiempo real. Aún faltan
secuencias temporales de condiciones de proceso, efectos de proceso derivados
de una lectura interceptada, MITM transparente y tráfico arbitrario; la
intercepción de respuestas Modbus FC03/FC04 por ruta explícita se describe en
RF-38.

### RF-40 · secuencias temporales de condiciones de proceso (2026-10-08)

Se añadió `field_sequence`: de 2 a 16 pasos seleccionan objeto, propiedad,
operador y valor BOOL o numérico; el motor evalúa un solo paso por muestra y exige
que el siguiente se observe en una muestra posterior. Un `max_gap_seconds`
opcional reinicia el avance al vencer. Los matches, valores observados y hora
se persisten junto al run; pause/resume desplaza las marcas. El editor guiado
permite elegir y quitar pasos, y configurarlos sobre varios objetos de campo.
`test_exercises.py` cubre orden, timeout, persistencia y pausa; `test_frontend.py`
verifica los controles. Pasan 31 pruebas de ejercicios y 49 de frontend; la
suite backend completa pasa 442 pruebas. Esta evaluación consulta el runtime de
campo de Anvil; no representa lógica PLC ni telemetría física en tiempo real.
Las condiciones temporales de fallo permanecen pendientes. La evaluación por
auditoría puede puntuar una coincidencia de intercepción Modbus FC03/FC04, pero
no sigue el efecto de esa lectura alterada sobre el proceso; MITM transparente
y tráfico arbitrario también permanecen pendientes.

### RNF-02/RF-19 · retardo configurable en rutas TCP (2026-10-08)

Las rutas S7, Modbus TCP y TCP genéricas aceptan `latency_ms` entre 0 y 10.000
(por defecto 0). El inspector permite editarlo y
`GET /api/scenarios/{id}/network-routes` devuelve el valor. El proxy demora cada
bloque TCP antes de reenviarlo en ambas direcciones; no es latencia por paquete,
no tiene precisión de tiempo real y se suma al retardo/jitter del servidor.
`test_api.py` cubre límites/tipos, persistencia, API y configuración del
contenedor; `test_frontend.py` verifica el control; `test_policy.py` rechaza
etiquetas de gateway inválidas. `integration/tcp_route_smoke.py` pasó en Docker
Engine 29.8.1 con imágenes temporales construidas desde este checkout: mantuvo
lecturas/escrituras S7/Modbus y aislamiento de zonas, y midió al menos 60 ms de
ida y vuelta con 40 ms configurados por sentido. CI ejecutará el mismo smoke con
las imágenes que ya construye; aún no hay resultado de GitHub para este cambio.
No se modificaron los contenedores de preview. La compilación estricta de MkDocs
también pasó.
### RF-32 · ingreso protocolario por loopback (2026-10-08)

Se corrigió el listener del relay para enlazar su interfaz del bridge Docker,
manteniendo por separado la IP privada de zona usada para salud y destino. La
publicación sigue siendo opt-in y el binding predeterminado es loopback; los
puertos directos en nodos PLC/Modbus se rechazan. `external_protocol_probe.py`
pasó en Docker Engine 29.8.1 con COTP y lectura/escritura Snap7, además de
lectura/escritura de coil PyModbus, desde el mismo host por `127.0.0.1` y puertos
efímeros. Al ejecutarlo detrás de una instancia temporal del código actual de
`DockerGate`, la primera reproducción reveló que la regla del relay comparaba
el ID de imagen fijado con la lista de tags autorizados. Se ajustó a validar la
referencia declarada por etiqueta, que ya está respaldada por la validación del
pin inmutable; se añadió regresión unitaria para esa combinación. Después,
manager, DockerGate y protocolo pasaron extremo a extremo. La instancia temporal
escuchó solo en `127.0.0.1`; el gateway Docker del preview no se reemplazó ni
reinició. El probe previo al arreglo fallaba desde Windows/WSL. En la repetición
del 2026-10-08, `external_bundle_smoke.py` verificó con Python estándar de
Windows el handshake COTP de S7 y lectura/escritura Modbus TCP de una coil en
ambos estados mediante el forwarding loopback de WSL. Esta prueba no realizó
lectura/escritura de DB S7 desde Windows; esas operaciones se verificaron desde
Linux con Snap7. Tampoco se probaron otra máquina, interfaz LAN o firewall
remoto. En la continuación se implementó el exportador Compose del
relay y un bridge uplink propio de proyecto (el bridge especial `bridge` no
aceptó aliases Compose). `external_bundle_smoke.py` pasó en Docker Engine con
lectura/escritura protocolaria S7 y Modbus, loopback, destinos privados y rechazo
de un peer no autorizado. El workflow CI incluye ese smoke, pero aún no hay una
ejecución publicada del workflow. RF-32 queda parcial por el alcance local y el
acceso remoto aún no verificado.

### RF-38 · objetivos de auditoría filtrables (2026-10-08)

Los objetivos de tipo `audit_event` aceptan hasta ocho condiciones escalares
exactas sobre una allowlist de detalles (`node_id`, `address`, `source`,
`route_id`, `object_id`, etc.). Las condiciones se aplican tanto a presencia
como a ausencia y parada inmediata; eventos del mismo tipo sobre otro punto no
se confunden con el objetivo. El editor guiado valida JSON y la API valida de
nuevo la estructura. `test_exercises.py` pasó 33 pruebas, incluidos el filtro
de coincidencia/no coincidencia y `fail_fast` filtrado; `test_event_hub.py` y
`test_srs_traceability.py` completaron 39 pruebas en conjunto. El smoke de
navegador `exercise_builder_ui.cjs` pasó en Edge sobre backend y SQLite
temporales: generó la escritura, guardó un objetivo auditado por dispositivo y
dirección y mostró el ejercicio superado. Esta evaluación utiliza auditoría
semántica de Anvil. La lectura Modbus FC03/FC04 puede manipularse mediante la
intercepción inline acotada documentada en RF-38; captura/interceptación
transparente, otras funciones y reescritura de solicitudes siguen pendientes.

La misma ejecución de navegador descubrió que el snapshot WebSocket podía incluir
`datetime` de métricas Docker no serializable. Se corrigió la codificación del
payload y se añadió `test_snapshot_encodes_docker_metric_timestamps_as_json`.
La verificación conjunta de ejercicios, eventos, trazabilidad y frontend pasó
89 pruebas.
El backend temporal, su SQLite y el contenedor del smoke se limpiaron; no se
modificaron los previews 8003, 8005, 8008 ni 8009. No se hizo commit ni push.

### RF-40 · contexto de escrituras API rechazadas (2026-10-09)

Los eventos `api.request_rejected` de escrituras HMI, SCADA y consolas S7/Modbus
incluyen, si la petición llegó a la validación de permisos, el cliente, su
`source_node_id`, el `target_node_id` y la dirección. Las consolas registran el
dispositivo como origen y destino. El valor enviado nunca se añade al rechazo.
Así un objetivo `audit_event` puede comprobar que se denegó un punto concreto y
`fail_fast` puede terminar un ejercicio por una coincidencia filtrada. La prueba
dirigida
`test_firewall_blocks_hmi_and_direct_console_reads_and_writes_when_port_is_not_allowed`
pasó y verifica HMI/S7; `test_firewall_rejection_audits_modbus_console_target_and_address`
verifica además la consola Modbus. Ambas comprueban el destino/dirección,
respuesta 403 y ausencia del valor en el evento. Esta evidencia es de
autorización/firewall en la API de Anvil; no prueba
identidad ni bloqueo de un cliente conectado directamente al servidor S7 o
Modbus. No incluye aún detección de escrituras protocolarias como “no
autorizadas” según una identidad de cliente: la auditoría del protocolo conserva
origen de red y operación, no una identidad autenticada.

### RF-40 · objetivos sobre fallos del proceso (2026-10-09)

`field_value` permite observar `fault` como BOOL y exigir que permanezca
activado durante `duration_seconds`; al liberar la perturbación antes del
umbral, el scheduler reinicia la duración. `field_sequence` acepta `fault` para
comprobar activación y recuperación en muestras distintas. El editor muestra
la propiedad como «Fallo activo» en válvulas, bombas, motores, variadores y
sensores analógicos/digitales. Esta propiedad refleja el fallo inyectado al
runtime, no una detección autónoma del PLC ni un diagnóstico de hardware.
`test_timed_fault_objective_requires_continuous_fault_and_sequences_fault_states`
comprueba validación, reinicio del temporizador, duración sostenida y secuencia;
pasaron 38 pruebas de ejercicios y 50 de frontend. El 2026-10-09
`integration/exercise_smoke.py` pasó con Docker real: inyectó y liberó un fallo
de bomba y el objetivo de secuencia observó `[true, false]` mientras el resto
del circuito PLC/Modbus/campo completó con score 100. El muestreo sigue siendo
software nominal y puede demorarse por carga del host; no equivale a seguridad
funcional ni a una señal de diagnóstico de un PLC físico.


### RF-40 · detección de pares fuera de allowlist (2026-10-09)

Se añadió el objetivo `protocol_write_policy` al editor y al esquema de ejercicios.
Vigila un PLC S7 o servidor Modbus TCP, compara la IP peer observada en trazas
nativas de escritura contra una lista exacta por ejercicio y genera el evento
durable `security.protocol_write_policy_violation`, sin registrar el valor
escrito. El ciclo del ejercicio procesa trazas nuevas de forma idempotente y
realiza una recogida final antes de puntuar. `test_protocol_write_policy_detects_only_non_allowlisted_writes_and_is_idempotent`,
la validación de IP duplicadas/malformadas y destino de protocolo, y
`integration/exercise_smoke.py` prueban el detector; el smoke usa clientes
Snap7/PyModbus reales en contenedores y espera detecciones S7 y Modbus. La IP es
solo una observación de red; en rutas interzona puede ser la del gateway. Esta
función detecta para evaluación, no rechaza la petición ni autentica al cliente.
No se hicieron commit, push ni despliegues de producto.

### RNF-10 · límite de sesiones del relay de ingreso (2026-10-09)

El relay protocolario de acceso externo ahora limita el servicio a 64 sesiones
simultáneas mediante semáforo acotado. Cuando se alcanza el límite, cierra la
conexión entrante sin crear un hilo; el `finally` libera el cupo al terminar
cualquier sesión. `plc/tests/test_ingress_proxy.py` cubre rechazo al llenar el
pool, liberación posterior y el valor configurado. Esto reduce el riesgo de
consumo ilimitado de hilos en una publicación optativa. El relay no registra
cada bloque de datos; Docker rota sus logs con el límite fijo de 10 MB × 3 en
el manager y en los bundles Compose exportados. Docker Gate rechaza otra
política de logs para ese rol. Pruebas de manager, gateway y exportación Compose
comprueban la configuración. Estas cuotas acotan hilos y logs, pero no
proporcionan defensa volumétrica ni sustituyen al firewall del host. El acceso
remoto desde otra máquina sigue sin validarse.

### RNF-03/04 · preflight de benchmark de gran escala (2026-10-09)

`integration/capacity_benchmark.py` ahora exige, para más de 30 nodos, opt-in
explícito y tres identidades del entorno aislado: versión de Docker, SHA-256 del
nombre del daemon e identidad exacta `instance_id` de `/api/health`. Si no puede
consultar Docker/API o no coinciden las identidades, aborta antes de enumerar
subredes o crear escenarios. El backend expone el campo opcional
`ANVIL_INSTANCE_ID`; sin configurar permanece el contrato previo
`{"status":"ok"}`. Las pruebas cubren rechazo por falta de opt-in/identidad,
daemon inaccesible y versión no coincidente. Pasaron las 512 pruebas de
`backend/tests` y el build estricto de MkDocs. Esto hace verificable el límite
de seguridad del benchmark, pero **no** mide capacidad superior a 30 nodos: el
daemon disponible comparte previews/servicios y no se lanzó carga grande.
RNF-03/RNF-04 siguen parciales hasta realizar esa medición en infraestructura
aislada y caracterizar carga de clientes/proceso.

### RF-27 / RF-41 · autorización del reinicio completo (2026-10-09)

La revisión de todas las rutas HTTP de escenario encontró que
`POST /api/scenarios/{scenario_id}/restart` no estaba incluido en la política
explícita de operaciones del middleware. Se añadió al grupo de ciclo de vida:
instructor y admin pueden reiniciar; designer y participant se deniegan por
defecto. Las pruebas de política comprueban los cuatro roles y la prueba de
inventario OpenAPI confirma que todas las rutas del escenario tienen al menos
un rol no-admin autorizado. Después del cambio pasaron 524 pruebas backend,
161 de runtime PLC, 35 del Docker Gate y la prueba `test_srs_traceability`.
El smoke RF-27 Docker/API había pasado previamente con contenedor PLC real,
cambio de `StartedAt`, evento durable de auditoría y limpieza del escenario.
El build estricto de MkDocs pasó tras instalar las dependencias en
`/tmp/anvil-docs-deps`, evitando escrituras en `.venv` sobre el sistema de
archivos compartido; no hubo errores ni avisos de enlaces rotos.

### RF-38 · intercepción de escritura de coils FC05/FC15 (2026-10-09/10)

La intercepción del relay admite FC15 para modificar coils seleccionadas en
peticiones de escritura múltiple. El modelo valida espacio `coil` y reemplazo
0/1; el parser comprueba cantidad, byte count, framing y eco, conserva coils
vecinas y padding, y el Docker Gate solo acepta reglas válidas. El editor muestra
coil y estado booleano; el colector valida y persiste metadatos sin valores. Las
pruebas focales (132 parser/API/scheduler/auditoría/frontend y 35 del Docker
Gate) pasaron. `integration/modbus_interception_smoke.py`, con la imagen
`anvil/plc-s7:rf38-fc15-local` construida desde este checkout, pasó en Docker
Engine real: coil apagada solicitada, coil encendida observada en el servidor,
auditoría durable, aislamiento interzona y cero puertos publicados. La prueba de
navegador `exercise_builder_ui.cjs` pasó el 2026-10-10 en Edge, configurando y
concluyendo un ejercicio con FC05/FC15 y objetivos aprobados; el smoke Docker
verifica FC05 end-to-end. FC23 se incorporó en el avance posterior del 2026-10-09; MITM S7 y otras
técnicas del SRS siguen fuera de esta implementación, por lo que RF-38 permanece
parcial.

### RF-38 · reescritura Modbus FC22 Mask Write Register (2026-10-09)

El relay de rutas permite ahora que una regla FC22 fuerce un UINT16: reescribe
AND mask a `0x0000` y OR mask al valor elegido, conserva Transaction ID, Unit ID
y dirección, y solo registra una coincidencia si la respuesta echo devuelve la
misma dirección y máscaras. El modelo de ejercicios, runtime, colector de
metadatos, Docker Gate y editor incluyen FC22. MITM transparente sigue
fuera del alcance; FC23 se cubre en el avance siguiente.

Pasaron 174 pruebas de parser, API/scheduler, auditoría, frontend, Docker Gate y
trazabilidad SRS. `integration/modbus_interception_smoke.py` pasó en Docker real:
forzó el registro a 42330 (`0xA55A`), verificó el eco `0000a55a`, la lectura
posterior, auditoría durable, aislamiento directo entre zonas y cero puertos
publicados. `integration/exercise_builder_ui.cjs` pasó en Edge con backend y
SQLite efímeros: configuró, guardó y ejecutó un ejercicio con la regla FC22,
verificó objetivos aprobados y eliminó el escenario temporal.

### RF-38 · intercepción de FC23 Read/Write Multiple Registers (2026-10-09)

La regla FC23 apunta a un holding register existente. El parser valida ambas
secciones de la petición (read start/quantity y write start/quantity), byte count,
longitud, límites de cantidad y rangos de 16 bits. Conserva el request completo,
por lo que el servidor recibe y almacena sus valores de escritura. El correlator
retiene el rango de lectura por Transaction ID, comprueba Unit ID y función en la
respuesta, sustituye solo los registros del rango leído que coinciden con reglas
y registra metadatos de dirección `response` sin valores. La UI explica que el
segmento de escritura pasa intacto.

Verificación: 26 pruebas de parser/correlator, 150 pruebas de API/scheduler,
auditoría, editor y Docker Gate, más `test_srs_traceability`, todas pasaron.
El smoke Docker `integration/modbus_interception_smoke.py` verificó petición y
respuesta FC23 reales, escritura `0x5678` preservada en el servidor y respuesta
leída sustituida por `0xC0DE`, evidencia durable, aislamiento interzona y cero
puertos publicados. El test de navegador `integration/exercise_builder_ui.cjs`
configuró y guardó una regla FC23, ejecutó la cronología, observó objetivos
aprobados y limpió el escenario efímero. RF-38 sigue parcial por otros incidentes
no implementados y porque no existe MITM transparente.

### RF-38 · lectura de coils y discrete inputs FC01/FC02 (2026-10-13)

El modelo de ejercicio, la validación contra puntos declarados, el editor, el
parser MBAP y el Docker Gate aceptan FC01/FC02 con reemplazo booleano. La
petición se reenvía sin cambios; la respuesta modifica solo el bit indicado,
conservando bits vecinos y padding. El smoke Docker confirmó [1,0,1] antes,
durante y después para una coil y una discrete input; comprobó las dos
evidencias durables, el estado original del servidor, el bloqueo interzona y
la ausencia de puertos publicados. Pasaron 162 pruebas focales. No hay
intercepción S7 ni redirección transparente de tráfico arbitrario; RF-38
permanece parcial.
