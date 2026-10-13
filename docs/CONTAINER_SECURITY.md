# Confinamiento de contenedores de escenario

Un **contenedor** es una instancia de proceso empaquetada con sus dependencias.
Comparte el kernel del host: no es una máquina virtual ni una frontera
absoluta. Anvil ejecuta cada runtime de dispositivo en un contenedor separado
para aislar su ciclo de vida y conectarlo a las redes que necesita. La imagen
contiene el software; el contenedor es la instancia que se crea desde esa
imagen.

El **daemon Docker** crea contenedores, redes e imágenes y tiene autoridad
elevada sobre el host. Como el backend necesita orquestar dispositivos, el
acceso a ese daemon es una parte especialmente sensible del diseño. La API
filtrada de Docker incluida por Anvil limita las operaciones aceptadas, pero
sigue siendo un componente de confianza de alto impacto; el confinamiento de
los runtimes no elimina ese riesgo.

El gateway Docker de los tres perfiles Compose se ejecuta por defecto como
UID/GID `65534:1001`, sin capacidades Linux, con raíz de solo lectura y
`no-new-privileges`. El GID predeterminado coincide con el grupo del socket del
host usado por el perfil de desarrollo de este checkout; comprueba el grupo real
de `/var/run/docker.sock` en cada host. Si difiere, define
`ANVIL_DOCKER_API_USER=65534:<gid-del-socket>` antes de iniciar Compose. El
backend no monta el socket: solo alcanza la API limitada del gateway por una
red interna de control. El gateway aún puede ejecutar operaciones Docker de alto
impacto permitidas por su política, por lo que no equivale a aislar el daemon.

El orquestador aplica una política fija de mínimos privilegios a cada contenedor creado para escenarios:

- usuario numérico no root `65534:65534`;
- sistema de archivos raíz de solo lectura y `/tmp` temporal con `nosuid,nodev,noexec`;
- `cap_drop=ALL`, `no-new-privileges` y límite de 32 procesos;
- límites de memoria y CPU por familia de dispositivo;
- logs de protocolo PLC/Modbus con el driver `json-file`, limitados a tres
  ficheros de 10 MB por contenedor para acotar disco y ampliar la ventana de
  recogida de auditoría legacy; los runtimes actuales además usan una cola SQLite
  de auditoría en un volumen Docker privado por dispositivo;
- sin contenedores `privileged`, red host, dispositivos ni mounts del host; la única excepción de red host es el perfil de captura de segmento, descrito abajo;
- solo `NET_BIND_SERVICE` para PLC S7 (TCP/102) y Modbus TCP cuando el puerto configurado es menor de 1024.

Las redes de escenarios usan `internal` y `com.docker.network.bridge.gateway_mode_ipv4=isolated`: no tienen egress implícito ni dirección gateway en el host. Se requiere Docker Engine 28 o posterior para este modo. Una prueba crea un listener temporal en el gateway del bridge normal e intenta alcanzarlo desde el escenario; la conexión debe fallar. La publicación protocolaria está desactivada por defecto. Si se solicita explícitamente, el checkout actual crea un relay S7/Modbus y un binding en una IP host concreta (`127.0.0.1` por defecto), pero el probe desde WSL/Windows no alcanza el servicio. Por tanto, ese prototipo no debe usarse como acceso funcional hasta superar las pruebas descritas en [acceso controlado](ACCESS.md). Evita `0.0.0.0` y no abras directamente la red/puerto del escenario como workaround. Si un adaptador requiere más privilegios o persistencia sobre el filesystem raíz, la política debe revisarse con una prueba de protocolo y confinamiento antes de incluirlo.

