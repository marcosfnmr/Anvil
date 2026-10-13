# Diseñar y administrar escenarios

Un escenario evoluciona desde borrador a runtime desplegado. Separar esas fases
ayuda a comprender qué cambios están guardados y qué contenedores están activos.

## Crear y abrir

Desde **Escenarios**, crea uno vacío o usa una plantilla. La plantilla completa
una topología inicial, pero no despliega runtimes. El mapa abre el escenario
seleccionado; guarda el borrador después de modificar direcciones, dispositivos,
objetos, relaciones o zonas.

Usa un nombre de nodo estable (por ejemplo `plc-bombeo`) para facilitar lectura
de logs, DNS y auditoría. La IP del runtime pertenece al nodo; un sensor/válvula
de campo no tiene dirección IP.

## Editar, guardar y versionar

El inspector configura el elemento seleccionado. El mapa dibuja las relaciones
y permite mover nodos sin cambiar su configuración. La API valida la topología
completa antes de persistirla. Los diagnósticos pueden incluir advertencias
consultivas aunque `valid` sea verdadero; resuelve advertencias relevantes antes
de desplegar.

Si se habilita acceso externo a más de un servicio del mismo escenario, cada
`external_port` publicado en el host debe ser único. El esquema lo rechaza al
guardar; el gestor Docker repite la comprobación antes de crear recursos. Esos
son puertos del host y no deben confundirse con el puerto OT interno del
contenedor (por ejemplo, S7/102 o el puerto Modbus configurado).

Los cambios guardados crean revisiones inmutables cuando difieren del estado
anterior. Restaurar una revisión requiere parar el escenario y crea una nueva
versión con el estado que se reemplaza. La exportación/importación JSON permite
copiar escenarios. El JSON exportado usa `schema_version: 2`; Anvil migra
importaciones v1 añadiendo `network_connections: []` si falta. Los documentos
sin sobre se interpretan como v1. Cada paso de migración es explícito; versiones
futuras se rechazan hasta que se implemente su transición. La importación no
modifica el documento fuente ni los escenarios guardados.
Consulta [perfiles de dispositivo](DEVICE_PROFILES.md) si solo quieres copiar un
nodo.

En la interfaz, **Guardar** persiste la definición y sus cambios; no inicia
contenedores. **Desplegar** guarda primero cualquier edición pendiente y luego
ejecuta el diagnóstico previo. Si hay errores, el despliegue se detiene y la
actividad muestra la ruta y el motivo que hay que corregir. Si solo hay avisos,
la interfaz los presenta y permite cancelar o continuar. Cancelar deja guardado
el borrador, pero no envía la orden de despliegue. Conviene pensar en dos pasos:
guardar conserva la topología; desplegar crea o actualiza el runtime.

El backend repite la validación del modelo persistido al recibir la orden de
despliegue. Así, una llamada directa a la API o un registro antiguo/corrupto no
puede saltarse los errores de esquema de la interfaz: recibe HTTP 422 y Docker
no se invoca. Las advertencias de ingeniería siguen siendo consultivas para
permitir laboratorios incompletos; la interfaz pide confirmación antes de
continuar con ellas.

## Desplegar

Antes del despliegue, Anvil valida los modelos, direcciones, conexiones, redes y
presupuestos de recursos. Los contenedores se crean dentro de redes etiquetadas
para el escenario. Los puertos del host no se publican por defecto. Los
healthchecks y el estado Docker se actualizan en el runtime.

Un despliegue correcto significa que Anvil aceptó la orden y creó el runtime;
no garantiza que cada ruta entre equipos funcione ni que la lógica de control
produzca el resultado esperado. Después de desplegar, revisa el estado por nodo
y la actividad: las sondas de rutas se muestran ahí cuando hay rutas que probar.
Una sonda comprueba conectividad al puerto configurado; no valida la semántica
del protocolo, las direcciones de registros ni la lógica PLC/HMI. Los enlaces
de campo tienen diagnósticos aparte. Un error de diagnóstico en ejecución se
registra como tal y no revierte automáticamente los contenedores ya creados.

