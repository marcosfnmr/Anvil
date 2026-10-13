# Instalación y primer arranque

Esta guía lleva desde un checkout limpio hasta el formulario de inicio de
sesión. Para operación continua, lee después [Operaciones](OPERATIONS.md) y
[Cuentas y permisos](PLATFORM_ACCESS.md).

## Requisitos

- Git.
- Docker Engine **28 o posterior** y Docker Compose v2.
- Acceso del usuario operador al daemon Docker.
- Puertos disponibles: 8000 para la interfaz predeterminada y los puertos de
  runtime que se quieran publicar explícitamente.

La versión mínima de Docker importa: las redes con gateway `isolated` se usan
para evitar que el host tenga una dirección dentro de la zona. No ejecutes la
instancia en un equipo con servicios OT de producción.

## Obtener el código y construir runtimes

```bash
git clone https://github.com/marcosfnmr/Anvil.git
cd Anvil
docker build -t anvil/plc-s7:dev ./plc
docker build -t anvil/modbus-tcp:dev -f plc/Dockerfile.modbus ./plc
docker build -t anvil/field:dev -f plc/Dockerfile.field ./plc
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
```

El Compose predeterminado configura las imágenes runtime con nombres `:dev`.
Si se construye otro tag, actualiza el entorno Compose para que el backend y su
gateway autoricen la misma etiqueta. Las reglas de cada familia están descritas
en las guías [S7](DEVICE_FIDELITY.md), [Modbus](MODBUS.md),
[campo](FIELD_OBJECTS.md) y [SCADA](SCADA.md).

## Primera cuenta y arranque

Genera el secreto de bootstrap en la terminal. No lo escribas en el repositorio,
en un escenario o en una captura:

```bash
export ANVIL_BOOTSTRAP_TOKEN="$(openssl rand -hex 32)"
docker compose up -d --build
curl --fail http://localhost:8000/api/ready
```

Abre <http://localhost:8000/>. En una base sin usuarios, Anvil pide correo,
contraseña y ese token. El correo es la identidad que tú eliges para la cuenta;
el token es el valor de `ANVIL_BOOTSTRAP_TOKEN` generado en el paso anterior.
No se envía por correo. El bootstrap solo sirve para crear la primera cuenta.

Después de crear el administrador, retira el token del entorno y recrea el
backend para que no siga disponible:

```bash
unset ANVIL_BOOTSTRAP_TOKEN
docker compose up -d backend
```

En una base existente, inicia sesión con una cuenta creada por un administrador.
Si olvidaste la contraseña, Anvil no envía un enlace de recuperación. Un admin
puede restablecerla desde la administración de cuentas; para más información,
consulta [cuentas, roles y sesiones](PLATFORM_ACCESS.md).

## Comprobar salud

```bash
docker compose ps
curl --fail http://localhost:8000/api/health
curl --fail http://localhost:8000/api/ready
```

`health` indica que el proceso HTTP responde. `ready` también comprueba la base
SQLite y la conexión al gateway Docker. OpenAPI está en
<http://localhost:8000/docs>.

## Parar sin borrar los datos

Detén primero los escenarios desde Anvil y después para el backend:

```bash
docker compose down
```

No añadas `--volumes` si quieres mantener la base de datos. Las redes y
contenedores de escenario tienen su propio ciclo de vida y se deben detener
desde el panel. La guía de [operación](OPERATIONS.md) detalla actualización,
copias y diagnóstico.

## Alternativa: preview de demo

Este checkout también puede tener un preview aislado en un puerto distinto del
stack de desarrollo. Consulta [DEMO_PREVIEW.md](DEMO_PREVIEW.md) para saber qué
build está publicado localmente; no presupongas que comparte base o versión con
el Compose predeterminado.
