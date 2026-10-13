# Motor de proceso y runtime de campo

## Proceso industrial y su modelo en Anvil

En una planta, el proceso es la transformación física que se desea controlar:
por ejemplo, mover agua, llenar un depósito o mantener presión. La
instrumentación observa magnitudes y los actuadores las modifican. El PLC
decide las órdenes a partir de entradas; los protocolos permiten intercambiar
datos, pero no sustituyen el comportamiento físico ni la lógica de control.

Anvil separa esas responsabilidades. Los objetos de campo representan sensores,
válvulas, bombas y equipos; un runtime independiente por escenario calcula sus
estados y un modelo concentrado de proceso. Los canales de PLC/Modbus reciben o
envían señales al runtime. HMI y SCADA luego consultan esos puntos usando el
protocolo del dispositivo. Esta separación permite seguir cada tramo de una
variable y encontrar qué conexión falta.

### Las tres relaciones del mapa

1. **Proceso:** representa caudal o una medida física entre equipos (por
   ejemplo, bomba → tubería → depósito). Cambia magnitudes simuladas.
2. **Señal:** lleva un dato tipado entre un puerto de campo y un punto de E/S
   del PLC o Modbus (por ejemplo, `sensor.value` → entrada BOOL/REAL, o una
   salida → `valve.command`).
3. **Red/protocolo:** permite que un cliente HMI/SCADA u otro PLC alcance el
   servicio e inicie una petición S7/Modbus para una dirección concreta.

Una línea de proceso no conecta una entrada del PLC; una señal no crea una
ruta entre zonas; una ruta de red no selecciona tags. Las tres pueden ser
necesarias para una demostración completa. El [mapa](PROCESS_MAP.md) explica
cómo reconocerlas visualmente y [conceptos](concepts.md) muestra el recorrido
de extremo a extremo.

## Conceptos clave

El **proceso** es el comportamiento modelado de la planta (por ejemplo, nivel
de depósito o caudal). El **runtime de campo** es el servicio que calcula ese
estado; un **puerto** es un extremo lógico tipado como `value`, `command` o
`process_out`, no una borna eléctrica ni un puerto TCP. Una **conexión de
proceso** transporta magnitudes del modelo; una **conexión de señal** enlaza
un puerto de campo con un punto de E/S PLC/Modbus. El protocolo y la red son
capas distintas, necesarias cuando un cliente HMI/SCADA consulta ese punto.

Los términos de proceso y sus unidades se definen en el
[glosario](glossary.md); las ecuaciones y supuestos están en
[Modelo de proceso](process-model.md).

## Qué es el motor de proceso

En automatización, el **proceso** es la parte física que cambia: por ejemplo,
el caudal que circula, el volumen de un depósito o la posición de una válvula.
El **motor de proceso** calcula esas magnitudes a partir de los equipos,
conexiones y órdenes que se han definido. No es el PLC, el protocolo de
comunicación ni el mapa visual.

En Anvil, el motor se ejecuta en un contenedor interno `field-runtime` por
escenario cuando hay objetos de campo. El contenedor mantiene el estado de
bombas, válvulas, motores, sensores y equipos de proceso, calcula las relaciones
de proceso y entrega/recibe señales de los nodos PLC/Modbus. Se ejecuta en
Docker; no necesita que el navegador permanezca abierto.

Un escenario puede combinar estas capas:

```text
Conexiones de proceso → runtime de campo → magnitudes calculadas
Conexiones de señal   ↔ runtime de campo ↔ puntos de E/S PLC/Modbus
Protocolo S7/Modbus   ↔ servidor PLC/Modbus ↔ cliente HMI/SCADA
Red/ruta              → permite alcanzar el servidor desde el cliente
```

Cada flecha tiene un contrato distinto. La tubería cambia el modelo de proceso;
la señal mueve un valor entre un objeto de campo y un punto de E/S; S7/Modbus
permite que un cliente solicite datos al servidor; la red permite que el cliente
llegue a ese servicio. Ninguna de estas relaciones crea automáticamente las
demás.

## Qué calcula y qué no

El motor actual implementa modelos concentrados de proceso para los tipos de
objeto y topologías admitidos. Puede calcular caudal, balance de volumen del
depósito, apertura/velocidad modeladas, presión aproximada, límites de tubería,
división y suma de caudal, así como medidas de sensores derivadas de esas
magnitudes. Los detalles y supuestos están en [Modelo de proceso](process-model.md)
y [Elementos de campo](FIELD_OBJECTS.md).

No es un solver hidráulico o físico general. No reproduce geometría 3D,
transitorios de presión, cavitación, curvas de fabricante completas, dinámica
eléctrica ni incertidumbre de hardware. Las unidades y ecuaciones son las que
declara el modelo Anvil; una prueba del runtime demuestra ese modelo, no su
equivalencia con una planta concreta.