La salida a Internet sigue bloqueada por defecto para los dispositivos del
escenario. Una excepción explícita está disponible para estaciones de ingeniería
y de participante con acceso de red habilitado: cada una puede tener una lista
de FQDN y puertos TCP permitidos. Anvil despliega un proxy CONNECT dedicado,
adjunto a la red aislada del escenario y al bridge Docker predeterminado; el
gateway Docker acepta ese segundo adjunto solo con la etiqueta de rol y la
allowlist válidas. El proxy valida la IP cliente, rechaza destinos IP literales
y resuelve el destino a IPv4 global antes de abrir la conexión. No termina TLS,
no inspecciona su contenido y no permite DNS directo desde los demás nodos. La
función requiere conectividad de salida del host. Los smokes
`integration/egress_proxy_smoke.py` y `integration/egress_scenario_smoke.py`
verifican el proxy y un despliegue completo mediante API y gateway Docker; no
sustituyen la revisión operativa del host.

## Acceso del backend al Docker Engine

En el despliegue Compose, el backend ya no monta el socket Docker. Se conecta a
`docker-api` por una red de control interna. El gateway permite solo inspección de
versión/salud, listados filtrados, operaciones sobre recursos con etiquetas Anvil,
imágenes runtime configuradas, redes internas de escenario y contenedores que
cumplen la política de seguridad. La inspección de imagen se limita a referencias
allowlisted y etiquetas `anvil-pinned`; crear una etiqueta local de pin requiere
que el ID coincida con una imagen que ya tenga un tag allowlisted y nunca permite
`force`. Rechaza montajes, dispositivos, namespaces del host, redes no
administradas y publicación de puertos fuera del bind explícito del
dispositivo. El modo predeterminado usa `127.0.0.1`; para modo sustituto se admite
una IPv4 concreta del host, nunca una dirección comodín. La imagen del gateway se
endurece como proceso no privilegiado por capacidades, FS raíz de solo lectura y
límites de recursos.

El gateway conserva el montaje del socket Unix y por ello sigue teniendo autoridad
potencialmente equivalente al administrador del host si su propia política o
proceso se compromete. Es una reducción de la superficie de API disponible para el
backend, no una frontera de seguridad fuerte frente a un atacante con ejecución en
el backend ni un sustituto de autenticación en la API de Anvil. Aísla también el
gateway y el backend del acceso desde las redes de escenario.

El backend usa raíz de solo lectura, `cap_drop=ALL`, `no-new-privileges`,
límite de 256 PIDs y `/tmp` tmpfs `nosuid,nodev,noexec` de hasta 512 MiB.
SQLite persiste en el volumen dedicado `/data`; sus archivos quedan con modo
`0600` y el directorio con `0700`, propiedad del UID/GID de servicio. Por
defecto Uvicorn ejecuta como `65534:65534`. Un bootstrap pequeño empieza como
root, prepara únicamente el directorio SQLite dentro de `/data` y luego vacía
grupos suplementarios y capacidades al cambiar al UID/GID configurado. El
bootstrap requiere temporalmente `CHOWN`, `FOWNER`, `SETGID` y `SETUID`;
Uvicorn acaba con `CapEff=0`. No configures SQLite fuera de `ANVIL_DATA_DIR`
ni montes datos compartidos en ese directorio. Si un volumen previo contiene
archivos con otros propietarios, el arranque cambia la propiedad y los modos de
la base SQLite y sus journal/WAL/SHM para que solo el backend pueda acceder. En
instalaciones con mapeo de UID/GID propio, configura
`ANVIL_BACKEND_UID` y `ANVIL_BACKEND_GID` de forma que el contenedor pueda
aplicar esos IDs al volumen.

`integration/backend_security_smoke.py` construye/recibe una imagen y la inicia
en red `none`, con raíz de solo lectura, `/data` efímero y una API Docker
simulada solo para ping/versión. Comprueba la configuración efectiva,
`/api/health`, propiedad/permisos SQLite, UID de Uvicorn y ausencia de
capabilities efectivas; no accede al daemon Docker real.

