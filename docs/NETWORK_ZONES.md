# Zonas Purdue y redes del escenario

## Qué aporta la red a un sistema OT

Los dispositivos OT intercambian datos por redes, pero la red solo permite
alcanzar servicios: no define por sí misma qué variable se lee ni qué significa
su valor. Un cliente necesita conocer la IP o nombre del equipo, el puerto del
servicio, el protocolo y los parámetros del punto. En Modbus también necesita
Unit ID, espacio y dirección; en S7 debe pedir un área y dirección de memoria.
Esas capas se configuran aparte.

En una planta física, switches, routers, VLAN, firewalls y zonas de seguridad
conforman la arquitectura de comunicación. Anvil aproxima parte de esta
topología con segmentos Docker aislados por escenario/zona y proxies TCP
dedicados a los flujos interzona permitidos. El switch y gateway dibujados en
el mapa son recursos representativos salvo donde se indique un runtime
concreto. No se emula una red industrial completa ni una política Purdue
certificada.

## Qué conceptos intervienen

Una **red** permite que dos runtimes intercambien tráfico; una **zona** es un
segmento con una frontera de conectividad; una **ruta** autoriza un flujo
concreto entre zonas. El modelo Purdue clasifica funciones de una instalación
(control, supervisión, operaciones), pero etiquetar una zona como L1 o L2 no
implementa por sí mismo una política de seguridad.

Para una comunicación industrial identifica siempre las capas por separado:
la IP localiza el equipo, el puerto TCP localiza el servicio, el protocolo
define las peticiones que entiende y la dirección identifica el dato dentro
del servicio. Para conceptos de servidor/cliente y puntos, consulta el
[glosario](glossary.md); para distinguir líneas de red, señal y proceso, mira
[cómo leer el mapa](PROCESS_MAP.md).

## Términos de red

Una **dirección IP** identifica un nodo dentro de una red; el **puerto**
identifica un servicio que escucha en ese nodo; el **protocolo** describe cómo
se entienden cliente y servidor; y la **dirección de punto** identifica un
dato dentro del mapa de ese protocolo. Por ejemplo, `172.30.11.10:102` llega
al servicio S7 de un PLC, pero el cliente aún debe solicitar un área/dirección
de memoria. Para Modbus también debe incluir el Unit ID, espacio y dirección.
No son datos intercambiables.

Una **subred** es el rango de IP que comparte una red. Una **zona** es una
frontera de diseño que Anvil implementa mediante una red Docker distinta. La
etiqueta **Purdue** sitúa funcionalmente esa zona (por ejemplo L1 control o L2
supervisión), pero no configura los equipos automáticamente ni certifica la
seguridad de la instalación. Una ruta declarada abre un flujo de servicio
acotado, no una ruta general entre todos los hosts.

Un **switch Ethernet** conecta interfaces de red dentro de un mismo segmento
de capa 2 (L2); reenvía tramas entre esos puertos y no decide qué tag Modbus o
S7 debe leer una HMI. En Anvil, el switch del catálogo representa el bridge
Docker de la zona: es un recurso de red, no un proceso/contenedor adicional.
Un **gateway** es un punto de paso entre segmentos. El gateway de una ruta Anvil
es un proxy TCP de aplicación con destino y puerto definidos; no es un router
IP general ni reproduce inspección de firewall industrial.

| Dato de conexión | Qué identifica | Ejemplo |
| --- | --- | --- |
| IP/nombre | Nodo alcanzable dentro de su segmento | `plc-control` o `172.30.11.10` |
| Puerto de servicio | Servicio que escucha en el nodo destino | S7 `102`, Modbus TCP `502` |
| Protocolo | Formato de petición que entiende ese servicio | S7comm, Modbus TCP |
| Punto/dirección | Dato solicitado una vez conectado al servicio | `DB1.DBD4`, o `unit_id=1`, `holding_register:4` |
| Puerto de escucha de una ruta genérica | Endpoint local del proxy que usa el cliente de origen | `listen_host:listen_port` en la zona origen |

Al usar una ruta, el cliente se conecta al listener del gateway, pero el proxy
reenvía al puerto de servicio del dispositivo destino. Son puertos diferentes.
En las rutas integradas S7/Modbus, Anvil configura ese endpoint en el runtime
HMI/SCADA/PLC correspondiente; en una ruta genérica el usuario configura el
cliente para usar el listener publicado por la API.

## Red frente a conexión de proceso o de señal

Una **zona** agrupa nodos de red; un **enlace Ethernet** declara/representa
conectividad dentro de esa zona; una **ruta** habilita un flujo de servicio
compatible entre zonas. Ninguno de ellos crea un tag, lee datos automáticamente
ni conecta una válvula al PLC. Eso se hace mediante una conexión de señal. El
fluido o una medición derivada se modela con conexiones de proceso. Consulta
[cómo leer el mapa](PROCESS_MAP.md) para distinguir los trazos visuales.

