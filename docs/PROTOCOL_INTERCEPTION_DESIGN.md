# Diseño: intercepción controlada de Modbus TCP en ejercicios

## Estado

El relay de runtime, su activación desde la cronología y la auditoría durable de
las peticiones/respuestas modificadas están implementadas. El primer flujo end-to-end está
verificado con scheduler, estación participante, lectura real y objetivo
auditado; RF-38 continúa parcial por los demás incidentes del SRS y porque la
ventana se expresa con eventos set/clear, sin una propiedad de duración dedicada.
`protocol_write` sigue siendo una escritura normal desde una estación
participante; no intercepta por sí sola el tráfico de otro cliente. El nuevo
evento `modbus_interception` activa o limpia reglas en una ruta inspeccionable.

El contenedor `tcp-gateway` puede inspeccionar framing MBAP para una ruta TCP
explícita cuyo destino sea un servidor Modbus TCP. Correlaciona FC01/FC02 y
FC03/FC04/FC23 por Transaction ID y sustituye un bit o registro exacto de la
respuesta; en FC23 la petición,
incluida su parte de escritura, pasa sin cambios. Para FC06 sustituye
el valor de una única petición de escritura holding-register, para FC16 puede
sustituir registros seleccionados dentro de una petición de escritura múltiple,
y para FC15 puede sustituir coils seleccionadas dentro de una petición múltiple.
Para FC22 fuerza un UINT16 exacto con AND mask `0x0000` y OR mask igual al valor
configurado, conservando la dirección y validando el eco completo del servidor.
El gateway preserva el resto de valores y la respuesta echo del servidor
confirma la dirección/rango aplicado. Las reglas se cargan y limpian
por un socket Unix dentro del contenedor; un token
aleatorio autentica el comando fijo `tcp_route_control.py`, que el Docker Gate
limita a un contenedor gateway Modbus, UID 65534, sin stdin ni shell. No existe
listener de control de red. La prueba unitaria cubre parser, intercalado,
framing y política Docker; `integration/modbus_interception_smoke.py` crea
cliente, gateway y servidor en dos redes internas, comprueba sustitución y
restauración, valor original persistente, bloqueo directo entre zonas y cero
puertos publicados. `integration/exercise_smoke.py` comprueba FC16 con un
cliente PyModbus desde la estación participante y confirma que solo cambia el
registro seleccionado en una escritura de dos registros. El smoke Docker
`modbus_interception_smoke.py` también verifica FC15: el cliente envía una coil
inactiva, el relay la cambia a activa y el servidor la conserva, con evidencia
durable ligada al ejercicio.

El modelo del evento rechaza campos extra, exige reglas únicas y una acción
clear posterior para cada acción set; una regla debe corresponder al Unit ID,
función y punto declarado del servidor. FC03, FC06, FC16, FC22 y FC23 solo se admiten para
holding registers existentes; FC04 para input registers; FC01/FC05/FC15 para
coils y FC02 para discrete inputs. FC01/FC02/FC05/FC15 solo aceptan reemplazos
lógicos 0/1; FC05 se codifica en la trama
como `0x0000` o `0xFF00`. FC22 solo admite reemplazos UINT16.
El scheduler aplica las acciones programadas y audita de forma durable
activación y limpieza. Cancelar, completar o fallar el run intenta limpiar
cualquier regla activa. La línea temporal ofrece
instantes de inicio/fin explícitos mediante eventos set/clear, no una propiedad
dedicada de duración. Cada mensaje realmente modificado entra en una cola
SQLite durable y acotada en el volumen del gateway, vinculada al ejercicio,
evento, ruta y regla. El colector la incorpora a la cadena de auditoría como
security.modbus_interception_match, sin almacenar valores ni tramas; los
objetivos audit_event pueden filtrar por esos identificadores.
integration/modbus_interception_smoke.py verifica en Docker la lectura real
alterada y la evidencia durable asociada. integration/exercise_smoke.py cubre
además el flujo combinado: el scheduler aplica set/clear, la estación lee el
valor cambiado y el colector entrega evidencia antes de puntuar el objetivo.
integration/exercise_builder_ui.cjs cubre por separado la edición y programación
de eventos desde Brave.

