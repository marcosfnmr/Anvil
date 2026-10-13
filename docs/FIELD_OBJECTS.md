# Elementos de campo independientes

## Estado del trabajo

Implementados modelo, validación, persistencia, importación/exportación,
clonación, catálogo de elementos independientes, editor de conexiones y motor
de campo en un contenedor por escenario. Los escenarios antiguos mantienen
su configuración y ejecución actual; no se convierten automáticamente.

Desde «Nuevo escenario», añade primero sensores, válvulas, motores o bombas
desde el catálogo. Se colocan como nodos independientes, sin PLC ni IP. Añade
después un PLC S7 o equipo Modbus. En «Configurar y conectar», elige el
controlador para cada puerto y pulsa «Conectar y crear canal». Guarda y
despliega para observar los valores y usar las órdenes del controlador.
El botón «Cargar fábrica con elementos independientes» prepara un ejemplo
con válvula, sensor, bomba, PLC, Modbus y HMI, sin desplegarlo automáticamente.

## Modelo

### Qué es un objeto de campo

En una instalación, el **nivel de campo** agrupa instrumentos y elementos que
observan o modifican el proceso: sensores, válvulas, motores, bombas y equipos
de proceso. Un sensor transforma una magnitud —por ejemplo, nivel— en una
señal; un actuador cambia el proceso al recibir una orden. El mando y la
respuesta son conceptos distintos: `command` es lo solicitado, `state` u
`opening` es el estado modelado que realmente alcanzó el actuador. Un atasco o
retardo puede hacer que ambos no coincidan de inmediato.

En Anvil esos elementos existen **independientemente** de los PLC. No tienen
IP, servidor S7/Modbus ni memoria de controlador. El runtime de campo simula
su comportamiento y procesa dos clases de relación:

- Las **conexiones de proceso** representan de dónde viene el caudal/medición y
  a dónde va; alimentan depósito, sensor y otros objetos compatibles.
- Las **conexiones de señal** conectan el puerto del objeto con un punto PLC o
  Modbus; entregan la medida al controlador o llevan el mando al actuador.

### Sensores, actuadores y puntos de E/S

Un **sensor** observa una magnitud, como nivel, presión o estado de marcha, y
produce una medida. Una **entrada digital** representa un estado binario, como
un contacto abierto/cerrado. Un **actuador** recibe un mando y cambia su estado:
una válvula modifica la apertura/caudal, mientras una bomba o motor representa
su marcha y caudal configurado. En un sistema real también habría transmisores,
tarjetas de E/S y señales eléctricas; esos componentes no se reproducen en este
modelo.

Un punto de E/S del PLC o servidor Modbus es el extremo del controlador; un
puerto de campo es el extremo del objeto. La conexión entre ellos se valida por
tipo y dirección. Por ejemplo, `sensor.value` (REAL) alimenta una entrada REAL;
una salida BOOL alimenta `valve.command`. El valor de mando no es la realimentación
del actuador: consulta `state`, `opening` o `fault` para observar el resultado
modelado. Si hace falta leer esa realimentación desde el PLC, crea otro puerto de
salida del objeto y una conexión de señal compatible.

La independencia del catálogo permite colocar primero los equipos de campo y
construir el circuito visual. La lógica solo se completa al añadir el PLC o
servidor Modbus, definir sus puntos y conectar las señales. La red solo hace
falta para clientes que consulten el protocolo; el runtime de campo intercambia
las señales internas sin que el sensor tenga dirección IP propia.

### Qué representa el elemento y qué representa su punto

Un instrumento físico suele incluir una parte sensible al proceso, electrónica
de acondicionamiento/transmisión y una señal hacia una entrada de control. Un
actuador recibe una consigna eléctrica/neumática y su mecanismo modifica el
proceso; su realimentación de posición puede ser otra señal independiente. Anvil
no modela esos circuitos eléctricos: representa el comportamiento del elemento
mediante puertos tipados. `value` es una medida calculada, `command` es una
orden recibida y `state`, `opening`, `flow` o `fault` describen estados
modelados. No deben confundirse una orden aceptada y una posición alcanzada.

El runtime de campo es quien calcula y conserva el estado de esos objetos. Los
PLC y servidores Modbus mantienen sus propios puntos de protocolo. Una
conexión de señal hace de puente entre ambos modelos; una conexión de proceso
define el efecto sobre caudal/nivel. Por tanto, tener un sensor en el mapa no
crea una entrada PLC, y tener una salida PLC no crea una válvula conectada.

