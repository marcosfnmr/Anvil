# Arquitectura funcional y cobertura del SRS

Esta página explica cómo se organiza Anvil desde el punto de vista de quien
diseña, despliega y opera un laboratorio. Sirve de guía entre conceptos,
funciones reales y requisitos del [SRS v1.0](SRS_REQUIREMENTS_v1.md). No cambia
la evaluación de cumplimiento: para el estado detallado, evidencia y brechas
por identificador, consulta la [matriz de trazabilidad](SRS_TRACEABILITY.md).

## Para qué sirve Anvil

Anvil es una plataforma para crear ciber-rangos OT reproducibles. En un
escenario se describe una planta simplificada, sus dispositivos, datos,
relaciones y redes. El backend valida y guarda esa descripción; al desplegar,
crea runtimes Docker separados y redes internas. El operador puede consultar
valores, observar los protocolos, introducir perturbaciones acotadas y
comprobar objetivos de un ejercicio.

Un escenario no es una sola máquina virtual ni una copia de una fábrica. Es
configuración declarativa que Anvil convierte en varios servicios de software:
PLC S7, Modbus, campo, HMI, SCADA, proxies explícitos y estaciones auxiliares.
Los servicios y las conexiones que realmente existen dependen de la
configuración y de la cobertura de cada adaptador.

## Arquitectura por capas

| Capa | Responsabilidad | Qué debe entender el usuario | Requisitos relacionados |
| --- | --- | --- | --- |
| Interfaz web | Editor del escenario, mapa, inspectores, operación y resultados | El mapa combina relaciones distintas; configurar un borrador no equivale a desplegarlo | RF-01–04, RF-12, RF-28, RF-36 |
| API y modelos | Autentica, valida, persiste, autoriza y convierte operaciones | La API rechaza topología, direcciones o escrituras incompatibles antes de aceptarlas | RF-02, RF-04–05, RF-35–43 |
| Adaptadores de dispositivos | Eligen esquema, imagen, recursos, redes y configuración runtime | Cada tipo de nodo tiene capacidades y permisos propios; el catálogo no carga plugins ejecutables | RF-08, RF-14, RF-24, RF-26 |
| Runtimes de protocolo | Exponen o consumen S7 y Modbus TCP/RTU | Un servicio compatible no es un PLC completo; los clientes inician lecturas/escrituras | RF-10, RF-15–19, RNF-01–02 |
| Runtime de campo | Actualiza actuadores, señales y el modelo concentrado del proceso | Un objeto de campo no tiene IP; el puente interno de señales no es un bus eléctrico. La imagen se puede seleccionar por configuración, pero su contrato HTTP actual es interno y no ofrece instalación de plugins | RF-11, RF-20–23, RNF-13 |
| Docker y redes | Ejecuta contenedores, segmentos, rutas dedicadas, healthchecks y recuperación | Las redes de escenario son internas; una ruta explícita solo deja pasar el flujo configurado | RF-24–30, RNF-08–10, RNF-15–16 |
| Evidencia y ejercicios | Guarda auditoría, muestras, capturas y resultados de cronología | Una auditoría hash no equivale a firma; un ejercicio no ejecuta ataques arbitrarios | RF-34–40 |

La estructura de código y el orden de una petición están en
[Diseño técnico](DESIGN.md). La interfaz de usuario se explica en
[Interfaz](INTERFACE.md), las rutas en [API](api.md) y el ciclo de vida en
[Escenarios](scenarios.md).

## Cómo se construye un laboratorio

1. **Diseño:** el usuario añade nodos de red y objetos de campo, elige
   configuración, direcciones y zonas. Sensores/actuadores y canales PLC son
   entidades diferentes.
2. **Conexión:** se definen por separado el proceso físico, el cableado de
   señal, la relación de control, los destinos de HMI/SCADA y los flujos de red.
3. **Validación:** Pydantic y los validadores de topología revisan tipos,
   rangos, IP, puertos, direcciones, solapes, permisos y enlaces. Algunos
   hallazgos de proceso o diseño son advertencias consultivas; `valid=true` no
   es una certificación de ingeniería.
