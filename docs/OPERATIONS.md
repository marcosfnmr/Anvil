# Operación de Anvil

## Requisitos

- Docker Engine 28 o posterior y el plugin Docker Compose v2. Engine 28 añade el
  modo `isolated` del bridge interno que retira la dirección gateway del host.
- El usuario operador debe poder ejecutar `docker ps` sin `sudo`, o utilizar
  `sudo` en todos los comandos Docker.
- Acceso de red al puerto 8000 desde el equipo administrador.

Pertenecer al grupo `docker` permite controlar el daemon y debe tratarse como
acceso administrativo a la máquina.

## Historial de escenarios

La primera creación produce la versión 1. Cada cambio de topología que se guarda
crea una revisión si difiere de la anterior. En la interfaz, abre **Versiones**
para revisar su fecha/conteo y restaurar una versión; restaurar requiere que el
escenario esté detenido y guarda el estado reemplazado como una nueva versión.
También se puede consultar por API:

```text
GET  /api/scenarios/{scenario_id}/revisions
GET  /api/scenarios/{scenario_id}/revisions/{revision_id}
POST /api/scenarios/{scenario_id}/revisions/{revision_id}/restore
```

El historial vive en la misma base SQLite que el escenario y se elimina al borrar
este. Las versiones no son ramas; no hay vista de diferencias ni retención
configurable.

## Retención y verificación de auditoría

Por defecto Anvil conserva todos los eventos de auditoría mientras exista el
escenario. Para habilitar una retención de, por ejemplo, 365 días, define en el
`.env` usado por Docker Compose:

```dotenv
ANVIL_AUDIT_RETENTION_DAYS=365
```

Se admiten valores de 1 a 3650 días; `0` o una variable omitida deshabilita la
purga. Reinicia el backend para aplicar el ajuste. El worker evalúa la política
al arrancar y una vez al día. Solo retira el prefijo contiguo más antiguo del log,
conserva un checkpoint SHA-256 para verificar la cadena restante y registra la
purga. No elimina eventos desde el inicio de ejercicios activos, aunque excedan
el plazo configurado.

La API comprueba la cadena sin modificarla:

```text
GET /api/scenarios/{scenario_id}/audit-events/integrity
```

Una respuesta con `valid: false` identifica el primer evento con contenido o
enlace inválido. La cadena detecta modificaciones, pero no impide que un
administrador de SQLite la recalcule; para tener una evidencia externa hace falta
exportar y custodiar el JSONL fuera del host. Borrar un escenario elimina también
su configuración operativa, pero conserva sus eventos/checkpoint y una copia de
propietario/membresías para consultar/verificar la auditoría con los mismos
permisos. El endpoint de escenario y demás recursos devuelven 404; las rutas
`/audit-events` y `/audit-events/integrity` siguen disponibles en su UUID
original. La exportación JSONL mantiene su restricción existente para
participant. La política de retención también alcanza estos archivos.

## Intercambio de escenarios

La descarga JSON de un escenario usa un sobre versionado:

```json
{
  "format": "anvil-scenario",
  "schema_version": 2,
  "scenario": {
    "name": "Ejemplo",
    "network": { "subnet": "192.0.2.0/24" },
    "devices": [],
    "network_connections": [],
    "field_objects": [{ "object_id": "tank-1", "type": "tank", "name": "Tanque" }]
  }
}
```

Usa `GET /api/scenarios/{scenario_id}/export` para obtenerlo y
`POST /api/scenarios/import` para importarlo. La versión actual es la 2; también
se acepta la versión 1 y se migra explícitamente a la 2. Esa transición añade
`network_connections: []` cuando el documento antiguo no incluye el campo. La
migración trabaja sobre una copia y no cambia archivos ni escenarios ya
guardados. El importador también acepta el formato antiguo sin sobre, tratándolo
como versión 1. Todos los formatos rechazan propiedades desconocidas para evitar
pérdidas silenciosas; versiones futuras reciben HTTP 422 hasta que exista una
migración explícita.

## Verificar un bundle de evidencias

