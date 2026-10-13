# PLC Siemens S7 en Anvil

Este capítulo explica primero qué hace un PLC y después cómo Anvil representa
un equipo S7. Está escrito para quien conoce la automatización industrial o
recién empieza; los nombres del inspector se relacionan con los conceptos de
memoria, protocolo y proceso.

## Qué es un PLC

Un **controlador lógico programable** (PLC, o autómata programable) es un equipo
industrial que lee entradas, ejecuta un programa de control y actualiza salidas
en un ciclo repetido. Por ejemplo, puede leer el nivel de un depósito, decidir
si arranca una bomba y energizar una salida conectada a un contactor. En una
instalación física también hay CPU, módulos de E/S, alimentación, firmware,
diagnóstico, watchdogs, reloj, memoria retentiva y buses industriales.

En Anvil, `plc-s7` es un **runtime software** que ofrece un servidor compatible
con operaciones S7 sobre TCP/102 y simula un conjunto acotado de señales y
bloques. No es una CPU Siemens virtual ni ejecuta un programa exportado desde
TIA Portal. Sus puntos se configuran desde el inspector/API y el motor aplica
la lógica declarada por el modelo Anvil.

### PLC, E/S y control: conceptos para leer el escenario

Una **entrada** lleva al controlador una observación: un final de carrera, el
nivel medido o el estado de una protección. Una **salida** lleva una orden:
abrir una válvula o arrancar una bomba. En una instalación real los módulos de
E/S convierten entre señales eléctricas y datos; en Anvil un canal es una
definición de software enlazada a un puerto de campo. BOOL representa apagado/
encendido; REAL representa un número de coma flotante de 32 bits. La dirección
S7 asigna esos datos a memoria accesible por clientes.

El **scan** es la repetición del ciclo de control: obtener/actualizar entradas,
evaluar la lógica y actualizar salidas. El parámetro de Anvil es un intervalo
nominal para el runtime; Docker y el sistema operativo deciden cuándo corre de
verdad cada ciclo. No tiene la garantía temporal ni las fases internas de una
CPU Siemens. Los bloques disponibles se describen en [Lógica PLC](PLC_LOGIC.md).

El **servidor S7** no es el programa de control. Es el servicio que responde a
clientes que piden datos. Anvil mantiene la memoria del protocolo, ejecuta su
conjunto documentado de bloques y conecta canales al runtime de campo. Un
cliente puede leer una dirección aunque esa señal no tenga conexión física;
en tal caso verá el valor inicial o el valor que haya escrito una operación
admitida, no una medición de una máquina real.

### Piezas del PLC físico y equivalente del simulador

| Pieza en una instalación | Para qué sirve | Representación y límite en Anvil |
| --- | --- | --- |
| CPU y firmware | Ejecutan el sistema y las instrucciones del fabricante | No se emulan como CPU Siemens ni firmware; Anvil ejecuta procesos software en contenedores. |
| Módulos de E/S | Adaptan señales eléctricas a bits y números | Se representan con canales BOOL/REAL y objetos de campo; no hay tensión, corriente ni electrónica. |
| Memoria | Guarda datos, marcas e imágenes de E/S | Se modelan DB y áreas M/I/Q con buffers y direcciones compatibles con operaciones probadas. |
| Programa de control | Decide cómo cambian las salidas | Se configura con reglas y bloques Anvil disponibles; no compila Ladder/ST ni proyectos TIA. |
| Comunicación | Permite que clientes lean/escriban datos | Servidor S7 compatible mediante `python-snap7` en TCP/102. |
| Scan y watchdog | Ordena el ciclo y detecta bloqueos | Hay scan nominal configurable bajo el scheduler de Linux/Docker; no hay tiempo real duro ni watchdog de CPU física. |
| Ingeniería | Configura CPU, módulos, símbolos y programa | Se usa inspector/API/JSON; no hay descarga, subida ni tabla simbólica TIA. |

