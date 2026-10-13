# Comunicación entre PLCs S7

## Qué es un enlace PLC a PLC

Un PLC S7 puede actuar como **servidor** que publica datos y como **cliente**
que solicita datos a otro equipo. En Anvil, un `remote_input` convierte una
señal local del PLC emisor en una lectura periódica S7 del PLC receptor; no es
un cable de E/S compartida ni sincroniza los scans. El enlace transporta un
punto DB seleccionado, por lo que requiere que el servidor y dirección existan
y que la ruta de red esté habilitada si los nodos están en zonas distintas.
Para las definiciones de servidor, cliente y calidad, consulta el
[glosario](glossary.md); para el comportamiento del runtime, consulta
[PLC S7](plc-s7.md).

Un PLC receptor puede consultar señales de otro PLC S7 del mismo escenario. Cada
`remote_input` crea un cliente S7 dentro del contenedor receptor: lee el DB remoto
por TCP/102 y actualiza una entrada local BOOL o REAL. Entre PLCs de la misma zona
usa la IP del emisor; entre zonas exige una ruta S7 explícita receiver→sender y
conecta a un gateway TCP de flujo único. El protocolo S7 sigue siendo real; dentro
de la zona del emisor, el servidor ve la IP del gateway.

Esta función simula un intercambio de datos de tipo GET. No ejecuta instrucciones
de un programa Siemens, lógica Ladder, PUT, PROFINET I/O ni comunicación de seguridad.
El receptor expone el dato recibido, pero todavía no ejecuta reglas de control que
reaccionen a él. Las señales locales del emisor pueden ser válvulas, motores,
entradas digitales, alarmas, sensores o la orden/caudal de una bomba.

## Configuración desde la interfaz

1. Añade dos PLCs S7 al escenario y configura las señales del emisor.
2. Selecciona el receptor en la lista del inspector.
3. Selecciona **Entrada remota S7**, pulsa **Añadir señal** y elige BOOL o REAL.
4. Selecciona el PLC emisor y una de sus señales del mismo tipo.
5. Si está en otra zona, activa **Permitir enlace S7 al PLC remoto mediante gateway**.
6. Guarda y despliega. El mapa asigna automáticamente una dirección local al dato recibido.
7. Consulta **Señales**: cada entrada remota muestra su origen, valor y calidad.

No se necesita habilitar el reenvío de puertos al host. El editor representa los
enlaces mediante las entradas configuradas, no mediante cables dibujados. Para
intercambiar datos en ambos sentidos, añade entradas en cada receptor. Las fuentes
deben ser señales locales: los enlaces entre entradas remotas se rechazan para
evitar ciclos de realimentación sin lógica de proceso.

## Contrato de configuración

```json
{
  "type": "remote_input",
  "name": "marcha_remota",
  "db": 1,
  "data_type": "BOOL",
  "source_node_id": "sender",
  "source_address": "DB1.DBX0.0",
  "source_name": "en_marcha",
  "period_ms": 200,
  "timeout_ms": 1000,
  "stale_after_ms": 1000,
  "stale_behavior": "hold"
}
```

`source_name` es opcional para los clientes de API. La interfaz lo guarda para
detectar que una modificación del emisor haya cambiado la señal de esa dirección.
Tras editar el mapa del emisor, revisa los enlaces y vuelve a seleccionar su señal
si la validación indica que cambió. Las referencias pertenecen al mismo escenario
y se conservan al guardar, importar, exportar o clonar.

El periodo admite 100–60000 ms, el timeout 100–10000 ms y el umbral de obsolescencia
100–120000 ms. Este último debe ser mayor o igual al periodo. Se admiten hasta 32
entradas remotas por PLC. Los tiempos son aproximados; el ciclo del simulador es
de 100 ms y Docker no proporciona una ejecución industrial de tiempo real.

## Calidad y fallos

- `waiting`: todavía no se ha recibido un valor. El DB parte de false/0; ese valor
  inicial no representa una lectura válida del emisor.
- `good`: la última lectura tiene una edad inferior al umbral configurado.
- `stale`: la última lectura superó el umbral. Se conserva el último valor recibido.

`stale_behavior` controla qué hace la lógica local con esa calidad. `hold` (el
valor predeterminado y compatible con escenarios existentes) conserva el
comportamiento normal usando el último valor. `force_off` mantiene la telemetría
con su último valor y marca la calidad, pero fuerza a OFF las salidas gobernadas
por bloques que consuman esa entrada mientras su estado sea `waiting` o `stale`.
También limpia el estado de los bloques SR, TON, CTU y secuenciador afectados; al
recuperar una lectura válida vuelven a evaluar la señal y continúan desde estado
inicial del bloque. Esta política se aplica a la lógica del simulador, no limita
escrituras S7 directas ni convierte la entrada en un interlock de seguridad.

La API `GET /api/scenarios/{id}/plcs/{node_id}/values` añade `communication` a las
entradas remotas: origen, calidad, edad en ms, última recepción y último error.
El lector reintenta los fallos con una espera creciente, hasta 5 segundos, y vuelve
a marcar el dato vigente cuando recibe una nueva respuesta. Un enlace bloqueado
no detiene el ciclo de simulación ni las otras entradas. Los estados `partial` y
`error` del escenario permiten consultar los PLCs que siguen funcionando.

