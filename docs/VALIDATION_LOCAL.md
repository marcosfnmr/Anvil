# Validación local de Anvil — 1 de octubre de 2026

## Regresión RBAC y suites completas — 2026-10-09

La ruta de solo lectura `GET /api/scenarios/{scenario_id}/services` lista los
servicios declarados por adaptadores para el inspector de escenario. Se añadió
a la allowlist de lectura autenticada; la autorización de propiedad/membresía
del escenario sigue aplicándose por el middleware. Solo admin, instructor y
designer pueden obtener este inventario; participant no ve los puertos
publicados del host. Las operaciones de escritura conservan sus permisos
específicos. La primera ejecución de la suite
backend encontró que el endpoint no era alcanzable por ningún rol no admin. Tras
el ajuste, pasó `test_each_scenario_http_route_is_reachable_by_an_explicit_non_admin_role`
y se repitieron las suites completas: PLC, **121 tests**; backend, **461**;
Docker proxy, **29**. Las tres suites finalizaron correctamente. La suite
backend imprime advertencias deprecadas de Starlette y ResourceWarning de
conexiones SQLite de tests; no causaron fallos.

La documentación se reconstruyó con MkDocs Material 9.7.7 en un entorno
virtual temporal bajo `/tmp` porque la instalación dentro del checkout montado
desde Windows se quedaba en espera de E/S. `tools/build_docs.py` finalizó y el
preview local sirvió portada, capítulo PLC y mantenimiento con HTTP 200. El
preview ya existente en `localhost:8009` no se reinició.

## Preparación de Compose para socket Docker rootless — 2026-10-09

Se ajustó `docker-compose.demo.yml` para permitir escoger el socket Docker y el
UID:GID del gateway mediante `ANVIL_DOCKER_SOCKET` y
`ANVIL_DOCKER_API_USER`, igual que los Compose de desarrollo/preview. La prueba
`test_compose_rootless.py` comprueba las tres configuraciones y que el gateway
mantenga raíz de solo lectura, `cap_drop=ALL` y `no-new-privileges`. `docker
compose config` confirmó que el perfil demo está bien formado tanto con valores
predeterminados como con un socket/UID sustitutos. No se aplicó la configuración
al preview 8008, que sigue corriendo con su proyecto y daemon actuales. El host
continúa sin `newuidmap`/`newgidmap`; el runtime sobre daemon rootless no está
validado todavía.

## Permisos de descarga de evidencias — 2026-10-09

`backend.tests.test_auth.AuthenticationTests.test_evidence_bundle_download_is_limited_to_admin_and_scenario_instructor`
crea un escenario y membresías en SQLite temporal y solicita el endpoint real de
ZIP con cada sesión: admin e instructor reciben `200`, con ZIP y hash del
manifiesto; designer propietario y participant miembro reciben `403`. La prueba
usa el manager simulado para logs y no despliega contenedores ni genera una
captura PCAP. Junto al smoke browser de admin y las pruebas de contenido e
integridad, esto cubre permisos HTTP, descarga y empaquetado por capas; la matriz
de roles todavía no se ejecuta desde navegador ni CI.

## Alcance del último CI publicado — 2026-10-09

