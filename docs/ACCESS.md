# Acceso controlado a dispositivos OT

Anvil mantiene cada escenario en su propia red Docker y no publica puertos del host por defecto. El inspector/API permiten solicitar `external_access=true`; el despliegue crea un relay dedicado y un binding en el host. En Docker Engine 29.8.1, el smoke de bundle Compose confirmó desde Linux del host lectura/escritura de DB S7 con Snap7 y lectura/escritura de coil Modbus con PyModbus. Una sonda adicional ejecutada con Python estándar de Windows confirmó que el forwarding local de WSL alcanza el endpoint S7 hasta el handshake COTP y que Modbus TCP permite leer y escribir la coil en ambos estados. Esto no prueba desde Windows la lectura/escritura S7 de un DB: esa parte solo se comprobó desde Linux. Otra máquina y publicación en una interfaz de red siguen sin verificar. Mantén el binding en loopback para uso local.

- S7: `127.0.0.1:102` hacia TCP/102 del PLC por defecto. El puerto local del host puede cambiarse, por ejemplo `127.0.0.1:1102` hacia TCP/102.
- Modbus TCP: el puerto del contenedor es el configurado en el dispositivo; el puerto local del host se elige por separado.

El relay de ingreso es un contenedor por dispositivo expuesto: escucha en un bridge Docker y reenvía un único puerto S7 o Modbus a una dirección de la zona declarada. Anvil solo publica el puerto del relay; el PLC/Modbus conserva su red OT interna y no recibe un binding directo. El binding del host queda limitado a la IPv4 configurada, `127.0.0.1` por defecto. El relay acepta como peer únicamente la IP gateway del bridge Docker; los demás clientes que lleguen al listener se rechazan. En el despliegue administrado Anvil obtiene el gateway del Engine y lo inyecta. Un bundle Compose crea un bridge uplink exclusivo del proyecto, lo elige como gateway por defecto y el relay lee su IP desde la tabla de rutas.

El ZIP de despliegue exporta el relay y la publicación cuando el escenario tiene `external_access=true`. El bundle se ejecuta sin el backend de Anvil, pero sí requiere que las imágenes enumeradas estén cargadas en Docker. Los nodos PLC/Modbus no publican puertos. Cada relay conecta su bridge uplink de proyecto con la red privada del escenario.

El relay limita a 64 las sesiones simultáneas. Si el límite está lleno, cierra
la conexión nueva sin crear otro hilo; al terminar una sesión libera su plaza.
No registra cada bloque reenviado. El Docker log driver rota los logs a 10 MB y
conserva hasta tres ficheros, también en los bundles Compose exportados. Estos
límites acotan hilos y logs del contenedor, pero no sustituyen el firewall del
host, una cuota de conexiones del host ni una defensa volumétrica de red.

El preflight del despliegue muestra una advertencia por cada publicación activa,
con IP y puerto host y puerto OT destino. Aunque recomienda loopback para
clientes locales y un firewall externo para una IPv4 de interfaz, esa advertencia
no verifica conectividad por sí sola ni sustituye pruebas desde cada tipo de cliente.

**Alcance de la verificación:** `external_bundle_smoke.py` levantó el ZIP Compose
en Docker Engine 29.8.1, verificó desde Linux del host escritura/lectura de DB
S7 con Snap7 y coil Modbus con PyModbus, confirmó que solo los relays publican
puertos y rechazó un peer de prueba conectado al uplink. En la repetición del
2026-10-08 también ejecutó una sonda de biblioteca estándar con Python de Windows:
el cliente estableció el handshake COTP en S7 y leyó/escribió la coil Modbus a
`false` y `true`, con lectura posterior de ambos estados. La sonda Windows no
envía una petición S7 de lectura/escritura DB; no se debe inferir esa cobertura.
El primer intento de runtime reveló que Docker no admite aliases Compose en su
bridge especial `bridge`; el bundle crea ahora una red uplink de proyecto. El
gateway Compose de preview no se reinició. No se afirma acceso desde otra máquina
ni publicación en una interfaz LAN. Un binding o healthcheck por sí solo no
demuestra conectividad.

## Acceso remoto: pendiente de verificación

Los comandos siguientes muestran una forma prevista de transportar el binding
local. El gateway temporal y el cliente local están verificados; falta probar
el túnel y el cliente desde la red remota de destino.

Cuando el cliente remoto y el túnel estén validados desde la red de destino, se
podrá usar un túnel SSH autenticado hacia el host Docker (ejemplo Modbus en
loopback:1502):

```bash
ssh -N -L 1502:127.0.0.1:1502 operador@host-anvil
```

El cliente Modbus de la estación se conecta a `127.0.0.1:1502`. Para S7, sustituye ambos puertos por `102`. El host debe permitir únicamente SSH desde estaciones administradoras; restringe usuarios y llaves SSH con la política de la organización.

