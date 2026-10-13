# Recorrido guiado: estación de bombeo

Este tutorial enseña a construir una topología pequeña que separa el equipo
físico del controlador y muestra el recorrido de una señal hasta el protocolo.
La demo JSON lista para importar está en
[`demo-planta-agua-mapa.json`](../examples/demo-planta-agua-mapa.json); para
presentarla paso a paso, sigue [la guía de demo](DEMO_PLANTA_AGUA.md).

## Antes de empezar: qué representa cada elemento

Un **PLC** (controlador lógico programable) lee entradas, evalúa lógica y
actualiza salidas en ciclos. En este tutorial el PLC S7 es un contenedor que
ofrece un servidor compatible con operaciones S7 probadas; no es una CPU
Siemens virtual ni ejecuta un proyecto TIA Portal. En Anvil configuras sus
canales, direcciones y bloques de lógica. Consulta [PLC S7: concepto,
protocolo y configuración](plc-s7.md) para ver qué partes se simulan y cómo
se conecta con otros equipos.

La **bomba**, la **válvula**, el **depósito** y el **sensor** son objetos de
campo independientes: tienen comportamiento de proceso, pero no una IP, un
programa PLC ni un servidor S7 propio. La HMI es un cliente que solicita tags
al servidor PLC. Si añades un equipo Modbus, ese equipo publica su propio mapa
de coils y registros y la HMI/SCADA lo consulta como cliente.

| Concepto | En este ejemplo | Qué debes configurar |
| --- | --- | --- |
| Proceso | Agua entre bomba, depósito y válvula | Conexiones físicas y parámetros de caudal/capacidad |
| Medida/orden de campo | Sensor `value`; válvula y bomba `command` | Cada objeto en su propio inspector |
| E/S del PLC | Entrada `LEVEL_PCT` y salidas `PUMP_CMD`, `VALVE_CMD` | Tipo, área/dirección y permiso de cada canal |
| Lógica | Regla ON/OFF que evalúa el nivel | Umbral, histéresis y canales de entrada/salida |
| Protocolo | HMI consulta tags del servidor S7 por TCP/102 | Destino PLC, dirección S7 y permisos del punto |
| Red | HMI y PLC se alcanzan por una zona Docker | Zona/ruta; esto no crea tags ni cableado de señales |

Las conexiones de **proceso**, **señal**, **protocolo** y **red** son distintas.
La guía [mapa de proceso](PROCESS_MAP.md) explica cómo reconocer cada línea.

## Resultado esperado

El mapa tendrá una bomba, un depósito, una válvula y un sensor de nivel como
objetos independientes, un PLC S7, un equipo Modbus opcional y una HMI. Las
líneas de proceso mostrarán por dónde fluye el agua; las líneas de señal
mostrarán cómo el controlador acciona el equipo y recibe mediciones. El
protocolo S7/Modbus permite comprobar los valores en vez de confiar solo en la
animación del mapa.

## 1. Crear escenario y objetos

1. Inicia sesión y elige **Nuevo escenario**.
2. Añade una bomba y un depósito desde el catálogo de campo.
3. Añade una válvula on/off y un sensor analógico. Nómbralos por su función,
   por ejemplo `Bomba alimentación`, `Válvula V-101` y `Nivel LT-101`.
4. Añade un PLC S7. Para supervisión, añade una HMI. Modbus es opcional para
   mostrar un controlador de dosificación independiente.
5. Coloca los objetos y usa **Centrar** para encuadrar el mapa. Arrastrar el
   fondo mueve la vista; **Mover** permite recolocar objetos.

Los sensores/actuadores de campo no son tarjetas internas del PLC. Se
configuran desde el inspector del propio objeto; el PLC solo posee canales de
entrada/salida.

## 2. Conectar el circuito físico

En el panel de conexiones del objeto, crea un camino sencillo:

```text
Bomba.process_out → Depósito.inlet
Depósito.outlet → Válvula.process_in
Válvula.process_out → (retorno/consumidor, si la topología de la demo lo usa)
Depósito.level → Sensor.value (sensor en modo process)
```

Usa los puertos que muestra el inspector para el tipo de elemento elegido. La
línea azul discontinua es proceso. El sensor `process` necesita una conexión a
una magnitud compatible; sin ella no hay una lectura de nivel calculada.

Configura la capacidad y nivel inicial del depósito, el caudal nominal de bomba
y el caudal máximo de válvula. El motor calcula el balance en L/min y el nivel
en el tiempo transcurrido. Es un modelo concentrado, no un solver hidráulico.

## 3. Añadir canales al PLC

En el inspector del PLC crea los puntos que el cableado necesita. Por ejemplo:

| Punto | Tipo y permiso | Uso |
| --- | --- | --- |
| `START` | BOOL, entrada R/O | Orden de marcha externa |
| `LEVEL_PCT` | REAL, entrada R/O | Medida del sensor |
| `PUMP_CMD` | BOOL, salida R/W | Mando de bomba |
| `VALVE_CMD` | BOOL, salida R/W | Mando de válvula |

El mapa asigna direcciones S7 a los canales. Elige direcciones libres o deja
que Anvil las resuelva automáticamente. Evita asignar el mismo punto a dos
salidas físicas.

## 4. Cablear las señales

Desde el inspector de campo conecta `PUMP_CMD` a `Bomba.command` y
`VALVE_CMD` a `Válvula.command`. Conecta el puerto `Sensor.value` a
`LEVEL_PCT` del PLC. La dirección de las flechas importa: orden desde el PLC al
actuador y medición desde el sensor al PLC. La línea verde indica señal.

Una salida de campo conectada no debe escribirse como si fuera una orden
manual independiente desde el panel de telemetría. El PLC es quien la controla
cuando el cableado asigna ese punto.

## 5. Configurar una lógica mínima

Añade un bloque ON/OFF que compare `LEVEL_PCT` con un umbral y una histéresis.
Conecta su salida BOOL a la orden de bomba o válvula usando los puertos del
inspector. Un bloque de este tipo es una función pequeña por scan; no es un
programa Ladder/ST general.

Si quieres hacer la primera comprobación sin lógica automática, deja las salidas
R/W sin un bloque de control y escribe `PUMP_CMD` desde la operación autorizada.
Así aislas fallos de proceso, cableado y control.

## 6. Guardar y desplegar

Guarda el borrador y revisa el diagnóstico de topología. Las advertencias de
proceso incompleto ayudan a detectar puertos sin conexión; un diagnóstico
válido no certifica que la planta esté bien diseñada. Despliega y espera a que
los nodos indiquen estado sano. Revisa logs si el estado no avanza.

## 7. Verificar de extremo a extremo

1. Confirma que el mapa muestra lectura de campo fresca y estado del actuador.
2. Lee `LEVEL_PCT` desde la consola de PLC o desde la HMI configurada.
3. Escribe `PUMP_CMD` solo si el punto está permitido como salida.
4. Comprueba que la bomba arranca, el caudal cambia y aumenta el nivel.
5. Confirma que el sensor vuelve a informar el nivel por su canal de entrada.
6. Inspecciona los eventos de auditoría para verificar las operaciones.

### Qué capas acabas de demostrar

Este recorrido usa varios mecanismos distintos. La tubería y el depósito forman
el **proceso** modelado; el sensor obtiene una medida de ese modelo; el enlace
verde es una **señal** interna entre el puerto del sensor y el canal de entrada;
el PLC ejecuta el bloque por su ciclo nominal; el enlace a la válvula lleva una
**orden** al runtime de campo; y la HMI consulta la memoria del PLC mediante el
protocolo S7. La red hace posible la petición entre cliente y servidor, pero no
crea la señal ni el tag.

Así puedes localizar fallos por tramo: si el nivel del depósito no cambia,
revisa proceso y actuadores; si el sensor no refleja el nivel, revisa su fuente;
si el PLC no refleja el sensor, revisa la señal y el canal; si la salida no
cambia, revisa el bloque o la escritura autorizada; si el mapa cambia y el
actuador no, revisa la señal de salida y el runtime de campo; si el actuador
cambia pero la HMI no, revisa destino, dirección, ruta y calidad de lectura.

En una planta real, los módulos de E/S harían la conversión eléctrica y el
firmware ejecutaría el programa del fabricante. En esta demo, esas partes se
representan por canales y bloques de software documentados. El servidor S7
permite a clientes compatibles leer/escribir las áreas probadas; no convierte
el escenario en una CPU Siemens ni garantiza los tiempos de una instalación.
Consulta [PLC S7](plc-s7.md) para la correspondencia detallada.

Si la escritura funciona pero el nivel no cambia, comprueba el camino físico,
puertos, caudales y conexión del sensor. Si el actuador cambia desde el mapa
pero el PLC no lo refleja, revisa conexión de señal, dirección, permisos y
calidad del valor.

## 8. Limpiar el laboratorio

Detén el escenario desde Anvil. No elimines contenedores manualmente mientras
el orquestador sigue considerándolos parte del escenario. Para exportar la
topología antes de eliminarla, usa la acción de exportación o la API. Consulta
[mapa de proceso](PROCESS_MAP.md), [elementos de campo](FIELD_OBJECTS.md) y
[lógica PLC](PLC_LOGIC.md) cuando necesites más detalle.
