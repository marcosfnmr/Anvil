# Conceptos de Anvil

Este capítulo explica el vocabulario y las relaciones del escenario. Para una
vista más amplia de componentes internos, despliegue, seguridad y relación con
los requisitos del producto, consulta
[Arquitectura funcional y cobertura del SRS](SRS_ARCHITECTURE.md).

Si estás empezando, identifica primero si el elemento es un nodo de software
o un objeto de campo; después entiende qué dato publica o consume; configura
la relación adecuada (proceso, señal o protocolo); y comprueba la red y los
permisos. La guía [PLC S7](plc-s7.md) desarrolla qué es un PLC, cómo funciona
el servidor S7 del simulador, qué partes se configuran y cómo se conecta con
otros equipos. [Modbus](MODBUS.md), [campo](FIELD_OBJECTS.md), [HMI](hmi.md),
[SCADA](SCADA.md) y [redes](NETWORK_ZONES.md) explican las demás fichas.

## Escenario

Un escenario es el documento que define el laboratorio: nodos, configuración,
posiciones, enlaces, zonas y topología de proceso. Se puede guardar sin
desplegar. Exportar produce JSON versionado; importar valida la estructura y
clonar crea otro escenario independiente. Cada guardado crea una revisión
consultable/restaurable según la API actual.

El escenario es una especificación, no un contenedor único. Al desplegarlo,
Anvil crea una red y runtimes Docker separados. El estado `running` debe leerse
junto con el estado de cada nodo: puede haber nodos `missing`, `error` o sin
healthcheck sano. Usa el panel de estado y diagnósticos antes de asumir que todo
el laboratorio está operativo.

## Dispositivos y adaptadores

Cada clase de nodo se define en un adaptador del backend. El adaptador selecciona
el modelo de configuración, la imagen permitida, los recursos, las redes y las
operaciones compatibles. El frontend mantiene un catálogo/inspector asociado.
Los tipos principales incluyen PLC S7, Modbus, HMI, SCADA, estación de
ingeniería/participante y switch lógico. Los objetos físicos pertenecen a otra
colección de campo y no tienen IP.

### Conceptos industriales y su representación

Antes de cablear el mapa, conviene separar funciones que en una fábrica pueden
configurarse con herramientas distintas:

| Función | Qué significa en una fábrica | Qué significa en Anvil |
| --- | --- | --- |
| Medición | Un instrumento mide una magnitud y entrega una señal | Un sensor de campo calcula un valor del proceso simplificado y expone `value` |
| Control | Un PLC toma decisiones cíclicas a partir de entradas | El runtime S7 evalúa canales y bloques Anvil durante un scan nominal |
| Actuación | Una válvula, motor o bomba modifica el proceso al recibir una orden | Un objeto de campo cambia su estado y alimenta el cálculo de proceso |
| Comunicación | Clientes consultan o escriben datos de servidores | S7/Modbus atienden peticiones protocolarias en contenedores de dispositivo |
| Operación | Una persona vigila la planta y solicita acciones permitidas | La HMI presenta tags y mandos autorizados; una escritura es una petición al servidor |
| Adquisición | Un sistema recoge y guarda observaciones de varios equipos | SCADA sondea tags elegidos y el backend conserva muestras y alarmas |
| Red | Segmentos y controles definen qué equipo puede llegar a otro | Redes Docker y rutas proxy limitadas dan conectividad a servicios declarados |

En una instalación, “sensor”, “PLC” o “HMI” pueden designar productos físicos
con muchos módulos y funciones. Aquí describen el papel que desempeña cada
componente del escenario. Anvil solo implementa la parte software detallada en
las fichas de [PLC S7](plc-s7.md), [Modbus](MODBUS.md), [campo](FIELD_OBJECTS.md),
[HMI](hmi.md), [SCADA](SCADA.md) y [redes](NETWORK_ZONES.md).

El catálogo de componentes es una biblioteca de configuraciones permitidas. No
es un instalador de módulos: Anvil no importa código ejecutable enviado por el
usuario.

### Conceptos base: dispositivo, modelo y runtime

En este manual, **dispositivo** es un término de usuario para PLC, servidor
Modbus, HMI, SCADA u otra estación que Anvil ejecuta como servicio. Un
**runtime** es el proceso que implementa el comportamiento de ese dispositivo.
El backend valida el modelo y crea un contenedor para cada runtime; el
contenedor no es una CPU ni una caja física. Los objetos de campo —por ejemplo,
un sensor o una válvula— pertenecen a otro modelo y son calculados por el
runtime de campo del escenario.