4. **Guardado:** la API persiste la definición y revisiones del escenario. No
   inicia ni actualiza por sí sola los runtimes activos.
5. **Despliegue:** el gestor Docker crea la red, dispositivos y runtimes
   internos en el orden requerido. La imagen instalada debe incluir el código
   y dependencias del runtime correspondiente.
6. **Operación:** HMI/SCADA/API consultan datos; las escrituras permitidas viajan
   por clientes protocolarios; el proceso de campo calcula respuestas. Estado,
   calidad, logs y auditoría ayudan a localizar fallos.
7. **Parada/recuperación:** detener quita los runtimes del escenario y conserva
   su definición. Reconciliación y operaciones por nodo intentan dejar las
   acciones recuperables; no se debe borrar manualmente un contenedor que el
   orquestador todavía gestiona.

Para construir paso a paso el ejemplo de agua, sigue
[el tutorial](tutorial.md). Para comandos de arranque y actualización, usa
[Operaciones](OPERATIONS.md).

## Qué significa cada relación del mapa

| Relación | Capa y sentido | Efecto implementado | No implica |
| --- | --- | --- | --- |
| Proceso físico | Puerto de proceso a puerto compatible | El runtime de campo calcula caudal, balance de tanque y magnitudes aproximadas | IP, permiso de escritura, cableado eléctrico o solver hidráulico general |
| Señal PLC↔campo | Punto de dispositivo y puerto de campo | El runtime de campo lee/escribe el punto mediante bridge interno y aplica tipo/escala declarados | Protocolo S7/Modbus entre esos dos objetos o transductor físico |
| PLC↔PLC S7 | `remote_input` en receptor consulta punto DB de emisor | Lectura periódica desde el PLC receptor, con calidad de comunicación | E/S cíclicas, PROFINET, escritura bidireccional o sincronización de scan |
| Cliente HMI/SCADA | Destino y puntos elegidos contra servidor | Peticiones protocolarias, visualización, adquisición y alarmas compatibles | Descubrimiento automático de tags por estar en la misma red |
| Ethernet lógico | Enlace visual entre nodos de una zona | Sin switch es declarativo; con switch representa membresía en el mismo bridge L2 Docker | VLAN, STP, filtro por puerto o permiso interzona |
| Ruta TCP | Origen, destino, puerto/protocolo y estado | Proxy de un único flujo entre segmentos; el origen se limita al nodo declarado | Router L3, ACL arbitraria o conectividad a destinos no declarados |

Las definiciones y puertos concretos están en [Mapa de proceso](PROCESS_MAP.md),
[Objetos de campo](FIELD_OBJECTS.md), [S7](S7_LINKS.md) y
[Zonas y rutas](NETWORK_ZONES.md).

## Subsistemas y límites

### PLC y control

El nodo `plc-s7` ofrece un servidor Snap7 compatible con operaciones S7
probadas. La configuración declara rack/slot, tamaño de DB, áreas/canales,
componentes, reglas de lógica, ciclo nominal y fallos admitidos. Un conjunto
limitado de bloques se ejecuta por scan. El servidor S7 publica memoria; no
compila Ladder/ST, no ejecuta proyectos TIA y no implementa firmware ni CPU
Siemens. El cliente es quien abre la conexión y solicita cada dato. Revisa
[PLC S7](plc-s7.md), [bloques](PLC_LOGIC.md) y
[fidelidad](DEVICE_FIDELITY.md).

### Modbus

El nodo `modbus-tcp` ofrece un mapa configurado de coils, discrete inputs,
holding registers e input registers. Las escrituras dependen tanto del espacio
protocolario como del mapa, punto seleccionado y permiso de usuario. RTU usa
un puerto serie virtual y un túnel de bytes; conserva la trama pero no emula
cable RS-485. Modbus por sí solo es un mapa de datos, no un programa de control.
Consulta [Modbus TCP y RTU](MODBUS.md).

### Proceso y campo

