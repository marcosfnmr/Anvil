# Biblioteca compartida de componentes

La biblioteca conserva presets de configuración para componentes que ya soporta
un adaptador Anvil. No carga módulos Python ni JavaScript, imágenes ni código
proporcionado por usuarios.

## Presets de componentes

1. Abre un PLC S7 o dispositivo Modbus TCP y añade/configura el componente en el
   inspector.
2. Si tu cuenta es admin o instructor, pulsa **Compartir preset** en su tarjeta,
   completa nombre y descripción y publícalo.
3. Cualquier usuario autenticado puede abrir **Componentes compartidos**. Selecciona
   un dispositivo compatible detenido y añade el preset desde la biblioteca.
4. El nuevo componente queda en el escenario local y debe guardarse antes de
   desplegar. Quien publicó puede retirar el preset; admin puede retirar cualquiera.

## API

- `GET /api/components/templates` lista presets y su configuración validada.
- `POST /api/components/templates` crea un preset. Admin/instructor únicamente.
- `DELETE /api/components/templates/{preset_id}` elimina el preset y su historial.
  Solo quien lo publicó o admin.

Cada entrada declara `schema_version: 1`, `device_type` y `component_type`. La API
comprueba que el dispositivo admite el tipo y valida el objeto con el modelo de
configuración real del adaptador antes de guardarlo; también rechaza campos
desconocidos dentro de objetos anidados. `io_channel` recibe un ID de
canal nuevo cuando se inserta para evitar reutilizar un enlace de campo del
escenario del publicador. Las entradas S7 `remote_input` no se pueden compartir
porque contienen referencias a otro PLC y direcciones específicas del escenario.

Los presets no son una extensión ejecutable del catálogo. Para añadir un tipo de
componente nuevo hacen falta cambios de código en el modelo, runtime, adaptador,
interfaz y pruebas. Los presets sí admiten revisiones sucesivas: el publicador o
un admin puede editar nombre, descripción y configuración, consultar versiones
anteriores y restaurar una de ellas como una nueva revisión. Cada edición debe
aportar la revisión esperada; si cambió entre la lectura y el guardado, la API
responde `409`. La configuración editada y restaurada se valida otra vez con el
esquema real del adaptador y conserva el mismo tipo de nodo y componente.

- `PUT /api/components/templates/{preset_id}` modifica un preset con
  `expected_revision` y uno o más campos `title`, `description` o `config`.
- `GET /api/components/templates/{preset_id}/revisions` enumera las revisiones
  inmutables; cualquier usuario autenticado puede leerlas.
- `POST /api/components/templates/{preset_id}/revisions/{version}/restore`
  restaura como una nueva revisión; solo publicador/admin.

### Quién puede hacer cada operación

| Operación | Usuario autenticado | Publicador | Admin |
| --- | --- | --- | --- |
| Listar y aplicar presets | Sí | Sí | Sí |
| Consultar revisiones | Sí | Sí | Sí |
| Publicar un preset | No | Sí, si tiene rol instructor | Sí |
| Editar o restaurar | No | Sí, los propios | Sí, cualquiera |
| Retirar | No | Sí, los propios | Sí, cualquiera |

El rol instructor puede publicar; la edición está limitada al autor del preset
y a administradores. Estas reglas se aplican también en API, no solo ocultando
botones. Una revisión conserva título, descripción y configuración validados,
autor de la acción, fecha y número. Restaurar no rebobina ni elimina el historial:
copia el contenido seleccionado a la siguiente revisión. Si dos personas editan
la misma revisión, la segunda escritura con un `expected_revision` obsoleto
recibe `409 Conflict` y debe volver a leer antes de guardar.

La migración de base de datos añade revisión y fecha de actualización a presets
anteriores, y crea su revisión base de forma idempotente. Todavía no hay
migración automática entre futuras versiones del esquema, moderación por
organización ni perfiles S7 remotos con referencias portables.

## Plantillas de escenario compartidas

Una plantilla de escenario contiene una instantánea declarativa validada del
escenario de origen, junto con nombre, categoría, descripción, etiquetas y
versión de esquema. Admin/instructor pueden publicarla; solo quien la publicó o
un admin puede cambiar sus metadatos o sustituir su contenido por otro escenario
al que tenga acceso. Todos los usuarios autenticados pueden consultar el
historial e instanciar la versión actual como escenario propio e independiente.

Cada edición crea una revisión inmutable. La solicitud incluye la revisión que
el editor vio; si otra persona ya la cambió, la API responde `409` y evita
sobrescribir el cambio nuevo. Restaurar una versión antigua copia su contenido
a una revisión nueva, sin borrar las versiones posteriores. Las bases antiguas
reciben una versión inicial de forma idempotente al iniciar Anvil.

- `GET /api/templates/{template_id}/revisions` lista las versiones anteriores y actual.
- `PUT /api/templates/{template_id}` edita la plantilla; requiere `expected_revision`
  y al menos un campo modificable (`title`, `category`, `summary`, `complexity`,
  `tags` o `scenario_id`). Solo publicador/admin.
- `POST /api/templates/{template_id}/revisions/{version}/restore` restaura como
  nueva revisión. Solo publicador/admin.

El contenido usa actualmente `schema_version: 1`; las revisiones no equivalen a
un sistema de migración automática para formatos futuros. Tampoco incluyen
comparación visual de diferencias ni moderación multi-organización.