Crear una válvula desde el catálogo solo crea/configura el objeto. Para
accionarla hay que añadir un punto de salida, conectarlo a `command`, desplegar
el escenario y cambiar el punto mediante la lógica PLC o una operación de
cliente autorizada. Para que su efecto sea visible en caudal/nivel, también se
necesita la conexión física de proceso correspondiente.

El escenario añade dos colecciones opcionales, vacías por defecto:

- `field_objects`: elementos con identidad propia, sin IP ni DB.
- `signal_connections`: conexiones dirigidas entre un puerto de campo y una
  dirección del mapa de E/S de un dispositivo.

Se pueden guardar borradores que contengan elementos de campo sin PLCs.
Los tipos disponibles son `analog_sensor`, `digital_input`, `alarm`, `valve`,
`proportional_valve`, `variable_speed_drive`, `motor`, `pump`, `pipe`,
`flow_splitter`, `flow_mixer` y `tank`.
`position: {x, y}` pertenece al objeto y se conserva sin
depender del identificador interno de Drawflow.

Los sensores analógicos en modo `constant` o `sine_wave` pueden añadir ruido
uniforme acotado mediante `simulation.noise` (fracción 0–1 del rango de medida).
`simulation.seed` es una semilla entera opcional entre 0 y 4294967295. Si se
configura, el mismo sensor (mismo `object_id`) genera la misma secuencia al
recrear el runtime con la misma configuración y el mismo orden de muestras;
sensores con IDs distintos usan secuencias independientes aunque compartan la
semilla. Si se omite, la secuencia se inicializa aleatoriamente. El modo
`process` ignora la onda, el ruido y la semilla porque su valor procede del
objeto físico conectado.

La reproducibilidad se verifica en el runtime de campo dentro de Docker, no
solo en el generador Python: `integration/field_noise_seed_smoke.py` crea dos
contenedores aislados sin publicar puertos, consulta seis muestras por el
endpoint interno `/exchange` y exige que ambas secuencias coincidan. CI ejecuta
este smoke después de construir `anvil/field:ci`:

```bash
docker build -t anvil/field:ci -f plc/Dockerfile.field ./plc
.venv/bin/python integration/field_noise_seed_smoke.py --image anvil/field:ci
```

| Elemento | Puertos de salida | Puertos de entrada |
| --- | --- | --- |
| Sensor analógico | `value` REAL | — |
| Entrada digital / alarma | `state` BOOL | — |
| Válvula / motor | `state`, `fault` BOOL | `command` BOOL |
| Válvula proporcional | `opening` REAL (%), `fault` BOOL | `command` REAL (0–100 %) |
| Variador de frecuencia | `speed` REAL (%), `flow` REAL (L/min), `pressure` REAL (kPa), `fault` BOOL | `command` REAL (0–100 %) |
| Bomba | `state`, `fault` BOOL; `flow` REAL y `pressure` REAL (kPa) | `command` BOOL |
| Tubería | `process_out` REAL (L/min), `pressure` REAL (kPa) | `process_in` REAL (L/min) |
| Depósito | `level` REAL (%), `pressure` REAL (kPa) | `inlet` y `outlet` de proceso (L/min) |

La dirección se expresa desde el elemento de campo. Una válvula recibe una
orden y devuelve un estado; estos puertos son distintos. `response_seconds`
configura el retardo real de respuesta del simulador. Orden y estado son
distintos; un fallo/atasco mantiene el estado físico anterior aunque cambie
la orden. La bomba produce cero caudal cuando está parada y el punto medio
de su rango cuando funciona. Enlazada a un depósito, el caudal bombeado cambia su nivel.

### Abrir y cerrar una válvula

Para que una válvula independiente responda a una orden, configura y verifica
estas tres relaciones por separado:

1. **Proceso:** enlaza los puertos de caudal que correspondan, por ejemplo
   `bomba.process_out → válvula.process_in → depósito.inlet` (usa los puertos
   disponibles para los tipos de equipo de tu escenario). Sin este camino, la
   posición puede cambiar pero no habrá efecto en caudal o nivel.