`evidence-bundle.zip` incluye `manifest.json` con un inventario SHA-256 y tamaño
de cada archivo del ZIP, excluyendo al propio manifiesto. El backend proporciona
`services.evidence_export.verify_evidence_bundle` para comprobar hashes, tamaños,
archivos omitidos y entradas duplicadas. La verificación detecta daños o cambios
en los archivos respecto al manifiesto; para autenticar también el manifiesto,
guarda su hash o firma en un canal confiable separado del ZIP.

También puedes verificar un ZIP descargado desde la línea de comandos, sin tener
Anvil en ejecución. Ejecuta desde la raíz del repositorio:

```bash
python3 integration/verify_evidence_bundle.py /ruta/al/anvil-evidence.zip
```

El comando devuelve JSON con `valid`, el número de archivos comprobados y el hash
SHA-256 del manifiesto. Sale con código `0` si el inventario coincide y `2` si el
ZIP no se puede leer o hay alguna discrepancia. Para comparar además un hash
conservado por otro canal confiable, añade `--expected-manifest-sha256`:

```bash
python3 integration/verify_evidence_bundle.py /ruta/al/anvil-evidence.zip \
  --expected-manifest-sha256 '<hash-de-64-caracteres-guardado-por-separado>'
```

Una verificación sin esa opción demuestra consistencia con el manifiesto incluido,
pero no autentica quién creó el ZIP. El hash tampoco es una firma digital; para
verificar autoría se necesitaría una firma y gestión de claves confiables.

## Primer despliegue

```bash
docker build -t anvil/plc-s7:dev ./plc
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
docker build -t anvil/egress-proxy:dev -f plc/Dockerfile.egress ./plc
mkdir -p data
docker compose up -d --build
curl --fail http://127.0.0.1:8000/api/ready
```

## Presupuestos de recursos por escenario

Antes de crear redes, el backend suma los límites de CPU y memoria que asignará
a los contenedores y rechaza planes que superen el presupuesto. Por defecto, un
escenario admite como máximo 64 runtime nodes, 4 GiB de límites de memoria y 12
CPU (nano-CPU Docker). Los límites de cada contenedor siguen aplicándose además
de este presupuesto.

Se pueden ajustar al host en el archivo `.env` de Docker Compose y reiniciar
Anvil:

```dotenv
ANVIL_SCENARIO_MAX_NODES=64
ANVIL_SCENARIO_MAX_MEMORY_BYTES=4294967296
ANVIL_SCENARIO_MAX_NANO_CPUS=15000000000
```

Son techos de límites configurados para un escenario, no reservas de capacidad
ni coordinación entre escenarios simultáneos. El valor predeterminado de 15
CPU-quota permite el perfil medido de 30 nodos S7/Modbus (15 CPU-quota sumadas),
incluso en un host que expone 12 CPU: Docker reparte el tiempo físico disponible
y cada contenedor sigue limitado individualmente. Si muchos PLC ejecutan lógica
intensiva a la vez, competirán por CPU y sus ciclos pueden retrasarse; ajusta el
techo a la capacidad que quieras ofrecer y deja margen para backend, host y otros
escenarios. Una topología con gateways internos también cuenta esos nodos
runtime. Este presupuesto se aplica por escenario, no globalmente.

## Actualización segura

1. Detener desde la interfaz todos los escenarios en ejecución.
2. Crear una copia de la base de datos.
3. Descargar el código y reconstruir las imágenes.
4. Comprobar readiness y logs.

```bash
mkdir -p backups
docker compose stop backend
cp data/anvil.db backups/anvil-before-upgrade.db
git pull --ff-only
docker build -t anvil/plc-s7:dev ./plc
docker build -t anvil/modbus-tcp:dev -f plc/Dockerfile.modbus ./plc
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
docker build -t anvil/egress-proxy:dev -f plc/Dockerfile.egress ./plc
docker compose up -d --build
curl --fail http://127.0.0.1:8000/api/ready
docker compose logs --tail=100 backend
```

Si la actualización falla, detener el backend, restaurar la copia de SQLite y
volver al commit anterior.

## Reconciliación

En cada arranque se comparan los estados guardados con los contenedores reales.
`ANVIL_CLEANUP_ORPHANS=true` habilita la retirada automática de recursos
huérfanos. La limpieza solo actúa sobre recursos con las etiquetas
`anvil.managed=true` y `anvil.scenario_id`.

Desactívala en despliegues temporales que compartan daemon Docker con otra
instancia de Anvil:

```yaml
environment:
  ANVIL_CLEANUP_ORPHANS: "false"
```

La parada y la limpieza opcional de huérfanos intentan retirar el resto de los
recursos aunque falle una eliminación. Si Docker rechaza una operación, Anvil
identifica el contenedor o la red afectada; el resultado de reconciliación
conserva los conteos parciales y aun actualiza el estado de los escenarios
persistidos. La limpieza general de huérfanos no se reintenta automáticamente:
corrige el problema del daemon y reinicia el backend con
`ANVIL_CLEANUP_ORPHANS=true` para repetirla. Las intenciones `recreate` de nodos
sí se reintentan desde el journal del reconciliador.

## Diagnóstico

```bash
docker compose ps
docker compose logs --tail=200 backend
docker ps --filter label=anvil.managed=true
docker network ls --filter label=anvil.managed=true
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/ready
```

Un escenario `error` puede detenerse desde la interfaz para retirar sus
recursos y desplegarse de nuevo después de corregir la configuración.

Si un solo contenedor gestionado ha desaparecido y el escenario sigue activo,
selecciona ese nodo en el inspector y usa **Reconstruir nodo ausente**. Anvil
vuelve a crear únicamente ese contenedor a partir del plan guardado y lo conecta
a las redes existentes etiquetadas para el escenario. La acción espera su
healthcheck y queda registrada en auditoría. Si falta una red de la zona, la
reconstrucción se rechaza y no crea ni modifica redes. Las operaciones de nodo
guardan intención y resultado en `scenario_node_operations` antes y después de
actuar sobre Docker. Startup y el reconciliador periódico reintentan las entradas
`pending` bajo el mismo lease de escenario; las operaciones convergen al estado
pedido y una entrada completada no se repite. `GET /api/scenarios/{id}/node-operations`
devuelve las más recientes (hasta 200), con acción, estado, intentos y último
error; solo el instructor o administrador puede consultarlas. Si el escenario
queda `partial` y conserva otro runtime activo, el reconciliador detecta el
contenedor ausente, registra `recreate` en el journal y lo procesa bajo lease.
El reconciliador también reconstruye redes Docker internas que falten a partir
del plan runtime persistido y después recrea los contenedores ausentes, siempre
que haya intención activa. `running`/`partial` la conservan; para `error`, la
última transición auditada debe venir de un estado activo. Un error de despliegue
(`deploying → error`) y un escenario guardado como `stopped` no se relanzan.
Solo se crean redes con las etiquetas exactas del escenario; si el nombre o la
subred colisiona con recursos ajenos, Anvil los preserva y el operador debe
resolver la colisión. Consulta la [matriz SRS](SRS_TRACEABILITY.md) para
cobertura y validación Docker.
Para serializar el ciclo de vida, Anvil usa un lease SQLite por
escenario: deploy, stop, restart, delete, pause, resume y operaciones de nodo lo adquieren
antes de actuar y lo renuevan cada 10 segundos mientras realizan trabajo
externo. Los eventos temporizados del motor de ejercicios adquieren el mismo
lease; si está ocupado, el evento se conserva para el siguiente tick.
Un worker no marca como incompleto un despliegue que otro worker todavía está
ejecutando. Si el backend muere mientras el estado es `deploying`, al expirar el
lease el reconciliador limpia los contenedores y redes gestionados de ese
escenario; un fallo de Docker deja la operación pendiente para reintentarlo cada
10 segundos. No borra los volúmenes de retención. Comprueba el log del backend
para `Runtime reconciliation` y el estado del escenario; una limpieza que siga
fallando requiere resolver primero la causa Docker. Un fallo de Docker en una
operación de nodo se conserva como pendiente y se reintenta cada 10 segundos;
revisa `node-operations` si permanece pendiente. El reinicio usa la identidad,
contador y hora de inicio observados antes de la operación para distinguir un
reinicio aún no ejecutado de uno que Docker ya aplicó. El borrado también toma el lease
exclusivo antes de quitar datos de retención; la eliminación de ese lease y de
la fila del escenario se confirma en una misma transacción, para que un deploy
concurrente no pueda empezar sobre un escenario que se está borrando.