Una **zona de red** es un conjunto de activos con necesidades de comunicación
parecidas y una frontera que limita los flujos hacia otros conjuntos. Purdue
es un modelo de referencia que clasifica funciones: L0 representa el proceso
físico; L1 control básico; L2 supervisión local; L3 operaciones de planta; y
L4/L5 servicios empresariales. La DMZ suele actuar como zona intermedia. Las
plantas pueden organizarse de otra forma: etiquetar una zona como Purdue no
acredita cumplimiento. En Anvil, el nivel es metadato de diseño; el aislamiento
efectivo lo proporcionan las redes Docker separadas y las rutas descritas aquí.

Anvil permite agrupar equipos en zonas etiquetadas L0–L5 o DMZ. Cada zona tiene un ID, nombre, nivel Purdue y subred IPv4. En el editor, abre **Zonas Purdue**, añade las zonas y asigna cada PLC/equipo desde el inspector. Los sensores, actuadores y demás elementos de campo se asignan desde su inspector; los enlaces de señal entre campo y PLC deben permanecer dentro de una zona.

Al guardar y desplegar, Anvil crea una red bridge Docker `internal` con gateway IPv4 `isolated` por cada zona usada. El gateway isolated evita asignar al bridge una dirección del host: las redes internas estándar permiten que los contenedores accedan a servicios que escuchen en ese gateway. Se requiere Docker Engine 28 o posterior. Los contenedores reciben una IP fija de esa subred, un `hostname` configurable y un alias DNS con ese mismo nombre; si se omite, se deriva de `node_id` (por ejemplo, `plc-1`). El nombre debe ser una etiqueta DNS válida y única dentro de la zona. Los nombres resuelven solo desde contenedores conectados a la misma red de zona. Los contenedores solo se conectan a su red asignada. Las subredes repetidas o solapadas, IPs fuera de zona y direcciones reservadas se rechazan. Los escenarios antiguos que no declaran zonas conservan su única red compartida y su formato de entrada.

## Rutas explícitas por servicio

Un SCADA o una HMI puede adquirir/conectar con un PLC S7 o Modbus TCP de otra
zona mediante una ruta TCP declarada. En el inspector, selecciona el PLC remoto
y activa **Permitir ruta TCP S7/Modbus por gateway**. La ruta aparece en el
mapa como **TCP · ruta permitida**; si está desactivada, se muestra bloqueada y
no se crea el proxy. El cliente conserva su selección y reporta fallo de
comunicación porque las redes Docker siguen aisladas.
Para clientes integrados HMI/SCADA, la ruta debe ser del protocolo de destino
(`s7` o `modbus-tcp`): una ruta TCP genérica al mismo puerto es un endpoint manual
y no configura el cliente del panel. El preflight lo marca como destino no
alcanzable y propone sustituirlo por la ruta de protocolo.

Para un `remote_input` PLC→PLC, selecciona el PLC emisor desde el inspector del
receptor y activa la opción de ruta S7. Para una señal PLC↔campo entre zonas,
activa **Permitir señal al runtime de campo mediante gateway HTTP** en el
inspector del objeto conectado. El gateway usa un destino fijo: el runtime
interno de campo, TCP/8765. Las señales conectadas de un escenario deben
pertenecer a una sola zona de campo porque solo se crea un runtime de proceso.

Cada ruta crea un contenedor proxy dedicado, sin `NET_ADMIN`, con una sola IP y
un único destino/puerto de protocolo. La operación del SCADA se origina en su
runtime; las lecturas y escrituras HMI se ejecutan en el contenedor HMI, que usa
la IP local del proxy. El proxy abre el flujo hacia el dispositivo de la otra
zona. El contenedor se
conecta únicamente a las dos redes de esa ruta y no publica puertos en el host.
El guardián de Docker rechaza adjuntar dos redes a contenedores fuera del perfil
de ruta y rechaza redes de otro escenario o zonas no declaradas. S7 usa el
puerto 102; Modbus utiliza el puerto configurado en el dispositivo.

No es enrutamiento IP transparente: es un proxy TCP por servicio con sentido
SCADA/HMI→PLC, PLC receptor→PLC emisor S7 cuando un `remote_input` lo declara,
y PLC↔runtime de campo en TCP/8765 cuando la conexión de señal lo habilita.
Una señal PLC↔campo puede conservarse como ruta declarada pero desactivada; el
mapa la marca bloqueada y Anvil no crea el gateway. Todas las señales
interzona de un PLC comparten el mismo estado de ruta al runtime de campo.
Las rutas TCP genéricas permiten cualquier nodo conectado como origen o destino,
pero cada ruta se limita a un único puerto TCP de destino. No son forwarding IP
transparente ni abren puertos arbitrarios fuera de rutas declaradas. El gateway
representativo del catálogo sigue siendo solo visual.

