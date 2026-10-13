# Preview persistente de demos Anvil

## Demo actualizada — 9 de octubre de 2026

La versión local actual está en **http://localhost:8010/**, proyecto
`anvil-rnf-preview`, volumen `anvil-rnf-preview-data`. Las imágenes usan
`demo-20261009-current`: código actual construido sobre imágenes locales
por una espera al resolver la base en Docker Hub. Captura reutiliza su runtime
anterior sin cambios. GitHub sigue en `12a51da`; no se desplegó en una VM remota.

La instancia estaba vacía. Se creó una cuenta administradora por bootstrap y
una demo **Planta de agua · Lazo hidráulico**, ahora desplegada. El login solo
requiere correo y contraseña: las credenciales se entregan fuera del repositorio.
Los previews 8003, 8005 y 8008 conservaron sus contenedores y datos.

Para levantar estas imágenes locales conservando datos:

```bash
docker compose --env-file docs/demo-20261009.env.example \
  -p anvil-rnf-preview -f docker-compose.preview.yml \
  up -d --no-build --wait --wait-timeout 90
curl --fail http://localhost:8010/api/ready
```

El archivo de entorno de ejemplo solo contiene referencias de imágenes.
El respaldo SQLite previo, verificado, está fuera del repositorio en
`/tmp/anvil-rnf-before-demo-20261009.db`.

Pruebas: backend 512, PLC 161, gateway 35 correctas. La primera ejecución backend
falló en la prueba concurrente del scheduler al compartir una conexión SQLite;
la prueba ahora utiliza una base temporal con conexiones separadas y la suite
completa repetida pasó. En vivo: login, listado/creación de escenario, deploy,
lectura de campo, HMI/SCADA con calidad buena y lectura/escritura Modbus de coil
y holding register; los valores escritos se restauraron.

Se corrigió la consulta de volúmenes ausentes para devolver el 404 de Docker y
permitir crearlos, se declaró la imagen SCADA en el gateway y se autorizaron los
comandos internos fijos de operación por dispositivo/huella SHA-256. No se admite
Python arbitrario ni shell en equipos sin terminal autorizado. Cuando cambie un
script de operaciones, ejecutar `python3 tools/update_runtime_exec_policy.py`,
pasar las pruebas del gateway y reconstruir su imagen.

## Historial del preview aislado (8 de octubre)

El preview de integración del checkout local está disponible en
<http://localhost:8010/>. Usa el proyecto Compose `anvil-rnf-preview`, el
volumen vacío `anvil-rnf-preview-data`, imágenes con la etiqueta
`rnf-preview-20261008` y un binding HTTP solo en loopback. Los protocolos OT no
se publican en el host; los escenarios nuevos viven en recursos Docker
etiquetados por Anvil. `ANVIL_CLEANUP_ORPHANS=false` evita que el preview intente
retirar recursos ajenos. La base está en modo `0600`, y el backend corre con raíz
de solo lectura, sin capabilities y sin elevación de privilegios.

El primer acceso requiere crear la cuenta administradora. El token de bootstrap
se generó fuera del repositorio y se guarda en un archivo local con modo `0600`:

```bash
sed -n 's/^ANVIL_BOOTSTRAP_TOKEN=//p' /tmp/anvil-rnf-preview-bootstrap-20261008.env
```

Elige el correo y la contraseña al completar el formulario. El token solo crea
la primera cuenta; no es la contraseña ni sirve para iniciar sesión. No añadas
ese archivo a Git ni lo compartas en capturas. El volumen de este preview es
independiente del volumen de demo de 8008 y de los datos de los previews 8003 y
8005.

Para comprobar o detener exclusivamente este preview:

```bash
docker compose -p anvil-rnf-preview --env-file /tmp/anvil-rnf-preview-bootstrap-20261008.env \
  -f docker-compose.preview.yml ps
curl --fail http://localhost:8010/api/ready
docker compose -p anvil-rnf-preview --env-file /tmp/anvil-rnf-preview-bootstrap-20261008.env \
  -f docker-compose.preview.yml down
```

No añadas `--volumes` si quieres conservar la cuenta y escenarios creados en
8010. Este preview es local al checkout; no está publicado en GitHub.