2. **Punto de control:** crea un punto BOOL de salida en el PLC S7 o una coil
   en el dispositivo Modbus. Tiene que ser escribible desde el método que vas a
   usar y no debe estar alimentado como entrada de solo lectura.
3. **Señal:** conecta el punto de salida al puerto `valve.command`. La dirección
   es controlador → válvula. Una entrada del sensor va en el sentido contrario:
   `sensor.value → entrada PLC/Modbus`.
4. **Despliegue:** guarda y despliega el escenario. Comprueba que el dispositivo
   y el runtime de campo están sanos, y que la conexión de señal aparece con
   calidad `good`.
5. **Orden:** con el escenario desplegado, escribe `true` para abrir y `false`
   para cerrar una válvula BOOL. Puedes usar un bloque PLC o una operación
   autorizada de Anvil/HMI/cliente Modbus sobre el punto conectado. La orden
   manual puede volver al valor que impone la lógica en el siguiente scan.
6. **Respuesta:** observa `command` y `state` de la válvula y el caudal/nivel
   resultante. Si `response_seconds` es mayor que cero, el estado cambia tras
   ese retardo. Si `fault` está activo, el actuador conserva su estado anterior.

Una escritura que modifica el coil/canal solo demuestra que cambió el punto de
control. La válvula se abre cuando el runtime recibe esa señal y cambia su
estado; el efecto sobre el proceso requiere, además, una conexión física de
caudal. El endpoint de control de campo permite activar un fallo de prueba, pero
no sirve para mandar abrir/cerrar el actuador: las órdenes normales llegan por
`command`. Al liberar el fallo, revisa la orden actual y el retardo configurado.

En válvulas proporcionales `command` y `opening` son REAL en 0–100 %; no uses
un BOOL de apertura/cierre. El caudal máximo permitido por el modelo es
`flow_rate_lpm × opening / 100`. Para un variador, la consigna también es
0–100 %, pero representa velocidad y su caudal tiene su propio límite. Consulta
[modelo de proceso](process-model.md) para las ecuaciones y
[PLC S7](plc-s7.md) o [Modbus](MODBUS.md) para configurar el punto de salida.

Ejemplo de las nuevas colecciones para un escenario con un PLC que ya tenga
un punto BOOL escribible en `DB1.DBX0.0`:

```json
{
  "field_objects": [
    {
      "object_id": "valvula-entrada",
      "type": "valve",
      "name": "Válvula de entrada",
      "initial_state": false,
      "response_seconds": 2,
      "position": {"x": 100, "y": 300}
    }
  ],
  "signal_connections": [
    {
      "connection_id": "orden-apertura",
      "source": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBX0.0"},
      "target": {"kind": "field", "object_id": "valvula-entrada", "port": "command"}
    }
  ]
}
```

Se rechazan IDs duplicados, referencias inexistentes, puertos con dirección
incorrecta, tipos o unidades incompatibles y varias fuentes para una misma
entrada. Los puntos `remote_input` mantienen su origen S7 y no admiten una
segunda conexión de campo. Un punto de solo lectura no sirve de orden de
actuador. REAL → UINT16 requiere `conversion` con `input_min`, `input_max`,
`output_min` y `output_max`; se limita al rango y se redondea a entero. Al
conectar una medida a Modbus desde la interfaz se crea y muestra explícitamente
una escala desde el rango físico a 0…65535. El registro expone el valor bruto,
sin atribuirle las unidades físicas del sensor.

Los PLCs nuevos empiezan sin componentes físicos. Sus canales `io_channel`
admiten BOOL/REAL y dirección de entrada o salida, sujetos a las áreas y
conexiones compatibles. Las direcciones se calculan en el
backend. Las conexiones creadas en la interfaz guardan también `channel_id`,
que permite recalcular la dirección tras reordenar canales y rechazar el
guardado si se elimina un canal conectado. Cada canal BOOL nuevo reserva un
byte para no compartir un byte de memoria con órdenes escritas por clientes.

## Conexiones físicas de proceso

