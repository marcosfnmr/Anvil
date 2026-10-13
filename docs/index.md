# Anvil

**Anvil** permite diseñar y ejecutar laboratorios OT aislados con PLCs S7,
dispositivos Modbus, equipos de supervisión y un proceso físico simplificado.
Esta documentación reúne el recorrido de usuario, la configuración técnica,
la operación y las limitaciones verificadas del simulador.

Anvil es una plataforma para construir ciber-rangos OT reproducibles. Un
escenario es una descripción persistida de dispositivos, redes, señales y
conexiones de proceso. Al desplegarlo, el backend coordina contenedores Docker
aislados y observa su estado. El protocolo S7 o Modbus funciona dentro de esos
contenedores; la interfaz no inventa una lectura cuando no existe respuesta.

## Elige por dónde empezar

| Quiero… | Empieza aquí |
| --- | --- |
| Entender el vocabulario industrial | [Glosario OT y de Anvil](glossary.md) |
| Entender qué es el PLC y qué simula Anvil | [PLC S7 en Anvil](plc-s7.md) |
| Comparar PLC, Modbus, campo, HMI y SCADA | [Conceptos de Anvil](concepts.md#que-hace-cada-tipo-de-equipo) |
| Entender qué son HMI y SCADA | [HMI](hmi.md) y [SCADA](SCADA.md) |
| Instalar Anvil y abrir el panel | [Instalación y primer arranque](getting-started.md) |
| Crear mi primera fábrica | [Recorrido guiado](tutorial.md) |
| Entender el mapa y conectar objetos | [Mapa de proceso](PROCESS_MAP.md) y [elementos de campo](FIELD_OBJECTS.md) |
| Conectar dos PLCs | [Enlaces S7](S7_LINKS.md) |
| Configurar un servidor Modbus | [Modbus TCP y RTU](MODBUS.md) |
| Crear una demo para presentar | [Demo de planta de agua](DEMO_PLANTA_AGUA.md) |
| Operar una instancia ya desplegada | [Operaciones](OPERATIONS.md) |
| Consultar endpoints | [API y WebSocket](api.md) o `/docs` en la instancia |
| Revisar si Anvil reproduce un PLC físico | [Fidelidad y límites](DEVICE_FIDELITY.md) |
| Entender qué calcula el proceso y cómo se conecta a PLC/Modbus | [Motor de proceso y runtime de campo](PROCESS_ENGINE.md) |
| Entender qué significa cada parte del PLC de Anvil | [Conceptos](concepts.md#como-leer-la-ficha-de-un-plc) y [PLC S7](plc-s7.md) |
| Ver el estado requisito a requisito | [Matriz SRS](SRS_TRACEABILITY.md) |
| Entender cómo se relacionan los módulos y el SRS | [Arquitectura funcional y cobertura](SRS_ARCHITECTURE.md) |

## Conceptos que conviene entender antes de configurar

Si una página introduce un término industrial, puedes seguir la ruta de
referencia correspondiente. La explicación parte del concepto general y luego
describe la implementación y los límites concretos de Anvil:

| Concepto | Qué conviene distinguir | Guía |
| --- | --- | --- |
| PLC | Controlador, servidor S7, memoria, ciclo, bloques de control y diferencias frente a CPU/firmware físico | [PLC S7](plc-s7.md) y [lógica](PLC_LOGIC.md) |
| Modbus | Cliente/servidor, Unit ID, cuatro espacios, direcciones base cero y permisos de lectura/escritura | [Modbus TCP y RTU](MODBUS.md) |
| Instrumentación | Medición de proceso, orden, estado de actuador y conexiones físicas frente a señales | [Elementos de campo](FIELD_OBJECTS.md) |
| HMI y SCADA | Cliente, tag, sondeo, calidad, alarmas, sinóptico e histórico | [HMI](hmi.md) y [SCADA](SCADA.md) |
| Red OT | IP, puerto, protocolo, dirección de punto, zonas y rutas explícitas | [Zonas y rutas](NETWORK_ZONES.md) |
| Proceso | Magnitudes, unidades, balance de caudal/volumen y simplificaciones del modelo | [Modelo de proceso](process-model.md) |
| Comunicación completa | Cómo fluye una variable de proceso hasta PLC, actuador y HMI | [Conceptos de Anvil](concepts.md#recorrido-de-una-variable-desde-el-proceso-hasta-la-hmi) |

La ruta más práctica es leer [Conceptos de Anvil](concepts.md), recorrer la
[estación de bombeo](tutorial.md) y después consultar la ficha del protocolo o
equipo que quieras configurar. Cada ficha explica el concepto industrial antes
de enumerar los campos del inspector y responde estas preguntas:

1. Qué significa el equipo o concepto en una instalación y para qué sirve.
2. Qué papel tiene en Anvil y qué componentes/software realizan su función.
3. Qué se simula, qué se configura y cuáles son sus valores, tipos y permisos.
4. Cómo se conecta, quién inicia el intercambio y qué puntos o rutas necesita.
5. Cómo comprobar el recorrido completo y localizar fallos por cada capa.
6. Qué no está implementado o verificado, para no confundir simulación con
   equivalencia física.

## Cómo leer cualquier ficha de equipo

Las páginas de referencia siguen el mismo recorrido, para que puedas pasar de
la idea industrial a una topología funcional en Anvil:

1. **Qué es el equipo:** su función habitual en una fábrica y qué otros equipos
   suelen interactuar con él.
2. **Qué ejecuta Anvil:** el proceso software, el protocolo y el comportamiento
   que se han implementado. Una compatibilidad protocolaria no implica emular
   CPU, firmware, electrónica ni todos los comportamientos de fabricante.
3. **Qué se configura:** el modelo guardado en el escenario, con sus tipos,
   direcciones, valores iniciales, permisos, ciclos y opciones soportadas.
4. **Cómo se conecta:** quién inicia la comunicación, qué punto viaja, en qué
   sentido y si hace falta una relación de proceso, señal o red.
5. **Cómo comprobarlo:** una lectura/escritura o una prueba de extremo a extremo
   que confirme valor, calidad y estado del proceso.
6. **Qué queda fuera:** límites relevantes para no presentar el simulador como
   un equipo certificado o una réplica completa.

Esta distinción importa porque compartir una red no configura los datos, una
línea de proceso no equivale a un cableado de E/S y el dibujo de una pantalla no
crea una tag. El [recorrido guiado](tutorial.md) muestra todas esas capas en un
solo ejemplo.

Por ejemplo, [PLC S7](plc-s7.md) explica qué hace un PLC, qué papel cumple S7,
qué partes del controlador reproduce el runtime y cómo se conectan los canales
con campo, otros PLC, HMI y SCADA. El mismo recorrido se aplica a
[Modbus](MODBUS.md), [sensores y actuadores](FIELD_OBJECTS.md), [HMI](hmi.md),
[SCADA](SCADA.md), [proceso](PROCESS_ENGINE.md) y [redes OT](NETWORK_ZONES.md).
Para distinguir las líneas de proceso, señales, red y protocolo en la interfaz,
consulta el [mapa](INTERFACE.md).

## Cómo leer este manual

Los capítulos **Primeros pasos** y **Diseñar un escenario** explican las tareas
en el orden en que normalmente se realizan. **Dispositivos y protocolos** entra
en la configuración de cada runtime. **Operar y administrar** cubre la vida del
escenario ya desplegado. Los capítulos de **Seguridad y límites** separan el
comportamiento implementado de las propiedades aún pendientes. **Desarrollo**
describe cómo modificar Anvil sin saltarse sus adaptadores, validación y pruebas.

Las guías especializadas conservan el detalle de referencia y se enlazan desde
la navegación. La matriz SRS es la fuente de estado por requisito. “Parcial”
significa que existe una porción demostrable y que el texto enumera sus límites;
no debe presentarse como cumplimiento completo.

## Mapa de conceptos

Anvil representa una planta en varias capas. Conviene distinguirlas antes de
montar el primer escenario:

| Capa | Qué existe en una instalación | Cómo se representa en Anvil | Guía de referencia |
| --- | --- | --- | --- |
| Proceso | Fluido/material, equipos y magnitudes físicas | Objetos de campo y conexiones de proceso calculadas por un runtime | [Modelo de proceso](process-model.md), [motor de proceso](PROCESS_ENGINE.md), [mapa](PROCESS_MAP.md) |
| Instrumentación y actuación | Sensores, válvulas, motores, bombas y variadores | Objetos independientes con puertos de mando/estado/medición; no tienen IP | [Objetos de campo](FIELD_OBJECTS.md) |
| Control | PLC, entradas/salidas y programa cíclico | PLC S7 simulado, mapa de memoria y conjunto documentado de bloques | [PLC S7](plc-s7.md), [lógica](PLC_LOGIC.md) |
| Comunicación | Ethernet, protocolos, clientes, servidores y rutas | Redes Docker por escenario/zona; S7/Modbus en los runtimes, rutas TCP explícitas entre segmentos | [Zonas y rutas](NETWORK_ZONES.md), [S7](plc-s7.md), [Modbus](MODBUS.md) |
| Supervisión | Pantalla de operación, adquisición, alarmas e históricos | HMI y SCADA como nodos separados con destinos y puntos seleccionados | [HMI](hmi.md), [SCADA](SCADA.md) |
| Ciber-rango | Usuarios, ejercicios, auditoría y evidencias | API y contenedores gestionados, políticas y ejercicios de alcance limitado | [Acceso](PLATFORM_ACCESS.md), [ejercicios](EXERCISES.md), [auditoría](AUDIT.md) |

### Dispositivos: qué son, qué reproduce Anvil y cómo se conectan

La siguiente tabla sirve como orientación antes de abrir los capítulos de
referencia. **Simular** significa ejecutar el comportamiento de software que
se indica; no significa emular todo el hardware o firmware del fabricante.

“Qué es en una instalación” describe el papel industrial, no una promesa de
equivalencia. “Qué reproduce Anvil” identifica el proceso o protocolo que sí
ejecuta el checkout; “qué se configura” enumera el modelo guardado que controla
ese comportamiento; y “cómo se conecta” identifica quién inicia cada
intercambio y qué relación hay que declarar. Una ruta de red da alcance al
servicio pero no conecta un sensor, no crea lógica PLC y no selecciona tags HMI.

| Elemento | Qué es en una instalación | Qué reproduce Anvil | Qué se configura | Cómo se conecta | Qué no representa |
| --- | --- | --- | --- | --- | --- |
| [PLC S7](plc-s7.md) | Controlador que lee entradas, ejecuta lógica y actualiza salidas | Servidor S7 de software, áreas de memoria y canales compatibles, ciclo nominal y bloques de lógica incluidos en el modelo | Rack/slot, tamaño DB, canales BOOL/REAL, direcciones, bloques, scan, fallos y retención admitidos | Canales a sensores/actuadores por señales; `remote_input` consulta un DB de otro PLC por S7; HMI/SCADA son clientes S7 | CPU/firmware Siemens, proyecto TIA/Ladder/ST, E/S eléctrica, PROFINET y tiempo real duro |
| [Dispositivo Modbus](MODBUS.md) | Servidor que expone valores a clientes mediante coils y registros | Servidor Modbus TCP y transporte RTU virtual con mapa configurable | Unit ID, transporte, puerto, espacio, dirección, valor inicial y acceso de cada punto | HMI/SCADA consultan el servidor; señales de campo se enlazan a puntos compatibles con conversiones explícitas | Lógica de PLC implícita, bus RS-485 físico, escalado industrial supuesto o todas las funciones Modbus |
| [Sensores y actuadores](FIELD_OBJECTS.md) | Instrumentación que mide el proceso o lo modifica | Estado de válvulas/motores/bombas, medidas configuradas y modelo concentrado de proceso | Tipo, rango, unidad, estado inicial, respuesta, ruido/fallo y conexiones | Señales unen `value`/`command` con canales PLC o puntos Modbus; conexiones de proceso unen equipos | IP propia, transmisor electrónico, tensión/corriente, curvas completas o planta física |
| [HMI](hmi.md) | Pantalla del operador para observar y emitir mandos autorizados | Cliente S7/Modbus y vista web con tags, alarmas y sinóptico disponibles | Destinos, puntos, alarmas, permisos y asociación de datos gráficos | Inicia peticiones al servidor PLC/Modbus; la conectividad de red no selecciona tags por sí sola | Panel físico de fabricante, proyecto gráfico importado o control automático independiente |
| [SCADA](SCADA.md) | Sistema de supervisión y adquisición de varios equipos | Cliente que sondea puntos, mantiene buffer y persiste muestras/alarmas soportadas | Destinos, direcciones, periodo, capacidad de histórico y reglas | Consulta PLC/Modbus por red compartida o ruta TCP declarada; API/HMI muestran histórico recogido | Historiador HA, adquisición durante paradas, OPC UA o alarmas certificadas |
| [Zonas, switches y rutas](NETWORK_ZONES.md) | Segmentos y caminos de comunicación entre activos | Redes internas Docker por zona y proxies TCP limitados a flujos declarados | Zona, subred, extremos, protocolo, puerto y estado de ruta | Los clientes alcanzan servicios dentro de su zona o mediante una ruta habilitada | Switch físico, router L3 general, VLAN/STP o firewall industrial completo |
| [Modelo de proceso](process-model.md) | Comportamiento físico: caudal, nivel y presión del sistema | Balance concentrado para elementos y topologías actualmente soportados | Capacidad, caudales, límites, presión nominal, fuga, demanda y condición inicial | Recibe el estado de actuadores y alimenta sensores; las señales llevan resultados al PLC | Solver CFD/hidráulico general, transitorios, cavitación o curvas reales de equipos |

En todos los casos hay cinco preguntas distintas: **qué equipo existe**, **qué
datos publica**, **qué lógica o proceso modifica esos datos**, **qué cliente los
consulta** y **qué red permite llegar al servicio**. Un escenario solo funciona
de extremo a extremo cuando se configuran las relaciones que necesita cada
capa. Por ejemplo, dibujar una tubería no crea una entrada de PLC y conectar
una HMI a la misma zona no configura automáticamente su lista de tags. El
capítulo [Conceptos de Anvil](concepts.md) desarrolla esta separación; el
[tutorial](tutorial.md) la aplica a una estación de bombeo.

### Ruta de lectura recomendada

1. Empieza con [Conceptos de Anvil](concepts.md) y el [glosario](glossary.md)
   para distinguir nodos, objetos de campo, señales, protocolo y proceso.
2. Sigue el [tutorial de bombeo](tutorial.md) para crear el circuito, sus
   conexiones, canales PLC y cliente HMI.
3. Abre el capítulo del protocolo que realmente usarás: [PLC S7](plc-s7.md) o
   [Modbus](MODBUS.md); consulta [lógica PLC](PLC_LOGIC.md) si automatizas
   salidas.
4. Para entender los sensores/actuadores independientes, sigue
   [elementos de campo](FIELD_OBJECTS.md) y [modelo de proceso](process-model.md).
5. Para conexiones entre PLCs, zonas y clientes de supervisión, consulta
   [enlaces S7](S7_LINKS.md), [redes](NETWORK_ZONES.md), [HMI](hmi.md) y
   [SCADA](SCADA.md).
6. Comprueba siempre la [fidelidad y los límites](DEVICE_FIDELITY.md) antes de
   afirmar compatibilidad con un equipo o función industrial concreta.

Un **PLC** lee entradas, ejecuta lógica y escribe salidas. **S7** es la interfaz
protocolaria de lectura/escritura de datos que Anvil ofrece para el PLC
`plc-s7`; no es su programa de control. **Modbus** organiza datos en coils y
registros, pero no proporciona por sí mismo lógica de PLC. Un **sensor** mide
una magnitud simulada; una **válvula** cambia el proceso cuando recibe un
mando. La HMI presenta datos al operador y SCADA los adquiere e historiza.

### Qué ocurre al conectar los componentes

Piensa en una bomba que llena un depósito. La bomba, el depósito, la válvula y
el sensor se crean como objetos de campo. Las conexiones de proceso hacen que
el runtime calcule el caudal y nivel. Una conexión de señal entrega `sensor.value`
a una entrada PLC; otra conecta una salida PLC con `valve.command`. El PLC
ejecuta su lógica por scan. HMI o SCADA consultan el punto S7/Modbus como
clientes de red y muestran una lectura obtenida del servidor. Cada flecha tiene
un papel distinto:

```text
Proceso:     bomba ──caudal──> depósito ──> válvula
Medición:    depósito ──nivel──> sensor ──señal──> entrada PLC
Control:     entrada PLC ──scan/lógica──> salida PLC ──señal──> válvula
Supervisión: HMI/SCADA ──petición S7 o Modbus──> servidor PLC/dispositivo
Red:         los contenedores se alcanzan por la zona o ruta configurada
```

Una línea del mapa no equivale necesariamente a un cable de red. La conexión
de proceso no autoriza una escritura de protocolo; la conexión de señal no
crea una ruta IP; poner dos nodos en una zona no configura los tags que la HMI
debe leer. Cada relación se guarda y valida por separado. Consulta
[Conceptos de Anvil](concepts.md) para la explicación extensa y
[Recorrido guiado](tutorial.md) para crear este ejemplo en la interfaz.

### Qué significa “simular”

Anvil ejecuta servicios de software en contenedores y calcula el comportamiento
que se ha modelado. Puede reproducir intercambios protocolarios probados,
valores, señales, bloques de lógica acotados, estados de actuadores y algunas
magnitudes del proceso. No ejecuta firmware Siemens ni proyectos TIA, no
reproduce electricidad, tiempos de ciclo de tiempo real, todas las instrucciones
IEC 61131-3 ni una planta hidráulica de ingeniería. Las fichas de cada capítulo
separan lo implementado, lo configurable y lo no modelado; la referencia
[Fidelidad y límites](DEVICE_FIDELITY.md) reúne la cobertura verificada.

## Qué ejecuta Anvil

- Un backend FastAPI con persistencia SQLite sirve la API y la interfaz web.
- Un contenedor Docker separado ejecuta cada runtime de dispositivo; el PLC S7
  habla el protocolo mediante Snap7 y el equipo Modbus implementa TCP/RTU.
- Los escenarios usan redes Docker propias; las zonas Purdue opcionales crean
  segmentos internos separados y las rutas interzona son explícitas.
- Los sensores, válvulas, bombas, depósitos y tuberías simulados son objetos de
  campo sin IP propia. El runtime de campo calcula las señales y el balance del
  proceso; PLCs y dispositivos reciben esas señales mediante sus canales.
- HMI y SCADA leen puntos a través de protocolos industriales. Los permisos de
  escritura se aplican al mapa de E/S y a la operación autenticada.

La arquitectura, incluida la frontera de autoridad del Docker API, está en
[Diseño técnico](DESIGN.md). Para ver las diferencias frente a equipos reales,
consulta [fidelidad de dispositivos](DEVICE_FIDELITY.md).
La explicación funcional de los subsistemas y su relación con los RF/RNF está
en [Arquitectura funcional y cobertura](SRS_ARCHITECTURE.md); el estado probado
requisito por requisito está en [la matriz SRS](SRS_TRACEABILITY.md).

## Versión de esta documentación

La documentación describe el checkout que se está revisando. Las imágenes y
previews locales pueden corresponder a una versión anterior; la guía
[Preview persistente](DEMO_PREVIEW.md) identifica su puerto e imagen. Una prueba
de código no demuestra que una imagen publicada o un PLC físico tenga el mismo
comportamiento.