## Qué configura quien diseña el escenario

La configuración se guarda en el escenario y se compila para el runtime al
desplegar:

1. Se añaden objetos independientes (por ejemplo, bomba, tubería, depósito,
   sensor o válvula) con sus parámetros, estado inicial y ubicación.
2. Se conectan puertos compatibles mediante conexiones de **proceso** para
   definir por dónde se transmite una magnitud.
3. Se conectan puertos `value` o `command` a puntos de PLC/Modbus mediante
   conexiones de **señal**. Los tipos y conversiones permitidos se validan.
4. El PLC ejecuta la lógica configurada en sus ciclos, o un cliente autorizado
   escribe un punto de salida. El runtime de campo recibe la orden y actualiza
   el objeto/proceso.
5. HMI/SCADA leen los puntos del servidor protocolario como clientes; no leen
   directamente el runtime de campo por el mero hecho de compartir una zona.

Las señales de campo conectadas de un escenario se ejecutan en un único runtime
y deben pertenecer a una zona de campo compatible. Las señales entre zonas
requieren la ruta HTTP interna explícita que muestra el inspector. No se crea
una ruta IP general.

## Interfaz entre Anvil y el runtime

El backend adapta el esquema persistido a `FIELD_CONFIG` (JSON) al crear el
contenedor. La implementación actual escucha en TCP/8765 dentro de la red del
escenario, sin publicar ese puerto en el host. Las operaciones internas son:

| Operación | Petición | Respuesta | Uso |
| --- | --- | --- | --- |
| `GET /snapshot` | Sin cuerpo | Objetos con estados/valores y calidad de conexiones | Vista de valores del campo y diagnóstico |
| `POST /exchange` | `node_id` y `outputs` indexado por ID de conexión | `inputs` indexado por ID de conexión | Ciclo de intercambio PLC/Modbus ↔ señales de campo |
| `POST /control` | ID de objeto y cambio admitido, como fallo u override de sensor | Snapshot actualizado | Operación del escenario autorizada por la API |

El cuerpo está limitado a 1 MiB. El runtime valida el frame completo antes de
aplicar las órdenes, rechaza tipos/rangos inválidos con HTTP 422 y usa calidad
`waiting`, `good`, `stale` o `fault` para distinguir una lectura ausente,
reciente o degradada. El backend llama al runtime gestionado dentro de Docker;
estas rutas internas no son endpoints públicos para un navegador o cliente
externo. Las operaciones de control de usuario pasan por la API de escenario y
se registran en auditoría.

El contrato interno declara la versión `1` y el endpoint `/capabilities` la
publica junto con las rutas disponibles. La configuración de runtime también
incluye esa versión y el motor oficial rechaza otra que no conozca. El
motor interpreta como versión 1 una configuración persistida antigua que no
contenga el campo, para permitir recrear escenarios guardados antes de este
cambio. Las nuevas configuraciones siempre incluyen la versión explícita. El
adaptador instala una sonda Docker que consulta ese endpoint y el gestor espera
el estado `healthy`, incluso si la imagen seleccionada no definió su propio
healthcheck. Por ello, una imagen alternativa debe ofrecer la respuesta
compatible en `/capabilities` y disponer de Python para ejecutar la sonda.
Esto detecta contrato incompatible durante el despliegue cuando la imagen
respeta la sonda impuesta; no convierte `/exchange` en API pública estable ni
es una frontera de seguridad frente a una imagen deliberadamente engañosa.
`ANVIL_FIELD_IMAGE` permite seleccionar la imagen al desplegar, pero no hay
registro de motores, migración automática de configuración ni sandbox para
plugins de terceros. El código integrado está en `plc/field_server.py` y su
adaptador de despliegue en `backend/devices/field_runtime.py`.

## Cómo comprobar el flujo

Para una demostración completa, sigue el [tutorial de la estación de bombeo](tutorial.md):
conecta una bomba a un depósito, conecta el sensor a una entrada del PLC y la
salida del PLC a la válvula. Despliega el escenario y verifica por separado:

- **Proceso:** volumen, caudal y estado de actuadores en el snapshot del campo.
- **Señal:** calidad `good` y valor coherente en la entrada PLC/Modbus.
- **Control:** salida cambia por lógica o escritura autorizada y llega al puerto
  `command` de la válvula.
- **Protocolo:** HMI/SCADA lee una dirección configurada desde el servidor y
  recibe una calidad válida.
- **Red:** diagnóstico confirma que el cliente alcanza el servidor por su zona
  o ruta declarada.

`integration/process_pipe_smoke.py` verifica topologías y balance del modelo en
Docker; `integration/field_route_smoke.py` prueba intercambio de señales
PLC↔campo a través de zonas. Ninguna de las dos es una validación frente a
instrumentación o planta física.