GitHub Actions registra un run exitoso para el commit publicado
[`12a51da`](https://github.com/marcosfnmr/Anvil/actions/runs/35992883214). La
API pública de Actions muestra que ese run ejecutó el workflow de ese commit:
compilación, suites PLC/backend, build de PLC/backend y smoke S7. No incluyó los
pasos de `docker_proxy`, Compose rootless, runtimes adicionales ni el navegador
que aparecen en el workflow modificado del checkout actual. Ese archivo y los
300 cambios locales aún no están en GitHub; por eso el resultado remoto no
valida el CI ampliado ni este estado de trabajo. La suite backend local actual
(463 pruebas) sí pasó, junto a PLC (121) y Docker proxy (29) en la revisión
local anterior.

## Escrituras S7 y Modbus desde estación participante — 2026-10-09

RF-38: `protocol_write` ejecuta clientes nativos desde el contenedor
`attacker-station` opt-in. `integration/exercise_smoke.py` creó una estación en
zona L2, PLC S7 y servidor Modbus en L1, con rutas TCP separadas a TCP/102 y
TCP/502. Docker Engine confirmó que ambos gateways eran alcanzables. La
cronología envió una escritura BOOL con Snap7 a `DB1.DBX0.0` y una escritura con
PyModbus a `coil:0`; el smoke leyó ambos valores de vuelta desde los servidores
y exigió eventos de auditoría filtrados por origen, destino, protocolo y ruta.
También pasaron interrupción/restauración de ruta, pausa/reanudación y fail-fast;
el ejercicio principal obtuvo score 100. El smoke retiró su escenario temporal,
no publicó puertos del host y no modificó previews. Las imágenes S7, SCADA y
campo existentes se usaron localmente; la imagen Modbus de la prueba se
construyó como `anvil/modbus-tcp:rf38-dual-protocol-local`.

Pasaron `37` pruebas focales de `test_protocol_attack` y `test_exercises`, además
de `py_compile` para el smoke. Las pruebas unitarias/API validan despacho
Snap7/PyModbus, estación opt-in, origen, destino y ruta; el smoke demuestra el
flujo real dentro de Docker. Esto prueba escrituras iniciadas por el participante,
no MITM: no intercepta ni altera paquetes de otro cliente. GitHub Actions no se
ejecutó para este cambio local.

La prueba `integration/exercise_builder_ui.cjs` pasó en Edge contra Uvicorn en
`127.0.0.1:18006`, una SQLite temporal privada y Docker Engine. Desde el editor
añadió una acción S7 gestionada, cambió un segundo evento a `protocol_write`,
seleccionó la estación opt-in, la coil `coil:0` y la ruta `participant-modbus`,
añadió objetivos de auditoría con filtros y ejecutó el ejercicio. La definición
se guardó con HTTP 201 y ambos objetivos de auditoría se superaron. El primer
intento encontró que cambiar el tipo conservaba `node_id` del evento anterior;
la API lo rechazó por campo extra. El editor ahora sustituye los campos del
evento al cambiar de tipo, y la repetición browser pasó. El escenario de la
prueba eliminó su propio despliegue; se detuvo Uvicorn y no se modificaron los
previews. Esta cobertura comprueba la configuración/flujo de UI; el smoke Docker
anterior comprueba la petición PyModbus nativa y su lectura de retorno.

## Repetición browser de descarga de evidencias — 2026-10-09

Se repitió `integration/evidence_bundle_ui.cjs` contra el checkout actual, con
Microsoft Edge mediante Playwright. Se construyó una imagen temporal del Docker
API gateway desde `docker_proxy/`; Uvicorn usó una SQLite en un directorio
temporal privado y se conectó al gateway por un puerto de loopback temporal. El
smoke inició sesión por bootstrap, creó un escenario con nodos en dos zonas y
verificó en navegador el filtro de pares de la misma zona, la no selección
implícita de captura activa y la descarga del ZIP real (HTTP 200, tipo ZIP,
nombre esperado, archivo no vacío y SHA-256 de manifiesto). El smoke eliminó el
escenario; el proceso terminó correctamente y retiró gateway, imagen, base
temporal y archivo de credenciales. No se desplegaron dispositivos ni se
reiniciaron previews. Esta ejecución valida Chromium-compatible UI mediante
Edge en el host Windows/WSL; no sustituye el paso Playwright Chromium de Ubuntu
en el workflow local, que aún no tiene resultado de Actions.

## Reconciliación automática de nodos ausentes — 2026-10-08

Cuando un escenario activo queda `partial` porque desapareció un contenedor,
el reconciliador periódico añade una operación durable `recreate` al journal.
En el ciclo siguiente se ejecuta bajo el lease del escenario usando el plan
runtime persistido; los fallos quedan reintentables. También se recuperan todos
los contenedores ausentes si la configuración sigue indicando un escenario activo.
No se reconstruyen redes Docker de zona perdidas; la operación queda visible y
reintentable hasta restaurar la red o redeplegar. Los escenarios guardados como
`stopped` no se inician automáticamente. Las pruebas
`backend.tests.test_reconciliation` cubren scheduling parcial y total, replay,
protección por lease y que un nodo activo no se recrea. `integration/node_recovery_smoke.py`
pasó en Docker Engine 29.8.1: eliminó primero uno de dos PLC y después ambos;
comprobó recuperación por scheduler, healthcheck, journal completado, auditoría,
preservación del peer durante la pérdida parcial y ausencia de puertos publicados.
También se corrigió la
serialización JSON del resultado del journal para convertir timestamps `datetime`;
el smoke había revelado que `json.dumps` fallaba después de ejecutar la operación.

Preview: `http://localhost:8003/`. Imagen backend: `anvil/backend:audited`.
PLCs S7: `anvil/plc-s7:s7-links`. Modbus: `anvil/modbus-tcp:ui-review`.

## Comprobaciones

- Backend: 63 pruebas unitarias.
- Runtime de dispositivos: 24 pruebas unitarias, en Python 3.11 con las
  dependencias S7 y Modbus de las imágenes locales.
- Interfaz en Brave: teclado, creación, guardado y reapertura, importación,
  HMI, orden/caudal de bomba S7, escritura Modbus, fallo parcial y móvil.
- Mapa: componentes y relaciones E/S/S7/HMI, posiciones guardadas, visibilidad,
  configuración, válvula y caudal reales, valores remotos y navegación con
  ratón, teclado y zoom sin modificar las posiciones de los dispositivos.
- S7 entre contenedores: valores BOOL/REAL, IP del receptor en las operaciones,
  rechazo de escrituras remotas, emisor detenido, conservación del último dato
  y recuperación automática.
- Docker: IPs configuradas, red del escenario, puertos del host sin publicar,
  estación de participante aislada por defecto y, al habilitarla, conectividad
  Snap7 desde la estación a su zona interna; clientes S7/Modbus reales. Modbus comprobó
  lectura/escritura de coils y holding registers y lectura de input registers.
- Arranque en Brave: listado y creación de escenarios, guardado/reapertura,
  ausencia de errores JavaScript, fallo de catálogo tolerado y módulos versionados.
- Auditoría automática de accesibilidad en escritorio y móvil. Esta comprobación
  automática no equivale a una certificación de accesibilidad.

## Correcciones durante la revisión

- El lienzo tenía un nombre accesible sobre un elemento sin rol adecuado. Se
  añadió el rol `region` y se publicó con una nueva versión de los módulos.
- Los selectores de dos pruebas de navegador coincidían también con las tarjetas
  del mapa. Se limitaron a `select[data-component-type]`.
- Una prueba del canal de eventos dependía de dormir 35 ms. Ahora espera al
  segundo sondeo mediante un evento y comprueba que existe un único watcher.
- La prueba de navegación permite seleccionar explícitamente el escenario de
  auditoría mediante `ANVIL_SCENARIO_ID`.

Los escenarios de prueba se crean y eliminan de forma independiente. La
configuración, disposición y estado inicial de los tres escenarios del usuario
se comparan antes y después de la revisión. No se realizan commits ni push.

## Comandos de pruebas

Desde la raíz del repositorio, con Docker y dependencias instaladas:

Los smokes que hablan con un backend con autenticación usan
`integration/platform_smoke.py`, que inicia sesión automáticamente. Exporta las
credenciales de un administrador desde el entorno; si la base de datos aún no
tiene cuentas, proporciona también el token inicial configurado en el backend.
La lectura interactiva evita guardar la contraseña en el repositorio o en el
historial del shell:

```bash
export ANVIL_TEST_EMAIL=admin@example.test
read -r -s -p 'Contraseña de Anvil: ' ANVIL_TEST_PASSWORD; echo
export ANVIL_TEST_PASSWORD
# Solo si la base aún no se ha inicializado:
read -r -s -p 'Token de bootstrap: ' ANVIL_BOOTSTRAP_TOKEN; echo
export ANVIL_BOOTSTRAP_TOKEN
```

Omite únicamente la lectura y exportación de `ANVIL_BOOTSTRAP_TOKEN` si el
backend ya tiene usuarios. Sigue proporcionando `ANVIL_TEST_EMAIL` y
`ANVIL_TEST_PASSWORD` mediante el gestor de secretos de tu entorno.

```bash
ANVIL_CLEANUP_ORPHANS=false .venv/bin/python -m unittest discover -s backend/tests
.venv/bin/python -m unittest discover -s plc/tests
.venv/bin/python integration/s7_links_smoke.py --base-url http://127.0.0.1:8003
.venv/bin/python integration/docker_network_smoke.py --base-url http://127.0.0.1:8003
```

Para la suite PLC, instala `plc/requirements.lock` y
`plc/requirements.modbus.lock`. Las pruebas de navegador están en
`integration/ui_smoke.cjs`, `workspace_ui.cjs`, `evidence_bundle_ui.cjs`, `process_map_ui.cjs`,
`map_navigation_ui.cjs` y `startup_ui.cjs`; configura `ANVIL_BASE_URL` y
`ANVIL_PLAYWRIGHT_PATH`. No ejecutes pruebas sobre subredes ocupadas.
`process_map_ui.cjs` también crea desde el mapa un enlace Ethernet lógico,
verifica en la demo que se distingue de E/S, S7 y HMI, y en la plantilla de
fábrica que permanece separado de los enlaces de proceso, señal, control y HMI
después de guardar y reabrir el escenario. Antes de desplegar la demo confirma que el
endpoint de diagnósticos identifica actuadores sin señal de mando. El smoke no
despliega contenedores de dispositivos; los despliegues y rutas del runtime se
verifican en pruebas de integración separadas.
`evidence_bundle_ui.cjs` crea y elimina un escenario de prueba, verifica el
control por rol, que la captura no se incluya por defecto y que el navegador
inicie la descarga del ZIP. La estructura y los datos del ZIP se prueban en
`backend/tests/test_api.py` y `integration/packet_capture_smoke.py`.

## Preflight de caminos hidráulicos — 2026-10-08

- El endpoint de diagnósticos detecta objetos de proceso conectados por separado
  que no pertenecen a un camino dirigido continuo desde bomba/variador/tanque
  fuente hasta un tanque receptor o una válvula terminal.
- Las aristas de medición de sensores se excluyen del grafo hidráulico. El aviso
  `field_process_path_incomplete` es consultivo: no invalida ni impide guardar
  un escenario con esquema correcto.
- Tres pruebas API focalizadas cubren una topología completa, fragmentos
  desconectados y distribuidores/colectores completos. La suite backend completa
  pasó: 353 pruebas en 26,5 s. Emitió avisos ya conocidos de Starlette sobre el
  nombre de estado HTTP 422 y un ResourceWarning durante teardown, pero terminó
  con código 0 y todas las pruebas OK.
- `integration/process_map_ui.cjs` ahora comprueba la advertencia para la demo
  conectada y para una copia fragmentada temporal. El navegador no se ejecutó en
  los previews de producto: se ejecutó contra un backend efímero en 8004 con
  SQLite temporal, autenticación desactivada y limpieza Docker de huérfanos
  desactivada. El smoke pasó y eliminó sus escenarios temporales.
- Guía explicativa servida aparte en `http://localhost:8007/GUIA_REVISION_ANVIL.html`.
  Es contenido estático de `docs/`, enlazado solo a loopback y aislado de los
  servicios 8003/8005; no despliega el producto ni altera esos previews.

`integration/exercise_builder_ui.cjs` crea y despliega un PLC temporal,
configura desde el navegador una escritura de salida y un objetivo, ejecuta el
ejercicio y elimina el escenario al terminar. Usa la subred `172.30.246.0/24`,
por lo que comprueba antes que no esté ocupada. Requiere un backend de prueba
aislado con autenticación desactivada, las imágenes PLC disponibles y Playwright
configurado con las dos variables anteriores.

`integration/template_onboarding_ui.cjs` verifica la incorporación sin código:
inicia sesión, crea desde el catálogo «Fábrica · Campo independiente, S7 y
Modbus», confirma los dispositivos, objetos, enlaces y reglas de la plantilla,
la despliega y comprueba mediante la API que el nivel derivado del proceso llega
al 50 % (consigna 45 % más histéresis de 5 %) y que el PLC detiene la bomba y
cierra la válvula de entrada; además compara por API/Snap7 la entrada REAL del PLC
con el nivel del tanque. Requiere
un backend de prueba aislado con autenticación habilitada, imágenes de runtime y
`ANVIL_TEST_EMAIL`, `ANVIL_TEST_PASSWORD`; en una base nueva también
`ANVIL_BOOTSTRAP_TOKEN`. Configura además `ANVIL_BASE_URL`,
`ANVIL_PLAYWRIGHT_PATH` y, si hace falta, `ANVIL_BROWSER_EXECUTABLE`. El smoke
detiene y elimina el escenario temporal al finalizar.

`integration/component_preset_revisions_ui.cjs` recorre en navegador la
biblioteca compartida: edita un preset desde el formulario, detecta una
actualización concurrente como conflicto 409, consulta el historial y restaura
la revisión inicial como una nueva. Requiere un backend aislado con autenticación
habilitada y cuenta admin; una base nueva también necesita
`ANVIL_BOOTSTRAP_TOKEN`. Configura `ANVIL_BASE_URL`, `ANVIL_TEST_EMAIL`,
`ANVIL_TEST_PASSWORD`, `ANVIL_PLAYWRIGHT_PATH` y `ANVIL_BROWSER_CHANNEL=msedge`
o `ANVIL_BROWSER_EXECUTABLE`. Publica y elimina su propio preset al finalizar;
no lo ejecutes contra una biblioteca compartida de producción.

## Elementos de campo — 2026-10-06

- 81 pruebas de backend y 31 de runtimes, incluidas siete del motor de campo.
- `integration/field_smoke.py`: crear la planta sin PLCs, conectar posteriormente,
  S7 y Modbus TCP con clientes reales en Docker, medidas y feedback, retardo,
  atasco/restauración, override, permisos de entradas, ausencia de puertos del
  host y pérdida/recuperación del PLC y del motor de campo. Verificación local
  adicional: fuga de depósito de 6 L/min reduce el volumen en vivo y queda
  incluida en la misma prueba Docker. La prueba cambia la demanda a 12 L/min en
  ejecución y verifica que el runtime refleja 18 L/min de salida total con la
  fuga activa. El depósito calcula presión hidrostática y el smoke sigue esa
  medida hasta un canal REAL S7 y un input register Modbus escalado.
- `integration/field_ui.cjs` en Brave: objetos independientes antes del PLC,
  canales creados al conectar, siete líneas, posiciones y conexiones al reabrir,
  orden/estado/fallo reales, vista móvil y ausencia de errores JS.
- Botón de ejemplo revisado en Brave: válvula, sensor y bomba independientes,
  PLC S7, Modbus y HMI; guardado real desde la interfaz.
- Imágenes locales: `anvil/backend:field-live`, `anvil/plc-s7:field-v1`,
  `anvil/modbus-tcp:field-v1` y `anvil/field:v1`.
- Se mantienen los tres escenarios previos. La copia automática desde modelos
  antiguos y la hidráulica de un proceso completo siguen pendientes. Las líneas
  de señales representan E/S simuladas; el transporte interno es HTTP.

Construcción y arranque con Docker CLI: [FIELD_OBJECTS.md](FIELD_OBJECTS.md).

## Reproducibilidad y pausa de escenarios — 2026-10-07

- `integration/docker_network_smoke.py` pasó contra una instancia temporal de la
  API en `127.0.0.1:8004`, con base SQLite nueva en `/tmp` e imágenes Docker locales.
- La prueba pausó/reanudó el escenario y un nodo, resolvió alias DNS, verificó que
  la red interna bloquea egress y usó clientes S7/Modbus reales.
- Detuvo y volvió a desplegar la misma topología y comparó imagen/digest local,
  variables, hostname, puertos, usuario, límites, capacidades, política de
  seguridad, IPs, alias DNS y configuración de redes. La comparación pasó.
- Esta evidencia es de un único host/daemon. No compara estado interno exhaustivo
  ni demuestra variabilidad de rendimiento de 20–30 nodos o fluidez con 100 nodos.

Comando para repetirlo cuando exista una API temporal en 8004 y estén configuradas
las imágenes locales:

```bash
PYTHONPATH=backend .venv/bin/python integration/docker_network_smoke.py --base-url http://127.0.0.1:8004
```

### Repetición con runtimes construidos desde el checkout — 2026-10-08

`integration/docker_network_smoke.py` volvió a pasar contra una API efímera del
checkout en `127.0.0.1:18006`, con SQLite independiente, autenticación
desactivada y acceso directo al Docker Engine local. S7 y Modbus se construyeron
desde `plc/` con las etiquetas `anvil/plc-s7:ca05-current-20261008` y
`anvil/modbus-tcp:ca05-current-20261008` (IDs `sha256:aafa9025…` y
`sha256:05147673…`). La prueba verificó aislamiento de egress y de un listener
del host, DNS, ausencia de publicación de puertos, clientes Snap7/PyModbus,
pausa/reanudación del escenario/nodo y comparación de las opciones Docker entre
dos despliegues. Modificó salidas S7 y coil/holding register, después comprobó
que DB/M/I/Q y los cuatro espacios Modbus regresaran a sus valores iniciales.

El escenario se detuvo y eliminó; el proceso API se cerró y el puerto 18006
quedó libre. No se usaron los previews. Es una ejecución en Docker Engine
29.8.1 de este host; no prueba estado completo de proceso, otros hosts ni
aislamiento frente a todas las configuraciones posibles.

## Rutas TCP entre zonas — 2026-10-07 y 2026-10-08

- `integration/tcp_route_smoke.py` pasó con Docker local: SCADA adquirió S7 y
  Modbus con calidad `good`; SCADA y HMI leyeron/escribieron salidas S7 y coils
  Modbus a través de proxies independientes. Las conexiones directas desde SCADA
  y HMI a ambas zonas remotas fallaron. Cada gateway solo se conectó a sus dos redes
  y no publicó puertos del host.
- El 2026-10-08 se amplió el mismo smoke con cinco probes iniciados desde los
  contenedores origen: setup S7 y lectura Modbus en los flujos de SCADA y HMI, más
  conexión TCP genérica. Los cinco respondieron; los probes no escriben valores de
  proceso. También se añadió el probe al endpoint de diagnósticos para rutas activas;
  tras desplegar, la interfaz consulta ese endpoint y muestra fallos de ruta en la
  actividad del escenario y en una notificación.
- La prueba usa un UUID y subredes temporales; retiró sus contenedores y redes
  al terminar. No cambió los previews 8003/8005.
- La prueba `integration/s7_peer_route_smoke.py` pasó con dos PLC en zonas
  distintas: BOOL/REAL llegan por un gateway S7 de flujo único, el acceso
  directo está bloqueado, el último dato se conserva al detener el emisor y la
  lectura se recupera al reiniciarlo. Verificó redes y ausencia de puertos host.
- `integration/field_route_smoke.py` pasó con el PLC en L1 y el runtime de campo
  en L0: gateway HTTP/8765, orden/feedback BOOL y lectura REAL operaron; el
  acceso directo PLC→campo falló y no se publicaron puertos del host. El
  2026-10-08 el endpoint de diagnósticos añadió una sonda originada en el PLC:
  GET a una ruta inexistente con respuesta 404, sin intercambio de señales ni
  avance del balance físico; el smoke verificó `field-http: reachable`.
- La variante `--route-state blocked` pasó sin crear gateway: las señales
  permanecieron `waiting`, el acceso directo siguió bloqueado y no hubo puertos
  publicados.
- Suites locales: backend 174/174, gateway Docker 9/9 y runtime PLC 48/48.
  Compose valida (`docker compose config --quiet`); `compileall` y
  `git diff --check` también pasaron.
- No se ha demostrado routing L3, múltiples zonas de campo/runtime en un
  escenario ni ACL generales.

Para repetir la prueba de lecturas, escrituras y aislamiento con las imágenes
locales existentes:

```bash
ANVIL_VIRTUAL_IMAGE=anvil/plc-s7:s7-peer-route-test \
ANVIL_PLC_IMAGE=anvil/plc-s7:s7-peer-route-test \
ANVIL_SCADA_IMAGE=anvil/scada:hmi-alarm-srs \
ANVIL_HMI_IMAGE=anvil/scada:hmi-alarm-srs \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev \
PYTHONPATH=backend .venv/bin/python integration/tcp_route_smoke.py
```

Para el enlace S7 PLC→PLC entre zonas, construye una imagen PLC de prueba y
ejecuta el smoke aislado. Sustituye `anvil/runtime-audit:local` por una imagen
PLC local que tenga las dependencias S7 si no está disponible:

```bash
docker build --build-arg PLC_BASE_IMAGE=anvil/runtime-audit:local \
  -t anvil/plc-s7:s7-peer-route-test ./plc
ANVIL_PLC_IMAGE=anvil/plc-s7:s7-peer-route-test \
ANVIL_VIRTUAL_IMAGE=anvil/plc-s7:s7-peer-route-test \
PYTHONPATH=backend .venv/bin/python integration/s7_peer_route_smoke.py
```

Para probar una señal PLC↔campo entre zonas con el Docker local y una API
temporal aislada (PLC `anvil/plc-s7:s7-peer-route-test`, campo `anvil/field:v1`):

```bash
PYTHONPATH=backend .venv/bin/python integration/field_route_smoke.py \
  --base-url http://127.0.0.1:8014
```

## Duración visible del despliegue — 2026-10-07

El endpoint `POST /api/scenarios/{id}/deploy` incluye
`deployment_duration_seconds`, medido desde el inicio de la orquestación Docker
hasta que los contenedores superan healthchecks. La interfaz muestra ese tiempo
al completar. Pruebas focalizadas tras el cambio: API 25/25, Docker manager 19/19
y frontend 23/23 en la revisión inicial; `compileall`, Compose y `git diff --check`
pasaron. El benchmark PLC indicado a continuación mide 20 nodos en un perfil
local concreto; no caracteriza topologías mixtas ni otros hosts.

El benchmark [`capacity_benchmark.py`](../integration/capacity_benchmark.py) se
ejecutó tres veces con 20 PLC S7 por escenario, cada uno con una válvula BOOL,
un sensor REAL y el healthcheck TCP/102 de la imagen, contra un backend temporal
con SQLite separada. Resultados: 8,555 s, 8,669 s y 8,607 s; mediana 8,607 s.
Véase el [informe del host y límites](benchmarks/RNF04-2026-10-07.md).

## Capacidad mixta de despliegue — 2026-10-08

`integration/capacity_benchmark.py` confirmó 22 nodos mixtos (11 S7 y 11
Modbus TCP) en 9,253 s de API y 10,406 s observados por el cliente, bajo el
objetivo orientativo de 300 s. El daemon local tenía 12 CPU y 8 146 598 144 bytes
visibles; la API usó SQLite aislada y los previews 8003/8005 no se modificaron.
El primer intento de 30 nodos fue rechazado porque el techo era 12 CPU-quota;
después se ajustó el techo predeterminado a 15 CPU-quota y se completó una
muestra de 30 (15 S7 + 15 Modbus) en 11,416 s de API y 14,731 s cliente. La cuota
no es una reserva, los límites individuales permanecen en 0,5 CPU por runtime y
el host tenía 12 CPU. No se midió carga concurrente y la cuota no coordina
escenarios paralelos. Todos los escenarios de benchmark se limpiaron. Véase el
[informe RNF-04](benchmarks/RNF04-2026-10-07.md). Una muestra no acredita
variabilidad ni capacidad en otros hosts. La suite backend pasó ahora 354
pruebas, incluida la prueba del presupuesto predeterminado para el plan mixto de
30 nodos.

## Terminal de estación participante — 2026-10-08

`integration/participant_terminal_smoke.py` crea una base SQLite y un escenario
efímeros, asigna una cuenta participant, despliega una estación con acceso opt-in,
simula la caída abrupta del worker cortando el socket del `exec` y verifica mediante
Docker Engine que el proceso termina y que el gateway libera el seguimiento. Después
ejecuta `id -u` por WebSocket y verifica UID 65534 y auditoría de apertura/cierre.
Levanta la política de `docker_proxy/proxy.py` en un puerto loopback efímero, pasa
las llamadas de Docker manager por ese gateway y retira el escenario/red al terminar.
Solo usa imágenes locales; el ejemplo requiere `anvil/scada:hmi-alarm-srs` y acceso
al Docker socket local:

```bash
docker run --rm --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/backend:/app:ro" -v "$PWD:/workspace:ro" \
  -e PYTHONPATH=/app -e ANVIL_CLEANUP_ORPHANS=false \
  anvil/backend:checkout-test-20261008 sh -lc \
  'pip install --quiet -r /app/requirements-dev.lock && python /workspace/integration/participant_terminal_smoke.py'
```

La prueba unitaria de ruta es `test_participant_terminal_is_limited_to_opted_in_station_and_relays_bytes`;
`test_participant_terminal_exec_is_non_root_and_only_targets_attacker_role` y
`test_exec_is_limited_to_owned_containers_and_non_root_user` y
`test_exec_tunnel_sends_terminal_eof_and_keeps_result_inspectable` comprueban
exec del Docker manager y política del gateway. El smoke integra API, WebSocket, gateway real
y Docker Engine. `participant_terminal_ui.cjs` automatiza con Playwright el login de
un participant, el acceso al inspector, apertura de terminal, ejecución de `id -u`
y salida de la shell. Requiere un backend temporal con auth habilitada, token de
bootstrap, Docker local y una imagen SCADA configurada; establece `ANVIL_BASE_URL`,
`ANVIL_BOOTSTRAP_TOKEN` y `ANVIL_PLAYWRIGHT_PATH` antes de ejecutarlo. La consola
comparte el escenario Compose, pero el smoke de navegador no lanza el stack Compose.


## Diagnóstico de cada enlace de campo — 2026-10-08

En un escenario desplegado, `/api/scenarios/{id}/diagnostics` comprueba cada
binding del bridge PLC↔campo: dirección PLC, ID del sensor/actuador, puerto de
señal y edad del último intercambio correcto (máximo 2 s). El bridge publica esa
calidad en `/tmp/anvil-field-links.json`; la sonda inspecciona el archivo dentro
del contenedor de origen como UID 65534. No llama a `/snapshot`, `/exchange` ni
`/control`, así que no avanza ni modifica el proceso. El resultado incluye un
estado por dirección y participa en el indicador de transacciones de protocolo.

`integration/field_route_smoke.py` ahora espera la ruta HTTP y todos los bindings
y valida sus estados antes de verificar el recorrido de mando, realimentación y
medición. Esta evidencia cubre los bindings configurados, pero no valida que cada
valor esté dentro de un rango de ingeniería específico.


## Recuperación de un nodo sin ocultar fallos del escenario — 2026-10-08

La API y el WebSocket comparan los contenedores vistos por Docker con el plan de
runtime persistido. Si falta un nodo de un escenario activo, lo incluyen como
`missing` y el escenario aparece como `partial` si otros nodos siguen activos o
`error` si no queda ninguno. Los escenarios detenidos no se marcan como fallo.
La reconciliación al iniciar el backend aplica la misma comparación, y la
reconstrucción individual sigue usando solo redes gestionadas ya existentes.

Verificación con Docker Engine local e imagen PLC de prueba:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:s7-peer-route-test \
ANVIL_VIRTUAL_IMAGE=anvil/plc-s7:s7-peer-route-test \
PYTHONPATH=backend .venv/bin/python integration/node_recovery_smoke.py
```

El smoke desplegó dos PLC sin puertos del host, reinició uno, eliminó su
contenedor para simular una caída, confirmó `partial`/`missing` por la API y lo
reconstruyó mediante el endpoint de ciclo de vida. El otro PLC conservó su mismo
ID de contenedor en ambos pasos y la reconstrucción generó un evento de auditoría.
Los recursos efímeros del escenario y la base SQLite de prueba se limpiaron.
La recuperación es explícita/manual; no hay reinicio automático ni garantía de
reconciliación transaccional si el daemon Docker falla durante una operación.


Pruebas tras integrar el contraste del plan persistido: `unittest discover -s
backend/tests` pasó 276/276 y `unittest discover -s docker_proxy/tests` pasó 12/12.
También pasaron `compileall` y `git diff --check`.

El inspector reconoce explícitamente el estado `missing`: ofrece reconstruir el
nodo y desactiva inicio/reinicio/pausa/resume sobre un contenedor inexistente; el
mapa marca el indicador en estado de error y no muestra métricas ficticias. La
suite frontend completa (37 pruebas) valida el contrato del inspector y los estilos;
`node --check` valida la sintaxis de `inspector.js` y `canvas.js`.

La prueba `test_startup_reconciliation_persists_partial_and_missing_runtime_nodes`
comprueba que el reinicio del backend actualiza el escenario y audita los estados
`missing`/`running` observados, mientras que escenarios sin contenedores quedan en
`error`. Las transiciones ya persistidas no se vuelven a emitir si no cambian.

## Auditoría de lecturas protocolarias — 2026-10-08

Las rutas de lectura S7 y Modbus, las lecturas por HMI y los snapshots de SCADA
registran la primera observación y los cambios de calidad `good`, `degraded` o
`failed` por origen, destino y protocolo. El estado se guarda en SQLite para
coalescer el sondeo periódico; no se almacenan los valores leídos. Un fallo
Docker/protocolario genera `protocol.read_failed`, y la siguiente lectura buena
genera `protocol.read_good`.

Verificación: `.venv/bin/python -m unittest backend.tests.test_modbus_api
backend.tests.test_api -q` pasó 45 pruebas. La prueba Modbus repite una lectura
correcta, simula caída y recuperación, verifica tres transiciones y confirma que
ningún valor de proceso aparece en los detalles auditados.

Revalidación posterior: `ANVIL_CLEANUP_ORPHANS=false .venv/bin/python -m
unittest discover -s backend/tests -q` pasó 278/278; proxy 12/12; frontend
(`test_frontend`, `test_runtime_frontend` y `test_srs_traceability`) 39/39.
`compileall` y `git diff --check` también pasaron.

La auditoría del sondeo en background se verifica con
`test_historian_audits_protocol_quality_transitions_without_poll_duplicates`:
el worker SCADA recorre lecturas buenas, calidad degradada, caída de adquisición
y recuperación, sin guardar los valores en los eventos ni duplicar una calidad
sin cambios.

## Integridad de la auditoría — 2026-10-08

Cada evento incluye `previous_hash` y `event_hash` SHA-256, calculados sobre el
contenido canónico del evento y el hash anterior del escenario. El endpoint
`GET /api/scenarios/{id}/audit-events/integrity` valida la cadena sin repararla;
el JSONL también incluye ambos hashes. El arranque migra logs legacy sin hashes
una sola vez y no reescribe una cadena parcialmente formada. Si detecta esa
condición, la verificación reporta el primer evento inválido y una nueva escritura
se rechaza para no continuar sobre una cadena rota.

`test_audit_integrity_endpoint_detects_event_content_tampering` cambia un evento
en SQLite y verifica que la API detecta el ID alterado. La migración aditiva se
prueba con dos eventos legacy y se ejecuta dos veces. Es detección de cambios,
no inmutabilidad: un administrador con acceso a la base podría recalcular hashes;
sin ancla externa tampoco se detecta el truncado del final del log. Al borrar un
escenario, la auditoría se conserva en un archivo lógico con snapshot de acceso.

Revalidación posterior a esta mejora: backend 279/279, proxy 12/12 y frontend/
trazabilidad 39/39; `compileall` y `git diff --check` pasaron.

## Retención optativa de auditoría — 2026-10-08

Compose pasa `ANVIL_AUDIT_RETENTION_DAYS` al backend. El valor omitido o `0`
deshabilita la purga; el rango aceptado es 1–3650 días. El worker corre al iniciar
y una vez al día. Solo borra el prefijo expirado contiguo y conserva su último
hash en `scenario_audit_checkpoints`, de modo que el verificador comprueba el
segmento retenido desde ese ancla. Una purga produce `audit.retention_applied`.
El worker limita el corte para preservar cualquier evento desde el inicio de un
ejercicio `running`, dado que sus objetivos consultan el log desde `started_at`.

`test_audit_retention_keeps_running_exercise_evidence_and_chain_anchor` verifica
el borrado de un evento expirado, la conservación de la evidencia de un ejercicio
activo, la idempotencia del siguiente ciclo y la validez de la cadena retenida.
Al borrar el escenario se conserva el archivo de auditoría, checkpoint y el
snapshot de propietario/membresías; lectura/integridad siguen disponibles con
los mismos permisos, exportación JSONL conserva el bloqueo para participant, y
el resto de rutas del escenario da 404. No existe archivo externo automático ni
retención configurable desde la interfaz.

Verificación tras esta ampliación: backend 280/280, proxy 12/12 y frontend/
trazabilidad 39/39; `compileall` y `git diff --check` pasaron.

El manifiesto de `evidence-bundle.zip` contiene `audit_integrity` y un inventario
SHA-256/tamaño por archivo (excepto `manifest.json`). La prueba
`test_evidence_bundle_packages_audit_scada_csv_and_selected_pcap` valida el ZIP
generado y después modifica el PCAP para comprobar que
`verify_evidence_bundle` detecta la alteración. Esto comprueba consistencia con
el manifiesto, no autentica el manifiesto sin un hash/firma externo confiable.
Después de descargar el ZIP, se puede hacer la misma comprobación sin dependencias
del backend ni servidor activo:

```bash
python3 integration/verify_evidence_bundle.py /ruta/al/anvil-evidence.zip
```

Devuelve JSON y código 0 si los archivos cuadran; cualquier archivo corrupto,
omitido o inválido devuelve un código distinto de 0.
El resultado incluye `manifest_sha256`. Para contrastarlo con un valor que hayas
guardado fuera del ZIP, añade `--expected-manifest-sha256 <hash>`. Esto permite
detectar la sustitución del manifiesto si el hash de referencia se transportó
por un canal confiable separado.

## Temporización de protocolo — 2026-10-08

PLC S7 y Modbus TCP aceptan `response_jitter_ms` de 0 a 10 000 ms, sumado al
retardo fijo configurado. La secuencia pseudoaleatoria está fijada por runtime
para repetir el mismo perfil al reiniciar con la misma configuración. El
transporte Modbus RTU rechaza retardo/jitter de respuesta TCP. Las suites de
backend (281/281) y runtimes PLC (66/66) pasaron; las pruebas dedicadas validan
rango, transferencia al contenedor y aplicación por solicitud en el handler
Modbus y callback S7.

## Interoperabilidad Modbus TCP — 2026-10-08

`integration/modbus_protocol_test.py` pasó con cliente PyModbus estándar contra
el servidor real. Comprueba lecturas FC01–04, escrituras simples FC05/06,
escrituras múltiples FC15/16 con lectura posterior, direcciones límite,
Illegal Data Address (2), Device Failure (4), retardo fijo y desconexión
inducida. En la última ejecución, retardo fijo 150 ms más jitter máximo 50 ms
midieron 155 ms en la primera respuesta.
Esto amplía las pruebas TCP; no demuestra igualdad de comportamiento con todos
los fabricantes.

### Jitter estadístico reproducible

La configuración añade `response_jitter_distribution` (`deterministic`,
`uniform` o `normal`) y `response_jitter_seed` en S7 y Modbus TCP. Uniforme
muestrea de cero al máximo; normal usa media máximo/2 y desviación máximo/6,
recortando el resultado al rango. El modo determinista y semilla 0 preservan la
conducta de escenarios anteriores. RTU rechaza perfiles TCP no predeterminados.

`integration/modbus_protocol_test.py` se ejecutó contra el servidor Modbus TCP
local y un cliente PyModbus estándar: 40 lecturas por distribución, semilla
2026, jitter máximo 60 ms y retardo fijo 20 ms. Uniforme midió media 35,3 ms y
rango observado 0,8–60,9 ms; normal midió media 30,8 ms y rango 1,5–54,2 ms
(mediciones de jitter tras restar el retardo fijo; el scheduler añade variación
de observación). Pruebas unitarias backend/runtime verifican esquema, límites,
semilla repetible, muestra normal centrada, transferencia de configuración,
aplicación al servidor/callback y controles del inspector. Suites completas:
350 backend y 89 PLC, ambas pasaron.

## Fallo controlado de ruta en ejercicios — 2026-10-08

`network_route` programa `interrupt` o `restore` para un `route_id` habilitado
del escenario. El runtime actúa exclusivamente sobre el gateway dedicado y
registra `scenario.network_route_interrupted` / `scenario.network_route_restored`.
`test_timeline_can_interrupt_and_restore_only_declared_network_routes` recorre
la cronología y comprueba el orden de acciones y auditoría;
`test_timeline_rejects_undeclared_network_route` valida el límite de alcance.
El smoke `integration/exercise_smoke.py` pasó con Docker real: el cliente de
prueba situado en la zona SCADA recibió S7 `reachable` antes del evento,
`unreachable` durante la interrupción y `reachable` tras restaurar la ruta. El
ejercicio completó objetivos de señal, proceso y auditoría con score 100. Se
construyeron etiquetas locales `anvil/plc-s7:route-exercise-local` y
`anvil/scada:route-exercise-local`; al terminar, una inspección no encontró
contenedores ni redes Anvil gestionados recientes. CI ejecuta el mismo smoke con
sus imágenes `:ci`.

## Modbus RTU FC15/FC16 — 2026-10-08

El primer smoke RTU falló antes de abrir el túnel: Docker mostró el contenedor
como `unhealthy` y sus logs indicaron `ModuleNotFoundError: No module named
'timing'`. `Dockerfile.modbus` omitía el módulo importado por `modbus_server.py`;
se añadió a la imagen y se reconstruyó con una etiqueta local aislada.
`integration/modbus_rtu_smoke.py` pasó después: un cliente PyModbus serie en un
contenedor separado verificó FC01–04, FC05/06 y FC15/16; la API, la HMI y SCADA
leyeron/escribieron el mismo mapa de seis puntos. Confirmó también cero puertos
publicados en el host. El smoke elimina su escenario al finalizar.

## Rangos de diagnóstico Modbus y configuración desde UI — 2026-10-08

Los registros holding/input admiten límites explícitos opcionales en el espacio
raw UINT16; coils y discrete inputs no muestran límites numéricos. Las lecturas
fuera del rango declarado conservan la calidad de protocolo y se marcan como
`out_of_range`; las escrituras fuera del rango se rechazan. Los rangos no aplican
conversión de escala.

`range_diagnostics_ui.cjs` pasó en Edge contra una instancia Docker temporal
construida desde este checkout. Configuró y guardó límites para una entrada REAL
S7 y un holding register UINT16 Modbus y comprobó los valores leídos de vuelta
por la API, sin errores JavaScript. El flujo detectó y permitió corregir dos
fallos del inspector: se descartaban las opciones de los campos desplegables de
dispositivo y al abrir un escenario existente no se actualizaba el selector de
dispositivos. Pasaron además 81 pruebas dirigidas: Modbus (8), API Modbus (4),
operaciones HMI (18), SCADA (12) y frontend (39), incluyendo regresiones de los
campos desplegables y límites por tipo. `node --check` y `git diff --check`
pasaron. Los previews 8003 y 8005 respondieron HTTP 200 antes/después; no se
reiniciaron ni se atribuye esta verificación a dichos previews. Se retiraron el
contenedor, volumen e imagen temporales de 8004.

## Variador de frecuencia como objeto de campo — 2026-10-08

Se añadió `variable_speed_drive`: entrada REAL de velocidad 0–100 %, velocidad
real, salida de caudal, retardo y fallo, con caudal proporcional a la velocidad
nominal configurada. La señal puede enlazarse a salida REAL S7 o a un holding
register Modbus con escala UINT16 explícita, y el puerto físico `flow` conecta a
tuberías o depósitos. El modelo rechaza consignas y velocidades iniciales fuera
de límites. No emula el circuito eléctrico, frecuencia física, curva par/velocidad,
inercia ni protecciones de un variador comercial.

Pasaron `test_field_objects.py` (incluye validación de rango, enlace S7 REAL y
puerto a depósito), `test_frontend.py` (40 pruebas, catálogo/inspector y conexión
de consigna) y `plc/tests/test_field_runtime.py` (17 pruebas; consigna, caudal,
balance del tanque, retardo y fallo/recuperación). `test_srs_traceability.py`,
`node --check backend/static/field-editor.js` y `git diff --check` también pasaron.
`integration/vfd_smoke.py` comprueba adicionalmente el VFD desplegado con S7
REAL, tubería, depósito y fallo/recuperación.

## Variador de frecuencia — 2026-10-08

- `integration/vfd_smoke.py` pasó con PLC S7, HMI y runtime de campo en
  contenedores Docker aislados. Una escritura REAL de 50 % por API produjo
  40 L/min en el variador, la tubería limitó el caudal a 30 L/min y el depósito
  recibió flujo. El fallo redujo el flujo del VFD a cero y al reset volvió a
  40 L/min. El escenario temporal se detuvo al terminar.
- Construye imágenes locales desde el checkout para repetirlo:

```bash
docker build -t anvil/plc-s7:vfd-local -f plc/Dockerfile plc
docker build -t anvil/field:vfd-local -f plc/Dockerfile.field plc
docker build -t anvil/scada:vfd-local -f plc/Dockerfile.scada plc
```

En un entorno Python con los extras de desarrollo backend instalados y acceso
al Docker daemon:

```bash
PYTHONPATH=backend:plc \
ANVIL_PLC_IMAGE=anvil/plc-s7:vfd-local \
ANVIL_FIELD_IMAGE=anvil/field:vfd-local \
ANVIL_HMI_IMAGE=anvil/scada:vfd-local \
python integration/vfd_smoke.py
```

Este caso cubre el modelo lineal del VFD, no curvas eléctricas/mecánicas ni
características de un variador físico.

## Estación de ingeniería — 2026-10-08

- `integration/engineering_station_smoke.py` pasó con Docker Engine real. Verificó
  una estación aislada (`NetworkMode=none`) y otra conectada por opt-in solo a
  su zona interna, lectura S7 al PLC desde Snap7 dentro de la estación, terminal
  como UID 65534 y ausencia de puertos publicados en el host. El escenario,
  sus contenedores y la red se eliminaron al acabar.
- `test_devices.py` (26), `test_field_objects.py` (41),
  `test_runtime_frontend.py` (1), `test_auth.py` (17) y
  `test_docker_manager.py` (39) pasaron; `docker compose config --quiet` pasó.
- La imagen se configura con `ANVIL_ENGINEERING_IMAGE`; en Docker Compose se
  agrega automáticamente a la allowlist. Si se omite, usa la imagen de
  participante/HMI. Para repetir el smoke, construye S7 y un runtime con Snap7:

```bash
docker build -t anvil/plc-s7:engineering-local -f plc/Dockerfile plc
docker build -t anvil/scada:engineering-local -f plc/Dockerfile.scada plc
```

Desde la raíz del checkout, con las dependencias de desarrollo backend
instaladas y acceso al Docker daemon:

```bash
PYTHONPATH=backend:plc \
ANVIL_PLC_IMAGE=anvil/plc-s7:engineering-local \
ANVIL_ENGINEERING_IMAGE=anvil/scada:engineering-local \
python integration/engineering_station_smoke.py
```

La estación no incluye TIA Portal ni herramientas propietarias de Siemens.

## Switch Ethernet — 2026-10-08

`integration/ethernet_switch_smoke.py` pasó con Docker Engine real: desplegó un
HMI y una estación cliente en el bridge de zona y comprobó un intercambio TCP
entre contenedores. El switch aparece como recurso de red; no crea un contenedor.
El smoke limpia el escenario que crea. Requiere imágenes locales con Python:

```bash
PYTHONPATH=backend \
ANVIL_HMI_IMAGE=anvil/scada:vfd-local \
ANVIL_PARTICIPANT_IMAGE=anvil/scada:vfd-local \
.venv/bin/python integration/ethernet_switch_smoke.py
```

El modelo representa una LAN compartida. No implementa VLAN, STP, filtrado por
puerto ni routing IP.

## Revalidación al desplegar — 2026-10-08

El endpoint de despliegue vuelve a validar el escenario persistido después de
adquirir el lease y antes de fijar imágenes o invocar Docker. Un registro con
una IP fuera de su subred devuelve HTTP 422 con errores accionables y conserva
el escenario detenido. Las advertencias de topología siguen siendo consultivas;
la UI presenta la confirmación antes de continuar.

Pruebas dirigidas:

```bash
PYTHONPATH=backend .venv/bin/python -m unittest \
  backend.tests.test_api.ScenarioApiTests.test_deploy_revalidates_stored_topology_before_touching_docker \
  backend.tests.test_api.ScenarioApiTests.test_deploy_status_and_stop_lifecycle \
  backend.tests.test_api.ScenarioApiTests.test_topology_diagnostics_reports_actionable_incomplete_setup_warnings -v
```

Resultado: 3 pruebas pasaron; se verificó también que el error no llama a
Docker y no cambia el estado `stopped`.

## Conflicto de puertos publicados — 2026-10-08

La validación del esquema ahora rechaza dos servicios de un mismo escenario
que solicitan el mismo puerto publicado en el host, antes de persistirlo. La
comprobación Docker anterior permanece para conflictos encontrados al crear
recursos. Pasaron las pruebas API de duplicado y valores IP inválidos, más la
prueba del gestor Docker que garantiza el rechazo previo a crear la red.

## Inventario de servicios OT — 2026-10-08

`GET /api/scenarios/{scenario_id}/services` devuelve los listeners que declaran
los adaptadores S7 y Modbus, sus puertos efectivos, identidad de red y binding
host opcional. La prueba API cubre S7/102, Modbus TCP en puerto 1502, túnel RTU
raw-TCP en 1503, el endpoint interno SCADA/8766 y la publicación S7 por
loopback. No comprueba salud del runtime; esa sigue siendo la función de
estado/healthcheck y de las sondas protocolarias.

El inspector consume el inventario al abrir un escenario y después de guardarlo.
Si la definición tiene cambios sin guardar, la vista identifica el inventario
como perteneciente al último guardado; no presenta el puerto anterior como si
fuera el de la configuración nueva. `test_runtime_frontend.py` comprueba la
conexión del inspector y el cliente API, y Node 24 completó `--check` para los
módulos `app.js`, `api.js` e `inspector.js`. También pasaron las suites
`backend.tests.test_runtime_frontend` y `backend.tests.test_frontend` junto con
la prueba API focal de inventario (52 pruebas en total).

El 2026-10-08 el preview 8008 se actualizó solo en el servicio backend con la
imagen `anvil/backend:srs-rf26-service-inventory-ui-20261008`. Antes se guardó
una copia SQLite consistente en
`/tmp/anvil-demo-pre-rf26-service-inventory-ui-20261008.db` (modo 0600). La base
tenía un escenario detenido, cero leases y una cuenta; tras el reinicio
conservó los tres. `/api/ready` indicó DB/Docker `ok` y el backend quedó
healthy. Se verificaron OpenAPI y el módulo del inspector. El gateway y el
volumen persistente no se recrearon. Los previews 8003, 8005, 8009 y 8010
respondieron HTTP 200. La documentación compiló con `tools/build_docs.py`.

## Rutas de campos en errores de validación — 2026-10-09

El cliente HTTP del navegador ya no descarta `loc` en errores estructurados de
FastAPI. El mensaje identifica el campo y el índice de lista, por ejemplo
`devices[0].config.rack: Field required`, y conserva varios errores separados
por `·`. El smoke `integration/workspace_ui.cjs` incluye un caso con dos errores
simultáneos; el mismo contrato pasó en Node 24 al inyectar una respuesta 422.
También pasaron `backend.tests.test_frontend` y
`backend.tests.test_runtime_frontend` (51 pruebas). La sintaxis de `api.js` y
del smoke se comprobó con `node --check`. `workspace_ui.cjs` pasó completo en
Microsoft Edge contra una API efímera con SQLite temporal: envió una respuesta
422 simulada al despliegue y verificó ambas rutas en el toast y en el registro
de actividad, además de las comprobaciones habituales de workspace. No se
iniciaron contenedores OT; al cerrar el servidor se retiró la base efímera.
RNF-07 sigue parcial por las demás categorías de fallos aún no probadas con
operadores.

El preview 8008 se actualizó solo en backend a
`anvil/backend:srs-rnf07-validation-path-20261008`. La copia previa está en
`/tmp/anvil-demo-pre-rnf07-validation-path-20261008.db` (modo 0600); el único
escenario estaba detenido, sin leases, y había una cuenta. Después se confirmó
backend healthy, `/api/ready` con DB/Docker `ok`, el módulo `api.js` servido con
la ruta de error y la conservación de escenario/cuenta. El gateway y volumen
persistente no se recrearon; los previews 8003, 8005, 8009 y 8010 respondieron
HTTP 200.

## Confinamiento del backend y diagnóstico rootless — 2026-10-09

Se construyó `anvil/backend:rnf09-local-20261009` desde el Dockerfile y el
checkout actuales. `integration/backend_security_smoke.py` pasó sobre Docker
Engine 29.8.1: el contenedor de prueba arrancó en red `none`, con raíz de solo
lectura, `cap_drop=ALL`, `no-new-privileges`, límite de 256 PIDs, `/tmp` sin
ejecución, `/data` efímero y sin montar el socket Docker real. `/api/health`
devolvió 200 y la base SQLite quedó en modo `0600`. El smoke retiró el
contenedor; la imagen local queda etiquetada para inspección. No se reiniciaron
servicios Anvil.

El daemon activo reporta Docker 29.8.1 y opciones `seccomp`/`cgroupns`, sin
`rootless`. `dockerd-rootless-setuptool.sh check` se detuvo porque el daemon
rootful de `/var/run/docker.sock` está activo y accesible. El diagnóstico
`dockerd-rootless-setuptool.sh --force check` informó que faltan `newuidmap` y
`newgidmap` (paquete `uidmap`); sí hay rangos para el usuario en `/etc/subuid` y
`/etc/subgid`, pero no existe socket Docker en `/run/user/1000/docker.sock`.
No se instaló software ni se cambió, detuvo o reconfiguró el daemon compartido.
Por ello, la configuración Compose que acepta un socket rootless sigue sin una
prueba de runtime en este host.

### Backend no-root tras preparar el volumen — 2026-10-09

La revisión posterior añadió un bootstrap mínimo para que los tres perfiles
Compose puedan dejar Uvicorn con UID/GID 65534 aunque el volumen SQLite ya tenga
propietario distinto. El contenedor de arranque recibe CHOWN/FOWNER/SETGID/SETUID
solo para preparar el directorio dedicado `/data`, la base y sus sidecars;
después elimina grupos suplementarios y ejecuta Uvicorn con `CapEff=0`.
Se construyó la imagen usando el contexto real `backend/` y
`integration/backend_security_smoke.py` pasó en Docker Engine 29.8.1: raíz de
solo lectura, health 200, SQLite propiedad de 65534:65534 en modo 0600, proceso
UID 65534 y ninguna capability efectiva. También pasó la suite backend completa
(491 pruebas), tres pruebas focales de entrypoint/Compose y la validación de los
tres Compose. El smoke usó `/data` efímero y una API Docker simulada; no accedió
al daemon real ni reinició previews. El UID/GID de servicio se puede ajustar
mediante `ANVIL_BACKEND_UID`/`ANVIL_BACKEND_GID`; el directorio montado debe
estar dedicado a SQLite.

## Descarga de paquete de evidencias desde la UI — 2026-10-09

`integration/evidence_bundle_ui.cjs` pasó en Microsoft Edge contra una API local
con SQLite efímera y autenticación habilitada. El smoke creó la cuenta inicial
de admin con un token de prueba temporal, inició sesión y la UI cargó el rol
autenticado antes de habilitar PCAP/exportación. El escenario detenido verificó
que el origen pueda elegirse entre los dispositivos, que el destino se limite a
un peer de la misma zona, que una captura activa no se seleccione por defecto y
que el botón descargue el ZIP real generado por el endpoint. La respuesta fue
HTTP 200, incluyó la cabecera SHA-256 del manifiesto y produjo un archivo no
vacío con nombre esperado. Esto acredita el camino permitido de admin, no las
denegaciones RBAC para otros roles. Las peticiones de creación y borrado enviaron
el `Origin` esperado por la protección CSRF. El test backend
`test_evidence_bundle_packages_audit_scada_csv_and_selected_pcap` comprueba el
contenido/manifiesto con captura, y las cinco pruebas de
`backend.tests.test_evidence_bundle_cli` validan integridad offline, hash externo
y rechazo de manipulación/rutas inseguras. También pasó la prueba de cobertura
de identificadores del SRS. La comprobación final de SQLite encontró cero
escenarios y cero leases; la cuenta temporal, API y base se retiraron. No se
modificaron los previews. El primer intento detectó y corrigió dos problemas del
smoke: las mutaciones omitían `Origin`, y el clic PCAP podía adelantarse a la
carga del rol autenticado.

## Reescritura Modbus FC16 en ejercicios — 2026-10-09

El relay de las rutas Modbus TCP admite FC16: dentro de una petición múltiple
reescribe solo los registros declarados por reglas activas, conserva los demás
valores y valida el eco de cantidad/dirección del servidor. La cola de auditoría
emite una entrada por cada regla coincidente, sin guardar valores ni tramas.

Pasaron 155 pruebas focales de parser, API/scheduler, colector de auditoría,
contrato del editor y política Docker:

```bash
PYTHONPATH=plc:backend:docker_proxy .venv/bin/python -m unittest \
  plc.tests.test_modbus_interceptor backend.tests.test_exercises \
  backend.tests.test_protocol_trace_collector backend.tests.test_frontend \
  docker_proxy.tests.test_policy -q
```

`integration/exercise_builder_ui.cjs` pasó en Edge contra Uvicorn en
`127.0.0.1:18006` con una SQLite temporal. Desde el editor eligió FC16, guardó
la regla con su evento `clear`, ejecutó el ejercicio y comprobó los objetivos.

El smoke Docker de extremo a extremo también pasó con score 100. La estación
participante envió una petición FC16 de tres registros: Anvil sustituyó dos
valores, mantuvo intacto el tercero y el servidor devolvió los tres valores
esperados. Dos objetivos auditados, uno por cada regla coincidente, pasaron.
También pasaron FC03/FC06, el proceso de bomba/tanque, las detecciones por IP,
las rutas y la recuperación del ejercicio. El smoke retiró su escenario y sus
contenedores/redes; no publicó puertos del host ni tocó los previews.

Para repetir el smoke con imágenes construidas desde este checkout:

```bash
docker build -t anvil/plc-s7:rf38-fc16-local ./plc
docker build -t anvil/modbus-tcp:rf38-fc16-local -f plc/Dockerfile.modbus ./plc
docker build -t anvil/scada:rf38-fc16-local -f plc/Dockerfile.scada ./plc
docker build -t anvil/field:rf38-fc16-local -f plc/Dockerfile.field ./plc

ANVIL_PLC_IMAGE=anvil/plc-s7:rf38-fc16-local \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:rf38-fc16-local \
ANVIL_SCADA_IMAGE=anvil/scada:rf38-fc16-local \
ANVIL_FIELD_IMAGE=anvil/field:rf38-fc16-local \
PYTHONPATH=backend:plc .venv/bin/python integration/exercise_smoke.py
```

Este avance amplía las funciones de escritura interceptables; no implementa
MITM transparente ni reescribe FC22 o FC23. FC15 y FC05 se añadieron después;
el apartado siguiente recoge la validación Docker de ambas funciones y el test
de navegador que configura FC05/FC15.

## RF-12 · refresco HMI sin reconstrucción de tarjetas (2026-10-09)

Se extrajo el reconciliador DOM a `backend/static/hmi-live-view.js` y se
añadió un caso al smoke `integration/workspace_ui.cjs`. En Edge sobre una API
efímera con SQLite temporal, el caso retuvo referencias DOM de grid/tarjeta/input
y sinóptico durante la actualización, aplicó valores nuevos, conservó el foco y
una consigna local sin enviar, y comprobó que la lectura posterior actualiza el
control tras limpiar el borrador. El smoke completo de workspace pasó; no desplegó
contenedores OT. `backend.tests.test_frontend` pasó 55 pruebas y Node `--check`
pasó para el módulo, `app.js` y el smoke browser.

## Suite backend completa después del cambio HMI (2026-10-09)

`.venv/bin/python -m unittest discover -s backend/tests` pasó **516 pruebas**
en 77,7 s, incluyendo trazabilidad SRS, frontend, runtime, API, seguridad y
recuperación. Los mensajes de `capacity_benchmark.py`, las fallas de auditoría
inyectadas y las advertencias de SQLite/deprecación forman parte de casos de
prueba; la suite terminó con código 0 y `OK`. No despliega contenedores OT ni
valida el estado de runtimes de previews.

## Ajustes de HMI, mapa e interfaz (2026-10-09)

La consola HMI conserva la última lectura correcta si se solicita actualizar
con el escenario detenido o con cambios pendientes. El mapa observa también el
tamaño de los nodos conectados para recalcular el anclaje de las flechas cuando
una tarjeta cambia de dimensiones. El editor reduce las barras de cabecera y los
controles de zonas en ventanas de poca altura y en móvil. La marca de Anvil usa
un SVG de yunque compartido con el favicon.

`backend.tests.test_frontend` pasó 55 pruebas. Después, las suites de API,
objetos de campo, exportación Compose y frontend pasaron juntas: 179 pruebas.
Se cubrieron HMI, enlaces, marca y migración del esquema de escenario.
`git diff --check` terminó limpio. El preview `localhost:8010` sirve la
imagen `anvil/backend:schema-v2-ux-20261009`; `/api/ready` devuelve `ready` con
base de datos y Docker disponibles. La sustitución preservó el volumen
`anvil-rnf-preview-data` y no tocó los demás previews.

## RF-27 · reinicio coordinado del escenario (2026-10-09)

`POST /api/scenarios/{scenario_id}/restart` reinicia los contenedores runtime
planeados con el orden estable por `node_id`. Usa el lease de ciclo de vida,
rechaza escenarios detenidos y conjuntos de contenedores incompletos, espera el
healthcheck después de cada reinicio y audita éxito o fallo. Si falla a mitad,
el backend consulta Docker y guarda el estado observado para que operador y
reconciliador trabajen desde la condición real. La interfaz ofrece **Reiniciar**
con una confirmación que avisa de la pérdida posible del estado volátil. Los
volúmenes de retención configurados no se eliminan por esta operación.

Pasaron 193 pruebas focales de API, gestor Docker y frontend, incluyendo el
reinicio exitoso, inventario parcial, healthcheck fallido, actualización a
estado `partial`, auditoría y controles visibles en UI. El smoke ejecuta el
recorrido contra Docker Engine real desde una API `TestClient`, con SQLite
temporal y autenticación desactivada solo para ese proceso. No modifica el
preview ni necesita sus credenciales. Construye primero la imagen PLC y ejecuta:

```bash
docker build -f plc/Dockerfile -t anvil/plc-s7:ci plc
PYTHONPATH=backend:integration .venv/bin/python integration/scenario_restart_smoke.py --local
```

El smoke crea un escenario temporal con un PLC, comprueba que `StartedAt`
cambió y que existe el evento de auditoría; después detiene y elimina el
escenario de prueba. La ejecución aislada pasó el 2026-10-09 con Docker Engine
29.8.1 y la imagen local `anvil/plc-s7:demo-20261009-current`. Sin `--local`, el
smoke usa `--base-url` (por defecto `http://127.0.0.1:8004`) y requiere las
credenciales `ANVIL_TEST_EMAIL` y `ANVIL_TEST_PASSWORD` si ese backend exige
autenticación.

### Autorización del reinicio

La revisión completa del backend detectó que `POST /restart` no estaba en la
lista explícita de operaciones permitidas del middleware de roles, aunque la
ruta ya comprobaba su contrato de ciclo de vida. Ahora se permite a
`instructor` y `admin`; `designer` y `participant` reciben denegación por
defecto. `test_role_route_policy_defaults_to_deny_for_unlisted_scenario_operations`
y `test_each_scenario_http_route_is_reachable_by_an_explicit_non_admin_role`
lo verifican. Tras la corrección pasaron las 524 pruebas backend, 161 pruebas
PLC y 35 pruebas del Docker Gate; la prueba de cobertura de identificadores SRS
también pasó. La autorización se aplica al reinicio del escenario completo; las
operaciones por nodo conservan la política de ciclo de vida existente.

## RF-38 · intercepción Modbus FC05/FC15 en escrituras de coils (2026-10-09/10)

El evento de ejercicio `modbus_interception` admite FC05 y FC15 sobre coils
existentes: el valor de sustitución es estrictamente 0/1. FC05 conserva el
formato de protocolo 0x0000/0xFF00; FC15 modifica
solo los bits seleccionados en la petición, preserva bits vecinos y padding, y
valida el eco del rango. La evidencia durable registra función, dirección,
ejercicio y regla, nunca los valores transmitidos. El editor permite elegir una
coil y estado activo/inactivo. FC23 se añadió en el avance siguiente; otras funciones siguen fuera del
alcance de la intercepción.

La revisión FC15 anterior pasó 132 pruebas focales de parser, API/scheduler,
colector y frontend, y 35 de Docker Gate. Tras añadir FC05, la suite combinada
de parser, API/scheduler, colector, frontend y Docker Gate pasó 170 pruebas.
El smoke Docker de tres contenedores confirmó que una
estación envía una coil apagada, la ruta interzona la modifica a encendida y el
servidor conserva el estado; también verificó evidencia durable, bloqueo del
camino directo y cero puertos publicados. Para repetirlo:

```bash
docker build -f plc/Dockerfile -t anvil/plc-s7:rf38-fc15-local plc
ANVIL_RF38_IMAGE=anvil/plc-s7:rf38-fc15-local \
  .venv/bin/python integration/modbus_interception_smoke.py
```

El humo elimina sus contenedores, redes y volumen temporales. FC05 también
pasó en el mismo smoke Docker: una solicitud de coil desactivada se reescribió
como activada, el servidor devolvió el eco correcto y mantuvo el estado; se
verificaron la evidencia durable, la ruta interzona y cero puertos publicados.
La imagen se construyó desde el checkout usando la base local que ya contenía
las dependencias:

```bash
docker build -f plc/Dockerfile --build-arg PLC_BASE_IMAGE=anvil/plc-s7:rf38-fc15-local \
  -t anvil/plc-s7:rf38-fc05-local plc
```

El smoke se repite con:

```bash
ANVIL_RF38_IMAGE=anvil/plc-s7:rf38-fc05-local \
  PYTHONPATH=plc:backend .venv/bin/python integration/modbus_interception_smoke.py
```

La prueba de navegador `integration/exercise_builder_ui.cjs` pasó el 2026-10-10
en Microsoft Edge:
configuró coils FC05/FC15 y sus estados booleanos, ejecutó el flujo con acciones
S7/Modbus y reescrituras FC03/FC15/FC16, y confirmó objetivos aprobados y limpieza.

## Intercepción de ejercicios Modbus FC22 — 2026-10-09

Las reglas `modbus_interception` aceptan FC22 para holding registers: el relay
cambia AND mask a `0x0000` y OR mask al UINT16 configurado, conserva Unit ID,
Transaction ID y dirección, y verifica que el eco del servidor coincida con la
petición modificada. La validación del modelo, editor, Docker Gate y auditoría
incluye esta función. El editor de ejercicios fue actualizado para seleccionar
FC22.

La suite focal de parser, ejercicios, trazas, frontend, Docker Gate y trazabilidad
SRS pasó 174 pruebas. La integración Docker `integration/modbus_interception_smoke.py`, con
`anvil/plc-s7:rf38-fc22-local`, también pasó: FC22 fijó el holding register en
42330 (`0xA55A`), comprobó el eco `0000a55a`, la lectura posterior, la evidencia
durable, el bloqueo directo entre zonas y cero puertos publicados. FC23 quedó implementado en el avance siguiente, documentado abajo. `integration/exercise_builder_ui.cjs` pasó en Edge contra backend y SQLite
temporales: guardó y ejecutó el ejercicio con la regla FC22 y confirmó objetivos
aprobados. Su `finally` eliminó el escenario y sus contenedores.

El 2026-10-09 se publicó además la actualización visual del yunque en el preview
reservado `http://localhost:8010/`. La imagen `anvil/backend:ui-hmi-map-compact-ux-20261011`
se derivó de la imagen ya desplegada y solo reemplaza `favicon.svg` y la referencia
de versión en `index.html`; así se conservó la base de código y el volumen
`anvil-rnf-preview-data`. `/api/ready` respondió `ready` y el HTML servido referencia
`anvil-forge-mark-20261011`. El reconciliador HMI, el anclaje adaptativo de flechas
y el CSS compacto ya estaban presentes en la imagen anterior de ese preview.

## RF-38 · intercepción de respuesta FC23 (2026-10-09)

FC23 ya puede configurarse en eventos `modbus_interception` y en el editor. El
modelo asocia el objetivo a un holding register existente. El parser valida
read start/quantity, write start/quantity, byte count, longitud y ambos límites
16-bit; el request completo pasa al servidor sin modificar. El correlator asocia
la respuesta por transaction/unit/function y solo modifica el registro del rango
leído que coincide con una regla. La parte de escritura continúa produciendo su
efecto normal en el datastore.

La suite focal de parser, ejercicios, trazas de auditoría, frontend y Docker Gate
pasó 150 pruebas; `test_srs_traceability` y las comprobaciones de sintaxis Python
/ JavaScript también pasaron. La suite focal pasó 150 pruebas, el parser PLC pasó 26 y `test_srs_traceability`
pasó su comprobación. `exercise_builder_ui.cjs` pasó en Edge: seleccionó FC23,
guardó y ejecutó la cronología hasta aprobar sus objetivos, y eliminó el escenario
temporal. El smoke Docker también pasó; para FC23 el cliente vio 49374 (`0xC0DE`)
y una lectura FC03 posterior confirmó que el servidor había almacenado 22136
(`0x5678`). Se comprobó auditoría durable, bloqueo directo interzona y cero
puertos publicados.

Después de las pruebas, RF-38 FC22/FC23 quedó publicado en el preview reservado
`http://localhost:8010/` con `anvil/backend:rf38-fc23-preview-20261009`,
`anvil/docker-api-gate:rf38-fc23-preview-20261009` y el runtime
`anvil/plc-s7:rf38-fc23-local`. El Docker Gate acepta ese runtime en su allowlist.
El backend y el Gate están healthy, `/api/ready` devuelve `ready` y el backend
sigue montando el volumen preexistente `anvil-rnf-preview-data` en `/data`. No se
recrearon los demás previews ni se desplegó un escenario de prueba persistente.


La compilación estricta de MkDocs no quedó verificada en este avance: la instalación
aislada en `/tmp` agotó el tiempo de espera al conectar a `pypi.org` (`ReadTimeoutError`,
15 s). No se instalaron dependencias dentro del checkout ni se generó un sitio a
partir de documentación sin validar.

## RF-38 · lectura de bits FC01/FC02 (2026-10-13)

Las reglas de intercepción admiten ahora FC01 para coils y FC02 para discrete
inputs existentes. El valor de sustitución es estrictamente 0 o 1. El relay
valida cantidad (1–2000), rango, byte count y longitud, correlaciona la
respuesta por Transaction ID/Unit ID/función y cambia solo los bits elegidos
en el payload LSB-first. Conserva los demás bits, incluido el padding. La UI
ofrece ambas funciones y un control booleano. El Docker Gate valida el comando
fijo y el colector puede asociar cada coincidencia a la regla/ejercicio.

Pasaron 162 pruebas focales de parser, ejercicios/API, frontend y Docker Gate.
El smoke real, ejecutado con `ANVIL_RF38_IMAGE=anvil/plc-s7:rf38-fc01-fc02-local`,
terminó con `result=passed`: FC01 y FC02 se observaron [1,0,1] antes, durante y
después de activar/retirar las reglas. El servidor conservó ambos valores; hubo
evidencia durable para las dos respuestas, bloqueo directo entre zonas y cero
puertos publicados. Los contenedores, redes y volumen efímeros de la prueba se
eliminaron en `finally`. El primer intento detectó una aserción antigua que
presuponía una sola evidencia en la cola; el segundo detectó una lectura del
registro anterior a la escritura en el cliente de prueba. Ambos fallos del
smoke quedaron corregidos y la tercera ejecución pasó completa.


## RF-38 · escritura combinada FC23 — 2026-10-13

FC23 permite ahora elegir si la regla sustituye el rango de lectura en la
respuesta (`phase: read`, predeterminado) o un punto del rango de escritura en
la petición (`phase: write`). Solo se alteran los registros configurados; las
lecturas, otros registros escritos y los límites de la ADU se conservan. Ambas
fases pueden seleccionar la misma dirección y producen evidencia de auditoría
`response` o `request` sin almacenar el valor. El editor ofrece el selector de
fase, y el Docker Gate limita `phase` a FC23.

Pasaron las suites focales: parser Modbus (30), gateway TCP (4), ejercicios API
(40), frontend (59) y Docker Gate (35), 168 pruebas en total. También pasaron
`py_compile`, las comprobaciones de sintaxis JavaScript y `git diff --check`.

El smoke Docker se ejecutó con una imagen PLC construida desde `plc/`, heredando
la imagen local `anvil/plc-s7:rf38-fc23-local` para evitar descargar una base
externa. Verificó FC23 `phase: write`: la estación envió `0x5678`, el gateway
lo sustituyó por `0xBEEF`, el servidor almacenó `0xBEEF`, y la cola durable
registró la petición con su regla/evento y sin valores. También pasaron las
regresiones FC01/FC02, FC05/FC15/FC16/FC22/FC23 lectura, aislamiento directo
interzona y cero puertos publicados. El preview en 8010 no se modificó.

El test browser `integration/exercise_builder_ui.cjs` ahora selecciona también
`phase: write` para FC23; queda pendiente ejecutar esa ampliación con Edge.