Conviene distinguir cuatro cosas que en una interfaz industrial a veces se
configuran en una sola herramienta:

1. **Modelo del escenario:** qué equipos existen, su identidad, configuración,
   dirección, zona y conexiones.
2. **Memoria o mapa de puntos:** qué datos publica cada servidor, con qué tipo,
   dirección, valor inicial y permiso.
3. **Lógica o comportamiento:** qué reglas cambian las salidas y qué proceso
   representan los sensores/actuadores.
4. **Cliente:** qué destinos y puntos lee una HMI, un SCADA u otro PLC, y qué
   escrituras tiene permitidas.

Crear un punto no lo conecta automáticamente a un sensor. Crear una conexión
de señal tampoco configura el tag de la HMI. Guardar el escenario persiste la
definición; desplegarla inicia o actualiza los contenedores que la ejecutan.
Esta separación ayuda a localizar errores: un valor puede existir en el mapa,
pero no tener fuente de campo, cliente configurado o ruta de red.

### Qué hace cada tipo de equipo

En el mapa, un PLC S7, un equipo Modbus, una HMI o un SCADA es un nodo de red:
tiene una dirección, un runtime Docker y un rol de cliente o servidor. Una
válvula, un sensor, una bomba o un depósito es un objeto de campo: pertenece al
modelo del proceso y no tiene IP. Esta diferencia explica por qué configurar
un objeto de campo se hace desde su propio inspector, aunque después se conecte
a un controlador.

| Equipo | Qué simula Anvil | Qué configuras | Cómo se relaciona con otros |
| --- | --- | --- | --- |
| PLC S7 | Servidor compatible con operaciones S7 probadas, memoria DB/M/I/Q y canales/bloques ejecutados por scan | Rack/slot, tamaño DB, scan nominal, canales BOOL/REAL, direcciones, reglas, enlaces S7 y fallos/retardos soportados | Ofrece datos a clientes S7; los canales se conectan a puertos de campo; `remote_input` consulta un DB de otro PLC |
| Equipo Modbus | Servidor Modbus TCP o RTU virtual con mapa de coils y registros; no ejecuta lógica de PLC | Unit ID, puerto/transporte, tipo/dirección/valor inicial y permisos de cada punto | HMI/SCADA lo consultan como clientes; un punto puede conectarse a campo con conversión explícita cuando sea necesaria |
| Objeto de campo | Comportamiento de proceso simplificado: estado de válvula, lectura de sensor, caudal, presión aproximada o nivel | Tipo, parámetros modelados, valor inicial, respuesta, fallos y posición en el mapa | Conexiones de señal unen puertos con puntos PLC/Modbus; conexiones de proceso unen bomba, tubería, depósito, válvula y sensor |
| HMI | Cliente de operación que consulta puntos seleccionados y ofrece sinóptico, alarmas y escrituras permitidas | Destinos, tags, alarmas, permisos y fuentes SCADA | Inicia lecturas/escrituras S7 o Modbus; no recibe todos los tags solo por compartir red |
| SCADA | Cliente de adquisición que sondea puntos elegidos, guarda muestras e informa alarmas | Destinos/puntos, periodo, capacidad de histórico y reglas de alarma | Consulta PLCs/equipos Modbus por protocolo y ofrece histórico a vistas autorizadas |
| Switch lógico / zona | Topología de red Docker; las zonas crean segmentos separados | Pertenencia a zona, capacidad declarada y rutas compatibles | Determina conectividad de red; no crea canales, tags ni conexiones de proceso |
| Gateway representativo | Símbolo de arquitectura para un equipo intermediario | Identidad, dirección y posición del nodo | No enruta ni filtra tráfico; para un flujo real se configura una ruta TCP explícita |
| Gateway TCP de una ruta | Proxy software de un único flujo entre dos zonas | Origen autorizado, destino, protocolo/puerto, estado y latencia opcional | El cliente seleccionado conecta al proxy, que reenvía al destino permitido; no hace routing IP general |
| Estación de ingeniería | Nodo auxiliar que representa una estación de mantenimiento/configuración | Zona y habilitación explícita de red/terminal; herramientas disponibles en su imagen | Solo llega a servicios permitidos por red/rutas; no es TIA Portal ni una CPU de ingeniería Siemens |
| Estación de participante | Entorno de cliente para ejercicios del ciber-rango | Habilitación, acceso de terminal, rol, duración y límites de sesión | Se conecta a su zona si se habilita; no obtiene acceso OT ni salida externa automáticamente |
| Firewall representativo | Símbolo de un control perimetral | Identidad y ubicación en el mapa | No filtra paquetes por sí mismo; el aislamiento efectivo viene de redes y rutas Docker |