Por eso, Anvil reproduce una superficie funcional acotada de datos,
comunicación, lógica y señales. No es una réplica integral ni permite asumir
equivalencia con cualquier modelo o firmware Siemens.

## Qué significa S7 y cómo circulan los datos

S7comm es el protocolo de aplicación usado por clientes y servidores Siemens
para acceder a datos del PLC. Normalmente viaja sobre ISO-on-TCP, en TCP/102.
Hay dos papeles: el **servidor** publica áreas de memoria y el **cliente** inicia
cada lectura o escritura. El cliente abre una sesión, negocia el acceso y
solicita leer o escribir un rango; la conexión Ethernet por sí sola no crea un
flujo continuo de señales. En Anvil, la librería `python-snap7` proporciona el servidor
S7 dentro del contenedor PLC. Un cliente Snap7 compatible —por ejemplo, la
consola de Anvil, una HMI/SCADA del escenario o una prueba de integración—
habla con ese servidor por la red Docker.

Decir que el protocolo está **simulado** significa que Anvil ejecuta un
servidor de software que recibe peticiones S7 y responde con los bytes de sus
áreas de memoria. No significa que emule la electrónica, el firmware o cada
función de todas las CPU Siemens. En la cobertura comprobada, un cliente
Snap7 lee y escribe DB, M y Q, y lee I; la API trata I como entrada de solo
lectura. La conexión usa TCP/102 dentro de la red del escenario y no se publica
en el host por defecto. La ficha de [fidelidad](DEVICE_FIDELITY.md#plc-siemens-s7)
enumera las pruebas, los comportamientos y las funciones que no están
caracterizadas. Así se distingue una compatibilidad medida de una afirmación
general de compatibilidad con “S7”.

```text
cliente S7 (HMI, SCADA, consola o prueba)
          │ S7 sobre TCP/102
          ▼
servidor python-snap7 en el contenedor plc-s7
          │
          ├── áreas DB / M / I / Q y puntos configurados
          ├── simulación de componentes y bloques por scan
          └── bridge de señales del runtime de campo
```

El protocolo no transporta la tubería, el sensor ni la lógica física por sí
mismos. Es una interfaz de acceso a los bytes/bits del PLC. El proceso de campo
es un modelo separado: al conectar una señal, Anvil actualiza el punto que el
cliente S7 puede leer.

### Recorrido concreto de una lectura y una orden

Supón que un sensor mide `63 %` y una válvula tiene que abrirse. El recorrido
de lectura es: (1) el runtime de campo calcula el valor del sensor a partir del
proceso; (2) una conexión de señal lo entrega a un canal de entrada del PLC;
(3) el ciclo del PLC refleja el canal en el área y dirección configuradas;
(4) HMI, SCADA o un cliente Snap7 solicita esa dirección por S7; y (5) el
cliente recibe el valor para presentarlo. Para la orden, una regla del PLC o
una operación autorizada escribe un canal de salida; la conexión de señal
entrega el BOOL/REAL al puerto `command` de la válvula; el runtime de campo
cambia su estado modelado y, si hay proceso conectado, actualiza el caudal.
Una lectura posterior confirma el resultado.

Hay dos caminos de escritura que no se deben confundir. La **lógica del PLC**
es evaluada por el scan y actualiza su salida según las entradas. La **operación
de HMI/consola** es una petición externa autorizada que escribe el punto
seleccionado. Si un bloque controla esa misma salida, el siguiente scan vuelve
a aplicar el resultado del bloque; la escritura manual puede ser transitoria.
La interfaz solo ofrece el control cuando el mapa, rol y política de Anvil
permiten esa operación.

```text
Sensor ──proceso──> runtime de campo
                         │ señal (medición)
                         ▼
HMI/cliente ──S7/TCP 102──> canal de entrada PLC
                                  │ scan y lógica
                                  │ canal de salida
                                  ▼
                         runtime de campo ──señal──> válvula
                                  │
                         proceso/caudal actualizado
```

Las flechas representan mecanismos distintos. `S7/TCP 102` es una petición de
red del cliente al servidor PLC; `señal` es el cableado lógico interno del
escenario; `proceso` modifica magnitudes simplificadas como caudal y nivel.
Una línea Ethernet entre HMI y PLC no crea una tag; una línea de proceso entre
bomba y válvula no conecta por sí sola una salida PLC.

## Qué se configura y qué ejecuta el runtime

La configuración del PLC describe los datos y funciones que Anvil debe
simular: dirección/rack/slot del endpoint S7, tamaño del DB, canales de E/S,
valores iniciales, bloques de lógica, periodo nominal, enlaces y perfiles de
fallo/retardo. En un PLC físico habría además que seleccionar CPU y módulos,
crear/compilar un proyecto de ingeniería y descargarlo a hardware. Anvil no
acepta ese proyecto: valida un modelo propio del escenario.

Al desplegar, el adaptador del backend convierte esa configuración en el
entorno del runtime, inicia el contenedor del PLC en la red del escenario y
registra su estado. Dentro del contenedor, el servidor S7 publica memoria y
atiende peticiones. Un ciclo aparte actualiza componentes y bloques; los
puertos de campo intercambian señales con el runtime de campo. HMI/SCADA y
clientes externos compatibles son clientes: se conectan a la IP/puerto y
solicitan las direcciones que conocen. El servidor no transmite automáticamente
todos los datos a cada equipo de la red.

El **mapa de E/S** es la tabla que relaciona el nombre funcional, el tipo, el
área/dirección S7 y los permisos efectivos. La **lógica PLC** es el conjunto de
reglas/bloques que se evalúan por ciclo. El **protocolo** es la interfaz de
petición/respuesta. Son tres piezas separadas: se puede probar el servidor S7
sin una regla de control, o configurar una regla y comprobar el resultado en un
canal, una señal de campo o un cliente.

## Qué partes simula Anvil

Aquí “protocolo” significa mensajes de red para acceder a memoria; “control”
son las reglas evaluadas por scan; “proceso” son las magnitudes simplificadas
de sensores y actuadores. Se ejecutan en piezas distintas y sus conexiones se
configuran de forma explícita.

| Parte | Implementación en Anvil |
| --- | --- |
| Acceso protocolario | Servidor S7 de `python-snap7` en TCP/102 dentro del contenedor. |
| Memoria | DB y áreas M/I/Q con buffers de bytes; canales BOOL y REAL con mapa resuelto. |
| Entradas simuladas | Entradas digitales, sensores, caudal de bomba, señales remotas y canales de entrada alimentados por campo. |
| Salidas simuladas | Válvula, motor, orden de bomba y canales de salida BOOL/REAL compatibles. |
| Ciclo | Scan lógico configurable de 10 a 5000 ms, 100 ms por defecto; el host/Docker planifica el proceso. |
| Bloques | Bloques disponibles en el esquema/runtime; consulta [lógica PLC](PLC_LOGIC.md) para su lista exacta, entradas/salidas, estado y límites. No es un conjunto de instrucciones IEC completo. |
| Comportamiento de red | Retardo/jitter de respuesta y modo de timeout configurables dentro de los límites del modelo. |
| Estado persistente opcional | Estado soportado de los bloques conservado por volumen Docker gestionado por Anvil. |

El ciclo de campo y el servidor S7 corren dentro del contenedor PLC. El
intercambio con un sensor/válvula independiente se hace a través del runtime de
campo de Anvil, por un transporte interno; no es un mensaje S7 dirigido al
objeto. En cambio, un `remote_input` sí ejecuta lecturas S7 como cliente hacia
otro PLC. Una HMI/SCADA también son clientes: realizan peticiones periódicas
para sus tags seleccionados.

El estado del contenedor y el valor de una señal son cosas distintas. El
healthcheck confirma que el servicio responde a su comprobación; no demuestra
que la lógica de proceso represente una máquina real o que cada dato sea
correcto.

### Qué ocurre dentro del runtime, paso a paso

El contenedor del PLC recibe una configuración que el backend construye a
partir del escenario guardado. El runtime valida esa configuración al arrancar,
crea los buffers de memoria y registra sus componentes y bloques. Después abre
el servidor S7 en TCP/102 e inicia los ciclos de simulación. De forma
simplificada, cada ciclo actualiza las entradas disponibles, evalúa los bloques
configurados y aplica sus salidas. En paralelo, el servidor responde a las
peticiones de clientes. La periodicidad configurada es nominal: los hilos de
Python, Linux y Docker pueden retrasar una iteración.

```text
Escenario guardado
      │ validación y traducción del adaptador
      ▼
Configuración JSON del contenedor PLC
      ├── buffers DB/M/I/Q + mapa de canales
      ├── ciclo nominal: componentes y bloques soportados
      ├── bridge interno ↔ runtime de campo
      └── servidor S7 TCP/102 ↔ clientes Snap7 compatibles
```

El servidor y el ciclo comparten los datos del runtime, pero son funciones
distintas. Abrir TCP/102 no activa una lógica de control; añadir una regla no
configura automáticamente una HMI; dibujar una conexión Ethernet tampoco
selecciona direcciones S7. La [arquitectura](SRS_ARCHITECTURE.md) detalla qué
parte valida, despliega y opera cada relación.

## Qué no simula

Anvil no implementa CPU, firmware o módulos físicos Siemens; ciclo de scan
determinista de tiempo real; catálogo de instrucciones completo; compilador
Ladder, FBD o Structured Text; símbolos TIA; descarga/subida de proyectos;
PROFINET I/O; seguridad funcional; modos STOP/RUN y diagnósticos específicos
de una CPU comercial; comportamiento eléctrico de canales; latencias garantizadas
ni compatibilidad certificada con todos los clientes/firmware. El servidor
acepta clientes compatibles con las operaciones probadas, pero eso no demuestra
equivalencia con una CPU física.

No importes un proyecto TIA esperando que Anvil lo ejecute. Modela las entradas,
salidas, canales y bloques que necesitas en el esquema Anvil y verifica las
operaciones que el escenario debe enseñar.

## Rack, slot y dirección IP

Cada nodo tiene un `node_id`, una dirección IP interna y configuración de
`rack`/`slot`. Rack y slot son parámetros de direccionamiento de cliente S7;
por compatibilidad habitual el ejemplo usa rack 0 y slot 1. Son configuración
del endpoint del simulador y no representan una CPU, bastidor o módulo físico
instalado.

Los nodos de un escenario se conectan por nombre/IP en sus redes Docker. Por
defecto el puerto S7 no se publica en el host. HMI/SCADA hablan con el PLC desde
un contenedor del escenario. Para conectar una herramienta de tu ordenador,
activa publicación explícita en el inspector; la publicación predeterminada se
limita a loopback y se valida el puerto. La guía [acceso OT](ACCESS.md) explica
esta excepción. No abras TCP/102 a una LAN sin restringir el firewall externo.

## Áreas de memoria S7

El mapa de un punto relaciona un nombre funcional con un área, número de DB,
byte, bit, tipo y dirección de operación.

| Área | Uso habitual | Cómo la trata Anvil |
| --- | --- | --- |
| `DB` (data block) | Datos organizados en bloques de datos | Un DB concreto puede incluir BOOL/REAL y componentes soportados. El inspector calcula el offset de componentes heredados; `io_channel` puede fijar offsets. |
| `M` (merker/flags) | Memoria interna de marcas | Canales BOOL/REAL configurables; se valida solape y tamaño. |
| `I` (inputs) | Imagen de entradas del PLC | Canales de entrada BOOL/REAL, de solo lectura desde rutas gestionadas de Anvil. |
| `Q` (outputs) | Imagen de salidas del PLC | Canales de salida BOOL/REAL compatibles, sujeto a mapa/permisos de la API. |

Un BOOL ocupa un bit direccionable dentro de un byte. En `DB1.DBX0.3`, por
ejemplo, `DB1` identifica el bloque, `DBX` indica bit, `0` es byte y `3` es
bit. Un REAL S7 ocupa cuatro bytes (IEEE-754 big-endian); `DB1.DBD4` se refiere
al REAL que empieza en el byte 4. No conectes dos valores a los mismos bits o
bytes.

Al configurar una señal hay que distinguir tres identificadores. El **nombre
funcional** (por ejemplo, `nivel_deposito`) ayuda a reconocer el punto; el
**área y dirección S7** (por ejemplo, `DB1.DBD4`) localiza sus bytes en el
servidor; el **permiso/papel** determina si el punto se trata como entrada,
salida, lectura o escritura en las operaciones gestionadas por Anvil. Cambiar
el nombre no mueve la dirección ni crea una asociación con el sensor. Un
cliente S7 externo direcciona el área y los bytes, no el nombre que aparece en
el inspector.

Los offsets pueden resolverse automáticamente o configurarse en `io_channel`.
Anvil rechaza solapes, canales fuera de `db_size`, REAL desalineados según su
validador y direcciones/tipos incompatibles. La asignación automática es un
mapa concreto guardado con el escenario; si reordenas canales, revisa el mapa
y las conexiones. Las direcciones de bloques heredados de componentes no se
editan igual que las de `io_channel`.

## Entradas, salidas, permiso y autoridad

El nombre “entrada” o “salida” describe el papel dentro del modelo, no es por sí
mismo un control de seguridad completo del protocolo:

- Un **canal de entrada** entrega al PLC una medición o estado; el operador de
  Anvil lo lee, pero no lo escribe desde los endpoints autorizados.
- Un **canal de salida** representa una orden que el PLC entrega al actuador.
  La HMI/consola solo puede escribirla si el punto está marcado R/W, su rol lo
  permite y el destino está seleccionado.
- Un **punto interno/bloqueado por lógica** no debe ser forzado desde HMI si la
  política del modelo lo declara administrado por el runtime.
- Un cliente S7 externo que alcance TCP/102 puede tener permisos de protocolo
  distintos a los de la API Anvil. La autorización de usuario para la consola
  no se inyecta dentro de S7comm ni reemplaza controles de red.

No confundas “solo lectura en la UI” con una ACL aplicada a todos los clientes
S7. Las zonas Docker/rutas determinan quién llega al servidor; el mapa/API
determina qué operaciones de Anvil acepta el backend.

## Componentes y bloques que puedes configurar

El inspector diferencia **componentes** (puntos o estados de E/S) de **bloques
de lógica** (transformaciones/decisiones ejecutadas por scan). Ejemplos:

- `digital_input`: estado BOOL de entrada.
- `analog_sensor`: valor REAL, rango, unidad y fuente constant/sine_wave según
  esquema. El ruido opcional acepta `simulation.seed` para repetir la secuencia
  al recrear el runtime con el mismo nombre, DB, offset y ritmo de scan; si no
  se configura, el ruido parte de una semilla aleatoria.
- `valve`, `motor`: estado BOOL de salida simulado.
- `pump`: orden BOOL y caudal REAL de lectura según el modelo.
- `remote_input`: lectura BOOL/REAL desde otro PLC por enlace S7 periódico;
  no es una réplica de PROFINET I/O.
- `io_channel`: canal BOOL/REAL con área, dirección, unidad y rango.
- Bloques ON/OFF y temporizadores/booleanos: conectan nombres de componentes
  permitidos y escriben un punto de salida que no se puede duplicar en otra
  regla.

Consulta [PLC_LOGIC.md](PLC_LOGIC.md) para polaridad, histéresis, memoria,
temporizadores, contadores, secuenciador y retención. El catálogo real del
checkout y la validación de API son la autoridad; no supongas que el inspector
acepta cualquier instrucción IEC.

### Qué se configura y qué ocurre al desplegar

| Momento | Qué define Anvil | Ejemplo |
| --- | --- | --- |
| Diseño | Tipo de runtime, IP interna, rack/slot, tamaño de DB, scan nominal, canales, bloques, fallos y conexiones | Canal `LEVEL` REAL de entrada enlazado a `sensor.value` |
| Validación | Comprueba tipos, rangos, direcciones y solapes; asigna offsets si se eligió automático | `LEVEL` ocupa una dirección libre y aparece en el mapa de E/S |
| Despliegue | Construye la configuración runtime y crea red/contenedor gestionados | El servidor S7 escucha dentro de la red privada del escenario |
| Ejecución | El ciclo aplica las reglas implementadas; clientes hacen lecturas/escrituras y campo intercambia valores | La salida BOOL acciona la válvula y el cliente consulta el estado |

La asignación automática no hace que el protocolo descubra tags en una HMI:
el destino y los puntos se seleccionan por separado en la HMI/SCADA. Los cambios
del borrador tampoco modifican el runtime desplegado; guarda y vuelve a
desplegar para aplicar la configuración.

## Añadir un PLC y conectar el escenario

Conviene separar cinco acciones: **añadir** crea un nodo con runtime y dirección
de red; **configurar** declara memoria, canales y lógica; **conectar por señal**
enlaza canales con objetos de campo; **conectar por Ethernet/ruta** describe
alcanzabilidad de red; **configurar HMI/SCADA** elige un cliente y sus tags.
Ninguna acción sustituye automáticamente a las demás. Una línea del mapa tiene
el efecto descrito por el tipo de relación que representa.

### Orden recomendado y comprobación de cada conexión

| Lo que quieres conectar | Primero configura | Después conecta | Cómo comprobarlo |
| --- | --- | --- | --- |
| Sensor a PLC | Sensor con rango/unidad y canal de entrada del tipo correcto | `sensor.value` → canal PLC de entrada | La lectura del canal sigue el sensor; el mapa de E/S muestra dirección y valor. |
| PLC a válvula/bomba | Canal BOOL/REAL de salida compatible y actuador con sus puertos | Canal de salida → `actuator.command` | Escribe o calcula la salida, observa el estado del actuador y verifica el caudal/nivel resultante. |
| PLC a PLC | Punto local en DB del emisor y `remote_input` compatible en receptor | Configura emisor y dirección fuente dentro del `remote_input`; habilita ruta si cruza zonas | En el receptor, observa valor, `good`/`stale` y la lectura S7 iniciada por ese PLC. |
| PLC a HMI/SCADA | Servidor PLC activo y tags/direcciones seleccionados en el cliente | Configura el destino y cada punto en HMI/SCADA; habilita ruta si hace falta | Verifica lectura `good`; prueba solo escrituras de salida permitidas. |
| Cliente externo a PLC | Publicación externa explícita y reglas de acceso de host | Usa host/puerto publicados y rack/slot/dirección compatibles | Prueba desde el cliente autorizado; por defecto no hay puerto publicado. |

La prueba debe recorrer cada capa: dato de origen, canal, dirección de memoria,
cliente/protocolo y, para una salida, actuador y efecto del proceso. Si falla,
comprueba la etapa más temprana que no cambió. Una conexión configurada no
demuestra por sí sola que el runtime se haya desplegado con la última versión;
guarda y vuelve a desplegar después de editar el escenario.

### PLC con otro PLC

Un PLC receptor puede incorporar `remote_input` BOOL/REAL que actúa como cliente
S7 hacia un emisor. Selecciona el dispositivo emisor y punto fuente, luego
configura periodo y timeout. La entrada remota se actualiza dentro del DB del
receptor y enseña calidad `waiting`, `good` o `stale`. Es una consulta periódica
de DB, no un cable de E/S cíclicas ni una relación bidireccional automática.
Para compartir datos en ambos sentidos configura un receptor en cada extremo.
Lee [comunicación entre PLCs](S7_LINKS.md) y
[zonas/rutas](NETWORK_ZONES.md) para cruces de segmento.

### PLC con sensor o actuador independiente

Añade el sensor/válvula/bomba desde el catálogo de campo, y luego crea canales
en el PLC. En el inspector del objeto de campo conecta `sensor.value` a un canal
de entrada, o un canal de salida a `valve.command`. La línea de señal no es la
tubería física. Un runtime de campo dentro de la red del escenario aplica el
valor/mando y lo devuelve al canal. El PLC conserva su propio endpoint S7.
Consulta [elementos de campo](FIELD_OBJECTS.md).

Una entrada del PLC es el destino de una medición que llega al controlador;
una salida es el origen de una orden hacia el campo. Sensor y válvula se crean
y configuran en sus propios inspectores. En el PLC se crean canales compatibles
y la conexión enlaza el puerto de campo con el canal. Que el canal se llame
`I` o “entrada” no convierte al PLC en cliente S7: el PLC ofrece su servidor,
mientras HMI/SCADA realizan peticiones como clientes.

### PLC con HMI y SCADA

Añade la HMI/SCADA y selecciona el PLC, tags/direcciones y permisos. Estos nodos
son clientes S7 que leen valores en su runtime; una conexión visual Ethernet
por sí sola no los configura como origen. Para destinos de otra zona crea ruta
S7 explícita. Consulta [HMI](hmi.md), [SCADA](SCADA.md) y
[zonas Purdue](NETWORK_ZONES.md).

## Configuración relevante del nodo

Los valores del esquema de PLC que suelen requerir decisión son:

| Campo | Valor/intervalo del esquema | Efecto |
| --- | --- | --- |
| `rack`, `slot` | 0/1 por defecto, enteros no negativos | Parámetros para clientes S7; no crean hardware. |
| `db_size` | 32 bytes por defecto, mayor que cero | Límite de memoria DB que el validador asigna a componentes. |
| `scan_cycle_ms` | 100 ms por defecto, 10–5000 ms | Cadencia nominal de simulación/lógica. No es garantía temporal. |
| `response_delay_ms` | 0 ms por defecto, 0–10000 ms | Retraso de respuesta a operaciones S7 modeladas. |
| `response_jitter_ms` | 0 ms por defecto, 0–10000 ms | Variación pseudoaleatoria acotada; perfil/semilla controlan repetibilidad. |
| `response_jitter_distribution`, `response_jitter_seed` | `deterministic`, semilla 0 por defecto | El esquema también admite distribuciones `uniform` y `normal`; comprueba el perfil de jitter en el inspector/API. |
| `fault_mode` | `none` o `timeout` | `timeout` omite respuestas de datos con la probabilidad configurada; conserva la sesión TCP. |
| `fault_probability`, `fault_seed` | 1.0, semilla 0 por defecto | Probabilidad 0–1 y semilla para el modo de fallo pseudoaleatorio. |
| `retain_logic_state` | `false` | Si se activa, persiste solo el estado soportado de bloques en un volumen Docker por nodo. |

Los valores provienen de `PlcS7ConfigInput`. La UI puede exponer solo una parte
de la estructura en un momento dado; la API valida el esquema completo. No
cambies configuración de un contenedor en marcha suponiendo que se actualiza
su memoria sin redeploy.

## Ciclo de construcción y verificación

1. Crea el nodo PLC y asígnale nombre/IP/zona sin publicar puertos del host.
2. Añade canales, dispositivos de campo y bloques compatibles.
3. Conecta señales y revisa dirección, tipo BOOL/REAL, byte/bit y permiso.
4. Guarda; abre el mapa de E/S y resuelve errores de solape/rango.
5. Despliega y espera a estado sano.
6. Lee un punto mediante consola/HMI o cliente Snap7 del escenario.
7. Escribe una salida permitida y confirma el cambio en el actuador y proceso.
8. Lee otra vez por S7; confirma que la medida de retorno cambió.
9. Consulta logs y auditoría si algo no concuerda.

Desde la raíz del repositorio, construye la imagen y ejecuta el smoke protocolario,
que arranca el runtime y usa un cliente Snap7 para leer y escribir las áreas cubiertas:

```bash
docker build -t anvil/plc-s7:dev ./plc
python3 integration/s7_smoke.py
```

El smoke del servidor necesita Docker accesible y la imagen local construida.
Los smokes de despliegue completo dependen además del backend, una subred libre y
credenciales de test según [VALIDATION_LOCAL.md](VALIDATION_LOCAL.md). Para
verificar comunicaciones entre dos PLCs usa `integration/s7_links_smoke.py` o
`integration/s7_peer_route_smoke.py` y consulta el procedimiento en
[S7_LINKS.md](S7_LINKS.md).

## Diagnóstico práctico

| Síntoma | Comprueba |
| --- | --- |
| Cliente no conecta | Estado/health del contenedor, destino TCP/102, red/zona, dirección/puerto cliente, ruta entre zonas. La publicación host está desactivada por defecto. |
| Responde, pero no cambia el valor | Tipo/área/dirección, si el componente es de entrada o salida, canal conectado y ciclo lógico. |
| BOOL cambia y actuador no | Que la conexión de señal termine en `command`, el punto sea salida compatible y el runtime de campo esté sano. |
| HMI no muestra dato | Que el PLC y dirección estén seleccionados en HMI, ruta permitida, lectura de protocolo y calidad `good`. |
| Enlace PLC→PLC queda `stale` | Emisor/runtime/red, dirección fuente y umbrales de `period_ms`, `timeout_ms`, `stale_after_ms`; el último valor se puede conservar. |
| El write API se rechaza | Rol, salida R/W, punto seleccionado, firewall, rango y si un bloque de lógica controla ese punto. |

### Ejemplo: conectar dos PLC y una HMI

Supón que el PLC `bombeo` publica `DB1.DBX0.0` como `bomba_en_marcha` y
`DB1.DBD4` como `caudal_l_min`. El PLC `supervision` añade dos entradas remotas
de esos mismos tipos que seleccionan `bombeo` y esas direcciones. Al desplegar,
`supervision` inicia las lecturas periódicas; la dirección de la HMI no se usa
en ese enlace. Si los PLC están en segmentos distintos, la ruta debe autorizar
el protocolo S7 desde el receptor hacia el emisor. Para comunicación en ambos
sentidos se crea una entrada remota en cada receptor.

La HMI se configura aparte: se añade `bombeo` como destino S7 y se seleccionan
sus tags para lectura. Si la pantalla debe escribir, el punto debe ser una
salida escribible y la operación debe estar permitida por rol/política. La HMI
es cliente y solicita los valores; no retransmite automáticamente los tags al
segundo PLC. Si se quiere que el segundo PLC use un dato, se configura su
`remote_input` directamente.

```text
PLC bombeo (servidor S7)
  ├── DB1.DBX0.0 ──lecturas S7 del receptor──> remote_input en supervision
  ├── DB1.DBD4   ──lecturas S7 del receptor──> remote_input en supervision
  └── DB1.*      <──peticiones S7──────────── HMI (cliente)
```

Esta topología no ejecuta un programa compartido ni sincroniza los scans. La
HMI y el receptor son clientes independientes. Revisa [comunicación entre
PLCs](S7_LINKS.md) para calidad, tiempos y limitaciones del enlace.

Si una lectura tiene calidad `stale`, no la presentes como telemetría actual.
Consulta [solución de problemas](troubleshooting.md) para la inspección del
backend, Docker y logs.

## Fidelidad y pruebas

`integration/s7_smoke.py` prueba áreas DB/M/I/Q con cliente Snap7; otros smokes
cubren lógica y comunicación PLC↔PLC. Estas pruebas demuestran las operaciones
que invocan contra la imagen de Anvil. No demuestran todas las funciones S7comm,
compatibilidad universal con versiones Snap7/TIA ni equivalencia frente a una
CPU Siemens. La ficha de [fidelidad](DEVICE_FIDELITY.md) anota los límites y la
traza semántica de Snap7 no es una captura PCAP.
