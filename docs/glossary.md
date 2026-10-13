# Glosario OT y de Anvil

Una misma palabra puede referirse a un objeto físico, a un punto de datos o a
una función del simulador. Este glosario fija el significado usado en este
manual. Los nombres entre comillas o en `monoespaciado` suelen coincidir con el
inspector, API o modelo.

## Automatización industrial

| Término | Significado aquí |
| --- | --- |
| **OT** (Operational Technology) | Tecnología que observa o controla procesos físicos: instrumentación, PLC, HMI y redes industriales. |
| **ICS** | Sistema de control industrial; conjunto de controladores, sensores, actuadores y supervisión de un proceso. |
| **PLC** | Controlador programable que lee entradas, evalúa lógica y manda salidas. Anvil simula comportamiento y acceso de protocolo definidos por su propio modelo; no ejecuta firmware Siemens. |
| **Servidor PLC** | Servicio que responde a clientes de protocolo con datos de memoria del controlador. En Anvil el servidor S7 es una parte del runtime PLC; no es el programa de control ni transmite todos los puntos espontáneamente. |
| **Cliente industrial** | Equipo/software que inicia peticiones a un servidor PLC o Modbus. HMI, SCADA, herramientas de ingeniería y enlaces PLC configurados pueden actuar como clientes con alcances distintos. |
| **E/S (I/O)** | Entradas y salidas del controlador. Entrada entrega datos al PLC; salida lleva una orden hacia el proceso. |
| **Sensor / transmisor** | Instrumento que mide una magnitud (nivel, presión, temperatura) y la presenta como señal. El sensor independiente de Anvil es un objeto de campo, no necesariamente un transmisor eléctrico modelado. |
| **Actuador** | Elemento que cambia el proceso al recibir una orden: válvula, motor o bomba. Mando es la señal; posición/estado es la respuesta modelada. |
| **HMI** | Human-Machine Interface: pantalla de operador para ver tags, alarmas y controles autorizados. No es el PLC ni la lógica del proceso. |
| **SCADA** | Supervisory Control and Data Acquisition: runtime de adquisición que sondea varios equipos, persiste tendencias y evalúa alarmas. HMI y SCADA pueden compartir funciones, pero no son el mismo nodo en Anvil. |
| **Tag / punto** | Nombre que identifica una medición, estado u orden y se relaciona con tipo, unidad, dirección y permisos. “Nivel” es un nombre funcional; `DB1.DBD4` es una dirección S7, no el nombre del punto. |
| **Scan** | Ciclo en el que el runtime evalúa puntos y bloques PLC. Anvil usa un intervalo nominal, sin garantía de tiempo real. |
| **Programa de control** | Reglas que deciden las salidas a partir de entradas. Anvil ofrece bloques configurables limitados; no importa ni ejecuta programas TIA/Ladder/ST arbitrarios. |
| **Canal de E/S** | Punto de datos BOOL/REAL de PLC con dirección y papel de entrada o salida; representa el dato lógico, no un módulo eléctrico físico. |
| **Mapa de E/S** | Relación entre nombres/canales, tipo, dirección y permisos de las operaciones gestionadas por Anvil. Es distinto de la lista de tags que un cliente HMI/SCADA consulta y no necesariamente impone ACL al protocolo directo. |
| **Entrada** | Punto que lleva una medida/estado hacia el modelo del controlador. La conexión de campo que lo alimenta normalmente lo hace de solo lectura en la UI. |
| **Salida** | Punto que lleva una orden desde el controlador hacia un actuador. Puede ser operable desde clientes Anvil si el punto, rol y política lo permiten. |
| **Bloque de lógica** | Función acotada (ON/OFF, AND/OR/XOR, temporizador, memoria, contador o secuenciador) que el scan evalúa; no es una instrucción arbitraria ni un proyecto Ladder/ST. |
| **BOOL** | Dato lógico verdadero/falso, normalmente asociado a una entrada o salida digital. |
| **REAL** | Número de coma flotante S7 de 32 bits. En Anvil se usa para magnitudes como porcentaje, nivel o presión según unidad/rango configurados. |
| **UINT16** | Entero sin signo de 16 bits (0–65535); tipo de punto de registro Modbus en el modelo actual. |
| **Consigna / setpoint** | Valor objetivo usado por una lógica o actuador. No es necesariamente la medida actual. |
| **Histéresis** | Banda entre umbrales de encendido y apagado que evita conmutar repetidamente cerca de un único umbral. |
| **HMI sinóptico** | Vista gráfica de operación vinculada a tags leídos; el dibujo aislado no genera telemetría ni conecta equipos. |
| **Sondeo** | Petición periódica iniciada por cliente HMI/SCADA para leer un punto. No es una suscripción automática del PLC. |
| **Buffer** | Memoria temporal y acotada de muestras recientes antes de persistirlas o rotarlas. No equivale al histórico durable. |
| **Fail-safe / seguridad funcional** | Diseño físico certificado para llevar el proceso a estado seguro; Anvil no certifica ni implementa funciones SIL. |
| **TIA Portal** | Entorno de ingeniería Siemens para configurar hardware y desarrollar/descargar proyectos compatibles. Anvil no importa ni ejecuta proyectos TIA. |
| **Ladder / FBD / Structured Text (ST)** | Lenguajes/notaciones de programación de PLC. Anvil no compila estos programas; ofrece bloques propios configurables, descritos en [Lógica PLC](PLC_LOGIC.md). |
| **IEC 61131-3** | Norma que define lenguajes y elementos de programación de PLC. Usar algunos conceptos parecidos (por ejemplo, TON) no significa que Anvil sea un runtime completo conforme a la norma. |
| **VFD / variador de frecuencia** | Equipo que regula la velocidad de un motor mediante electrónica de potencia. El objeto de Anvil modela una consigna y caudal proporcional, no frecuencia eléctrica, par, rampas ni protecciones del variador. |