La demo local de Anvil tiene reservado el puerto **8008** y se abre en
<http://localhost:8008/>. El Compose `docker-compose.demo.yml` construye la
versión desde este checkout, usa las imágenes de runtime identificadas como
demo, conserva su base en el volumen Docker `anvil-demo_demo-data` y limita el
puerto HTTP a `127.0.0.1`. Requiere Docker Compose v2.

El manual navegable de usuario, operación y desarrollo está disponible aparte
en <http://localhost:8009/>. Se construye desde los Markdown versionados con
MkDocs Material y se sirve mediante `docker-compose.docs.yml`; no comparte
backend, base de datos ni volumen con Anvil.

Este stack no reutiliza el `./data` ni los puertos de `docker-compose.yml`.
`ANVIL_CLEANUP_ORPHANS=false` impide la retirada de recursos Docker ajenos al
preview. Al actualizarlo, `docker compose up -d --build` reconstruye solo sus
servicios y conserva la base de datos del preview. Los escenarios creados desde
esta demo son contenedores Docker separados y se pueden detener desde Anvil.

El checkout actual se desplegó en 8008 con `anvil/backend:srs-rf27-lifecycle-lease-20261008`,
`anvil/plc-s7:srs-fault-prob-20261008`, `anvil/field:rf23-sensor-fault-20261008` y
`anvil/docker-api-gate:srs-exec-scope-20261008`. La etiqueta documenta esta
versión de demo; las imágenes y el volumen `anvil-demo_demo-data` son locales.

El 2026-10-08 se actualizó solo el backend a
`anvil/backend:srs-rf26-service-inventory-20261008` para enumerar los listeners
S7, Modbus TCP/RTU y la API interna SCADA con sus puertos efectivos en
`GET /api/scenarios/{scenario_id}/services`. La copia SQLite consistente previa
está en `/tmp/anvil-demo-pre-rf26-service-inventory-20261008.db` (modo 0600);
el único escenario estaba detenido y no había leases de ciclo de vida. Se
conservaron el gateway Docker, el volumen y los runtimes. Se verificó readiness
tras el cambio y el resto de previews no se recreó.

El mismo día se actualizó solo el backend a
`anvil/backend:srs-rf26-service-inventory-ui-20261008`. El inspector de cada
nodo muestra ahora los servicios que declara su adaptador, nombre/IP/puerto,
protocolo, transporte, alcance y binding de host cuando existe. Indica que el
inventario corresponde al último guardado si el escenario tiene cambios sin
guardar, y aclara que la lista es declarativa: la salud se comprueba aparte.
HMI se identifica como cliente sin listener OT propio. La copia SQLite previa
está en `/tmp/anvil-demo-pre-rf26-service-inventory-ui-20261008.db` (modo 0600);
había un escenario detenido, cero leases y una cuenta. El backend quedó healthy,
`/api/ready` confirmó DB/Docker `ok`, se verificaron OpenAPI y el módulo del
inspector, y los puertos 8003, 8005, 8009 y 8010 siguieron respondiendo HTTP 200.
El gateway y el volumen persistente no se recrearon.

Después se actualizó solo el backend con
`anvil/backend:srs-rnf07-validation-path-20261008` para conservar las rutas de
campo en mensajes de validación estructurada. La copia previa está en
`/tmp/anvil-demo-pre-rnf07-validation-path-20261008.db` (modo 0600); había un
escenario detenido, cero leases y una cuenta. Tras actualizar, el backend quedó
healthy y `/api/ready` informó DB/Docker `ok`. OpenAPI y `api.js` respondieron
HTTP 200; escenario, cuenta y lease count se conservaron. Los previews 8003,
8005, 8009 y 8010 siguieron HTTP 200; el gateway y volumen no se recrearon.

El 2026-10-08 se actualizó el backend de forma aislada para incluir el bundle
Compose portable de RF-30 y la recuperación de despliegues interrumpidos de
RF-27. Antes de cada actualización se guardó una copia SQLite en
`/tmp/anvil-demo-pre-rf30-portable-20261008.db` y
`/tmp/anvil-demo-pre-rf27-recovery-20261008.db`; el único escenario existente
estaba detenido. El gateway Docker y el volumen de datos no se recrearon. Tras
la última actualización, `/api/ready` respondió `ready` con DB y Docker `ok`, y
el servicio en 8008 respondió HTTP 200. Los previews 8003/8005 no se recrearon.