## Objetivo y alcance actual

Permitir que un ejercicio habilite temporalmente una ruta inline entre una
estación participante y un servidor Modbus TCP del mismo escenario. El proxy
puede sustituir respuestas de lectura FC01/FC02/FC03/FC04/FC23 sin cambiar el
estado real del bit o registro. También puede cambiar valores de peticiones de
escritura FC05/FC06/FC15/FC16/FC22 antes de que lleguen al servidor. Las
escrituras modificadas quedan en el datastore y pueden propagarse
al proceso si los puntos están conectados. Así el ejercicio puede observar
tanto una lectura falseada como una orden alterada.

El alcance actual cubre FC01 (lectura de coils), FC02 (lectura de discrete
inputs), FC03 (respuesta de lectura de holding register), FC04
(respuesta de lectura de input register), FC23 (solo la respuesta de su rango de
lectura; su petición combinada pasa sin cambios), FC06 (petición de escritura de un
holding register), FC16 (petición de escritura de varios holding registers), FC05 (petición de
escritura de una coil), FC15 (petición de escritura de varias coils) y FC22
(petición Mask Write Register). Cada regla
identifica una dirección base cero, Unit ID y valor; FC01/FC02/FC05/FC15 limitan
el valor lógico a 0/1 y los demás códigos modifican UINT16. FC01/FC02 admiten
hasta 2000 bits por lectura y solo sustituyen posiciones seleccionadas del
payload LSB-first, conservando los bits vecinos y el padding. FC05 valida y conserva el
formato de cable `0x0000`/`0xFF00`. FC22 fuerza el registro con AND mask `0x0000`
y OR mask igual al reemplazo. La ruta fija origen y destino. FC16 admite hasta 123
registros y FC15 hasta 1968 coils; solo se sustituyen direcciones con reglas y
los demás valores/bits pasan sin cambios. No se inspeccionan otras conversaciones.
Otras funciones no descritas aquí no se reescriben.

## Flujo esperado

1. El diseñador crea una ruta Modbus TCP explícita de la estación participante
   al servidor del mismo escenario, con el puerto real del servidor.
2. El escenario valida que la estación tenga acceso opt-in, el destino sea un
   servidor Modbus existente y la ruta una conexión cruzada de zonas declarada.
3. El ejercicio declara una regla exacta de lectura FC01/FC02/FC03/FC04/FC23 o escritura FC05/FC06/FC15/FC16/FC22
   con un evento `set` y un evento `clear` posterior en la cronología. No puede
   aportar comandos, expresiones, código ni destinos arbitrarios.
4. Durante la ventana, el cliente conecta al listener de esa ruta. El gateway
   abre la conexión upstream al único servidor configurado.
5. El proxy conserva el framing MBAP y el Transaction ID. Para FC01/FC02 cambia
   solo el bit seleccionado en la respuesta y conserva bits vecinos y padding.
   Para FC03/FC04/FC23 correlaciona la solicitud pendiente con la respuesta y
   sustituye solo los
   dos bytes del registro cuya dirección coincide. En FC23 la petición entera
   pasa sin cambios: el servidor aplica sus registros escritos y solo se cambia
   el valor del registro leído que coincida con la regla. Para FC06 cambia solo los
   dos bytes de valor. Para FC16 cambia solo los dos bytes de datos de cada
   dirección coincidente, sin tocar dirección inicial, cantidad, byte count,
   Unit ID, Transaction ID ni los registros no seleccionados. El servidor
   recibe y almacena esos valores. Para FC15 cambia solo los bits de coils
   seleccionadas (LSB-first), preserva los demás bits y el padding, y valida el
   eco de dirección/cantidad. Para FC22 el eco debe repetir dirección y ambas
   máscaras reescritas. Los demás bytes y funciones permanecen sin cambios.