## Protocolos y direccionamiento

| Término | Significado aquí |
| --- | --- |
| **S7comm** | Protocolo de acceso a datos Siemens; el runtime S7 de Anvil ofrece un servidor `python-snap7` en TCP/102 para operaciones verificadas. |
| **DB** | Data Block: área numerada de datos S7; por ejemplo, `DB1`. |
| **M / I / Q** | Marcas de memoria interna, imagen de entradas y de salidas de proceso S7. El mapa Anvil distingue sus permisos y direcciones. |
| **BOOL / bit** | Valor verdadero/falso representado como un bit; una dirección indica byte y bit, por ejemplo `DBX0.3`. |
| **REAL** | Número de coma flotante S7 de 32 bits (IEEE-754, cuatro bytes); `DBD4` indica el comienzo del valor en un DB. |
| **Modbus coil** | Punto booleano de lectura/escritura del modelo Modbus. |
| **Discrete input** | Punto booleano de solo lectura por Modbus. |
| **Holding register** | Registro de 16 bits que admite lectura y escritura según las funciones Modbus. Las rutas gestionadas de Anvil solo escriben el punto si su mapa lo publica como `read_write`; Modbus directo no recibe por ello una ACL de usuario Anvil. |
| **Input register** | Registro de 16 bits de solo lectura protocolaria. |
| **0-based address** | Índice desde cero que usa el protocolo/API para puntos Modbus; las convenciones de etiquetas humanas `4xxxx` pueden sumar un offset y no se deben copiar sin conversión. |
| **MBAP** | Cabecera de Modbus TCP. El modo RTU usa trama/CRC y túnel serial virtual, no MBAP. |
| **Unit ID** | Campo de la petición Modbus que selecciona la unidad lógica dentro del servidor alcanzado. No reemplaza IP, puerto, espacio ni dirección. |
| **Function code (FC)** | Código Modbus que indica la operación solicitada, por ejemplo leer coils o escribir holding registers. La ficha de [fidelidad](DEVICE_FIDELITY.md) enumera los códigos comprobados en Anvil; esa lista no implica que todos estén implementados. |
| **Cliente / servidor Modbus** | El cliente inicia una petición; el servidor responde con el punto solicitado o una excepción. La conexión TCP abierta no transmite todos los puntos espontáneamente. |
| **Modbus TCP / RTU** | Transportes del protocolo Modbus. TCP lleva mensajes con cabecera MBAP sobre una conexión TCP; RTU usa tramas con CRC y normalmente un bus serie. Anvil ofrece RTU sobre pseudo-terminal y túnel virtual, no un bus RS-485 eléctrico. |
| **Quality / calidad** | Indicador que acompaña al dato y describe su disponibilidad/vigencia. No debe confundirse con el valor numérico ni con el estado general del contenedor. |
| `waiting` | Aún no hay una primera muestra válida para esa conexión/cliente. No significa valor cero. |
| `good` | La última muestra se obtuvo correctamente y está dentro de la ventana de vigencia que aplica a esa función. |
| `stale` | Se conserva un último valor, pero no se ha actualizado dentro del límite esperado; no debe presentarse como lectura actual. |
| `fault` | El modelo señala un fallo de dispositivo/sensor/actuador o de la conexión. Puede haber un último valor almacenado, que no implica una medición válida ahora. |
| `degraded` | La lectura o runtime presenta una degradación detectada por el modelo; consulta el diagnóstico asociado para distinguir retardo, fallo o condición concreta. No equivale necesariamente a pérdida total de conectividad. |
| **Fallo simulado** | Condición configurada o inducida para ensayar diagnóstico/recuperación. Por ejemplo, un sensor puede congelar su última lectura y un actuador puede mantener la posición anterior; no modela por sí misma un fallo eléctrico físico. |
| **Override** | Valor de prueba que sustituye temporalmente la lectura de un sensor/entrada cuando el endpoint lo permite. No es una orden normal a un actuador ni un cambio de su proceso fuente. |