#### Qué significa cada dispositivo en una planta

**PLC.** Es el controlador que toma decisiones locales. En una planta lee
entradas, aplica un programa y ordena salidas; en Anvil se configura una
instancia `plc-s7`, sus áreas de memoria/canales y los bloques limitados del
simulador. El runtime anuncia un servidor S7 para que clientes consulten datos.
Anvil no ejecuta firmware Siemens ni un proyecto descargado desde TIA Portal.
Consulta [PLC S7](plc-s7.md) para ver cómo se representan la memoria, el scan,
las señales y los enlaces a otros PLC.

**Servidor Modbus.** Es un equipo que publica un mapa de datos para que otros
clientes lean o escriban puntos autorizados. Anvil configura Unit ID, puerto,
transporte y coils/registros; Modbus no ejecuta por sí solo la lógica que
arranca una bomba. La aplicación que toma esa decisión debe estar en el PLC o
en un cliente autorizado. Ver [Modbus TCP/RTU](MODBUS.md).

**Sensor y actuador.** El sensor mide una magnitud que calcula el modelo de
proceso; la válvula, motor o bomba modifica el proceso al recibir una orden.
Son objetos de campo independientes, no nodos Ethernet: no tienen IP, servidor
S7 ni mapa Modbus propio. En Anvil configuras cada objeto desde su inspector,
lo conectas al proceso y luego enlazas sus puertos de señal a canales de PLC o
puntos compatibles. Ver [Objetos de campo](FIELD_OBJECTS.md).

**HMI.** Es la interfaz del operador. El runtime HMI de Anvil actúa como
cliente de los servidores PLC S7 y Modbus: inicia lecturas periódicas de los
tags seleccionados y, cuando el mapa, rol y política lo permiten, envía una
escritura al servidor correspondiente. No es un servidor Modbus ni transmite
datos por sí sola.
Un sinóptico es una vista ligada a esos datos; dibujar un símbolo no crea un
sensor ni un punto de protocolo. Ver [HMI](hmi.md).

**SCADA.** Es un cliente de adquisición que sondea los puntos que se le han
configurado, mantiene un buffer acotado y puede persistir muestras y alarmas
mediante Anvil. En el modelo actual es un runtime separado de la HMI. No es un
historiador de alta disponibilidad ni adquiere automáticamente cada dato de la
red. Ver [SCADA](SCADA.md).

**Zona, switch y ruta.** La zona representa un segmento de red interno de
Docker. El switch lógico describe topología/capacidad dentro del modelo, y una
ruta habilita flujos TCP concretos entre zonas. No se emula un switch físico
con VLAN/STP ni un router general. La membresía de red no enlaza señales de
campo ni selecciona tags; ver [Zonas de red](NETWORK_ZONES.md).

#### Una señal completa, de extremo a extremo

Para que una válvula responda al nivel de un depósito, tienen que coincidir
varias relaciones configuradas por separado:

1. La tubería/equipo y el depósito forman una conexión de **proceso**.
2. El sensor obtiene una medida del proceso y publica su puerto `value`.
3. Una conexión de **señal** enlaza ese puerto con una entrada/canal del PLC.
4. La lógica PLC evalúa la entrada y actualiza un canal de salida.
5. Otra conexión de señal lleva la salida al puerto `command` de la válvula.
6. La válvula modifica el proceso; el ciclo de campo calcula el nuevo estado.
7. HMI o SCADA consultan por S7/Modbus los puntos que configuraron como tags.

La ruta de red es adicional: permite que el cliente llegue al servidor, pero
no realiza los pasos 2–6. De igual forma, una conexión de proceso no crea
comunicación de protocolo. El [tutorial](tutorial.md) recorre un ejemplo y el
[mapa de proceso](PROCESS_MAP.md) explica cómo crear las relaciones.

#### Cómo leer la ficha de un PLC