Para modo sustituto en una red OT, configura `IP de escucha del host` con la IPv4 concreta de la interfaz que debe recibir conexiones y aplica en el firewall del host una allowlist de las estaciones maestras autorizadas. El gateway Docker valida la IP/puerto host y el puerto interno del protocolo como valores separados; no admite `0.0.0.0`. Anvil no implementa autenticación de protocolo, TLS ni allowlists de direcciones origen, por lo que este modo solo debe usarse en una red de laboratorio controlada. Un proxy de acceso compartido debe terminar TLS/autenticación y reenviar al bind interno según la política del operador.

Al desactivar `external_access` y desplegar de nuevo, el plan nuevo no debería
crear el relay. Comprueba etiquetas y bindings Docker si limpias recursos de una
prueba interrumpida.

## Diagnosticar la publicación loopback

`integration/external_protocol_probe.py` conserva un diagnóstico reproducible.
Construye imágenes locales, crea un escenario temporal con binds loopback e
intenta leer/escribir con Snap7/PyModbus desde el host. El probe directo y una
ejecución con el manager apuntando a una instancia temporal de `DockerGate`
pasaron con el checkout actual. Si se proporciona `ANVIL_WINDOWS_NODE`, también
intenta el protocolo desde un `node.exe` de Windows; ese camino no se ha
repetido tras el ajuste. Al finalizar, el probe limpia su escenario temporal y
red.

```bash
python3 -m venv /tmp/anvil-access-test-env
/tmp/anvil-access-test-env/bin/python -m pip install -r backend/requirements-dev.lock -r plc/requirements.lock -r plc/requirements.modbus.lock
docker build -t anvil/plc-s7:host-access-test ./plc
docker build -t anvil/modbus-tcp:host-access-test -f plc/Dockerfile.modbus ./plc
docker build -t anvil/protocol-ingress:host-access-test -f plc/Dockerfile.ingress ./plc
ANVIL_PLC_IMAGE=anvil/plc-s7:host-access-test \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:host-access-test \
ANVIL_INGRESS_IMAGE=anvil/protocol-ingress:host-access-test \
PYTHONPATH=backend /tmp/anvil-access-test-env/bin/python integration/external_protocol_probe.py
```

Para verificar el bundle standalone, construye/carga las imágenes anteriores
en el mismo Engine y ejecuta:

```bash
ANVIL_PLC_IMAGE=anvil/plc-s7:host-access-test \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:host-access-test \
ANVIL_INGRESS_IMAGE=anvil/protocol-ingress:host-access-test \
PYTHONPATH=backend /tmp/anvil-access-test-env/bin/python integration/external_bundle_smoke.py
```

El smoke exporta el escenario directamente desde el modelo, valida el YAML,
levanta un proyecto Compose temporal, prueba S7 y Modbus desde loopback, intenta
un acceso desde un peer no autorizado y ejecuta `docker compose down` para ese
proyecto al finalizar. Requiere Docker Compose v2 con soporte para `gw_priority`
y puertos loopback libres.

Para repetirlo a través de un gateway temporal de prueba ya iniciado en
`127.0.0.1:12375`, añade `DOCKER_HOST=tcp://127.0.0.1:12375` a la ejecución y
configura ese gateway con las tres imágenes anteriores en su allowlist. Si el
gateway deniega inventarios Docker sin filtro, elige un `/24` libre después de
revisar las redes del Engine y pásalo como `ANVIL_TEST_SUBNET`; por ejemplo
`10.247.180.0/24`. El probe elimina al acabar los contenedores y la red que llevan
su UUID de escenario. No uses `ANVIL_KEEP_EXTERNAL_PROBE=1` salvo que necesites
conservar ese escenario para diagnóstico.

Para repetir el smoke con la prueba Windows, define `ANVIL_WINDOWS_PYTHON` con la
ruta WSL al `python.exe` de Windows (la sonda solo usa la biblioteca estándar):

```bash
ANVIL_WINDOWS_PYTHON='/mnt/c/Users/usuario/AppData/Local/Programs/Python/Python314/python.exe' \
ANVIL_PLC_IMAGE=anvil/plc-s7:host-access-test \
ANVIL_MODBUS_IMAGE=anvil/modbus-tcp:host-access-test \
ANVIL_INGRESS_IMAGE=anvil/protocol-ingress:host-access-test \
PYTHONPATH=backend python3 integration/external_bundle_smoke.py
```

La ruta es un ejemplo; usa el intérprete disponible en tu equipo. Sin esa
variable se ejecutan las comprobaciones Linux y el aislamiento, pero se omite la
sonda Windows. No abras el bind a una interfaz de red para ampliar el alcance sin
validar firewall y origen. La aceptación desde otra máquina con una allowlist de
firewall sigue pendiente.