6. Al ejecutar `clear`, cancelar el ejercicio o terminarlo, el proxy reenvía
   respuestas sin modificar. La ruta continúa operativa hasta que el ejercicio
   la interrumpa o el escenario pare.
7. La auditoría durable registra activación y limpieza, además de cada regla
   cuyo mensaje modificado se reenvía. El evento identifica dirección request o
   response, función, Unit ID, registro, ruta y ejercicio; no registra el valor
   original/sustituido ni tramas completas.

    FC03/04: cliente -- lectura --> gateway --> servidor
             cliente <-- respuesta alterada -- gateway <-- respuesta original
    FC06/16: cliente -- escritura original --> gateway (sustituye valor(es)) --> servidor
             cliente <-- eco de dirección/rango -- gateway <-- eco del servidor

La conexión en la ruta debe configurarse explícitamente en el cliente para que
atraviese el proxy. No se promete redirección transparente de sockets,
suplantación ARP, envenenamiento de DNS ni captura/modificación de tráfico que
no use esa ruta.

## Modelo de datos vigente

La API acepta una secuencia de eventos de este tipo:

    {
      "event_id": "alterar-presion",
      "at_seconds": 15,
      "kind": "modbus_interception",
      "route_id": "ruta-estacion-modbus",
      "action": "set",
      "rules": [{
        "rule_id": "presion-alterada",
        "unit_id": 1,
        "function_code": 3,
        "address": 12,
        "replacement_value": 62000
      }]
    }

El fin se representa con otro evento `modbus_interception` de la misma ruta,
`action: "clear"`, sin `rules` y con un `at_seconds` posterior. La duración es
la diferencia entre ambos eventos. La API exige una ruta TCP interzona activa
desde una estación participante habilitada hacia un dispositivo Modbus TCP,
el puerto real del servidor y un registro existente del tipo correspondiente:
FC03/FC06/FC16/FC22/FC23 para holding register, FC04 para input register,
FC01/FC05/FC15 para coil o FC02 para discrete input. Cada regla usa el Unit ID
configurado y una dirección única; FC01/FC02/FC05/FC15 requieren valor 0/1 y
las demás funciones admitidas usan UINT16.

La definición rechaza campos extra y valida que:

- la fuente sea una estación de participante opt-in;
- fuente y destino pertenezcan al escenario y a zonas distintas;
- la ruta TCP genérica habilitada tenga esa fuente, destino y puerto Modbus TCP;
- el objetivo exista en el espacio indicado por la función: holding register
  (FC03/06/16/22/23), input register (FC04), coil (FC01/05/15) o discrete input (FC02);
- Unit ID, función, dirección y valor estén dentro de rango;
- cada `set` tenga un `clear` posterior y las reglas no dupliquen dirección,
  función, Unit ID o identificador dentro de la misma acción.

Las reglas FC01/FC02/FC04 son solo de respuesta; un input de solo lectura sigue
siendo de solo lectura en el servidor. FC01/FC02/FC03/FC04 cambian lo
que observa ese cliente, no el punto ni el proceso. FC06/FC16 cambian la
escritura que recibe el servidor y, si el registro está conectado a un actuador
o lógica compatible, pueden cambiar el proceso modelado.

## Aislamiento y controles

- Solo redes internas propiedad del escenario y un proxy ya creado desde una
  ruta exacta; no network_mode host, NET_ADMIN, raw sockets, publicación de
  puertos ni rutas hacia Internet.
- El gateway conserva UID no root, raíz de solo lectura, cap_drop ALL,
  no-new-privileges, límites de recursos y un único origen/destino/puerto.
- El panel de ejercicios mantiene la autorización instructor/admin existente;
  la estación participante no puede crear ni modificar reglas desde su shell.
- El control de activación lo emite el scheduler de Anvil mediante una interfaz
  autenticada y limitada a ese gateway y escenario. El token no se devuelve en
  lecturas del escenario, logs o API pública.
- Cancelación, finalización o fallo del ejercicio intentan limpiar la regla y
  registran el resultado durable; la pausa detiene los contenedores y conserva
  la regla en memoria para continuar el mismo run al reanudar.