Un PLC físico es un controlador industrial: recibe señales de módulos de
entrada, ejecuta un programa repetidamente y actualiza salidas. El programa
local suele mantener el control básico aunque se pierda la HMI o la red. Un
PLC también puede exponer datos a clientes de protocolo; esa comunicación es
una capacidad distinta de la lógica de control.

En Anvil, el dispositivo `plc-s7` se ejecuta como un contenedor de software.
`python-snap7` implementa el servidor S7 en TCP/102 y publica las áreas de
memoria que el modelo configura. Un ciclo de software actualiza los canales y
bloques compatibles; el sistema operativo anfitrión y Docker planifican ese
ciclo, por lo que el intervalo es nominal y no de tiempo real. Anvil no ejecuta
CPU/firmware Siemens ni proyectos TIA/Ladder/ST.

| Parte | Qué se configura en Anvil | Qué hace el simulador | Qué queda fuera |
| --- | --- | --- | --- |
| Endpoint S7 | IP del nodo, rack, slot y red/ruta | Atiende solicitudes compatibles de clientes en el puerto interno 102 | CPU Siemens, firmware, PROFINET I/O y compatibilidad universal |
| Datos | Canales BOOL/REAL, áreas/direcciones, tamaño DB y permisos | Guarda/lee valores en DB/M/I/Q y valida rangos/solapes | Módulos eléctricos, cableado analógico o símbolos descargados de TIA |
| Control | Bloques/rules disponibles y periodo nominal de scan | Evalúa el conjunto documentado de bloques por ciclos | Programa PLC arbitrario, todas las instrucciones IEC 61131-3 y tiempo real duro |
| Señales | Conexiones entre punto y puerto de campo | Entrega medidas a entradas y órdenes a actuadores modelados | Bus de E/S físico, tensión, corriente y diagnóstico de módulos |
| Retención | Opción de persistir estados de bloques admitidos | Conserva esos estados en volumen gestionado por Docker | Memoria retentiva general equivalente a una CPU real |

Para conectarlo a otros elementos hay que escoger el tipo de relación:

| Conexión | Configuración necesaria | Efecto |
| --- | --- | --- |
| PLC ↔ sensor/válvula/bomba | Crear un canal PLC compatible y conectar `sensor.value` a una entrada o una salida a `actuator.command` | El runtime de campo intercambia valores con el PLC; no usa S7 entre esos dos objetos |
| PLC ↔ PLC | Añadir `remote_input` en el receptor con PLC/punto DB de origen, periodo y timeout | El receptor consulta al emisor por S7; es sondeo periódico unidireccional. Para ambos sentidos se configura un receptor en cada PLC |
| PLC ↔ HMI/SCADA | Añadir el destino y elegir explícitamente tags/direcciones y permisos | HMI/SCADA inicia solicitudes S7 como cliente; compartir Ethernet no selecciona tags |
| PLC ↔ otro segmento | Añadir una ruta de protocolo compatible y sus extremos/puerto | Autoriza el flujo declarado entre zonas; no crea routing IP general |

Esta matriz describe qué se configura y cómo se comporta el runtime; no
presupone que una línea dibujada sea una conexión funcional. La guía
[PLC S7](plc-s7.md) desarrolla memoria, scan, permisos, direcciones, pruebas y
límites. [Fidelidad](DEVICE_FIDELITY.md) indica exactamente qué operaciones
protocolarias están comprobadas.

La columna “simula” describe el comportamiento implementado, no una réplica de
hardware. El servidor Modbus publica un mapa, pero no decide por sí solo cuándo
arranca una bomba. Esa decisión puede residir en la lógica S7 configurada o en
un cliente autorizado. El runtime de campo aplica la orden y actualiza el
proceso modelado.

#### Qué significa una conexión PLC en el mapa

Una conexión solo es útil si se sabe qué dato viaja y quién inicia la operación.
En Anvil hay tres patrones frecuentes:

1. **PLC–campo:** el runtime de campo actualiza el canal de entrada desde el
   puerto `value` del sensor, o lee el canal de salida y aplica la orden al
   puerto `command` del actuador. No se abre una sesión S7 entre el PLC y el
   objeto de campo.
2. **PLC–PLC:** el PLC receptor actúa como cliente S7 y sondea un punto DB del
   emisor mediante `remote_input`. El emisor sigue siendo servidor. Es una
   lectura periódica de una sola dirección; no comparte la lógica ni sincroniza
   los ciclos. Para invertir el sentido, configura otra entrada remota.
