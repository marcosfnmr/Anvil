# Añadir un tipo de dispositivo integrado

Esta guía describe el mecanismo que usa el checkout actual para añadir un nuevo
tipo de nodo mantenido dentro de Anvil. Los registries son puntos de extensión
internos de Python y JavaScript; Anvil no carga plugins de terceros en ejecución.
Un adaptador nuevo forma parte del código y de la imagen backend revisados por el
operador. Las plantillas compartidas permiten guardar configuraciones de tipos ya
integrados, no instalar código ejecutable.

Al importar y registrar un adaptador, el registry comprueba antes de
incorporarlo que el identificador sea una cadena minúscula de hasta 64
caracteres con segmentos alfanuméricos separados por guiones (`modbus-tcp`),
que herede de `Device` y que no conserve métodos abstractos. `internal` y
`network_resource` deben ser booleanos exactos. `component_types` debe ser una
colección de nombres de componente no vacíos; `protocols` y `capabilities`
deben ser listas o tuplas de cadenas no vacías. Todo tipo visible en catálogo
debe declarar un esquema Pydantic y `label`, `description` e `icon` no vacíos.
Los adaptadores `internal` pueden omitir el esquema y los metadatos visibles,
pero también deben implementar el contrato abstracto. Un error hace fallar el
registro durante la importación, en lugar de esperar a que un escenario
intente desplegar ese tipo.

Estas comprobaciones solo validan la forma básica de la declaración. El
registry **no** valida los campos internos del esquema, las direcciones que
produce `build_io_map()`, que la imagen exista, que el contenedor arranque, ni
que un listener declarado responda al protocolo. Esas propiedades se validan
en el modelo de configuración, la fábrica/runtime y las pruebas de imagen y
protocolo correspondientes.

Cada adaptador puede declarar `protocols` y `capabilities`. `GET
/api/scenarios/catalog` publica estas listas para describir qué implementa el
tipo registrado. Son metadatos de catálogo: no habilitan una operación por sí
solos ni prueban fidelidad. Por ejemplo, `plc-s7` declara `s7comm`,
`modbus-tcp` declara Modbus TCP y RTU virtual, y `scada` declara clientes
S7/Modbus TCP. La HMI declara el mismo par de clientes con escritura
autorizada, alarmas y sinóptico. OPC UA, DNP3, IEC-104, EtherNet/IP, Profinet e IEC 61850 no deben
aparecer como capacidades hasta que existan runtime, configuración,
operaciones y pruebas.

## Recorrido de una configuración

Una configuración sigue este recorrido:

1. `backend/models/scenario.py` recibe el `device_type`, resuelve su adaptador y
   valida `config` con el modelo Pydantic que declara el adaptador.
2. `backend/devices/<tipo>.py` convierte el modelo público a datos de runtime,
   declara cómo crear el contenedor Docker y, si corresponde, expone un mapa de
   E/S mediante `build_io_map()`.
3. El frontend importa explícitamente el módulo del nodo en
   `backend/static/app.js`. El módulo registra su descriptor en
   `backend/static/nodes/registry.js`; los tipos de componentes registran sus
   formularios en `backend/static/components/registry.js`.
4. `backend/services/docker_manager.py`, las rutas de operaciones y el gateway en
   `docker_proxy/` gestionan imágenes y comandos permitidos. La creación de un
   adaptador no autoriza automáticamente operaciones de lectura/escritura ni
   añade una imagen al allowlist del gateway.

El contrato base ofrece además cuatro hooks opcionales, con efecto distinto:

- `external_port(config)` devuelve el puerto TCP del runtime para habilitar
  forwarding externo cuando el escenario lo solicita expresamente. El valor
  debe coincidir con el puerto permitido y etiquetado; no publica nada por
  defecto.
- `network_mode(runtime_device)` puede seleccionar el modo de red del
  contenedor. `none` sirve para runtimes aislados que no necesitan red. El
  gestor rechaza `host` y contenedores privilegiados, y el modo elegido afecta
  conectividad y compatibilidad con zonas; debe probarse con el despliegue.
- `network_resource` marca recursos de red gestionados por Docker, como un
  switch virtual. No son contenedores de dispositivo normales: el gestor crea
  el recurso y omite su configuración de contenedor estándar.
- `network_services(runtime_device)` enumera listeners que el runtime realmente
  ofrece. Cada entrada declara `service`, `protocol`, `transport`, `port`,
  `role: "server"` y opcionalmente `scope` (`ot` o `internal`) y
  `description`. La ruta `GET /api/scenarios/{id}/services` presenta este
  inventario junto con IP y puerto; declararlo **no** abre firewall, publica un
  puerto del host ni crea el servicio. El listener tiene que existir en la
  imagen y la ruta de red debe permitir alcanzarlo.

## Pasos para un nodo nuevo

1. **Definir el esquema.** Añade modelos Pydantic en `backend/models/` con tipos
   estrictos, límites, validación de direcciones duplicadas y valores iniciales.
   Rechaza propiedades desconocidas cuando proceda. No uses `bool` como entero ni
   coerciones silenciosas para datos de protocolo.