- La cola durable y la cadena de auditoría registran cada mensaje alterado
  con sus IDs, función, dirección de petición/respuesta y punto; no almacenan
  tramas ni valores del proceso. La cola
  está acotada a 100.000 eventos/32 MiB y comunica desbordamientos al colector.

## Verificación de aceptación

1. Pruebas de parser MBAP: tramas parciales, concatenadas, Transaction IDs
   simultáneos, excepciones, longitudes inválidas, códigos no admitidos y
   desconexión.
2. Pruebas de correspondencia: dirección inicial, offsets, respuestas con
   varios registros, Unit ID distinto y solicitudes de otras funciones no se
   modifican.
3. Pruebas de validación API: rechazar estación, destino, puerto, ruta, espacio,
   dirección, valor, ventana y campos no permitidos inválidos.
4. Smoke Docker con tres contenedores reales (cliente, proxy y servidor Modbus)
   en redes internas: FC01/FC02/FC03/FC23 demuestran respuestas sustituidas mientras el
   servidor conserva el valor de FC03; FC05/FC06/FC15/FC16/FC22 demuestran que el servidor almacena
   los valores reescritos aunque el cliente enviara otros; FC23 comprueba por separado la sustitución de lectura en la respuesta y la sustitución de escritura antes de llegar al servidor. FC05 y FC15 comprueban
   que coils modificadas se almacenan en el servidor. La cola durable
   registra cada regla coincidente por separado y se puede retirar. El smoke
   `integration/exercise_smoke.py` verifica además la cronología real, lectura
   FC03/FC23 y FC15/FC16 desde la estación y puntuación del objetivo.
5. Smoke de ejercicio end-to-end: `integration/exercise_builder_ui.cjs` verifica
   la edición guiada desde Brave. `integration/exercise_smoke.py` verifica en
   Docker set/clear del scheduler, lecturas alteradas FC03/FC23 y escrituras FC05/FC06/FC15/FC16/FC22
   reescritas desde la estación participante, persistencia de las coincidencias,
   puntuación del objetivo y valor efectivo almacenado por el servidor. El smoke
   Docker por ruta prueba FC05 real. Ambos limpian sus recursos temporales.
6. Regresión de rutas: S7, Modbus y TCP genérico sin regla mantienen el relay
   byte-transparente y el mismo aislamiento; la ruta no se publica en el host.

Las pruebas unitarias de transformación sin el smoke Docker no acreditan
intercepción en runtime. Un test del cliente que escriba el registro directamente
al servidor tampoco cumple este requisito.

## Fuera del alcance actual

No incluye falsificar respuesta S7,
inyectar tráfico sin ruta explícita, ataques ARP/DNS, secuestro de sesiones,
MITM TLS, captura universal ni destinos fuera del escenario. Si se añade otro
protocolo o función, deberá contar con parser y pruebas independientes; no se
aplicará una sustitución ciega de bytes TCP.

### FC23 Read/Write Multiple Registers

FC23 tiene dos rangos independientes en la petición: el rango que se lee y el
rango que se escribe. El relay valida ambos inicios/cantidades, límites de
protocolo, byte count y longitud antes de registrar la transacción pendiente.
En el modo `read` reenvía la ADU completa sin cambios. En el modo `write`, una regla exacta puede sustituir solo registros del rango escrito; el rango leído y los demás valores de escritura permanecen intactos. El gateway identifica cada regla con `phase` (`read` por defecto, `write` solo para FC23), así puede configurar ambos sentidos en una misma dirección sin colisión. Al recibir la respuesta correlacionada por
Transaction ID, Unit ID y función, solo sustituye registros del rango leído que
tengan reglas FC23 con `phase: read`. La respuesta debe tener el byte count correspondiente
a la cantidad solicitada; respuestas de excepción pasan intactas. Las reglas `phase: write` se auditan como dirección `request`; las de lectura como `response`. La auditoría no conserva los valores transmitidos.