3. **PLC–HMI/SCADA:** HMI y SCADA actúan como clientes que solicitan los puntos
   seleccionados al servidor. Una HMI no conecta una salida a otra PLC por el
   mero hecho de estar en la misma zona; el cliente, sus tags y una ruta de red
   interzona (si aplica) se configuran explícitamente.

En todos los casos identifica el **origen**, el **tipo**, la **dirección**, el
**sentido de lectura/escritura**, la **ruta de red** y el **indicador de
calidad**. Una flecha del mapa puede representar proceso, señal o relación de
red: consulta la leyenda y el inspector para verificar el tipo. Los pasos para
configurar y comprobar cada patrón están en [PLC S7](plc-s7.md) y
[Enlaces S7](S7_LINKS.md).

### Configurar, guardar, desplegar y operar

1. **Configurar** define el borrador: nodos, direcciones, puntos, componentes,
   reglas, zonas y relaciones. Los tipos y rangos se validan al guardar.
2. **Guardar** persiste la especificación del escenario; no arranca protocolos
   ni aplica por sí solo cambios a contenedores activos.
3. **Desplegar** crea o actualiza contenedores y redes. Cada dispositivo tiene
   runtime separado; el runtime de campo se despliega aparte por escenario.
4. **Operar** consulta datos mediante API, HMI, SCADA o un cliente protocolario.
   `waiting`, `stale`, error o escenario detenido no deben interpretarse como
   mediciones válidas.

Al cambiar el escenario desplegado, guarda y vuelve a desplegar para aplicar la
nueva configuración. Las operaciones en vivo permitidas (por ejemplo, escribir
una salida o cambiar la demanda del proceso) actúan sobre el runtime y pueden
quedar auditadas, pero no equivalen a editar la configuración persistida.

## Señales, proceso y red son relaciones diferentes

| Relación | Qué representa | Ejemplo |
| --- | --- | --- |
| Señal | Valor lógico entre un punto de controlador y un puerto de campo | `Q` del PLC → `command` de válvula |
| Proceso | Flujo físico simplificado | Bomba → depósito → válvula |
| Protocolo | Cliente/servidor industrial o ruta IP declarada | HMI → S7/102 del PLC |
| Ethernet | Pertenencia a un segmento L2 | PLC y HMI en un bridge de zona |
| Control | Relación deducida entre lógica de PLC y actuador/sensor | Sensor de nivel → bloque ON/OFF → bomba |

No interpretes una línea como equivalente a otra. Una conexión de proceso no
crea por sí misma una señal PLC; una relación Ethernet no habilita automáticamente
una ruta entre zonas.

### Ejemplo completo: sensor, PLC, bomba y HMI

Supón que el nivel medido debe arrancar una bomba cuando baja del 30 % y
detenerla al superar el 70 %. Para representar ese ciclo hacen falta varias
relaciones explícitas:

1. El **modelo de proceso** conecta bomba y depósito; el sensor obtiene su
   medida del nivel calculado.
2. Una **señal de entrada** conecta `sensor.value` con un canal del PLC.
3. Un **bloque de control** compara esa entrada con umbrales y produce una salida.
4. Una **señal de salida** conecta el canal PLC con `pump.command` o
   `valve.command`.
5. La **HMI** selecciona el PLC y la dirección S7/Modbus que debe leer y asocia
   opcionalmente ese tag con un símbolo del sinóptico.
6. La **red** permite que el cliente HMI alcance el servidor. Si cruza zonas,
   necesita la ruta de protocolo correspondiente.

El PLC no envía por iniciativa propia todos sus datos a la HMI: el cliente los
solicita. El sensor no tiene IP y no habla S7 directamente con el PLC: el
runtime de campo intercambia la señal configurada. La HMI tampoco sustituye al
bloque de control. Usa el [tutorial](tutorial.md) y el [mapa](PROCESS_MAP.md)
para inspeccionar proceso, señal, lógica, protocolo y red por separado.

## Recorrido de una variable, desde el proceso hasta la HMI

Para entender cómo colaboran los componentes, sigue una variable de nivel a
través de cada runtime. Cada etapa transforma o transporta un dato distinto:

1. **Proceso:** el runtime de campo calcula el caudal y el volumen del depósito
   a partir de las bombas, válvulas, conexiones y parámetros configurados.
   El resultado es una magnitud modelada, no una medida de hardware.
2. **Sensor:** un sensor en modo `process` toma el nivel compatible del proceso
   y publica su puerto `value`. El objeto no tiene dirección IP ni protocolo.
3. **Señal de entrada:** una conexión enlaza `sensor.value` con un canal de
   entrada del PLC, por ejemplo `LEVEL_PCT` de tipo REAL. El intercambio entre
   runtime de campo y dispositivo es interno a Anvil; no representa corriente
   analógica ni una trama S7.
4. **Control:** durante sus ciclos nominales, el PLC lee el canal y evalúa los
   bloques de lógica configurados. La salida resultante es un punto BOOL/REAL;
   el intervalo nominal no equivale a una garantía de tiempo real.
5. **Señal de salida:** otra conexión lleva ese punto al puerto `command` de
   una válvula. Orden y estado son distintos: un retardo o fallo configurado
   puede impedir que la posición cambie inmediatamente.
6. **Proceso actualizado:** el runtime de campo aplica el estado del actuador
   y vuelve a calcular caudal y nivel. Así se cierra el lazo de simulación.
7. **Protocolo y supervisión:** la HMI/SCADA inicia una lectura S7 o Modbus
   contra un servidor y muestra el punto configurado. La conexión de señal no
   añade automáticamente una tag al cliente.

Si HMI/SCADA y servidor están en zonas distintas, la ruta de red permite que el
cliente llegue al servicio. La ruta no crea la tag ni el cableado de señal. Para
localizar una lectura incorrecta, revisa proceso, fuente del sensor, calidad de
señal, canal de entrada, bloque, salida, estado del actuador, punto de
protocolo, configuración del cliente y ruta. El [tutorial](tutorial.md) sigue
este recorrido desde la interfaz.

### Quién inicia cada intercambio

| Relación | Quién la actualiza/inicia | Dato que circula | Configuración necesaria |
| --- | --- | --- | --- |
| Proceso | Runtime de campo | Caudal, volumen, nivel y presión aproximada | Objetos, parámetros y conexiones de proceso |
| Campo ↔ PLC/Modbus | Intercambio interno entre runtimes | Medida hacia entrada y orden hacia actuador | Punto E/S, puerto compatible, dirección y conversión admitida |
| HMI/SCADA → PLC S7 | Cliente HMI/SCADA | Petición de lectura o escritura de punto | Destino, área/dirección, permiso y ruta S7 si cruza zonas |
| HMI/SCADA → servidor Modbus | Cliente HMI/SCADA | Petición con Unit ID, espacio y dirección | Destino, Unit ID, punto, permiso y ruta Modbus si cruza zonas |
| PLC receptor → PLC emisor | `remote_input` del receptor | Lectura periódica de un punto DB | Origen, DB, tipo, periodo, timeout y ruta S7 si cruza zonas |
| Backend → runtime gestionado | Backend Anvil | Snapshot, control o intercambio interno | Despliegue del escenario; no es un endpoint público para navegador |

En el uso cotidiano, “conectar” puede querer decir crear un camino de proceso,
asociar una señal, seleccionar un punto en un cliente o habilitar conectividad
de red. Indica cuál de esas relaciones se ha creado: un cable visual no implica
las otras.

### Qué quiere decir que un dispositivo esté simulado

Anvil ejecuta servicios de software en contenedores y calcula el comportamiento
que describe el modelo del escenario. Por ejemplo, el runtime PLC ofrece las
operaciones S7 verificadas y evalúa los bloques documentados; el servidor
Modbus publica su mapa; el runtime de campo actualiza actuadores y proceso; la
HMI consulta tags seleccionadas. Esto no significa que haya electrónica,
firmware del fabricante, módulos eléctricos, ejecución determinista en tiempo
real o cobertura completa del estándar.

Una lectura satisfactoria desde un cliente Snap7/PyModbus demuestra la
operación probada contra Anvil. No demuestra compatibilidad con todo PLC,
firmware, herramienta de ingeniería ni planta. La matriz de operaciones,
pruebas y límites está en [Fidelidad del simulador](DEVICE_FIDELITY.md).

### Ejemplo: sensor, PLC, válvula y HMI

