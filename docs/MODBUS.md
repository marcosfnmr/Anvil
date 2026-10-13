# Dispositivo Modbus TCP y RTU

## Qué significa hablar Modbus

Modbus es un protocolo cliente/servidor: el cliente inicia solicitudes de
lectura o escritura y el servidor devuelve una respuesta o una excepción. El
protocolo describe cómo localizar datos en un mapa, pero no define el programa
de control ni el significado de ingeniería de cada valor. Un registro que un
fabricante documente como “presión” solo adquiere ese significado por la
configuración de la aplicación y su escala.

Anvil representa un dispositivo Modbus como un servidor software aislado en un
contenedor, con Unit ID, transporte y datastore configurables. La simulación
implementa operaciones Modbus probadas y conserva valores del mapa; no
convierte automáticamente el dispositivo en un PLC. Para tener un ciclo
causal, conecta los puntos compatibles con objetos de campo, o configura un
cliente/PLC que use esos puntos. [HMI](hmi.md) y [SCADA](SCADA.md) son clientes
de adquisición; consulta [fidelidad](DEVICE_FIDELITY.md) para ver el conjunto
concreto de operaciones comprobadas.

### Qué problema resuelve y qué no

En una instalación Modbus suele ser la interfaz de datos entre un cliente
(maestro en terminología histórica) y un servidor (esclavo): identifica un
valor y permite solicitar su lectura o escritura. No define por sí solo una
aplicación, una secuencia de control, una unidad de ingeniería ni el cableado
de un sensor. Un PLC puede ser cliente o servidor Modbus, pero el nodo
`modbus-tcp` de Anvil modela el servidor y su mapa de puntos, no una CPU que
ejecute lógica. Cuando una orden debe afectar al proceso, hace falta una señal
al actuador y un mecanismo que escriba el punto, como lógica de otro controlador
o una operación autorizada.

## Qué es Modbus y cómo se representa

Modbus es un protocolo industrial de petición/respuesta. Un cliente (por
ejemplo, HMI o SCADA) solicita a un servidor leer o escribir direcciones. En
Modbus TCP los datos viajan sobre TCP con una cabecera MBAP; en Modbus RTU se
envían tramas binarias con CRC por un enlace serie. Anvil implementa un
servidor por dispositivo en su propio contenedor. El nodo no es un PLC completo
y el protocolo no define por sí mismo la lógica del proceso.

Una petición identifica primero un **servidor** mediante IP y puerto, luego una
unidad lógica mediante Unit ID y finalmente un espacio, dirección y cantidad.
El **cliente** inicia cada lectura o escritura; el servidor responde a esa
petición. Esto no es una señal continua ni un cable entre puntos. En una
integración física, el fabricante puede definir escalas, orden de bytes,
funciones soportadas y significado de cada registro; el mapa Anvil solo conoce
lo que se configura en el escenario.

El modelo Modbus separa cuatro espacios. **Coils** son bits de lectura y
escritura; **discrete inputs**, bits de solo lectura; **holding registers**,
palabras de 16 bits que pueden leerse o escribirse; e **input registers**,
palabras de solo lectura. El servidor devuelve valores configurados o
actualizados por escrituras admitidas. Una dirección es un índice dentro de
uno de esos espacios. Etiquetas como `00001` o `40001` son convenciones de
documentación que pueden sumar un offset; no son el índice transmitido. En
Anvil, `address: 0` significa índice cero.

Los nombres de los cuatro espacios describen el tipo de dato y las operaciones
estándar disponibles; no son cuatro equipos diferentes. Las direcciones son
independientes: coil 0 y holding register 0 pueden coexistir y representan
puntos distintos. Las etiquetas humanas `00001` y `40001` suelen ser notación
de manual; antes de copiarlas a un cliente, comprueba si ese fabricante cuenta
desde cero o suma un offset.

### Qué representa el dispositivo Modbus de Anvil

Modbus define cómo un cliente direcciona datos de un servidor, no una CPU ni un
programa de automatización. Anvil crea un contenedor con servidor y mapa de
puntos configurado; las escrituras cambian los puntos que el protocolo permite,
pero el servidor Modbus por sí solo no decide que arranque una bomba. Ese valor
debe conectarse a lógica/cliente compatible o a un puerto de campo cuando el
modelo y el mapa de E/S permitan esa conexión.

