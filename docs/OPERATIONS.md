# Operación de Anvil

## Requisitos

- Docker Engine y el plugin Docker Compose.
- El usuario operador debe poder ejecutar `docker ps` sin `sudo`, o utilizar
  `sudo` en todos los comandos Docker.
- Acceso de red al puerto 8000 desde el equipo administrador.

Pertenecer al grupo `docker` permite controlar el daemon y debe tratarse como
acceso administrativo a la máquina.

## Primer despliegue

```bash
docker build -t anvil/plc-s7:dev ./plc
mkdir -p data
docker compose up -d --build
curl --fail http://127.0.0.1:8000/api/ready
```

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

## Exposición de red

La versión actual está diseñada para una VM o una LAN de laboratorio. Antes de
exponerla fuera de ese entorno se debe añadir autenticación, TLS, filtrado de
red y una capa de acceso restringido al Docker API.