Los contenedores de escenarios ejecutan con usuario no root, capacidades
reducidas, filesystem raíz de solo lectura y límites de recursos. La prueba
reproducible está en [confinamiento de contenedores](CONTAINER_SECURITY.md).

## Exposición de red

La plataforma incluye autenticación/RBAC, publicación de puertos OT optativa y
un gateway con allowlist para las operaciones Docker del backend en Compose. Eso
no sustituye el endurecimiento perimetral: antes de exponerla fuera de una VM o
LAN controlada, configura TLS, firewall, acceso autenticado restringido y
monitorización. El gateway Docker sigue hablando con un daemon cuyo socket le
confiere privilegios muy altos; para reducir el impacto se puede usar Docker
rootless (ver `CONTAINER_SECURITY.md`), todavía pendiente de validación en todos
los smokes de esta instalación.

El reenvío opcional de S7/Modbus escucha en `127.0.0.1` por defecto; para un modo
sustituto, se puede configurar una IPv4 concreta de la interfaz del host. No se
acepta `0.0.0.0` y Anvil no instala reglas de firewall ni restringe IP origen.
Valida el acceso desde otra máquina en una red controlada antes de depender de
ese modo. Consulta
[la guía de acceso controlado](ACCESS.md) para usar un túnel SSH o un proxy
externo autenticado, con TLS y restringido a la red autorizada.

## Ejecutar un escenario fuera de Anvil

Selecciona un escenario guardado y pulsa **Paquete Docker**. El navegador descarga
un ZIP con `docker-compose.yml`, `scenario.json`, `README.txt`, `images.txt`,
`.env`, `network-addresses.json`, `readdress.py` y los scripts de imágenes. El paquete sigue sin incluir las
capas Docker: ejecuta `./export-images.sh` en una máquina que tenga las imágenes.
Genera `anvil-images.tar` junto con `anvil-images.lock`, que registra los IDs
inmutables de las capas exportadas. Transfiere ambos al host destino. Allí:

```bash
unzip anvil-<id>-deployment.zip -d anvil-deployment
cd anvil-deployment
chmod +x export-images.sh check-images.sh
python3 readdress.py --zone default=10.250.0.0/24
docker load -i /ruta/transferida/anvil-images.tar
cp /ruta/transferida/anvil-images.lock .
./check-images.sh
docker compose config
docker compose up -d
docker compose ps
docker compose down
```

`check-images.sh` compara cada referencia con el ID registrado y falla si falta una
imagen o la etiqueta apunta a otra versión. Compose usa `pull_policy: never`, por
lo que no sustituye las imágenes por descargas del registry. El bundle conserva el aislamiento de red interno y los límites de los contenedores.
Los puertos no se publican por defecto. El modo local usa `127.0.0.1`; el modo
sustituto permite una IP concreta del host y requiere firewall externo para
limitar las estaciones de origen.
No incluye capas de imagen, backend, SQLite ni datos vivos de proceso; copiar el
ZIP a otro host no traslada esos elementos. Valida `docker compose config` antes de
arrancar si cambiaste etiquetas de imagen o versión de Compose.

El `.env` del bundle no contiene secretos. Si el escenario original va a seguir
desplegado en el mismo host o alguna subred está ocupada, ejecuta
`readdress.py --zone ZONE_ID=IPv4_CIDR` antes de `docker compose config`. Repite
`--zone` para cada zona que deba cambiar. El helper mantiene los offsets de IP,
rechaza redes solapadas y actualiza `.env` de forma atómica. Si un puerto host
publicado ya está ocupado, cambia su variable `ANVIL_BUNDLE_HOST_PORT_*` en ese
archivo.

En el primer despliegue de un escenario, Anvil resuelve los tags configurados a
IDs Docker inmutables y añade una referencia local `anvil-pinned/…` para poder
recrear nodos aunque luego se mueva el tag original. La referencia y el ID quedan
en el plan runtime persistido; la política del gateway vuelve a comprobar ambos
antes de crear el contenedor. Un bundle descargado después del despliegue conserva
la referencia fijada. Si quieres actualizar la imagen de un escenario existente,
deténlo, vuelve a guardar su configuración desde la interfaz/API y despliega de
nuevo: esa edición resuelve el tag configurado actual. Los tags `anvil-pinned/…`
son etiquetas adicionales locales; Anvil no las borra automáticamente.