### Recorrido rápido desde la interfaz

1. En **Escenarios**, crea uno vacío o abre una plantilla y selecciónalo.
2. En el mapa, añade nodos y objetos de campo. Configúralos en el inspector;
   conecta señales y proceso donde corresponda.
3. Pulsa **Guardar**. Confirma que la interfaz muestra el escenario como guardado.
4. Pulsa **Desplegar** y lee el resultado del diagnóstico previo. Corrige los
   errores; para cada aviso, decide si es aceptable para este laboratorio.
5. Tras aceptar avisos, espera los estados de runtime. Comprueba cada nodo, sus
   logs y las rutas que se informen en Actividad/Diagnóstico.
6. Prueba lecturas y escrituras desde el destino configurado y confirma en el
   proceso que el cambio se propagó. Para liberar recursos, pulsa **Detener**;
   la definición guardada permanece en la lista de escenarios.

Si sales con cambios sin guardar, el navegador pide confirmación. Si el escenario
está desplegado, deténlo antes de editar: el inspector bloquea cambios mientras
el runtime está activo.

Después de desplegar:

1. Espera a que cada nodo informe estado sano.
2. Abre el diagnóstico de red y resuelve rutas no alcanzables.
3. Lee primero puntos antes de intentar escribir.
4. Comprueba la vista de proceso y logs del nodo.
5. Usa auditoría para confirmar operaciones y resultado.

Un escenario cuyo estado global diga `running` todavía puede tener un nodo
ausente. El reconciliador periódico detecta la pérdida de contenedores y, si
el escenario conserva intención activa, registra y reintenta su recreación. Si
también falta una red Docker gestionada, intenta reconstruirla desde el plan
guardado antes de recuperar los nodos. Respeta escenarios detenidos y no toma
redes ajenas; una colisión de nombre/subred requiere intervención. La acción
manual **Reconstruir nodo ausente** recrea solo el nodo y presupone que su red
ya está disponible. Los estados `exited` o `unhealthy` no implican que todos
los fallos se reparen automáticamente: revisa healthcheck y logs, y usa las
acciones de nodo apropiadas. Consulta [operaciones](OPERATIONS.md) para las
condiciones exactas y [la matriz SRS](SRS_TRACEABILITY.md) para la evidencia.

## Pausar, reanudar y detener

Pausar el escenario congela las operaciones de los runtimes compatibles y los
ejercicios guardan el índice/tiempo restante para continuar al reanudar. Pausar
no detiene el contenedor. Reanudar restaura el estado operativo; detén el
escenario para liberar sus runtimes. Las acciones individuales de iniciar,
detener, reiniciar, pausar o reanudar un nodo se registran en auditoría.

Detener retira los contenedores y redes asociadas, pero conserva la definición
del escenario en SQLite. Borrar el escenario es otra acción: elimina la
configuración operativa y no debe usarse como sustituto de detener. La política
de auditoría preserva la cadena de eventos según lo descrito en
[AUDIT.md](AUDIT.md).

## Exportar, clonar y compartir

- **Exportar escenario:** crea un documento versionado con la configuración.
- **Importar escenario:** valida e inserta una definición nueva.
- **Clonar:** crea una copia para editar sin afectar el origen.
- **Perfil de dispositivo:** copia configuración de un único nodo.
- **Bundle de despliegue:** empaqueta información y artefactos para reproducir
  el despliegue; no es una copia completa de imágenes Docker ni secretos.

Al compartir un escenario entre cuentas, asigna la membresía/rol mínimo
necesario. Las contraseñas y token de bootstrap no forman parte de una
exportación. Las credenciales de cuenta no se recuperan por correo.

## Elementos legacy y migración de campo

Los escenarios antiguos pueden guardar sensores y actuadores dentro del
dispositivo PLC. El catálogo actual permite crear objetos independientes, pero
Anvil no convierte silenciosamente una topología vieja porque eso podría
desconectar sus señales. Revisa el preview de migración y valida las conexiones
antes de aplicar cambios. El modelo independiente se explica en
[FIELD_OBJECTS.md](FIELD_OBJECTS.md).
