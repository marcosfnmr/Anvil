# Cuentas, sesiones y permisos

La plataforma activa autenticación en el arranque normal del backend. Los tests o
integraciones que llaman `create_app(initialize_database=False)` pueden desactivarla
explícitamente; ese modo no debe exponerse como servicio compartido.

## Primera cuenta

Antes del primer arranque, configura `ANVIL_BOOTSTRAP_TOKEN` como secreto de al
menos 32 caracteres en el entorno del contenedor (secret manager o archivo `.env`
local excluido de Git). No lo pases como argumento de línea de comandos, no lo
incluyas en escenarios ni en el repositorio. Abre Anvil: si no hay cuentas, muestra
el formulario de configuración inicial. El token se envía una sola vez y solo se
acepta mientras la base de datos no tenga ningún usuario. Después de crear la cuenta
admin, elimina el token del entorno y reinicia el backend. Si no se configuró token,
la página explica cómo habilitar el bootstrap sin crear credenciales por defecto.

La interfaz admin permite crear cuentas, asignar rol, activar/desactivar cuentas,
restablecer contraseñas y consultar/verificar el registro administrativo de cuentas.
El restablecimiento administrativo
requiere una contraseña nueva de al menos 12 caracteres y revoca todas las sesiones
de esa cuenta; el administrador debe entregar la contraseña temporal por un canal
seguro. No existe recuperación de autoservicio por correo. Login y bootstrap se limitan a
diez intentos en una ventana móvil de 60 segundos por origen de red. El contador
atómico se comparte entre workers que usan la misma base SQLite; persiste marcas
temporales ligadas al hash SHA-256 del origen, no la dirección IP.

`GET /api/admin/audit-events` pagina los cambios administrativos y de contraseña;
`GET /api/admin/audit-events/integrity` comprueba la cadena SHA-256. Solo los admins
pueden consultar estas rutas. Los detalles nunca incluyen contraseñas ni hashes de
contraseña; el registro es distinto de la auditoría por escenario y no tiene un
ancla externa contra cambios realizados directamente sobre SQLite.
Si SQLite no está disponible, esas rutas fallan cerradas con HTTP 503. Esta defensa
no coordina réplicas con bases separadas, no mitiga redes distribuidas y no
sustituye protección perimetral.

## Roles

| Rol | Alcance |
|---|---|
| `admin` | Administración de usuarios y acceso a todos los escenarios. |
| `instructor` | Crear, desplegar, operar y administrar acceso a escenarios compartidos con su membresía. |
| `designer` | Crear y editar escenarios propios; solo lectura sobre los compartidos. No puede desplegar ni asignar instructor. |
| `participant` | Ver escenarios asignados y operar únicamente rutas API explícitas de escritura/acknowledgement. No puede desplegar, editar ni exportar PCAP. |

El creador de un escenario es su propietario. Admins e instructores pueden añadir
miembros por correo en `POST /api/scenarios/{id}/members`, cambiar su rol de
escenario o quitar acceso en `DELETE /api/scenarios/{id}/members/{user_id}`. El
endpoint `GET .../members` también requiere ser admin, propietario o instructor.
Los escenarios antiguos sin propietario siguen accesibles a admin; un admin debe
clonarlos/compartirlos con los usuarios correspondientes para el acceso multiusuario.

Las sesiones usan una cookie aleatoria opaca `HttpOnly`, `SameSite=Strict`, con
expiración de ocho horas; la base guarda solo SHA-256 del token. En HTTPS se marca
`Secure` automáticamente. Detrás de un proxy TLS hay que configurar correctamente
el esquema seguro del proxy o `ANVIL_COOKIE_SECURE=true`. Los Compose principal y
de demo pasan esa variable al backend; por ejemplo:

```bash
ANVIL_COOKIE_SECURE=true docker compose up -d
```