La consola crea un exec fijo `/bin/sh -i` solo en contenedores etiquetados como estación
attacker o engineering del escenario, con `User=65534:65534`, `Privileged=false` y TTY.
El gateway vuelve a comprobar que el contenedor es gestionado, que su rol es una de esas
dos estaciones, que lleva la etiqueta de acceso opt-in y que exec conserva la identidad
no-root. Rechaza la shell interactiva `exec` en PLC, Modbus, SCADA, HMI, gateways TCP
y capturadores, aunque un cliente conozca su ID. La excepción PLC/Modbus para la cola
de auditoría solo acepta `python protocol_audit.py pending <límite>` y
`python protocol_audit.py ack <cursor>` con directorio, UID, token, etiqueta y volumen
inspeccionados; no acepta comandos alternativos ni stdin. La ruta WebSocket valida el
rol efectivo, la estación opt-in y el estado en ejecución; la autorización del escenario
sigue aplicándose en el middleware API. Se limita una sesión por estación, 16 por
worker y una hora; el túnel Docker cierra tras cinco minutos sin tráfico. Solo se guardan
eventos de apertura/cierre, nunca la entrada del terminal. Si el WebSocket se cierra,
se pierde el worker o vence el timeout del túnel, el gateway envía EOF al `exec` para
cerrar la shell interactiva y libera su registro de seguimiento. La sesión no concede
acceso al socket Docker desde el navegador.

## Ejecutar el stack con un daemon Docker rootless

Los perfiles `docker-compose.yml`, `docker-compose.preview.yml` y
`docker-compose.demo.yml` permiten montar el socket de un daemon separado
mediante `ANVIL_DOCKER_SOCKET`; el gateway usa el socket que se le monta y el
backend sigue accediendo únicamente a la API filtrada del gateway. Si ese socket
pertenece al usuario rootless y Compose se ejecuta contra otro daemon, configura también
`ANVIL_DOCKER_API_USER` con el UID:GID que pueda acceder al socket. En el modo
rootless puede usarse `0:0` si el propio stack se inicia dentro del namespace del
daemon; el valor predeterminado `65534:1001` sirve para un socket rootful cuyo
grupo sea 1001.