`process_connections` almacena relaciones de caudal/nivel separadas de
`signal_connections`. Una bomba puede alimentar la entrada de un depósito; la
salida de este puede pasar por una válvula on/off o proporcional; el nivel del depósito puede
alimentar un sensor en modo `process`. El motor integra el balance de caudal
(L/min) por el tiempo transcurrido, limita el volumen a la capacidad y actualiza
el sensor en el rango configurado. La bomba entrega el punto medio de su rango
cuando está activa. La válvula on/off admite como máximo `flow_rate_lpm` y lo
bloquea cuando está cerrada. La válvula proporcional tiene una capacidad actual
de `flow_rate_lpm × apertura_pct / 100`; el motor aplica ese límite también al
caudal de la fuente, de modo que el flujo medido en bomba, válvula y depósito
se conserva en el camino. Por ejemplo, una válvula de 80 L/min abierta al 25 %
deja pasar hasta 20 L/min. El retardo configura cuándo cambia la apertura
efectiva y un fallo mantiene la última posición. La tubería limita el caudal de paso a `max_flow_lpm`. `pressure_drop_kpa`
configura su pérdida nominal al caudal máximo; la pérdida real escala con el
cuadrado de `caudal / max_flow_lpm` y la presión de salida nunca baja de cero.
El `flow_splitter` reparte el caudal de entrada con `split_ratio_pct` hacia la
rama A y envía el resto a la rama B; el `flow_mixer` suma dos entradas
independientes en una salida. Ambos conservan balance de caudal y pasan la
presión de forma simplificada (el colector toma la menor presión de sus entradas
con flujo). No añaden resistencia propia ni resuelven la presión de la red.
La bomba expone `max_head_kpa` mientras funciona; el variador genera presión
proporcional al cuadrado de la velocidad respecto a su `max_head_kpa`. Ambos
usan 100 kPa por defecto y la pérdida de tubería es 0 kPa por defecto. En un
camino en serie, el motor suma `K = pressure_drop_kpa / max_flow_lpm²` de cada
tubería y limita el caudal potencial de origen a
`min(caudal_configurado, sqrt(presión_disponible / suma(K)))`. Los límites de
caudal de las tuberías y las válvulas se aplican además en sus respectivos
tramos; las ramas con válvulas cerradas o con poca apertura se dimensionan con
su capacidad actual antes de repartir el caudal de la bomba. Esto conserva el
caudal para el modelo concentrado, pero no calcula una curva de presión/Cv de
la válvula.
Si la cabeza disponible supera la pérdida nominal, se conserva el caudal de
origen configurado y queda presión residual a la salida. El variador recibe
un canal REAL S7 de 0–100 % o un holding register Modbus
escalado explícitamente; su caudal ideal es `max_flow_lpm × velocidad / 100`.
Retardo y fallo afectan la velocidad/caudal, y la salida `flow` puede alimentar
una tubería o un depósito. No modela curva par/velocidad, inercia del motor,
frecuencia eléctrica ni protecciones del variador físico. Las salidas de
actuadores analógicos pueden recibirse por holding register UINT16 mediante una
conversión explícita (el mapeo UI usa 0–65535 para el punto raw y escala al rango
físico del mando).
Los rangos de conversión y del canal se validan antes del despliegue. Los canales
REAL S7 empiezan en el mínimo configurado para que el valor inicial sea válido.
El depósito también acepta `leak_flow_lpm` (0 por defecto): esa fuga constante
se suma a la salida, reduce el volumen de forma determinista y deja de fluir
cuando el depósito llega a cero. El inspector muestra el caudal perdido.
`demand_flow_lpm` define la demanda inicial (0 por defecto). Durante el despliegue
el operador/instructor puede cambiar la demanda o la tasa de fuga desde el
inspector; ambas se suman al balance de salida y quedan registradas en auditoría
como controles de campo. Para cerrar una fuga dinámica, aplica 0 L/min. La tasa
dinámica aparece en el mapa junto a la fuga configurada, demanda efectiva y
caudal total de salida. Los ejercicios también pueden programar `leak_flow_lpm`
para inyectar y retirar fugas durante una cronología.
La presión manométrica del depósito se calcula como `9.80665 × fracción de
llenado × height_m`, en kPa (altura 2 m por defecto). Los puertos físicos
`pressure` de bomba, variador, tubería o depósito pueden conectarse a un sensor
analógico `process` con unidad kPa y, desde este, a una entrada REAL S7 o un
registro Modbus escalado. Es una aproximación concentrada: no usa diámetro,
longitud, rugosidad, elevación ni viscosidad. Tampoco modela golpe de ariete,
cavitación ni curvas reales de bomba.