En PowerShell usa `$env:ANVIL_COOKIE_SECURE="true"` antes de ejecutar Compose.
No se confía en `X-Forwarded-Proto` directamente: el proxy debe reemplazar sus
cabeceras reenviadas y el operador debe habilitar explícitamente la cookie
segura en este despliegue. Cambiar la contraseña
revoca las demás sesiones; desactivar una cuenta revoca todas. No hay recuperación
de contraseña, MFA, SSO, caducidad por inactividad ni sesiones multi-host.

Las mutaciones con cookie verifican que `Origin` corresponda al host de Anvil. Las
conexiones WebSocket también comprueban Origin y requieren sesión. Las respuestas
usan 404 para escenarios sin acceso, para reducir la exposición de su existencia.
Todas las respuestas HTTP bajo `/api/` incluyen `Cache-Control: no-store`, también
las respuestas públicas de estado, login/bootstrap y los ZIP de evidencia; así
los navegadores y proxies compatibles no deben conservar configuración de
escenario, datos de cuenta o capturas en caché. Esto no controla capturas de
pantalla, descargas guardadas explícitamente ni intermediarios que ignoren la
directiva.
`/api/health`, `/api/ready`, estado/login/bootstrap y assets web son públicos; los
datos y operaciones de escenarios no lo son.

## Despliegue y límites

- Mantén TLS en un proxy inverso administrado si Anvil se comparte fuera de localhost.
- En el arranque normal, el backend intenta restringir el archivo SQLite y sus
  sidecars (`-journal`, `-wal`, `-shm`) al usuario del proceso (modo `0600`) y
  fija una máscara `umask 077` para proteger los archivos que SQLite cree después.
  Es un endurecimiento de permisos del sistema de archivos, no cifrado: root y
  quien controle la cuenta del proceso pueden leer la base. El cambio se aplica
  cuando se inicia una versión del backend que lo incluye; no reescribe el
  contenido de la base. Si el volumen o filesystem no permite cambiar permisos,
  el backend lo registra como advertencia. Protege también el volumen y las
  copias de seguridad, y no las guardes en el repositorio.
- Configura el token bootstrap solo durante la provisión inicial y luego retíralo.
- Esta implementación no sustituye una revisión de seguridad ni es un IdP corporativo.
- La autoridad del Docker API y su impacto potencial en el host siguen siendo una brecha crítica documentada en [seguridad de contenedores](CONTAINER_SECURITY.md).

La API está descrita en `/docs`; endpoints de identidad: `/api/auth/status`,
`/api/auth/bootstrap`, `/api/auth/login`, `/api/auth/me`, `/api/auth/logout`,
`/api/auth/password`, `/api/users`.


## Estación de participante

Añade una estación de participante desde el catálogo. Su opción **Conectar a la red
interna de su zona** está desactivada inicialmente; al activarla, el contenedor se
conecta solo a la red Purdue seleccionada, con IP propia y sin puertos publicados
ni salida a Internet. Para desplegar la imagen de runtime cliente:

```sh
docker build -t anvil/scada:dev -f plc/Dockerfile.scada ./plc
```

Dentro están disponibles los clientes Python `snap7` y `pymodbus`. Si el instructor
activa y guarda **Conectar a la red interna de su zona** para la estación y despliega
el escenario, los roles participant, instructor y admin pueden abrir **Consola de
participante** desde el inspector de esa estación. El terminal queda limitado a una
sesión por estación y hasta 16 sesiones por worker backend. Las reservas se comparten
por la base SQLite, se renuevan cada cinco segundos y caducan tras 20 segundos sin
renovación. Esto coordina workers de una instancia que comparten la misma base, pero
no réplicas con bases separadas. Cada sesión dura como máximo una hora y acepta hasta
4096 bytes por mensaje. La shell se ejecuta como UID 65534 sin privilegios.
Anvil audita apertura y cierre; al cerrar registra también el número de marcos
de entrada, bytes, saltos de línea y duración de la sesión. Esos contadores ayudan
a reconstruir cuándo y cuánto se interactuó con la estación, pero no identifican
qué comandos se ejecutaron. El texto de entrada no se guarda ni se hashea porque
la shell puede recibir contraseñas, tokens u otros secretos. La conexión deja de
estar disponible al detener el escenario o desactivar el acceso opt-in.