### Ejemplo: HMI en L2 consulta un PLC en L1

1. Asigna el PLC a una zona L1 y la HMI a una zona L2 con subredes distintas.
2. Declara una ruta con origen HMI, destino PLC y protocolo `s7` (o
   `modbus-tcp` si el destino es ese servidor); habilítala.
3. Mantén en la HMI el PLC y los tags/direcciones que debe consultar. La ruta
   da conectividad al servicio, pero no selecciona tags.
4. Despliega y comprueba que el gateway de la ruta está sano y conectado solo
   a las dos redes declaradas, sin puertos publicados en el host.
5. Lee un tag conocido y verifica valor/calidad. Revisa el área/dirección S7 o
   Unit ID/espacio/dirección Modbus si el cliente conecta pero el punto falla.

Si deshabilitas la ruta, la configuración del cliente se conserva, pero el
flujo no debe llegar al destino. Una ruta TCP genérica no sustituye al tipo de
ruta de protocolo del cliente HMI/SCADA integrado: su endpoint es para un
cliente que el usuario pueda configurar manualmente.

Además de S7/Modbus y del gateway de campo, **Enlaces y rutas** permite definir
una ruta TCP unidireccional desde cualquier equipo con red a otro equipo de una
zona distinta, limitada a un puerto del destino. Al guardar, Anvil reserva un
listener en la zona de origen y crea un proxy con exactamente las dos redes y
el destino/puerto declarados. El proxy comprueba la IP TCP de origen y acepta
solo conexiones del equipo seleccionado en la ruta; otros nodos de la misma
zona no pueden reutilizar ese listener. El endpoint `listen_host:listen_port` se muestra
en el mapa y está disponible en `GET /api/scenarios/{id}/network-routes`. El
cliente de origen debe configurarse para usar ese endpoint; Anvil no reescribe
automáticamente clientes arbitrarios. El acceso directo al destino sigue
bloqueado. No se permiten rutas TCP genéricas dentro de una zona, porque ahí
los equipos ya comparten la red Docker. En **Enlaces y rutas** puedes cambiar
cada ruta entre permitida y bloqueada; cuando está bloqueada, el despliegue no
crea su proxy.

### Latencia simulada en una ruta

Cada ruta habilitada admite **Latencia por sentido (ms)** entre 0 y 10.000;
por defecto vale 0. El proxy espera ese tiempo antes de reenviar cada bloque de
bytes que recibe, en las dos direcciones del flujo. Por ejemplo, una petición
y su respuesta suelen atravesar dos sentidos y acumular aproximadamente dos
retardos configurados, además del tiempo de los runtimes y la red Docker. El
valor aparece también como `latency_ms` en el escenario y en
`GET /api/scenarios/{id}/network-routes`.

Esto permite ensayar clientes ante una ruta lenta. TCP es un flujo de bytes:
los bloques dependen de cómo el sistema operativo agrupe las lecturas, así que
no es una demora de red por paquete ni un modelo de propagación física. Tampoco
garantiza tiempos exactos o de tiempo real. El retardo de ruta se suma al
`response_delay_ms`/jitter configurado en el servidor cuando ambos se usan; un
timeout del cliente puede vencer antes de recibir la respuesta. Las rutas
bloqueadas no despliegan proxy y no transportan datos.

## Enlaces Ethernet del mapa

En **Enlaces Ethernet** puedes guardar y quitar una relación lógica entre dos
equipos de la misma zona. El mapa la dibuja como línea discontinua color ámbar
con la etiqueta **Ethernet · lógico**. Se conserva en el escenario, revisiones,
clonado e importación/exportación. Es una anotación de topología: no añade
puertos, reglas ni contenedores, y no cambia la conectividad real. En una misma
zona, Docker ya conecta los equipos a la red compartida; entre zonas hay que
configurar una ruta de protocolo compatible. El formulario rechaza enlaces
entre zonas para no sugerir que el cableado visual las hace alcanzables.

## Salida controlada para estaciones

En **Enlaces y rutas**, una estación de ingeniería o de participante con su
acceso de red habilitado puede recibir reglas explícitas de salida. Cada regla
autoriza un FQDN completo y un puerto TCP (por defecto 443); no admite comodines,
IPs literales ni dominios locales/reservados. La estación solo obtiene el proxy
cuando tiene al menos una regla habilitada. El proxy HTTP CONNECT se dedica a
esa estación, valida la IP del cliente y resuelve el destino a IPv4 global antes
de conectar; direcciones privadas, locales, reservadas y respuestas DNS mixtas
se rechazan. Los demás contenedores de la zona no se adjuntan a la red de salida.
El proxy no termina ni inspecciona TLS: el cliente conserva la validación TLS
del servidor. Las conexiones no proxy (incluido DNS directo) no constituyen una
regla de salida.

