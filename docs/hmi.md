# HMI y operación del proceso

## Qué es una HMI

Una HMI es la interfaz que permite a una persona observar y operar un proceso.
Suele presentar valores, estados, alarmas y mandos. La HMI normalmente actúa
como cliente de los PLC: solicita tags y, si existe autorización, escribe una
consigna o salida. La lógica local de protección y control sigue perteneciendo
al PLC; una pantalla no debe ser el único mecanismo que mantenga seguro un
proceso.

### Qué partes tiene una HMI y cuáles modela Anvil

Una HMI industrial suele tener una aplicación de operador, un cliente de
comunicaciones, un catálogo de tags, pantallas sinópticas, alarmas y mandos.
Anvil representa el cliente de software y una vista web integrada: configura
fuentes y puntos, consulta S7/Modbus, muestra calidad y estados, presenta el
sinóptico y ofrece las operaciones autorizadas. No emula el panel táctil, el
sistema operativo de una HMI comercial ni importa un proyecto WinCC.

El sinóptico es una representación visual; una **tag** es el dato que se
consulta en el servidor. Se asocian para que un símbolo use una lectura, pero
son objetos distintos. El PLC sigue ejecutando su control independientemente
de la pantalla, y la escritura del operador solo es una petición al punto de
protocolo seleccionado.

En Anvil, la HMI es un nodo runtime de software y su panel de operación está en
la aplicación web. El runtime consulta puntos S7/Modbus configurados. El panel
puede mostrar dichos valores en un sinóptico asociado, alarmas y controles que
las políticas habiliten. No es una pantalla física de fabricante ni importa
proyectos de WinCC u otro editor comercial. El dibujo de un tanque o válvula no
crea el dispositivo, su señal ni el tag protocolario.

### Roles en la comunicación

El servidor PLC/Modbus conserva el punto; el runtime HMI es cliente y abre las
peticiones de lectura/escritura; la aplicación web muestra la respuesta y su
calidad. La red solo permite alcanzar el servidor. El destino selecciona el
equipo, el tag selecciona su punto/dirección y el sinóptico asocia ese dato con
una representación gráfica. Son tres selecciones distintas. Para escribir,
además, el punto debe admitir escritura y la operación debe estar permitida por
el rol/política del usuario. El cliente directo del protocolo no hereda la
sesión web de Anvil.

### Qué necesita una lectura HMI

Para ver una medida en vivo deben coincidir el dispositivo fuente, el punto
correcto, la conectividad y una respuesta válida. Seleccionar un PLC no hace que
la HMI descubra todas sus direcciones; cada tag se configura de manera
explícita. Si el valor viene de un sensor de campo, además se necesita la
conexión de señal desde el sensor al punto del controlador. Para representar el
valor junto a un símbolo, el sinóptico debe asociar ese tag con el objeto visual.

Una orden atraviesa el camino inverso: el operador solicita un valor, la HMI
valida destino, dirección, tipo, permisos y rol, el runtime cliente envía una
escritura S7/Modbus y el servidor responde. Si el punto está enlazado a una
salida de campo, el runtime de campo entrega la orden al actuador. El estado
modelado puede tardar en cambiar o permanecer igual ante un fallo; la respuesta
protocolaria exitosa por sí sola no demuestra que el proceso cambió. Consulta
[elementos de campo](FIELD_OBJECTS.md) para distinguir orden y estado.

## HMI y SCADA: funciones relacionadas, nodos distintos

| Aspecto | HMI de Anvil | SCADA de Anvil |
| --- | --- | --- |
| Papel principal | Vista de operación para un operador, con sinóptico y controles | Colector de adquisición para varios destinos y consulta de histórico |
| Rol de protocolo | Cliente que inicia lecturas y escrituras seleccionadas por S7/Modbus | Cliente que sondea los puntos configurados por S7/Modbus |
| Datos guardados | Estado y transiciones de alarmas propias; reconoce alarmas de SCADA asociado | Muestras históricas, estado activo/reconocido de alarmas y exportación CSV |
| Dependencia de ejecución | La telemetría nueva y la operación requieren escenario/runtime disponible | La adquisición requiere SCADA activo; el histórico ya persistido se puede consultar con el runtime detenido |
| Qué no hace | No reemplaza el scan ni descubre automáticamente los tags | No es historiador HA ni lee todos los puntos de la red automáticamente |