Las entradas remotas son de solo lectura en la API de Anvil. Un cliente S7 directo
puede escribir los DB del simulador; una nueva lectura del enlace volverá a actualizar
la entrada. La política de consola se aplica a las rutas API, no al tráfico S7 entre
PLCs. Una ruta peer solo permite el S7 iniciado por ese receptor hacia ese emisor;
el acceso directo entre las subredes sigue bloqueado. No sustituye a una ACL
general ni a un firewall industrial.

## Construir y probar con Docker CLI

Desde la raíz del repositorio, con Docker accesible:

```bash
docker build -t anvil/plc-s7:s7-links ./plc
docker build -t anvil/backend:s7-links ./backend
docker run -d --name anvil-s7-links-preview \
  -p 127.0.0.1:8004:8000 \
  -e ANVIL_DATABASE_URL=sqlite:////data/anvil.db \
  -e ANVIL_PLC_IMAGE=anvil/plc-s7:s7-links \
  -e ANVIL_CLEANUP_ORPHANS=false \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v anvil-s7-links-preview-data:/data \
  anvil/backend:s7-links
curl --fail http://127.0.0.1:8004/api/ready
```

Importa `examples/s7-peer-links.json` desde el panel para probar un escenario de
dos PLCs. La prueba automática crea y limpia su propio escenario en
`172.30.243.0/24`; necesita que esa subred esté libre. No debe ejecutarse mientras
esté desplegado el ejemplo en esa misma subred.

El smoke inicia sesión automáticamente contra backends con autenticación. Define
`ANVIL_TEST_EMAIL` y `ANVIL_TEST_PASSWORD`; si la base no tiene aún usuarios,
define también `ANVIL_BOOTSTRAP_TOKEN`. La guía [VALIDATION_LOCAL.md](VALIDATION_LOCAL.md)
incluye comandos para leerlos sin guardarlos en el repositorio.

```bash
python3 -m venv .venv-links
. .venv-links/bin/activate
python -m pip install -r backend/requirements-dev.lock -r plc/requirements.lock -r plc/requirements.modbus.lock
python -m unittest discover -s backend/tests
python -m unittest discover -s plc/tests
python integration/s7_links_smoke.py --base-url http://127.0.0.1:8004
```

La integración verifica IPs, ausencia de puertos host, valores BOOL/REAL, escritura
rechazada en el receptor y tráfico con la IP del receptor en la traza. Además
interrumpe temporalmente el emisor, verifica timeout y datos obsoletos, y comprueba
la recuperación. Solo manipula los contenedores del escenario que acaba de crear.

Para la prueba del inspector, usa Node.js y Playwright en el entorno de pruebas:

```bash
npm install --prefix /tmp/anvil-browser-tests playwright
/tmp/anvil-browser-tests/node_modules/.bin/playwright install chromium
ANVIL_PLAYWRIGHT_PATH=/tmp/anvil-browser-tests/node_modules/playwright \
  ANVIL_BASE_URL=http://127.0.0.1:8004 node integration/s7_links_ui.cjs
```

Esta prueba configura los enlaces por teclado, valida su persistencia y observa
la orden BOOL y el caudal REAL recibidos. Usa `172.30.244.0/24` y limpia su escenario.
`ANVIL_AXE_PATH` permite incluir una auditoría automática de accesibilidad con axe-core.

Detén los escenarios desde Anvil antes de detener el backend:

```bash
docker stop anvil-s7-links-preview
```

## Validación local del 1 de octubre de 2026

- 61 tests del backend y 24 del runtime PLC.
- Prueba S7 entre dos contenedores: BOOL/REAL, IPs configuradas, trazas del receptor,
  permisos de consola, emisor detenido, valores obsoletos y recuperación automática.
- Prueba del inspector con Playwright/Edge: selección por teclado, persistencia,
  valores recibidos y auditoría automática axe sin infracciones A/AA en esa vista.
- Regresión de aislamiento Docker y clientes S7/Modbus en la red del escenario.

La construcción habitual encontró un timeout al consultar
`registry-1.docker.io` para `python:3.11-slim`. Las imágenes locales probadas se
construyeron copiando el código sobre `anvil/plc-s7:ui-review` y
`anvil/backend:ui-review`, ya presentes y con las dependencias del proyecto.
La construcción completa desde la imagen base queda pendiente de que Docker Hub
sea accesible. El preview local usa estas imágenes verificadas; no se ha publicado
el código ni se ha desplegado en la VM.


### Enlaces entre zonas — 2026-10-07

`integration/s7_peer_route_smoke.py` verifica dos PLC en redes Purdue
independientes: el receptor conecta al gateway configurado, lee BOOL/REAL, el
tráfico directo hacia el emisor queda bloqueado, el dato pasa a obsoleto y se
recupera al volver el emisor. El gateway solo se conecta a las dos redes
declaradas y no publica puertos del host. Esta prueba no modifica los previews.

### Política de calidad en bloques — 2026-10-08

`stale_behavior` ofrece `hold` (compatibilidad por defecto) y `force_off` para
entradas remotas. La segunda opción mantiene el último valor telemétrico y su
marca `stale`, pero desactiva los bloques de control dependientes mientras la
entrada espera o está obsoleta. Al volver una lectura válida, el bloque vuelve a
evaluar su entrada. La prueba `integration/s7_links_smoke.py` pasó con dos PLC S7
Docker: comprobó BOOL/REAL, escritura remota rechazada, ausencia de puertos host,
salida de alarma OFF al parar el emisor, valor stale retenido y recuperación
automática. Suites verificadas en esta revisión: runtime PLC 97/97; backend 358
pruebas, con un fallo de aislamiento intermitente en otra prueba de terminal que
pasó al ejecutarla sola.