El `unit_id` no es la IP: la IP selecciona el nodo, el puerto selecciona el
servicio y el unit ID selecciona la unidad lógica de la petición. Después, el
espacio y la dirección seleccionan el dato. El transporte TCP usa MBAP; RTU usa
tramas con CRC sobre el pseudo-terminal y túnel raw documentados abajo.
Ninguno modela temporización eléctrica, electrónica del fabricante ni un
dispositivo físico completo.

`unit_id` identifica la unidad lógica de una petición, no una IP ni un puerto.
Cada cliente necesita dirección de red, puerto, unit ID, espacio, dirección y
cantidad de puntos. Una línea Ethernet del mapa representa conectividad, pero
no crea registros ni asigna señales de proceso. La HMI/SCADA debe seleccionar
explícitamente destino y puntos; para ligar un punto a un objeto de campo se
requiere una conexión soportada por el modelo.

### Qué se configura y qué ejecuta el servidor

En el inspector se configuran Unit ID, transporte/puerto y puntos. Cada punto
declara su espacio Modbus, nombre, dirección opcional y valor inicial. Los
registros pueden llevar rango y etiqueta de unidad; el coil y los registros
escribibles admiten escritura desde las operaciones gestionadas de Anvil según
su tipo y configuración. Al guardar, Anvil valida dirección, duplicados dentro
del espacio, tipo y valor. Al desplegar,
el adaptador crea un contenedor con el servidor y su datastore. El servidor
responde a clientes usando ese mapa. No se carga un programa de lógica Modbus
ni se ejecutan reglas PLC dentro del servidor.

El campo `access` que aparece en el mapa de E/S gobierna las rutas autenticadas
de lectura/escritura de Anvil y los controles de HMI/SCADA. No es una ACL
incorporada al protocolo Modbus. Un cliente que alcance directamente el puerto
del servidor puede emitir las funciones que permita el datastore Modbus; las
redes, publicación externa y reglas de firewall controlan quién puede llegar,
pero no autentican ni autorizan cada escritura según un usuario Anvil. Por eso
el reenvío externo está apagado por defecto y debe exponerse solo a clientes de
confianza y con controles de red apropiados. Véase [Acceso a dispositivos OT](ACCESS.md).

Una **coil** es un bit que el cliente puede leer y, si se autoriza, cambiar. Se
usa a menudo como mando o estado binario. Un **discrete input** también es un
bit, pero de lectura. Un **holding register** es una palabra de 16 bits de
lectura/escritura; puede servir para consignas o valores enteros escalados. Un
**input register** es de solo lectura desde el protocolo y suele representar
una medida. Esas convenciones son habituales, pero Modbus no define por sí solo
el significado industrial de cada dirección.

Modbus no incluye un tipo REAL estándar de 32 bits en un registro único. Si un
valor real se representa en registros, el modelo de aplicación debe especificar
cómo combinar palabras y qué orden usar. En Anvil el punto Modbus ordinario es
UINT16; cuando se conecta un sensor REAL a un registro, la interfaz configura
una conversión explícita al rango entero y presenta el valor bruto del registro.
La unidad escrita en el punto es informativa; no escala el dato por sí sola.

La conversión de una señal se configura en la conexión, no en el nombre ni en
la etiqueta `unit`. Por ejemplo, si un sensor representa 0–100 % y un holding
register usa todo el rango 0–65535, Anvil escala linealmente 50 % a 32768
(redondeado al entero más cercano); 0 % y 100 % corresponden a 0 y 65535. Los
extremos de entrada y salida deben aumentar y el valor se limita al rango
declarado. Un cliente Modbus sigue leyendo un UINT16 bruto: debe aplicar su
propia escala para mostrar porcentajes. La conversión inversa se configura para
llevar una consigna UINT16 al rango REAL del actuador. Esta función no codifica
un REAL IEEE-754 en dos registros ni supone el orden de palabras de un
fabricante.