La integración conecta el gateway únicamente al bridge Docker predeterminado
además de su red de escenario, y la política del Docker gateway limita ese
adjunto al perfil de proxy Anvil con allowlist válida. No se publica ningún
puerto en el host. El uso requiere conectividad de salida en el host.
`integration/egress_proxy_smoke.py` comprueba el proxy en Docker Engine real;
`integration/egress_scenario_smoke.py` comprueba además la creación de escenario
por API, despliegue desde el backend a través del gateway Docker, adjunto
restringido al bridge y la política de tráfico. Ambos smokes pasaron localmente
en Docker Engine 29.8.1. Queda pendiente una revisión operativa independiente.

## Limitaciones actuales
- La zona Purdue clasifica la topología; no representa segmentación IEC 62443 completa ni inspección industrial.
- El motor de proceso es único y se ejecuta en una zona. Las señales PLC/campo
  cruzan zonas por la ruta explícita descrita arriba, pero los objetos de campo
  conectados deben pertenecer a una sola zona.
- No hay router L3 ni ACL stateful/arbitraria (por ejemplo, filtros entre
  equipos de una misma zona). Cada flujo cross-zone declarado puede permitirse
  o bloquearse como unidad; los flujos no declarados quedan bloqueados por la
  segmentación. La restricción por IP descrita arriba protege los listeners de
  proxy, pero no filtra el tráfico directo entre equipos de una misma zona.
  Los smokes Docker prueban operaciones SCADA/HMI, enlaces PLC S7 y señales PLC↔campo
  permitidos por ruta, además de conexiones TCP genéricas permitidas y directas
  bloqueadas entre zonas y entre escenarios.
- El enlace Ethernet genérico del mapa es declarativo y solo se admite dentro de
  una zona; no aplica control de acceso ni concede conectividad.
- Los alias DNS son nombres de nodos, no una declaración de servicios industriales, protocolos, puertos disponibles ni reglas de comunicación.

Para repetir la verificación con una imagen local que incluya Python (por defecto `anvil/field:srs-v2`):

```sh
PYTHONPATH=backend .venv/bin/python integration/docker_zone_isolation_smoke.py
```

La prueba crea tres redes bridge internas y contenedores temporales con nombres
únicos: verifica TCP dentro de una zona y el bloqueo directo entre dos zonas y
entre redes que representan escenarios distintos. Elimina esos recursos al
terminar y no toca los escenarios de Anvil.

## Ejemplo de API

```json
{
  "name": "Planta segmentada",
  "network": {
    "subnet": "172.30.100.0/24",
    "zones": [
      {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.11.0/24"},
      {"zone_id": "supervision", "name": "Supervisión", "purdue_level": "L2", "subnet": "172.30.12.0/24"}
    ]
  },
  "devices": [
    {"node_id": "plc-1", "hostname": "plc-control", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.11.10", "zone_id": "control", "config": {"components": []}},
    {"node_id": "scada-1", "device_type": "scada", "name": "SCADA", "ip": "172.30.12.10", "zone_id": "supervision", "config": {"target_node_ids": "plc-1"}}
  ],
  "network_routes": [
    {"route_id": "supervision-control-s7", "source_node_id": "scada-1", "target_node_id": "plc-1", "protocol": "s7", "enabled": true}
  ]
}
```

`network.subnet` se mantiene para escenarios antiguos y como red de respaldo. Cuando se declaran zonas, cada dispositivo usa la subred de su zona. Los IDs solo pueden contener letras, números, punto, guion y guion bajo.

Prueba de extremo a extremo con tres subredes Docker: SCADA y HMI leen/escriben
S7 y Modbus a través de sus gateways; los accesos directos se rechazan.
Construye antes la imagen de runtime SCADA/HMI con sus clientes de protocolo:

```sh
docker build -t anvil/plc-s7:hmi-route-test ./plc
docker build -t anvil/modbus-tcp:hmi-route-test -f plc/Dockerfile.modbus ./plc
docker build -t anvil/scada:hmi-route-test -f plc/Dockerfile.scada ./plc
ANVIL_VIRTUAL_IMAGE=anvil/plc-s7:hmi-route-test ANVIL_PLC_IMAGE=anvil/plc-s7:hmi-route-test \
ANVIL_SCADA_IMAGE=anvil/scada:hmi-route-test ANVIL_HMI_IMAGE=anvil/scada:hmi-route-test \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:hmi-route-test \
PYTHONPATH=backend \
.venv/bin/python integration/tcp_route_smoke.py
```