La HMI y SCADA no son nombres para el mismo contenedor en Anvil. Se pueden
asociar para que la HMI muestre y reconozca alarmas administradas por SCADA.
En plantas reales, una estación puede combinar funciones de HMI, supervisión y
adquisición; aquí se separan para hacer explícitos sus destinos y flujos.

### Partes simuladas y recorrido de una lectura

Una HMI industrial suele combinar un cliente de comunicación, una lista de tags,
pantallas, alarmas y controles. Anvil simula el cliente para los protocolos
implementados y la vista web de operación: la HMI consulta el servidor PLC o
Modbus, valida el punto y permiso, y presenta su lectura/calidad. Una escritura
autorizada vuelve al punto del servidor y puede viajar por una conexión de
señal hasta un actuador. El dibujo sinóptico por sí solo no produce valores ni
conecta tags. No se cargan proyectos gráficos de fabricantes ni se emula un
panel físico.

Una **tag** o punto de HMI no es el objeto gráfico del sinóptico. La tag
identifica un dato del servidor: por ejemplo, el servidor PLC S7 `plc-1`, la
dirección `DB1.DBX0.0`, tipo BOOL y permiso de lectura/escritura. El sinóptico
puede asociar ese punto con el estado de una válvula para representarlo. Sin
destino, dirección compatible, conectividad y respuesta protocolaria no hay
telemetría que mostrar. El gráfico no sondea por sí solo todos los dispositivos.

## Configuración

En una fábrica real, la HMI es una estación de operador conectada a uno o más
controladores. Presenta estados, medidas, alarmas y mandos; no suele sustituir
la lógica cíclica local del PLC. En Anvil, el nodo HMI es un cliente software
del escenario y la pantalla HMI forma parte de la web de Anvil. El nodo consulta
los servidores S7/Modbus de los destinos configurados; no es una imagen HMI de
un fabricante ni un runtime que cargue proyectos gráficos comerciales.

La **fuente** es el equipo servidor (PLC S7 o dispositivo Modbus), el **tag** es
el punto/dirección que se consulta y la **calidad** indica si la lectura es
actual y válida. Seleccionar el nodo destino no selecciona automáticamente
cualquier señal: la HMI solo presenta los puntos que su vista/operación consulta
y para los que existe un endpoint compatible. El sinóptico puede dibujar la
planta, pero necesita una asociación con datos para mostrar telemetría.

1. Añade una HMI desde el catálogo.
2. Selecciona dispositivos destino y puntos a consultar.
3. Define alarmas compatibles con el tipo y la dirección del punto.
4. Configura escritura solo para salidas que el mapa de E/S permita y que estén
   seleccionadas por esa HMI.
5. Si el destino está en otra zona, declara una ruta compatible y verifica el
   diagnóstico de conectividad.

Al operar, el flujo es: la vista solicita valores a la API de Anvil; la
operación gestionada usa el cliente S7/Modbus del runtime para consultar los
destinos seleccionados; la respuesta incluye valor, dirección, timestamp o
calidad cuando están disponibles; el panel asocia ese punto al sinóptico o a
una regla de alarma. Una lectura `waiting`, `stale`, `fault` o con error no es
una muestra actual válida. Si se detiene el escenario, el historial persistido
puede seguir consultándose por SCADA, pero no se debe mostrar como telemetría en
vivo de la HMI.

La diferencia con SCADA en Anvil es de papel: HMI representa la estación de
operación y su sinóptico; el nodo SCADA representa adquisición multi-equipo,
histórico y alarmas centralizadas. En productos reales estas funciones pueden
convivir en un mismo sistema.