El mapa es **por espacio**: coil 0 y holding register 0 son direcciones distintas.
La asignación automática busca la primera dirección libre de ese espacio, pero
no convierte un valor a unidades de ingeniería, no lo conecta a campo y no
añade el punto a una lista HMI/SCADA. Son tres pasos separados: crear mapa,
conectar señal y configurar el cliente.

Anvil despliega cada dispositivo `modbus-tcp` en un contenedor independiente de la red aislada del escenario. El campo `config.transport` elige `tcp` (MBAP) o `rtu` (trama RTU sobre una serie virtual). Los puertos del host no se publican por defecto. TCP admite reenvío explícito enlazado a loopback o una IPv4 concreta; no se admite `0.0.0.0`.

En modo RTU, Anvil crea un pseudo-terminal dentro del contenedor y conserva las tramas RTU/CRC. Un túnel TCP raw lleva los bytes hasta ese puerto serie virtual; no encapsula en MBAP. Los clientes PyModbus usan `ModbusSerialClient(port="socket://host:puerto")`. El pseudo-terminal acepta 8N1 y los baudios nominales para el cálculo de silencios RTU; no reproduce tiempos eléctricos, transceptor RS-485, colisiones, topología multi-drop ni errores de cable.

## Construir la imagen

```bash
docker build -t anvil/modbus-tcp:dev -f plc/Dockerfile.modbus ./plc
```

La imagen contiene el proceso servidor y la versión fijada de PyModbus; el
contenedor creado al desplegar contiene una instancia configurada para un nodo
del escenario. El puerto TCP Modbus vive dentro de la red privada Docker y no
se publica en el host salvo que el usuario habilite explícitamente el acceso
externo. Esa publicación no incorpora autenticación por usuario Anvil en el
protocolo directo.

Anvil usa `ANVIL_MODBUS_IMAGE` para seleccionar la imagen. En Docker Compose puede configurarse así:

```yaml
environment:
  ANVIL_MODBUS_IMAGE: anvil/modbus-tcp:dev
```

El repositorio conserva el compose de base; para una ejecución de desarrollo puedes añadir temporalmente esta variable de entorno al servicio backend antes de `docker compose up -d --build`.

## Configuración del escenario

Configurar un punto define su tipo, dirección opcional, valor inicial y
permisos de protocolo. Los espacios tienen direcciones independientes, por lo
que una coil 0 y un holding register 0 no colisionan. Si se omite dirección,
Anvil asigna la primera libre dentro de ese espacio. Esa asignación no conecta
el punto a sensores ni lo hace aparecer automáticamente en HMI/SCADA: el
cableado de campo y la selección de tags del cliente son pasos aparte.

Ambos transportes usan una lista compartida de puntos: `coil` (BOOL R/W), `discrete_input` (BOOL solo lectura), `holding_register` (uint16 R/W) e `input_register` (uint16 solo lectura). Cada uno ocupa su espacio Modbus independiente. Las direcciones son 0–65535; pueden omitirse y se asignan desde cero de forma independiente, saltando las direcciones explícitas ocupadas.

Los holding/input registers admiten límites opcionales `min_value` y `max_value`, que deben declararse juntos, ser enteros crecientes entre 0 y 65535 y contener el `initial_value`. La lectura queda marcada `out_of_range` cuando el valor UINT16 medido queda fuera; HMI, SCADA y la API directa muestran el diagnóstico y rechazan escrituras fuera del rango configurado. Esos límites se aplican al valor entero que expone Modbus: Anvil no convierte automáticamente entre raw y unidades de ingeniería. `unit` es una etiqueta informativa.

```json
{
  "node_id": "modbus-1",
  "device_type": "modbus-tcp",
  "name": "Bomba Modbus",
  "ip": "172.30.100.11",
  "config": {
    "unit_id": 1,
    "port": 502,
    "transport": "tcp",
    "response_delay_ms": 0,
    "response_jitter_ms": 0,
    "fault_mode": "none",
    "fault_probability": 1.0,
    "fault_seed": 0,
    "points": [
      {"type": "coil", "name": "bomba", "initial_value": false},
      {"type": "discrete_input", "name": "presostato", "initial_value": true},
      {"type": "holding_register", "name": "consigna", "address": 4, "initial_value": 1200, "unit": "rpm", "min_value": 500, "max_value": 1800},
      {"type": "input_register", "name": "presion", "initial_value": 32, "unit": "kPa", "min_value": 0, "max_value": 250}
    ]
  }
}
```

