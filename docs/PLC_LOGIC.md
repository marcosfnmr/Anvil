# Bloques de lógica PLC

## Qué significa «lógica PLC» en Anvil

En un PLC, la lógica transforma entradas en decisiones y salidas. Un **bloque**
es una función configurada con entradas, parámetros y una salida; el **scan**
es el ciclo que vuelve a evaluar esos bloques. Anvil ejecuta el conjunto
descrito en esta página en software dentro del runtime S7: no compila Ladder,
FBD ni Structured Text, ni ejecuta instrucciones arbitrarias de una CPU
Siemens. Consulta [PLC S7](plc-s7.md) para la diferencia entre control local,
memoria y servidor de protocolo, y el [glosario](glossary.md) para los tipos.

El runtime S7 admite bloques ON/OFF con histéresis (REAL→BOOL), bloques
combinacionales AND/OR/XOR (BOOL, BOOL→BOOL) con contactos normalmente abiertos
o cerrados, temporizadores TON/TOF, memorias SR, CTU y secuenciadores one-hot,
evaluados una vez por scan. Son bloques pequeños con semántica definida; no es un
editor Ladder/ST ni un runtime IEC 61131-3.

## Qué se guarda y qué pasa en cada scan

El escenario persiste la **configuración** del PLC: puntos, direcciones, bloques,
entradas/salidas asignadas y parámetros. Al desplegar o recrear el contenedor,
Anvil vuelve a inicializar las áreas de memoria y componentes según esa
configuración, y después el runtime evalúa los bloques en sus scans. La memoria
S7 completa (todos los bytes de DB/M/I/Q) no se guarda automáticamente al
detener el PLC.

| Elemento | Al iniciar/recrear | En cada scan | Tras detener/recrear |
| --- | --- | --- | --- |
| Configuración de canal/bloque | Se carga desde el escenario | No cambia por sí sola | Permanece guardada en el escenario |
| Valor de componente | Se escribe su valor inicial o el que produce su simulación | Puede actualizarse por el componente, conexión de campo o cliente | Vuelve a inicializarse; no equivale a memoria retentiva S7 |
| Salida asignada a bloque | Se inicializa según el componente/buffer | El bloque calcula y escribe su valor | Vuelve al estado inicial, salvo estados de bloque con retención activada |
| Estado interno de bloque | Empieza en su estado inicial configurado | Se actualiza según entradas y temporización | Volátil por defecto; puede conservarse la parte soportada si está activada la retención |

Una salida debe pertenecer a un único bloque para que dos reglas no compitan por
el mismo bit. Si una HMI, consola o cliente S7 escribe directamente en una salida
controlada por un bloque, la escritura puede observarse brevemente, pero el
siguiente scan vuelve a aplicar la decisión del bloque. Para probar un mando
manual, usa una salida que no tenga bloque dueño o desactiva/edita la lógica y
vuelve a desplegar.

La opción **Conservar estado de bloques al recrear el PLC** no guarda toda la
memoria del controlador. Conserva únicamente estados documentados (por ejemplo,
histéresis, SR, contador, paso de secuencia y tiempo acumulado TON/TOF) en un
volumen Docker del escenario/nodo. El tiempo de temporizador queda pausado
mientras el contenedor está detenido. Si cambia la configuración de los
bloques, el estado guardado incompatible se descarta. Borrar el escenario
elimina ese volumen; detener/recrear el nodo lo conserva.

También admite memorias SR configurables con entradas SET y RESET BOOL. El bloque
conserva su salida entre scans y aplica prioridad de RESET si ambas entradas
están activas en el mismo scan. Por defecto su memoria es volátil. El PLC puede
activar **Conservar estado de bloques al recrear el PLC** para guardar el estado
en un volumen Docker gestionado por Anvil.

El bloque CTU cuenta flancos ascendentes de una entrada BOOL hasta un preset
(1–65535). Una entrada RESET distinta borra el conteo y prevalece si coincide
con un flanco en el mismo scan. La salida DONE se activa al alcanzar el preset;
el conteo se satura ahí y permanece hasta RESET. El valor empieza en cero tras
iniciar/recrear el contenedor si la retención está desactivada; con retención se
guarda también el acumulado interno. Solo se publica la salida BOOL DONE, no el
acumulado interno.

El secuenciador BOOL avanza una salida activa por flanco ascendente. Admite de 2
a 16 salidas únicas; mantiene el último paso sin volver al inicio. RESET es una
entrada separada y vuelve al paso 1 con prioridad si coincide con un flanco. El
paso activo empieza en el paso 1 al iniciar/recrear el PLC si la retención está
desactivada; con retención, se restaura el paso guardado.

