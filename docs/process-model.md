# Modelo de proceso físico

## Qué significa «proceso» en Anvil

El **proceso** es la parte que representa cómo cambian las magnitudes de una
planta: por ejemplo, el caudal que entra en un depósito, el nivel que sube y
la medida que entrega un sensor. Es diferente de la **lógica de control** del
PLC y de la **comunicación** S7/Modbus. El proceso consume órdenes/estados de
actuadores y produce medidas; las conexiones de señal llevan esos valores al
controlador y las peticiones del protocolo permiten que clientes los lean.

En el modelo actual, una **conexión física** une puertos de proceso compatibles;
una **conexión de señal** une el puerto de un sensor/actuador con un punto de
E/S de un PLC/Modbus. Para que el ciclo esté cerrado, se pueden necesitar ambas
relaciones además de la lógica del controlador. El objeto bomba no arranca solo
por estar conectado a un depósito: necesita que su puerto `command` reciba una
orden. Tampoco una tubería crea una señal de entrada al PLC.

Las unidades ayudan a interpretar la magnitud: `L/min` es caudal volumétrico,
`L` volumen, `%` fracción de llenado y `kPa` presión. Los modelos reducidos
pueden usar supuestos y límites distintos a la ingeniería de una planta. Las
propiedades exactas de cada objeto están en [Elementos de campo](FIELD_OBJECTS.md).

El runtime de campo simula estados y señales de elementos independientes. Su
objetivo es permitir pruebas de integración y ejercicios coherentes: una
entrada puede accionar la lógica del PLC, cambiar el actuador y afectar una
medida física que vuelve al PLC.

### Ejemplo: de la magnitud física a una lectura S7

Si una bomba llena un tanque, el runtime calcula el caudal de entrada y
actualiza el volumen/nivel según el balance y el tiempo. Un sensor configurado
en modo `process` obtiene su lectura del nivel y la entrega por su puerto
`value`. La señal lleva ese valor a una entrada del PLC. El PLC puede evaluarlo
con un bloque ON/OFF y mandar una salida; otra señal lleva la salida a
`pump.command` o `valve.command`. El actuador cambia el caudal modelado, que
vuelve a modificar el nivel. Finalmente, una HMI o cliente Snap7 consulta la
dirección de entrada del PLC.

Este bucle tiene proceso, instrumentación, control, señal y protocolo. El
protocolo no calcula el tanque; el sensor no ejecuta la lógica; y la conexión de
red entre HMI y PLC no cablea el campo. Si la HMI presenta un valor equivocado,
compara sucesivamente el nivel del proceso, la lectura de sensor, el canal PLC
y la respuesta S7. El [tutorial](tutorial.md) muestra cómo construirlo en el
editor.

## Estado y puertos

Cada objeto tiene un ID, un tipo, un nombre, propiedades y posición. Los puertos
definen dirección, tipo y unidad. El tipo `BOOL` no se conecta a un `REAL` sin
conversión; una lectura en porcentaje no debe conectarse como presión. Los
puertos compatibles se enumeran en [FIELD_OBJECTS.md](FIELD_OBJECTS.md).

El grafo usa colecciones diferentes para conexión de señal y conexión de
proceso. La API valida referencias, puertos, unidades, repetición de IDs y
entradas con múltiples fuentes. Las conexiones físicas no crean direcciones de
protocolo ni privilegios de escritura.

## Cálculo de nivel y caudal

El volumen del depósito cambia con el balance entre entrada, salida, demanda y
fuga. El caudal se expresa en L/min; el nivel se limita entre cero y la
capacidad. Una bomba activa entrega el caudal disponible dentro de los límites
del modelo. La válvula on/off limita el flujo a su caudal nominal y lo bloquea
cuando está cerrada. La capacidad actual de una proporcional es su caudal
nominal por el porcentaje de apertura; ese límite se aplica también a la fuente
antes de reportar el caudal, para conservar el balance en el camino. Por
ejemplo, una válvula nominal de 80 L/min al 25 % deja pasar hasta 20 L/min. El
retardo/fallo determina cuándo se aplica la posición modelada. Esto no calcula
la curva de caudal frente a presión (Cv) de una válvula real.