La API rechaza puertos/direcciones incompatibles, unidades distintas,
identificadores duplicados, entradas físicas con más de una fuente y sensores
en modo `process` sin enlace a una medición física compatible. La
UI muestra estas conexiones con línea azul discontinua y valores de proceso. El
preflight advierte si un distribuidor no tiene entrada o alguna rama conectada,
o si un colector no tiene sus dos entradas y la salida conectadas.

El modelo actual cubre un balance concentrado de nivel, límites de caudal,
caminos en serie, bifurcaciones desde bomba/variador y bloques explícitos de
distribución y unión. Las ramas directas desde una fuente conservan el caudal
total y lo reparten según límites y pérdidas cuadráticas; las rutas paralelas
sin límites activos comparten la caída calculada. El distribuidor permite fijar
la fracción de caudal hacia rama A; el colector suma dos ramas. Estos bloques
conservan caudal, pero no forman un solver nodal general. Siguen fuera la
geometría, resistencia de uniones, curvas reales de bomba, transitorios, ciclos
de retorno y múltiples fuentes convergentes sin un colector explícito.
El control proporcional limita el caudal a `flow_rate_lpm × apertura / 100`
y el runtime limita con ese valor el caudal de origen antes de informar el
balance; el comportamiento es lineal y no representa la curva característica
(Cv) o la dinámica de un actuador físico. La prueba integrada `integration/process_loop_smoke.py`
lee el sensor desde un runtime PLC S7 simulado en Docker mientras acciona bomba
y válvula.
`integration/process_pipe_smoke.py` comprueba en Docker las pérdidas en serie,
la división de ramas por presión/capacidad y el recorrido explícito distribuidor→
ramas→colector; `integration/process_manifold_ui.cjs` comprueba la creación y
configuración visual de estos bloques sin guardar el borrador.

## Persistencia y compatibilidad

Al inicializar una base antigua se añaden `field_objects_json` y
`signal_connections_json` con el valor `[]`, sin eliminar ni reemplazar filas.
La migración es idempotente.

Las rutas CRUD, importación, exportación y clonación conservan ambas
colecciones. En una actualización PUT, si un cliente antiguo omite estas
colecciones, se conservan las almacenadas y se vuelve a validar la topología
completa antes de guardar. Para eliminarlas se deben enviar arrays vacíos
explícitamente. El frontend las reconstruye como nodos del mapa, conserva sus
posiciones y dibuja las conexiones dirigidas. Los componentes antiguos siguen
apareciendo en su inspector para conservar los escenarios existentes; el
catálogo nuevo crea objetos independientes.

## Ejecución y API

El backend genera un contenedor interno `field-runtime` con una IP libre en
la red del escenario y sin puertos publicados en el host. Cada PLC/equipo
Modbus intercambia sus señales con él mediante un transporte HTTP interno
que representa el cableado simulado; no reproduce señales eléctricas ni un
bus industrial físico. La simulación no depende del navegador. S7 y Modbus
siguen sirviendo lecturas y escrituras reales a clientes.

La actualización de memoria S7 ocurre en el ciclo del PLC; la consulta HTTP
se hace en otro hilo para evitar bloquear ese ciclo. Modbus actualiza su
datastore mediante un ciclo propio, incluidos los input registers que no
admiten escritura mediante el protocolo. Ante pérdida de comunicación se
conserva el último valor y se marca la conexión obsoleta tras dos segundos.
La consola muestra calidad waiting/good/stale y bloquea la escritura en las
entradas alimentadas por elementos de campo. Clientes externos S7/Modbus
pueden tener permisos distintos a los de la consola de Anvil.

### Señales entre zonas Purdue

Una señal PLC↔campo que cruza zonas declara su ruta en el inspector con
**Permitir señal al runtime de campo mediante gateway HTTP**. Activada, Anvil
crea un proxy de flujo único en la zona del PLC, dirigido únicamente al runtime
interno de campo por TCP/8765. Desactivada, conserva la conexión como bloqueada
y no crea el proxy. El mapa muestra ambos estados. El PLC no puede alcanzar
directamente la subred de campo y el gateway no publica puertos del host. Las
señales de campo conectadas de un escenario deben estar en una
misma zona, porque el producto mantiene un runtime de proceso por escenario.
No es routing L3 ni permite otros destinos o puertos.

