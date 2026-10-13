# Perfiles portables de dispositivo

Anvil exporta e importa la configuración de un nodo en JSON con formato
`anvil-device-profile`, versión 1. El perfil conserva el tipo, protocolo,
configuración validada y el mapa de direcciones que Anvil resuelve para el
dispositivo. La importación vuelve a validar la configuración y recalcula el
mapa; el `resolved_mapping` que venga dentro del archivo es informativo y no se
usa como fuente de permisos ni de direcciones.

La importación no restaura una publicación de puertos en el host. La publicación
de S7/Modbus está temporalmente deshabilitada en la versión actual; los campos
antiguos de acceso en un archivo exportado se rechazan al crear el escenario.

En el inspector de un PLC S7 o un dispositivo Modbus TCP/RTU usa **Exportar
perfil JSON** o **Importar perfil JSON**. Para importar, Anvil solicita un nuevo
Node ID y una IP libre, y conserva la zona del dispositivo seleccionado. El
escenario debe estar detenido y guardado. La API devuelve `409` si está activo
y `422` ante configuración, dirección, zona o versión inválidas.

## API

```text
GET  /api/scenarios/{scenario_id}/devices/{node_id}/profile
POST /api/scenarios/{scenario_id}/devices/import
```

La exportación responde, por ejemplo:

```json
{
  "format": "anvil-device-profile",
  "schema_version": 1,
  "device_type": "plc-s7",
  "name": "PLC principal",
  "protocol": "S7comm",
  "config": { "rack": 0, "slot": 1, "components": [] },
  "resolved_mapping": [],
  "fidelity_note": "..."
}
```

Para reimportar, envía el perfil más los datos de colocación. La respuesta es
el escenario actualizado:

```json
{
  "profile": { "format": "anvil-device-profile", "schema_version": 1 },
  "node_id": "plc-copia",
  "ip": "172.30.10.11",
  "zone_id": "default"
}
```

En la API real se envía el objeto `profile` completo exportado, no solo el
fragmento abreviado del ejemplo.

Los perfiles facilitan comparar áreas S7/DB, marcas, entradas/salidas y
direcciones Modbus con una hoja de direccionamiento de un equipo equivalente.
No generan archivos TIA Portal, GSDML, EDS ni configuración de fabricante, y
no certifican equivalencia de scan, temporizadores, permisos o comportamiento.
Antes de usar el mapa con hardware real, contrástalo con la documentación del
fabricante y prueba en un entorno controlado.