En una bomba de agua, el sensor puede medir el nivel calculado del depósito.
Una conexión de **proceso** lleva ese nivel al sensor. Una conexión de **señal**
lleva `sensor.value` a un canal de entrada del PLC. El PLC publica el valor por
su servidor S7, y la HMI configurada como cliente consulta ese punto por la red.
Cuando la lógica PLC activa un canal de salida, otra conexión de señal lleva la
orden a `valve.command`; el modelo de proceso aplica el cambio al caudal:

```text
depósito --proceso--> sensor --señal--> entrada PLC
HMI --petición S7/Modbus--> servidor del controlador
salida PLC --señal--> válvula --proceso--> caudal
```

La HMI normalmente lee el punto a través del protocolo; no está conectada
directamente al cableado interno del sensor. Al crear el escenario se configuran
por separado el destino y tag de HMI, el canal PLC y las conexiones de
señal/proceso. Consulta el [recorrido PLC](plc-s7.md) y el
[tutorial de bombeo](tutorial.md) para ver los pasos en la interfaz.

### Recorrido paso a paso de una conexión

Para construir ese ejemplo en un escenario nuevo, crea primero los objetos de
campo y el PLC; configura la bomba, válvula, sensor y depósito en sus propios
inspectores. Conecta bomba, depósito, válvula y sensor por **proceso**. Después
crea en el PLC un canal REAL de entrada para el nivel y canales BOOL de salida
para bomba/válvula. Conecta `sensor.value` a la entrada, y cada salida a su
puerto `command`. Añade la lógica de control o deja las salidas escribibles
para una prueba manual. Finalmente configura una HMI con el PLC como destino y
selecciona las direcciones que mostrará; guarda y despliega el escenario.

Comprueba cada salto en orden: el runtime de campo muestra nivel/estado; el
mapa de E/S confirma dirección y permiso; el PLC recibe la medición; el bloque
produce la salida; el actuador responde; y HMI consulta los valores del servidor.
Si falla el proceso, revisa conexiones físicas. Si falla el mando o la medida,
revisa señales y canales. Si falla el cliente, revisa destino, protocolo, zona,
ruta y calidad. El [tutorial guiado](tutorial.md) documenta el mismo recorrido
con instrucciones de interfaz.

### Cliente, servidor, puerto y dirección

El **servidor** es el runtime que espera solicitudes en un protocolo y publica
su mapa de datos; el **cliente** inicia las solicitudes. En Anvil, el PLC S7 y
el dispositivo Modbus son servidores, mientras HMI, SCADA y algunos enlaces
PLC→PLC son clientes. La dirección IP identifica el nodo en una red; el puerto
identifica un servicio de ese nodo (por ejemplo TCP/102 para S7 o el puerto
configurado para Modbus TCP); la dirección de punto identifica un dato dentro
del mapa del protocolo. Son tres niveles distintos. Un puerto del contenedor
puede ser accesible desde la red Docker sin publicarse en el host.

Poner dos nodos en la misma zona permite la conectividad de red prevista, pero
el cliente aún necesita destino, puerto, protocolo y tag/dirección. Una conexión
de señal une un valor de campo con un canal del controlador dentro del modelo
Anvil; no representa un paquete S7/Modbus. Una tubería representa proceso, no
red. Consulta [mapa de proceso](PROCESS_MAP.md), [elementos de campo](FIELD_OBJECTS.md)
y [zonas y rutas](NETWORK_ZONES.md) para cada relación.

### Tipos, unidades y calidad de dato

Un valor no es solo un número. Su **tipo** indica cómo interpretarlo (`BOOL`,
`REAL` o `UINT16`); su **dirección** indica dónde lo encuentra el cliente; y
su **unidad** (por ejemplo, `%`, `kPa` o `rpm`) describe su escala física.
Modbus transporta palabras enteras, por lo que convertir una magnitud decimal
a un registro requiere una escala explícita. No se debe suponer que el registro
`1200` significa 1200 rpm salvo que el modelo y el cliente definan esa
convención.

La **calidad** acompaña la lectura e informa si el dato es actual, no disponible,
obsoleto o afectado por un fallo. `waiting` suele indicar que aún no llegó la
primera muestra; `stale` que se conserva un último valor, pero la comunicación
ya no está vigente. Un valor antiguo no debe presentarse como medición actual.
La calidad del contenedor y la calidad del punto también son distintas: el
servicio puede estar sano aunque un destino o dirección concreta no responda.