`integration/field_route_smoke.py` verifica un PLC en L1, runtime y objetos de
campo en L0, bloqueo de la ruta directa, orden de válvula, retorno BOOL/REAL y
ausencia de puertos publicados. Se ejecuta contra una API temporal aislada; por
ejemplo:

```bash
PYTHONPATH=backend .venv/bin/python integration/field_route_smoke.py \
  --base-url http://127.0.0.1:8014
PYTHONPATH=backend .venv/bin/python integration/field_route_smoke.py \
  --base-url http://127.0.0.1:8014 --route-state blocked
```

- `GET /api/scenarios/{id}/field/values`: estado real del contenedor de campo.
- `POST /api/scenarios/{id}/field/{object_id}/control`: `{"fault": true/false}`
  para actuadores y sensores analógicos/digitales; en sensores activa un fallo
  de lectura congelada que conserva el último valor enviado al PLC. La calidad
  de la conexión aparece como `fault` en el mapa. `{"override": valor/null}`
  fuerza o libera una lectura de sensor/entrada. Rangos y tipos se validan también
  en el motor; al restaurar el sensor vuelve a seguir el proceso.

Estas acciones son condiciones de simulación. Las órdenes normales de los
actuadores se escriben en las salidas del controlador desde Telemetría o un
cliente S7/Modbus. La interfaz no muestra un valor inventado si falla la API.

## Migrar componentes PLC antiguos a una copia

`GET /api/scenarios/{id}/field-migration-preview`

Es una consulta sin modificaciones. Propone objetos y conexiones a partir de
los componentes locales del PLC S7, mantiene sus direcciones DB, recupera las
posiciones conocidas del mapa y genera IDs deterministas. Las entradas
remotas entre PLCs permanecen como comunicación entre dispositivos. Los coils
y registros Modbus no se convierten automáticamente en objetos físicos: su
significado debe configurarse.

Para hacer la conversión desde la interfaz, selecciona el escenario y pulsa
**Migrar campo a copia**. La API correspondiente es
`POST /api/scenarios/{id}/field-migration-copy`, con `{"name":"nombre opcional"}`.
La operación crea un escenario nuevo y detenido; nunca modifica el origen. En la
copia, cada válvula, motor, bomba, sensor analógico, entrada digital o alarma PLC
local se convierte en objeto de campo más canales `io_channel` y conexiones de
señal. Se mantienen las áreas/DB/bytes/bits y los nombres usados por reglas,
temporizadores y bloques. La bomba conserva tanto el punto BOOL de mando como
el REAL de caudal. Las conexiones existentes se conservan y el preview no
duplica sus objetos.

La copia hereda la subred e IP de los nodos del escenario original. Antes de
desplegar ambas a la vez, asigna a la copia una subred libre y direcciones IP
únicas. Para evitar habilitar tráfico entre zonas por sorpresa, la conversión
automática requiere que el PLC y el runtime de campo queden en la misma zona;
si no, la API devuelve `422` y deja ambas definiciones intactas. Las entradas
`remote_input`, canales ya declarados, puntos Modbus, reglas de red y
conexiones de proceso no se reinterpretan como objetos físicos.

La respuesta de la vista previa conserva `applied: false`; es únicamente una
inspección. La conversión funcional se realiza solo con la acción explícita de
crear copia.

## Siguientes bloques

1. Conversión automática a una copia de un escenario antiguo, preservando
   direcciones y el original.
2. Extender el modelo físico con colectores explícitos, solver general de red,
   geometría, curvas de bomba y condiciones de contorno avanzadas.
3. Permitir más de una zona de campo/runtime por escenario y conexiones físicas
   de proceso distribuidas entre zonas.
4. Salidas analógicas adicionales y más formatos/conversiones Modbus.

## Pruebas de este bloque

```bash
ANVIL_CLEANUP_ORPHANS=false .venv/bin/python -m unittest discover -s backend/tests
```

Las pruebas unitarias usan SQLite en memoria y Docker simulado. El motor de
campo tiene pruebas de retardo, atasco/restauración, fallo de sensor congelado,
override, escala,
obsolescencia y un balance determinista bomba/depósito/válvula/sensor. Las integraciones siguientes requieren Docker real:

```bash
.venv/bin/python integration/field_smoke.py --base-url http://127.0.0.1:8003
```