La opción de retención también conserva la memoria ON/OFF, el estado de las
memorias SR, flancos previos de CTU/secuenciador y tiempo acumulado de TON. El
tiempo TON queda pausado mientras el contenedor está apagado. Cada PLC usa un
volumen Docker nombrado y aislado por escenario/nodo; detener o recrear conserva
el volumen, borrar el escenario lo elimina. La restauración se descarta si cambia
la configuración de bloques. Los guardados son atómicos y se hacen cuando cambia
el estado de bloque, con checkpoint periódico del TON. Esta retención es un
comportamiento del simulador, no memoria real certificada ni un sustituto de
pruebas ante pérdida de alimentación.

## Configuración

En el inspector de un PLC, añade una entrada analógica/REAL y una salida
BOOL escribible (válvula, motor, orden de bomba o canal BOOL de salida). En
**Programa PLC · bloques ON/OFF**, selecciona ambas señales, la acción,
consigna e histéresis. También puede declararse en el escenario:

```json
{
  "components": [
    {"type": "analog_sensor", "name": "Nivel", "min_value": 0, "max_value": 100},
    {"type": "pump", "name": "Bomba", "flow_min": 0, "flow_max": 100}
  ],
  "control_rules": [
    {"name": "Mantener nivel", "input_component": "Nivel",
     "output_component": "Bomba", "mode": "below",
     "setpoint": 30, "hysteresis": 5}
  ]
}
```

Con `mode: below`, activa la salida cuando la entrada baja a la consigna o
menos, y la desactiva al subir hasta `consigna + histéresis`. Con `mode: above`,
la activa al llegar a la consigna y la desactiva al bajar hasta
`consigna - histéresis`. Entre ambos límites conserva el estado anterior.
Una salida solo puede pertenecer a un bloque. Las alarmas, entradas digitales y
otras señales de solo lectura no pueden ser salidas de control.

## Lógica combinacional BOOL

En **Lógica BOOL · contactos y AND / OR / XOR**, selecciona dos entradas digitales distintas,
el operador y una salida BOOL. Las entradas admiten señales digitales, entradas
remotas BOOL y canales DB BOOL de entrada; las salidas admiten válvulas, motores,
órdenes de bomba y canales DB BOOL de salida. Una salida no se puede compartir
entre una regla ON/OFF, otro bloque BOOL, memoria SR o TON. El bloque se vuelve a
evaluar en cada scan y no retiene estado.

## Memoria SR

En **Memorias SR (reset prioritario)** selecciona dos entradas BOOL distintas y
una salida BOOL. SET activa la salida, que permanece activa tras liberar SET;
RESET la desactiva. Si ambas señales están activas durante un scan, RESET
prevalece. La memoria solo existe en el runtime: al reiniciar el contenedor la
salida parte del estado inicial configurado y vuelve a evaluarse.

```json
{
  "components": [
    {"type": "digital_input", "name": "Marcha"},
    {"type": "digital_input", "name": "Parada"},
    {"type": "motor", "name": "Bomba"}
  ],
  "latches": [
    {"name": "Memoria de marcha", "set_component": "Marcha",
     "reset_component": "Parada", "output_component": "Bomba"}
  ]
}
```

```json
{
  "components": [
    {"type": "digital_input", "name": "Permiso operador"},
    {"type": "digital_input", "name": "Protección activa"},
    {"type": "valve", "name": "Válvula"}
  ],
  "boolean_rules": [
    {"name": "Permiso de apertura", "input_a_component": "Permiso operador",
     "input_b_component": "Protección activa", "operator": "and",
     "output_component": "Válvula"}
  ]
}
```

`and` requiere ambas entradas activas, `or` requiere al menos una y `xor` requiere
exactamente una. Marca **Contacto normalmente cerrado** en una entrada para
invertir su estado lógico en la rung. Las entradas no pueden ser salidas
controladas, lo que evita ciclos combinacionales en este editor.

## Temporizadores TON y TOF

En **Temporizadores TON / TOF**, selecciona una entrada BOOL (entrada digital,
entrada remota o canal DB BOOL de entrada), una salida BOOL, el modo y un retardo
entre 10 ms y una hora. TON activa la salida cuando la entrada permanece activa
durante todo el retardo; al bajar la entrada, la salida se apaga y el conteo vuelve
a cero. TOF activa la salida inmediatamente con la entrada y la mantiene durante
el retardo después de que la entrada baje; si vuelve a subir durante ese tiempo,
se cancela el apagado pendiente. Ambos modos usan el reloj monotónico del runtime
y se evalúan por scan, así que la precisión está limitada por el ciclo PLC y la
planificación del contenedor. El estado no es retentivo por defecto. Al activar la
retención del PLC, el tiempo acumulado del modo correspondiente se restaura y
queda pausado durante la parada del contenedor.