En cada paso de simulación de duración `dt` segundos, el volumen nuevo es el
volumen anterior más `(caudal_entrada - caudal_salida - demanda - fuga) × dt / 60`,
limitado entre cero y la capacidad. La división por 60 convierte L/min en
litros por segundo de simulación. Demanda y fuga se cuentan solo mientras el
depósito tiene líquido. Esto es un balance de volumen concentrado, no una
simulación de tuberías con propagación y retardo.

El modelo de presión es concentrado: se deriva la cabeza disponible de bomba o
variador y las pérdidas de tuberías configuradas. Una bifurcación explícita
reparte según porcentaje y un colector suma dos entradas. El camino acíclico se
ordena por dependencia; los ciclos no convergen mediante un solver nodal.

## Sensores y calidad

Un sensor analógico independiente tiene rango `min_value`–`max_value`, unidad
informativa y modo de simulación. En `constant`, entrega el punto medio del
rango (por ejemplo, 50 para 0–100); no se configura un valor inicial arbitrario.
En `sine_wave`, oscila entre los extremos con el periodo configurado y parte del
punto medio. Ambos modos admiten ruido uniforme acotado con amplitud máxima
`noise × (max_value - min_value)`; el resultado se limita al rango. Una semilla
hace reproducible la secuencia para el mismo objeto, configuración y orden de
muestras. Sin semilla, el generador se inicializa aleatoriamente.

En modo `process`, el sensor necesita una conexión a una magnitud compatible.
Si la fuente es un nivel porcentual 0–100, ese porcentaje se mapea linealmente
al rango del sensor: un depósito al 50 % produce el punto medio. Si la fuente
es presión, se toma el valor en kPa y se limita al rango del sensor. La etiqueta
`unit` no convierte unidades automáticamente; debe ser coherente con la fuente.
El modo `process` ignora onda, ruido y semilla.

`waiting` indica que no llegó una primera muestra; `good`, que el intercambio
está actualizado; `stale`, que se conserva el último valor pero no se renovó
dentro del timeout; y `fault`, que el objeto o conexión está en fallo. El
timeout actual del intercambio de campo es dos segundos. Un valor aún visible
con calidad `stale` o `fault` no es una medición actual confirmada. Al perderse
el transporte, el último valor se conserva temporalmente en vez de sustituirse
por cero.

El control de fallo del sensor congela su última lectura. `override` sustituye
esa lectura hasta que se envía `null` para liberarlo. En actuadores, `fault`
mantiene la posición/estado modelado; no es una orden de apertura o cierre. La
orden normal llega por `command`. Conviene distinguir la orden, el estado del
modelo y el dato que lee el controlador.

El runtime de campo intercambia señales con PLC/Modbus por un contrato HTTP
interno, separado de S7/Modbus. Esta conexión de software no reproduce un bus
eléctrico; S7/Modbus se usa cuando un cliente solicita datos al servidor del
dispositivo.

## Cómo interpretar una demo

Usa las líneas del mapa para explicar tres relaciones: recorrido del fluido,
cableado de medida/mando y camino de comunicación del protocolo. Para probar
causalidad:

1. Lee la variable inicial desde S7/Modbus.
2. Cambia una orden permitida del controlador.
3. Comprueba el estado físico y el caudal.
4. Comprueba el cambio de sensor.
5. Lee de nuevo por el protocolo.

Si cualquiera de esos pasos no concuerda, el panel de diagnóstico indica si el
problema está en la red, señal, runtime de campo o modelo. Las limitaciones de
ingeniería hidráulica están documentadas en [mapa de proceso](PROCESS_MAP.md)
y en [fidelidad](DEVICE_FIDELITY.md).