Instala Docker Rootless siguiendo la [guía oficial de Docker](https://docs.docker.com/engine/security/rootless/)
y verifica el daemon antes de apuntar Anvil a él. La guía requiere `newuidmap`,
`newgidmap`, rangos de sub-UID/sub-GID y un helper de red rootless; no detengas el
daemon que mantiene servicios existentes solo para habilitar este modo. En Linux:

```bash
export ANVIL_DOCKER_SOCKET="${XDG_RUNTIME_DIR:?}/docker.sock"
export ANVIL_DOCKER_API_USER=0:0
docker --host "unix://${ANVIL_DOCKER_SOCKET}" info --format '{{json .SecurityOptions}}'
```

Confirma que la salida incluye `rootless`. Luego levanta Anvil con ese mismo daemon,
sin cambiar el contexto predeterminado de otros comandos Docker:

```bash
docker --host "unix://${ANVIL_DOCKER_SOCKET}" compose build
docker --host "unix://${ANVIL_DOCKER_SOCKET}" compose up -d
docker --host "unix://${ANVIL_DOCKER_SOCKET}" compose ps
```

Para usar específicamente el perfil demo, añade `-f docker-compose.demo.yml` a
cada comando `compose`. La prueba unitaria
`test_compose_rootless.py` comprueba que los tres perfiles conservan la
configuración explícita del socket y UID y los controles de endurecimiento del
gateway; esa comprobación de configuración no sustituye un smoke de runtime en
Docker rootless.

En este modo las imágenes, contenedores y redes del stack residen en el daemon
rootless. Los previews administrados por otro daemon no se reemplazan. La
publicación solicitada por UI/API usa un relay, pero el acceso S7/Modbus desde
el host no está demostrado en este checkout; no la uses como funcional ni
publiques directamente el puerto del PLC como workaround. La prueba de aislamiento/egress y la
interoperabilidad deben repetirse con el helper de red rootless elegido: esta
variante reduce la autoridad del daemon, pero no se declara verificada en un host
hasta ejecutar esos smokes allí.

## Prueba real

El smoke siguiente crea una red y contenedores temporales; comprueba atributos efectivos del Docker Engine, lecturas/escrituras S7 y Modbus y una operación del runtime de campo. Separa sus recursos por UUID y los elimina en `finally`; no reinicia ni toca los previews:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:srs-v2 \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:phase7-review \
ANVIL_FIELD_IMAGE=anvil/field:srs-v2 \
PYTHONPATH=backend .venv/bin/python integration/docker_security_protocol_smoke.py
```

Usa etiquetas de imagen locales que ya existan en tu daemon o reemplázalas por los nombres que hayas construido.

CI ejecuta este mismo smoke con `DOCKER_HOST` apuntando a un gateway recién
construido, de modo que prueba despliegue, inspección filtrada, operaciones y
limpieza a través de la allowlist efectiva; la selección inicial de subredes usa
el cliente Docker directo del runner para consultar redes existentes, ya que el
gateway deniega inventarios sin filtro.

## Riesgo que permanece

La plataforma incluye autenticación inicial por sesión y roles con propiedad y
membresías por escenario; sigue siendo necesario usar una red de laboratorio
confiable y no exponerla directamente a Internet. El gateway no elimina
el riesgo del socket: la opción de mayor aislamiento es un daemon/host dedicado a
Anvil o una API Docker rootless con controles adicionales. Aún falta una revisión
independiente de escape del gateway y pruebas sistemáticas de denegación de todos
los endpoints peligrosos.

La captura PCAP usa una imagen separada con UID 0, raíz de solo lectura,
`no-new-privileges`, sin mounts ni puertos publicados, hasta 40.000 paquetes con
snapshot de 128 bytes y tmpfs acotado. La captura de un dispositivo comparte solo su
namespace de red; la captura de pareja usa ese mismo namespace y filtra por la IPv4 del
segundo dispositivo, que el gateway comprueba pertenece a una red compartida. Ambos
perfiles reciben `NET_RAW`, `SETGID` y `SETUID`. Para observar todo un segmento,
el capturador comparte el namespace de red host y escucha en el bridge Docker interno
exacto de una zona Anvil; conserva solo esas tres capacidades y el gateway verifica
ID/labels de la red, interfaz, imagen y comando fijo. El gateway permite `exec` solo
en estaciones attacker/engineering y lo bloquea contra PLC, Modbus, SCADA, HMI,
capturadores y proxies TCP de ruta para que las consolas no puedan ejecutar comandos
en runtimes de dispositivos ni convertir un proxy en una ruta de salida arbitraria.
El exportador solo consume logs del sidecar Anvil. El modo host
concede visibilidad de red a nivel de host mientras vive el capturador; aunque el
comando está fijado y no se concede `NET_ADMIN`, es una excepción con mayor impacto y
requiere revisión independiente. La captura de segmento está verificada en Docker
Engine Linux, no en Docker Desktop ni daemon rootless. Si el engine no expone la
interfaz bridge, siguen disponibles las capturas en el namespace de un dispositivo
o de una pareja filtrada por IP.

El motor de ejercicios solo lanza escrituras a salidas mapeadas como escribibles
y acciones de ciclo de vida dentro del mismo escenario. No ejecuta comandos ni
código arbitrario. El scheduler actual requiere una sola instancia/worker del
backend. La política por rol limita operadores y escenarios, pero no convierte el
Docker gateway en un límite suficiente para una instalación hostil. Ver
[cuentas y permisos](PLATFORM_ACCESS.md).

Para levantar esta arquitectura desde la raíz del repositorio, con Docker Engine
disponible en el host:

```bash
docker compose build docker-api backend
docker compose up -d backend
docker compose ps
```

El servicio backend queda publicado en el puerto 8000 según el Compose incluido;
si ya está ocupado, cambia el mapeo local antes de arrancar. Esta configuración
Compose no cambia ni reinicia los previews desplegados por separado.