2. **Crear el adaptador.** Añade `backend/devices/<tipo>.py` y registra una clase
   con `@register_device("<tipo>")`. Hereda de `Device`, declara `label`,
   `description`, `icon`, `config_model` y `component_types`; implementa
   `build_runtime_device()` y `build_container_config()`. Implementa
   `external_port()` solo si ese protocolo tiene un puerto que se pueda publicar
   explícitamente. `backend/devices/__init__.py` descubre e importa los módulos
   Python del paquete al iniciar; un nombre de tipo duplicado hace fallar el
   registro. También rechaza adaptadores abstractos y tipos visibles sin
   esquema/metadatos válidos; prueba también nombre, flags y colección de
   componentes. Esto no sustituye las pruebas de configuración ni de despliegue.
   Declara `protocols` y `capabilities` solo para funciones realmente
   implementadas y verificadas.
3. **Resolver compatibilidad del escenario.** Revisa validadores y reglas de
   conexión en `backend/models/scenario.py` y `backend/models/field.py`. Añade
   compatibilidades de protocolo, topología y enlaces únicamente donde el nuevo
   dispositivo pueda operar realmente. No infieras conectividad de una línea
   dibujada en el mapa.
4. **Implementar el runtime.** Añade el servidor y su `Dockerfile` bajo `plc/` (o
   el runtime apropiado), healthcheck, límites y dependencias fijadas. No montes
   el socket Docker, el filesystem del host ni dispositivos del host dentro del
   runtime. Añade el nombre de imagen configurable a `docker-compose.yml`, al
   gateway `docker_proxy/` y al flujo de build de `.github/workflows/ci.yml`.
5. **Añadir la interfaz.** Crea `backend/static/nodes/<tipo>.js` con un descriptor
   registrado, función `create()` y renderizado que escape los valores del
   escenario. Impórtalo explícitamente en `app.js`. Si el dispositivo tiene
   puntos configurables, registra sus componentes/formularios en
   `backend/static/components/`; implementa los controles de inspector y sus
   permisos según el mapa real de E/S. Actualiza el identificador de caché de
   módulos si corresponde.
6. **Conectar API solo cuando exista runtime real.** Para lecturas o escrituras en
   vivo, implementa rutas autenticadas/aisladas por escenario, comprueba el mapa
   de E/S y permisos de cada punto, aplica límites de valor y registra operaciones
   auditables. La interfaz no debe presentar controles que solo cambien estado
   local del navegador.
7. **Documentar y probar.** Añade pruebas de esquema, registro/fábrica, compilación
   de configuración, permisos de API, interfaz y protocolo real desde Docker CLI.
   Verifica también rutas de red, fallo/desconexión, healthcheck y limpieza. Añade
   una ficha de fidelidad en `docs/DEVICE_FIDELITY.md` y el mapeo de cobertura en
   `docs/SRS_TRACEABILITY.md`.

## Contrato mínimo de un adaptador

```python
from devices import register_device
from devices.base import Device


@register_device("example-protocol")
class ExampleDevice(Device):
    label = "Dispositivo de ejemplo"
    description = "Runtime de protocolo de ejemplo"
    icon = "EX"
    config_model = ExampleConfigInput
    component_types = frozenset({"example_point"})

    def build_runtime_device(self, device):
        # Devuelve un dict JSON-safe; aquí se resuelven direcciones automáticas.
        ...

    def build_container_config(self, scenario_id, runtime_device):
        # Devuelve opciones docker-py sujetas a la política del gateway.
        ...

    def build_io_map(self, runtime_device):
        # Declara dirección, tipo, unidad, componente y access (read/read_write).
        ...
```

El esquema del escenario es `DeviceInput` con `device_type` y `config`; no se
requiere añadir una clase al union central. El modelo debe estar disponible antes
de validar escenarios, por lo que el módulo del adaptador debe importarse al
inicializar el backend. Los dispositivos internos, como proxies, no deben
registrarse como tipos seleccionables por el usuario.

## Límites actuales

- El descubrimiento carga módulos Python que ya están empaquetados dentro del
  backend; no existe instalación en caliente, firma de plugins, sandbox de código
  de extensión ni API pública de terceros.
- El catálogo frontend se compone de imports estáticos revisados. Un registro de
  backend no genera por sí solo un inspector web ni controles de operación.
- Varias reglas de interoperabilidad, operaciones, límites del gateway y
  exportación Compose contienen compatibilidades explícitas por tipo. Deben
  revisarse al añadir un protocolo; registrar una clase no garantiza que el
  dispositivo sea desplegable o alcanzable.
- Antes de habilitar forwarding externo, el adaptador debe declarar puerto
  permitido y etiquetado coherente; el gateway valida ambos. El modo externo
  permanece desactivado por defecto.

Ejemplos existentes para comparar: `backend/devices/plc_s7.py`,
`backend/devices/modbus_tcp.py` y sus módulos de runtime bajo `plc/`.

## Verificar el contrato de extensión

La prueba `test_documented_device_extension_is_visible_and_creatable_through_api`
registra un adaptador efímero y verifica que su descriptor aparece en
`GET /api/scenarios/catalog`, que `DeviceInput` aplica su esquema Pydantic y que
se puede crear un escenario por API con esa configuración. La prueba hermana
`test_registered_device_config_schema_is_selected_without_changing_device_input`
comprueba la construcción del runtime. Ejecuta ambas desde la raíz:

```bash
.venv/bin/python -m unittest \
  backend.tests.test_api.ScenarioApiTests.test_documented_device_extension_is_visible_and_creatable_through_api \
  backend.tests.test_devices.PlcS7DeviceTests.test_registered_device_config_schema_is_selected_without_changing_device_input
```

Estas pruebas no despliegan una imagen externa: el adaptador temporal solo
comprueba el contrato Python/API. Para verificar un runtime nuevo hacen falta
además pruebas Docker de su protocolo, políticas de imagen/red y limpieza.
