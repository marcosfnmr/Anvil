# Solución de problemas

Empieza por el endpoint, estado o log que falle. Conserva el escenario y los
datos hasta entender el error; limpiar el volumen Docker o borrar el escenario
puede destruir evidencia útil.

## La página no carga

```bash
docker compose ps
curl -i http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1:8000/api/ready
docker compose logs --tail=150 backend
```

Comprueba puerto, contenedor backend y gateway. `health=200` pero `ready` fallido
indica que hay que revisar SQLite o comunicación con Docker. Si usas preview,
consulta [su guía](DEMO_PREVIEW.md) y confirma que la URL corresponde al puerto
real; no presupongas que el preview y Compose usan la misma base.

## No puedo entrar

- En una base nueva hace falta crear la primera cuenta con bootstrap habilitado.
- El correo lo eligió quien creó esa cuenta; no hay una cuenta por defecto.
- El token es el valor de `ANVIL_BOOTSTRAP_TOKEN`, solo durante el bootstrap.
- En una base existente se usa una cuenta ya creada. El token no restablece
  contraseñas.
- Un administrador puede restablecer una contraseña; no hay recuperación por
  correo.

Nunca pegues contraseñas o tokens en tickets, chats o documentación.

## Crear/abrir escenario falla

Consulta consola del navegador, respuesta de API, esquema y permisos. Un
`422` suele indicar una dirección duplicada, puerto incompatible, referencia
huérfana, error en configuración o conversión inválida. Un `404` también puede
ser la forma de ocultar un escenario al que la cuenta no tiene acceso. Abre
**Diagnóstico** para advertencias de topología.

## El botón de escribir no está disponible

Comprueba que el escenario está desplegado, el nodo sano, el rol permite
operación, el destino y dirección están seleccionados por la HMI/SCADA y el
punto es una salida autorizada. Las entradas de campo y puntos de solo lectura
no se pueden escribir desde la operación de Anvil. Una mala calidad puede
bloquear una acción.

## El dispositivo aparece `missing` o `error`

Consulta estado de nodo y logs. Verifica la imagen/tag permitidos, salud del
gateway, recursos y red asociada. Si el contenedor desapareció y la red
gestionada persiste, reconstruye solo ese nodo desde el inspector. Si la red
falta, para/reconcilia el escenario según [OPERATIONS.md](OPERATIONS.md); no
recrees redes manualmente con IDs asumidos.

## No hay comunicación S7/Modbus

Comprueba protocolo y puerto del runtime, zona origen/destino y rutas
explícitamente configuradas. Un puerto de protocolo no se publica en el host por
defecto; el hecho de que funcione entre contenedores no significa que funcione
desde el navegador o una máquina externa. Para acceso host, lee
[ACCESS.md](ACCESS.md). Las rutas interzona son flujos limitados, no routing L3.

## El actuador cambia, pero el proceso no

Comprueba las conexiones de proceso y los límites de caudal. Una señal verde
solo muestra el cableado lógico; el camino del fluido tiene su conexión propia.
Verifica que sensor esté en modo `process`, que el puerto/unidad encaje, y que
la bomba esté activa. Las capacidades actuales están simplificadas y no
representan dinámicas hidráulicas completas.

## Los valores dicen `waiting` o `stale`

Revisa despliegue, selección de destino, conexión, dirección y logs del
contenedor. `waiting` puede indicar que aún no existe una muestra; `stale`
indica que no se ha recibido una actualización reciente. La UI debe mostrar la
calidad real; no uses el último valor como si fuera una lectura actual.

## Limpieza de un laboratorio fallido

Primero guarda/exporta el escenario y descarga logs/auditoría. Detén el escenario
desde Anvil. Si el daemon no responde, documenta el error, comprueba las
etiquetas `anvil.managed=true` y `anvil.scenario_id`, y usa la guía de
[operación](OPERATIONS.md). Nunca uses un `docker system prune` general para
resolver un problema de un escenario.