## Topología y runtime

| Término | Significado aquí |
| --- | --- |
| **Escenario** | Documento guardado con nodos, configuración, redes y conexiones. No es un solo contenedor. |
| **Nodo/dispositivo** | PLC, Modbus, HMI, SCADA u otro runtime desplegable con ID, IP interna, configuración e imagen. |
| **Objeto de campo** | Sensor/actuador/proceso independiente sin IP ni memoria de PLC. El runtime de campo lo calcula y conecta señales a nodos. |
| **Conexión de señal** | Enlace dirigido entre un punto de controlador y un puerto de campo; por ejemplo, salida BOOL → `valve.command`. |
| **Conexión de proceso** | Relación que transporta caudal o medición física simulada; no crea protocolo ni cableado de señal. |
| **Puerto de campo** | Extremo lógico de un objeto independiente, por ejemplo `sensor.value` o `valve.command`; no es un puerto TCP ni una borna eléctrica. |
| **Runtime de campo** | Servicio que mantiene objetos de campo y calcula estados/magnitudes del proceso simplificado y sus señales. No es un PLC adicional. |
| **Tag de supervisión** | Punto que HMI/SCADA configura para consultar en un servidor. Puede referenciar una dirección PLC/Modbus; no se crea automáticamente al dibujar un enlace. |
| **Objeto de proceso** | Elemento modelado como bomba, tubo, depósito, sensor o válvula; representa estado y comportamiento seleccionados, no geometría/física completa. |
| **Ruta de red** | Permiso de conectividad para un flujo declarado entre origen y servicio de otra zona. Las rutas de Anvil usan un proxy TCP acotado; no son routing IP general ni autenticación del protocolo. |
| **IP / dirección de red** | Identifica un nodo en una red IP. En Anvil la IP pertenece a un runtime/nodo, no a una válvula o sensor de campo independiente. No es la dirección de un punto PLC/Modbus. |
| **Puerto TCP** | Número que identifica un servicio dentro de un nodo IP; por ejemplo, S7 usa TCP/102 y Modbus TCP suele usar TCP/502. No es la dirección del registro ni el Unit ID. |
| **MBAP Transaction ID** | Identificador de transacción de Modbus TCP que permite asociar una respuesta con su petición dentro de una conexión. No identifica el dispositivo ni la sesión de usuario. |
| **Endpoint** | Destino de comunicación formado normalmente por dirección de red y puerto, más los parámetros propios del protocolo (por ejemplo, Unit ID Modbus). |
| **Capa 2 (L2) / capa 3 (L3)** | L2 describe comunicación dentro de un segmento Ethernet; L3 enruta entre redes IP. Los bridges Docker de Anvil comparten un segmento; un switch dibujado no implementa VLAN/STP ni un router L3. |
| **Modelo Purdue** | Referencia para organizar sistemas industriales en niveles desde proceso/control hasta operación/empresa. Los niveles de zona de Anvil son metadatos y segmentos aislados; no certifican una arquitectura Purdue real. |
| **Escala / unidad de ingeniería** | Conversión entre un número transmitido (por ejemplo, UINT16 0–65535) y una magnitud legible (porcentaje, presión). En Anvil la unidad etiquetada no escala el valor automáticamente; la conversión debe declararse donde se admite. |
| **Zona Purdue** | Agrupación funcional de activos OT por niveles de arquitectura de referencia; Anvil usa niveles L0–L5 y DMZ como metadatos y los vincula a segmentos Docker. No certifica que una instalación cumpla Purdue. |
| **Bridge / switch lógico** | Segmento L2 Docker compartido. El nodo switch del mapa representa la topología/capacidad; no añade por sí mismo VLAN, STP o filtrado de puerto. |
| **Gateway Docker API** | Servicio intermediario que aplica una allowlist a las llamadas de orquestación. Aun así, tiene autoridad sobre el daemon/host y constituye una frontera de alto impacto. |
| **Imagen Docker** | Paquete que contiene código y dependencias de un runtime. Los cambios al código requieren reconstruir la imagen correspondiente. |
| **Contenedor** | Instancia en ejecución de una imagen, con límites, red y ciclo de vida propio. |
| **Healthcheck** | Prueba de disponibilidad definida para la imagen; no garantiza que un proceso físico completo esté bien. |