No se permiten direcciones repetidas dentro del mismo espacio. Los registros aceptan enteros de 0 a 65535; coils y discrete inputs requieren valores booleanos. Solo se pueden escribir coils y holding registers; discrete inputs e input registers son de solo lectura.

`fault_mode` acepta `none`, `device_failure` (responde con excepción Modbus Device Failure, código 4) o `disconnect` (cierra la conexión sin responder). `fault_probability` acepta un valor entre 0 y 1: 0 desactiva la inyección y 1 (valor predeterminado) mantiene el fallo en cada solicitud, como antes. Los valores intermedios aplican el modo elegido a esa fracción de solicitudes en promedio. `fault_seed` hace reproducible la secuencia al iniciar de nuevo el dispositivo con la misma configuración; las solicitudes comparten una secuencia en el runtime.

`response_delay_ms` es el retardo fijo; `response_jitter_ms` configura un retardo adicional pseudoaleatorio entre cero y el máximo. La distribución puede ser determinista, uniforme o normal truncada, con semilla configurable. Retardo, jitter y fallos inducidos se aplican por solicitud en TCP. Son herramientas de prueba de comportamiento, sin garantías de tiempo real. Los modos de fallo afectan también a las lecturas/escrituras de HMI y SCADA. RTU no admite estos perfiles de respuesta TCP.

Para RTU, usa ID de unidad 1–247 y configura baudios nominales (1200–115200). El pseudo-terminal requiere 8N1. El puerto configurado es el extremo TCP raw del túnel serie (por ejemplo 1502), no un listener Modbus TCP. Retardo y fallos inducidos solo se admiten en modo TCP.

```json
{
  "node_id": "modbus-rtu-1",
  "device_type": "modbus-tcp",
  "name": "Variador RTU",
  "ip": "172.30.100.12",
  "config": {
    "transport": "rtu",
    "unit_id": 7,
    "port": 1502,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "points": [
      {"type": "coil", "name": "marcha", "initial_value": false},
      {"type": "holding_register", "name": "velocidad", "initial_value": 1200, "unit": "rpm"},
      {"type": "input_register", "name": "corriente", "initial_value": 32, "unit": "dA"}
    ]
  }
}
```

## Despliegue con Docker CLI

Construye primero las imágenes y configura la de Modbus para el backend:

```bash
docker build -t anvil/plc-s7:dev ./plc
docker build -t anvil/modbus-tcp:dev -f plc/Dockerfile.modbus ./plc
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev docker compose up -d --build
```

En Windows PowerShell, configura `ANVIL_MODBUS_IMAGE` con `$env:ANVIL_MODBUS_IMAGE="anvil/modbus-tcp:dev"` antes de `docker compose up -d --build`. Al desplegar el escenario desde la interfaz o la API, el backend crea el contenedor Modbus en la red privada del escenario. Confirma que no tenga puertos host publicados:

```bash
docker ps --filter label=anvil.managed=true --format '{{.Names}} {{.Ports}}'
```

La columna de puertos debe permanecer vacía. Para una prueba protocolaria directa dentro de la red aislada, identifica la red y la IP del contenedor:

```bash
docker network ls --filter label=anvil.managed=true
docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <nombre-contenedor>
```

Ejecuta la prueba de lectura/escritura con un contenedor cliente unido a la red del escenario:

```bash
docker run --rm --network <red-anvil> -v "$PWD/integration:/tests:ro" python:3.11-slim sh -c "pip install pymodbus==3.11.4 && python /tests/modbus_smoke.py --ip <ip-contenedor> --unit-id 1"
```

Para RTU, el cliente debe usar un puerto serie `socket://` porque el túnel transporta bytes RTU sin MBAP:

```bash
docker run --rm --network <red-anvil> --entrypoint python anvil/modbus-tcp:dev -c 'from pymodbus.client import ModbusSerialClient; c=ModbusSerialClient("socket://<ip-contenedor>:1502", baudrate=9600, bytesize=8, parity="N", stopbits=1); assert c.connect(); r=c.read_holding_registers(0, count=1, device_id=7); print(r.registers); c.close()'
```

El smoke completo crea un dispositivo y un cliente efímero en la misma red privada, prueba FC01–04 para leer los cuatro espacios, FC05/06 para escrituras simples, FC15/16 para escrituras múltiples y FC22/23 sobre holding registers, y confirma que Docker no publicó puertos del host:

```bash
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev ANVIL_SCADA_IMAGE=anvil/scada:dev ANVIL_HMI_IMAGE=anvil/scada:dev PYTHONPATH=backend .venv/bin/python integration/modbus_rtu_smoke.py
```

Las rutas del panel, HMI y SCADA leen los cuatro espacios; solo permiten escribir coils/holding registers. Discrete inputs e input registers se exponen como solo lectura también desde la API. Las pruebas API son `GET /api/scenarios/{id}/modbus/{node_id}/values` y `POST /api/scenarios/{id}/modbus/{node_id}/write` con `{ "address": "coil:0", "value": true }` o `{ "address": "holding_register:4", "value": 1500 }`.

## Trazas de protocolo y auditoría

El runtime Modbus TCP escribe una línea semántica por solicitud recibida y
respuesta enviada: origen TCP, Unit ID, código de función, operación, espacio,
dirección, cantidad y resultado (`ok`, excepción o desconexión inducida). No
registra los valores leídos/escritos. Estas líneas se pueden consultar en los
logs del contenedor desde **Telemetría → Logs** o por la API de logs del
escenario. Mientras el escenario está activo, el backend sondea esos logs cada
2 segundos e incorpora a la auditoría hash las escrituras y respuestas
fallidas; el cursor por dispositivo evita duplicarlas entre sondeos. La traza
RTU de bytes sigue disponible a nivel DEBUG y no tiene este recolector.

Las líneas del contenedor son evidencia operativa con la retención que ofrezca
Docker; el recolector durable procesa solo escrituras y respuestas fallidas,
no cada lectura correcta. Lee un máximo de 1.000 líneas por contenedor y ciclo:
un volumen superior entre dos sondeos o una rotación de logs puede dejar huecos.
El cursor se ha probado con el worker único documentado de Anvil, no con varias
réplicas concurrentes del backend.
Las escrituras realizadas por endpoints gestionados de Anvil también generan
su evento de auditoría de aplicación. Para conservar evidencia de red usa la
[captura PCAP](api.md#captura-pcap); para comprobar integridad de los eventos
durables consulta [Auditoría](AUDIT.md).

## Pruebas

`integration/modbus_protocol_test.py` arranca el servidor real y lo consulta con
un cliente Pymodbus. Comprueba FC01–04, FC05/06, FC15/16, FC22 (Mask Write
Register) y FC23 (Read/Write Multiple Registers) en TCP; el smoke RTU también
ejercita FC22/23 mediante un cliente serie en otro contenedor. Comprueba además direcciones configuradas
en 65535, excepción Illegal Data Address (código 2)
para huecos y lecturas que rebasan el último registro, retardo+jitter acotados,
Device Failure (código 4) y desconexión inducida. Puede ejecutarse dentro de la
imagen para usar su versión fijada de PyModbus:

```bash
python -m unittest discover -s plc/tests -v
python -m unittest discover -s backend/tests -v
python integration/modbus_protocol_test.py
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:dev ANVIL_SCADA_IMAGE=anvil/scada:dev ANVIL_HMI_IMAGE=anvil/scada:dev PYTHONPATH=backend .venv/bin/python integration/modbus_rtu_smoke.py
docker run --rm --network none -v "$PWD/integration:/tests:ro" -e ANVIL_PLC_DIR=/app -w /app --entrypoint python anvil/modbus-tcp:dev /tests/modbus_protocol_test.py
docker run --rm --network <red-anvil> -v "$PWD/integration:/tests:ro" python:3.11-slim sh -c "pip install pymodbus==3.11.4 && python /tests/modbus_smoke.py --ip <ip-contenedor> --unit-id 1"
```