El 2026-10-08 se actualizó solo el backend a
`anvil/backend:srs-rf27-lifecycle-lease-20261008` para coordinar deploy, stop,
delete, pause, resume, operaciones de nodo y eventos de ejercicio mediante
leases. La copia consistente previa está en
`/tmp/anvil-demo-pre-lifecycle-lease-20261008.db`; la comparación de copias
SQLite antes/después no encontró cambios en los recuentos de filas de las
tablas. El gateway y el volumen no se recrearon. `/api/ready` devolvió DB/Docker
`ok`, el backend quedó healthy y 8003, 8005 y 8009 respondieron HTTP 200.

## Primer arranque

Desde la raíz del repositorio:

```bash
docker build -t anvil/plc-s7:demo-20261008-quality1 -f plc/Dockerfile plc
# Solo para crear la primera cuenta en una base vacía:
export ANVIL_BOOTSTRAP_TOKEN="$(openssl rand -hex 32)"
docker compose -p anvil-demo -f docker-compose.demo.yml up -d --build
```

En una base vacía, crea la cuenta administradora usando el token que generaste en
el entorno del servidor; elige tu propio correo y contraseña. Después, quita el
secreto del entorno y recrea solo el backend para que deje de estar disponible:

```bash
unset ANVIL_BOOTSTRAP_TOKEN
docker compose -p anvil-demo -f docker-compose.demo.yml up -d backend
```

Si el volumen de datos ya contiene una cuenta, inicia sesión con esas credenciales;
el token bootstrap solo sirve para crear la primera cuenta y no permite iniciar
sesión ni recuperar una contraseña.

## Actualizar a una demo nueva

Las imágenes de runtime actuales incluyen
`anvil/plc-s7:demo-20261008-quality1` y
`anvil/scada:demo-20261008-alarm-delay1`. Para actualizar el runtime S7 o SCADA,
construye etiquetas nuevas y cambia en `docker-compose.demo.yml` las referencias
de backend y `ANVIL_ALLOWED_IMAGES`, conservando etiquetas viejas en la allowlist
si hay escenarios existentes que aún las usan. A continuación:

```bash
docker compose -p anvil-demo -f docker-compose.demo.yml up -d --build
```

El runtime SCADA del preview se construye con
`docker build -t anvil/scada:demo-20261008-alarm-delay1 -f plc/Dockerfile.scada ./plc`.

El backend y el gateway se construyen desde el checkout actual; reconstruir no
borra el volumen de datos ni modifica `anvil-phase7-preview` (8003) o
`anvil-srs-preview` (8005).

El backend del preview también puede actualizarse de forma aislada, sin recrear
el gateway ni las imágenes OT:

```bash
docker compose -p anvil-demo -f docker-compose.demo.yml build backend
docker compose -p anvil-demo -f docker-compose.demo.yml up -d --no-deps backend
curl --fail http://localhost:8008/api/health
```

La demo actual incluye edición e historial versionado de plantillas compartidas
y presets de componentes. Para los presets, el backend puede editar título,
descripción y configuración validada, consultar revisiones y restaurar una
versión como una nueva. La imagen se actualizó solo en el servicio backend; el
gateway y el volumen con usuarios/escenarios se conservaron. Antes del cambio
se guardó una copia de SQLite fuera del repositorio en
`/tmp/anvil-demo-pre-component-revisions-20261008.db`. Tras arrancar, `/api/ready`
indicó base y Docker disponibles; OpenAPI expone las rutas de edición, historial
y restauración, y 8003, 8005 y 8009 respondieron HTTP 200.

La demo actual pausa también el run de ejercicio cuando se pausa el escenario:
los eventos pendientes y los cronómetros de objetivos se congelan, y continúan
con el tiempo restante al reanudar.

## Comprobar o parar

```bash
docker compose -p anvil-demo -f docker-compose.demo.yml ps
curl http://localhost:8008/api/health
```

Para detener únicamente el preview, preservando sus datos:

```bash
docker compose -p anvil-demo -f docker-compose.demo.yml down
```

No añadas `--volumes` si quieres conservar escenarios y usuarios de demo.