## Seguridad y evidencias

| Término | Significado aquí |
| --- | --- |
| **RBAC** | Control de acceso por roles (admin, instructor, designer, participant) con reglas adicionales de propiedad/membresía del escenario. |
| **Bootstrap** | Creación inicial de la primera cuenta usando `ANVIL_BOOTSTRAP_TOKEN`; no es inicio de sesión o token permanente. |
| **Auditoría** | Eventos operativos persistidos que indican actor, tiempo y acción; la cadena SHA-256 detecta cambios internos, pero requiere custodiar externamente las pruebas para anclar confianza. |
| **PCAP** | Captura de paquetes. No es lo mismo que la traza semántica Snap7 que registra área, bytes y resultado en logs. |
| **Ejercicio** | Cronología acotada de eventos y objetivos de comprobación sobre un escenario aislado; no ejecuta código ni ataques arbitrarios. |
| **Evidencia** | Exportación de auditoría, logs, histórico y capturas pertinentes. Un manifiesto con hash prueba consistencia con ese manifiesto; no autentica autoría si ambos se reemplazan. |

Para la explicación completa de cada concepto, sigue los capítulos de
[PLC S7](plc-s7.md), [Modbus](MODBUS.md), [modelo de proceso](process-model.md),
[zonas](NETWORK_ZONES.md), [acceso](PLATFORM_ACCESS.md),
[auditoría](AUDIT.md) y [ejercicios](EXERCISES.md).