También puede declararse en el escenario:

```json
{
  "components": [
    {"type": "digital_input", "name": "Permiso"},
    {"type": "valve", "name": "Válvula"}
  ],
  "timers": [
    {"name": "Retardo de apertura", "input_component": "Permiso",
     "output_component": "Válvula", "preset_ms": 750}
  ]
}
```

El modo predeterminado es `ton`; para el retardo a la desconexión, declara
`"mode": "tof"`. Los escenarios antiguos que no incluyen `mode` conservan su
comportamiento TON.

## Contador CTU

En **Contadores CTU**, selecciona entradas BOOL distintas para conteo y RESET,
una salida BOOL disponible y un preset entero de 1 a 65535. Cada transición de
0 a 1 suma una unidad; mantener la señal a 1 no vuelve a contar. Libera la
entrada entre pulsos. DONE permanece activo al alcanzar el preset hasta activar
RESET.

```json
{
  "components": [
    {"type": "digital_input", "name": "Pulso"},
    {"type": "digital_input", "name": "Reset"},
    {"type": "valve", "name": "Lote completo"}
  ],
  "counters": [{"name": "Contar piezas", "count_component": "Pulso",
                "reset_component": "Reset", "output_component": "Lote completo",
  "preset": 10}]
}
```

## Secuenciador BOOL

En **Secuenciadores BOOL**, elige una entrada de avance, otra de RESET y las
salidas BOOL en orden. Cada flanco de avance cambia al paso siguiente; el paso
actual se desactiva y la siguiente salida se activa. La secuencia se queda en el
último paso hasta RESET, que vuelve al primero. No hay transición automática,
temporización por paso ni bucle configurable.

```json
{
  "components": [
    {"type": "digital_input", "name": "Avanzar"},
    {"type": "digital_input", "name": "Reset"},
    {"type": "valve", "name": "Entrada"},
    {"type": "valve", "name": "Transferencia"},
    {"type": "valve", "name": "Descarga"}
  ],
  "sequences": [{"name": "Transferir lote", "advance_component": "Avanzar",
                  "reset_component": "Reset",
                  "steps": ["Entrada", "Transferencia", "Descarga"]}]
}
```

La imagen `anvil/plc-s7` incluye el evaluador. El smoke integral usa Snap7 contra
un PLC Docker para comprobar una regla ON/OFF, las combinaciones de una compuerta
AND con contacto normalmente cerrado, la prioridad y retención de una memoria SR,
y un TON que respeta su preset y se reinicia cuando cae la entrada, además de un
CTU por flanco, saturación, RESET y salida DONE y un secuenciador one-hot.

```bash
docker build -t anvil/plc-s7:dev ./plc
ANVIL_PLC_IMAGE=anvil/plc-s7:dev \
PYTHONPATH=backend .venv/bin/python integration/control_logic_smoke.py
ANVIL_PLC_IMAGE=anvil/plc-s7:dev PYTHONPATH=backend .venv/bin/python integration/retained_state_smoke.py
```

Si el daemon no puede consultar Docker Hub pero ya existe una imagen S7 local,
`Dockerfile` permite usarla como base mientras se reconstruye el código actual:

```bash
docker build --build-arg PLC_BASE_IMAGE=anvil/plc-s7:srs-v2 \
  -t anvil/plc-s7:dev ./plc
```

Los bloques modelan solo comparadores ON/OFF, compuertas BOOL, TON, SR, CTU y un
secuenciador one-hot; no ejecutan código arbitrario, expresiones, aritmética de
usuario ni funciones de seguridad.
No sustituyen un PLC real ni certifican SIL/IEC 61508. Las escrituras externas
al mismo output son reescritas por el bloque en el siguiente scan. No hay
una garantía de tiempo real ni funciones de seguridad certificadas.

Las entradas remotas S7 ofrecen la política `stale_behavior`. Con `hold` (por
defecto) los bloques continúan usando el último valor recibido. Con `force_off`,
los bloques que consumen una entrada remota en estado `waiting` o `stale` fuerzan
su salida a OFF mientras dure esa calidad. Se reinician los estados SR, TON, CTU
y secuenciador afectados; tras la recuperación se vuelven a evaluar desde su
estado inicial. La telemetría sigue conservando y mostrando el último valor con
su calidad obsoleta. Esta es una política funcional del simulador y no una función
de seguridad industrial.