Los objetos de campo tienen estado y puertos en el modelo, pero no dirección IP
individual. Un runtime por escenario actualiza válvulas, bombas, sensores,
depósitos, tuberías, distribuidores y colectores conectados. El alcance
hidráulico es un balance determinista con aproximaciones de caudal/presión; no
calcula una instalación mediante un solver físico general. Retardo, fallo,
demanda y overrides son perturbaciones modeladas, no averías eléctricas.
Consulta [modelo de proceso](process-model.md) y [perturbaciones de campo](FIELD_OBJECTS.md).

### HMI y SCADA

La HMI es la vista web de operación asociada a un nodo cliente: consulta puntos
seleccionados, presenta sinóptico/estados/alarmas y permite escrituras
autorizadas. SCADA es un runtime de sondeo multiequipo: conserva muestras y
evalúa alarmas en el modelo Anvil. El backend persiste histórico y estado
operativo en SQLite. La cobertura no incluye OPC UA ni equivalencia con
productos comerciales; la continuidad de muestras depende de los buffers y
periodos configurados. Consulta [HMI](hmi.md) y [SCADA](SCADA.md).

### Red y Docker

Por defecto, cada escenario tiene red Docker interna y no publica puertos en el
host. Las zonas crean segmentos separados; para ciertos protocolos se puede
habilitar un proxy por flujo declarado. La estación participante/ingeniería
comienza aislada. Los runtimes usan controles de contenedor con privilegio
reducido, pero el backend alcanza un gateway Docker de autoridad alta. Ese
gateway y el host siguen dentro de la base de confianza. Las limitaciones de
host, rootless, captura y publicación externa figuran en
[Seguridad de contenedores](CONTAINER_SECURITY.md).

El reconciliador compara el runtime con el plan persistido. Si un escenario
con intención activa pierde una o todas sus redes Docker internas gestionadas,
las reconstruye desde las subredes y zonas persistidas, siempre que sus nombres
no estén ocupados por recursos externos. Después registra intenciones durables
para recuperar los nodos ausentes bajo lease y journal. Respeta escenarios
`stopped`, no adopta redes ajenas y deja visible la colisión que impide
recuperar una subred. Docker y SQLite no forman una transacción distribuida;
consulta [Operaciones](OPERATIONS.md) y la evidencia de RF-27/RNF-15/16 en la
[matriz SRS](SRS_TRACEABILITY.md).

### Cuentas, auditoría y ejercicios

La autorización combina sesión, rol y propiedad/membresía del escenario. Las
operaciones persistidas permiten reconstruir eventos y exportar bundles; un
hash en cadena detecta modificaciones posteriores, pero no autentica por sí
solo la fuente ni protege una base SQLite controlada por el host. Los ejercicios
programan acciones acotadas y verifican condiciones; no lanzan exploits,
MITM ni código arbitrario. Consulta [Cuentas](PLATFORM_ACCESS.md),
[Auditoría](AUDIT.md) y [Ejercicios](EXERCISES.md).

## Cobertura: cómo leer el estado

El SRS v1.0 enumera 84 identificadores funcionales, no funcionales, técnicos,
de interfaz, datos, aceptación y supuestos. La prueba automatizada confirma que
cada ID aparece en la matriz; comprueba integridad de referencias, no la
implementación. La matriz utiliza evidencia de código, pruebas unitarias,
smokes de Docker y pruebas de navegador con alcances distintos.

- **Hecho** significa que se implementó y la evidencia indicada cubre el
  alcance expresado. No implica identidad con un producto industrial.
- **Parcial** significa que hay capacidad demostrable y al menos una brecha
  explícita. Lee también la limitación del requisito.
- **Pendiente** o **futuro** requiere trabajo antes de presentarlo como
  disponible.
- Una prueba unitaria no demuestra interoperabilidad de red; una prueba Docker
  local no acredita GitHub, una VM ni otros motores. La fecha, checkout, imagen
  y preview deben corresponder a la evidencia citada.

La [matriz SRS](SRS_TRACEABILITY.md) ofrece el estado y evidencias por requisito.
La [ficha de fidelidad](DEVICE_FIDELITY.md) resume la interoperabilidad probada
por protocolo. Las próximas fases propuestas y las brechas de producto se
mantienen en la [hoja de ruta](ROADMAP.md).