La prueba aislada del fallo de sensor inicia un contenedor de campo temporal,
comprueba que su lectura sigue el nivel del tanque, congela el último valor al
inyectar el fallo y verifica que retoma el seguimiento al restaurarlo:

```bash
docker build -t anvil/field:rf23-sensor-fault -f plc/Dockerfile.field ./plc
.venv/bin/python integration/field_sensor_fault_smoke.py --image anvil/field:rf23-sensor-fault
```

`.venv/bin/python integration/process_loop_smoke.py --base-url http://127.0.0.1:8005`
comprueba con Docker que el nivel sube y baja al accionar bomba/válvula desde
la HMI, atraviesa el PLC S7 y llega al sensor de nivel. La prueba
`integration/hmi_control_smoke.py` verifica escrituras HMI reales S7 y Modbus TCP,
además de denegación de entradas y dispositivos no seleccionados.

Para verificar el balance de válvulas en un runtime de campo Docker aislado,
sin API de escenario ni puertos publicados en el host:

```bash
docker build -t anvil/field:rf11-valve-flow -f plc/Dockerfile.field plc
.venv/bin/python integration/field_valve_balance_smoke.py --image anvil/field:rf11-valve-flow
```

El smoke comprueba el flujo cero con una válvula cerrada, el caudal nominal
escalado por apertura en una válvula proporcional y la conservación del caudal
en una rama paralela abierta/cerrada. El contenedor usa `network_mode=none` y se
elimina al terminar.

`integration/field_ui.cjs` prueba en Brave el recorrido completo de interfaz,
guardado/reapertura, conexiones, orden/estado/fallo y vista móvil.

Para repetir la integración de HMI/SCADA, válvula proporcional y caudal con
contenedores reales (la prueba crea y elimina su propio escenario en una red
Docker efímera), desde la raíz del repositorio:

```bash
docker build -t anvil/plc-s7:prop-check -f plc/Dockerfile plc
docker build -t anvil/modbus-tcp:prop-check -f plc/Dockerfile.modbus plc
docker build -t anvil/scada:prop-check -f plc/Dockerfile.scada plc
docker build -t anvil/field:prop-check -f plc/Dockerfile.field plc
PYTHONPATH=backend \
  ANVIL_PLC_IMAGE=anvil/plc-s7:prop-check \
  ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:prop-check \
  ANVIL_SCADA_IMAGE=anvil/scada:prop-check \
  ANVIL_FIELD_IMAGE=anvil/field:prop-check \
  .venv/bin/python integration/scada_control_smoke.py
```

El smoke escribe la salida REAL desde HMI y SCADA, comprueba los valores
Modbus/S7 de retorno, verifica la auditoría y confirma que el caudal de la
válvula cambia el nivel del depósito. Los previews permanentes no participan
en esta prueba.

## Docker CLI

Desde la raíz del repositorio:

```bash
docker build -t anvil/plc-s7:field-v1 -f plc/Dockerfile plc
docker build -t anvil/modbus-tcp:field-v1 -f plc/Dockerfile.modbus plc
docker build -t anvil/field:v1 -f plc/Dockerfile.field plc
docker build -t anvil/backend:field-live backend
docker volume create anvil-field-demo-data
docker run -d --name anvil-field-demo --restart unless-stopped \
  -p 127.0.0.1:8005:8000 \
  -e ANVIL_DATABASE_URL=sqlite:////data/anvil.db \
  -e ANVIL_PLC_IMAGE=anvil/plc-s7:field-v1 \
  -e ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:field-v1 \
  -e ANVIL_FIELD_IMAGE=anvil/field:v1 \
  -e ANVIL_CLEANUP_ORPHANS=false \
  -v anvil-field-demo-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  anvil/backend:field-live
```

Este comando levanta otro backend en 8005 con datos independientes. Para ejecutar la
prueba integrada, crea primero un entorno con las tres imágenes OT en el mismo Docker
Daemon y luego ejecuta el script de arriba. El preview SRS revisado reutiliza una
imagen backend ya construida y monta el checkout local; no reemplaza el servicio 8003. Desde su interfaz
carga el ejemplo, guarda y despliega; los contenedores de campo y OT se crean
en la red del escenario. Las construcciones necesitan acceso a la imagen base
y dependencias; el despliegue local revisado reutiliza las bases ya disponibles.