### Qué significa cada tipo de conexión

Las líneas del mapa son relaciones con semántica distinta:

| Relación | Qué lleva o permite | Ejemplo | Qué no hace |
| --- | --- | --- | --- |
| Proceso físico | Magnitud modelada, normalmente caudal, nivel o presión | Bomba → depósito → válvula | No crea una dirección PLC ni tráfico Ethernet |
| Señal de campo | Valor BOOL/REAL/UINT16 entre un canal y un puerto de objeto | Sensor `value` → entrada PLC | No es un paquete S7 ni un cable eléctrico real |
| Protocolo | Petición de un cliente a un servidor de datos | HMI lee `DB1.DBD4` por S7 | No decide qué tag quiere leer ni conecta un actuador |
| Ethernet/zona | Alcanzabilidad de red del contenedor | HMI y PLC en el mismo segmento | No crea tags, permiso de escritura ni lógica |
| Ruta entre zonas | Flujo de servicio autorizado entre segmentos | SCADA → TCP/102 del PLC | No es routing IP general |
| Lógica de control | Decisión que evalúa señales y actualiza una salida | Nivel bajo → orden de bomba | No añade por sí misma el sensor, proceso o cliente HMI |

Una conexión solo está completa cuando existe el endpoint correcto en cada
capa. Para una válvula controlada por HMI, por ejemplo, debe existir un objeto
válvula, un punto de salida compatible, una conexión de señal, un servidor
accesible, una HMI que seleccione ese punto y permiso para escribirlo. Si se
espera una respuesta visual, la válvula debe exponer estado y el proceso debe
tener las conexiones físicas necesarias para que cambie el caudal.

## Direccionamiento y permisos

S7 presenta áreas/canales con direcciones resueltas; Modbus tiene espacios
separados (coils, discrete inputs, holding registers e input registers). Una
dirección dentro del mapa no implica que sea escribible. El backend determina
el permiso desde el tipo de punto, el canal configurado, las conexiones de
campo, el rol y el destino permitido por HMI/SCADA.

Las entradas de campo conectadas representan valores escritos por el runtime de
campo y son de solo lectura desde la consola/HMI/SCADA. Las salidas R/W se pueden
operar solo si el controlador las declara y la vista selecciona el punto. Las
lecturas muestran además calidad/estado; no se debe sustituir `waiting`, `stale`
o error por un valor inventado.

En S7, el tipo BOOL/REAL y el área DB/M/I/Q determinan cómo se interpreta el
dato. En Modbus, el tipo y espacio también importan: coils y holding registers
admiten escritura protocolaria, mientras discrete inputs e input registers son
de solo lectura. El permiso efectivo de una operación Anvil puede ser más
restrictivo que la capacidad del protocolo; una dirección válida no basta para
autorizar una escritura por la API, HMI o SCADA de Anvil. Estas comprobaciones
no instalan una ACL de usuario en el servidor S7/Modbus: un cliente
protocolario directo puede realizar las operaciones que acepte ese servidor.
Restringe ese acceso mediante las redes de escenario y la publicación del
puerto, desactivada por defecto; consulta [Acceso a dispositivos OT](ACCESS.md).

## Red y frontera OT

Sin zonas, los nodos de un escenario comparten una red Docker interna. Con zonas,
cada segmento tiene una subred y límites explícitos. Una comunicación entre
zonas necesita una ruta para el servicio/flujo compatible; no existe un router
IP general. Los puertos del host permanecen cerrados salvo que el operador
configure publicación explícita, restringida a loopback o IPv4 concreta.

El backend controla un gateway Docker porque necesita crear contenedores y redes.
Ese gateway conserva autoridad alta sobre el host; los contenedores OT
restringidos no eliminan ese riesgo. Revisa [seguridad](CONTAINER_SECURITY.md)
antes de compartir una instancia.

## Tiempo y fidelidad

Los estados de campo y los bloques de lógica se actualizan por ciclos del
simulador. Los retrasos, intervalos y marcas de tiempo de Anvil no son un
contrato de tiempo real. El sistema ofrece compatibilidad funcional para pruebas
de integración, aprendizaje y ejercicios; no es un PLC certificado ni clona el
hardware, firmware, diagnósticos, temporización o comportamiento eléctrico de
un fabricante. Consulta [fidelidad](DEVICE_FIDELITY.md) para el detalle.