La HMI es un **cliente**, mientras el PLC/Modbus configurado es el **servidor**.
La HMI inicia lecturas periódicas y recibe el dato y su calidad; no recibe una
emisión automática solo por compartir red. Una escritura inicia otra petición
del cliente al servidor y solo se ofrece si el punto está seleccionado,
escribible según el mapa y permitido por la política/rol. Aunque la HMI cambie
el dato del servidor, un bloque PLC puede volver a imponer su salida en el
siguiente scan.

Las entradas de campo y puntos de solo lectura no se convierten en salidas por
seleccionarlos en la HMI. Las escrituras se ejecutan desde el contenedor HMI a
través del protocolo habilitado y dejan registro de auditoría.

La autorización de una escritura reúne varias condiciones: el destino y punto
deben estar seleccionados, el mapa de E/S debe marcarlo `read_write`, el tipo y
rango del valor deben ser válidos, el rol debe tener permiso operativo y la
conectividad debe funcionar. Una HMI no puede convertir un input register o un
canal de sensor en salida. La validación de la ruta API de Anvil no crea una
ACL para un cliente externo que se conecte directamente al puerto S7/Modbus;
ese acceso se restringe con las redes y reglas de publicación del entorno.

### Ejemplo de extremo a extremo

Para visualizar la apertura de una válvula, configura un punto BOOL de salida
en el PLC, conéctalo desde el campo a `valve.command`, añade el PLC como destino
de la HMI, selecciona la dirección S7 del punto y vincula el tag al símbolo de
la válvula. Despliega y confirma que el tag presenta un valor y calidad
vigentes. Si la HMI debe mandar la válvula, habilita escritura solo para ese
punto y el rol correspondiente; tras escribir, verifica el estado en el objeto
de campo y la respuesta del proceso. Así separas el éxito de la petición del
efecto real sobre el actuador.

Si no aparece un valor, revisa por orden: runtime sano, servidor y protocolo,
dirección del punto, selección del tag, ruta entre zonas, conexión de señal y
asociación del sinóptico. Tener conectividad de red no resuelve los otros pasos.

## Sinóptico

El sinóptico representa elementos físicos y líneas de proceso configurados en
el escenario. Solo incorpora telemetría cuando hay un tag leído desde una
fuente seleccionada. Si no existe esa asociación, presenta el objeto sin
lectura, en lugar de generar un valor. La vista gráfica es una herramienta de
operación del simulador, no un editor CAD ni una pantalla compatible con un
fabricante específico.

## Alarmas

Las alarmas HMI aceptan puntos discretos y numéricos compatibles. Una regla
puede comparar el estado esperado de un BOOL o un umbral de REAL/UINT16. El
retardo se mide con los ciclos de consulta de la HMI; una lectura inválida
reinicia una condición pendiente y puede dejar una alarma activa como obsoleta.
Esto no ofrece temporización industrial. Las alarmas SCADA asociadas se pueden
mostrar/reconocer si la HMI tiene ese SCADA declarado.

El reconocimiento no borra la causa: el estado vuelve a normal cuando una
lectura válida muestra que la condición terminó. Las transiciones y
reconocimientos se conservan y auditan según el modelo implementado.

## Actualización de valores

La vista consulta los tags cada dos segundos. La actualización modifica las
lecturas en las tarjetas existentes en vez de reconstruir todo el panel, por lo
que mantiene la posición del foco y una consigna que el operador esté editando.
Cuando la petición falla o el escenario pasa por un estado transitorio, conserva
la última lectura y presenta el aviso; al cambiar de escenario elimina la vista
anterior para no mezclar valores. La zona de valores no se anuncia entera al
lector de pantalla en cada ciclo; el mensaje de estado informa de errores y del
resultado de la consulta.

## Verificar

Comprueba una señal en el propio protocolo cuando sea posible. Confirma calidad,
timestamp, dirección, rol, permisos de escritura y estado del runtime. Una
lectura persistida de histórico no equivale a telemetría en vivo. Las rutas API
y límites están en [SCADA.md](SCADA.md),
[PLATFORM_ACCESS.md](PLATFORM_ACCESS.md) y el `/docs` de FastAPI.
