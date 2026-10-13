# Backend de Anvil

FastAPI sirve la API, el editor estático y los canales WebSocket. Persiste los escenarios en SQLite y crea una red Docker aislada para cada uno.

## Ejecutar

```bash
docker build -t anvil/plc-s7:dev ./plc
docker compose up -d --build
curl http://localhost:8000/api/health
curl http://localhost:8000/api/ready
```

- `/api/health` comprueba que responde el proceso HTTP; `/api/ready` comprueba SQLite y Docker.
- `/docs` muestra OpenAPI.
- `GET /api/scenarios/{id}/io-map` calcula el mapa S7.
- `GET /api/scenarios/{id}/plcs/{node}/values` lee sus puntos configurados.
- `POST /api/scenarios/{id}/plcs/{node}/write` escribe solo válvulas BOOL R/W.
- `GET /api/scenarios/{id}/protocol` devuelve operaciones S7 recientes.
- `GET /api/scenarios/{id}/logs` y `WS /api/scenarios/{id}/logs` exponen logs acotados.
- `WS /api/scenarios/{id}/events` entrega estado en vivo.
- El campo opcional `canvas` conserva posiciones y conexiones del escenario.
- `GET /api/scenarios/{id}/modbus/{node}/values` consulta coils y registros Modbus.
- `POST /api/scenarios/{id}/modbus/{node}/write` escribe coils y holding registers (no input registers).
- `GET /api/scenarios/{id}/hmi/{node}/values` lee desde una HMI los puntos de los Node IDs S7/Modbus configurados.
- El nodo firewall valida el acceso de las rutas de consola/HMI por puerto TCP (S7/102, Modbus/puerto configurado). No inspecciona tráfico entre contenedores.
- El PLC acepta motores y bombas como salidas BOOL R/W, entradas digitales y alarmas de solo lectura, además de sensores analógicos. La bomba produce un caudal REAL derivado de su orden (cero apagada, punto medio del rango encendida).

La consola ejecuta clientes de protocolo dentro del escenario; el backend no se conecta directamente a la red OT. La pestaña Protocolo muestra una traza semántica, no un PCAP. La pestaña PCAP permite capturar y descargar el tráfico de la interfaz de un dispositivo seleccionado; no equivale a una captura íntegra de todo el segmento Docker. El historial de cambios de señales vive temporalmente en el navegador.

Los WebSockets de estado y logs comparten un único sondeo por escenario. Al arrancar, el backend reconcilia estados con Docker. `ANVIL_CLEANUP_ORPHANS=true` elimina únicamente recursos etiquetados de Anvil cuyo escenario ya no exista.

## Pruebas

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt -r plc/requirements.txt
python -m unittest discover -s backend/tests -v
python -m unittest discover -s plc/tests -v
```

Para validar despliegue y S7 real, ejecuta `integration/platform_smoke.py` desde el contenedor backend (ver `README.md`). El helper requiere credenciales efímeras en `ANVIL_TEST_EMAIL` y `ANVIL_TEST_PASSWORD`; en una base nueva usa también el token de bootstrap configurado en el contenedor.

## Seguridad

Los contenedores de escenarios usan usuario no root, filesystem raíz de solo lectura, capacidades reducidas y límites. El gateway Docker sigue otorgando capacidades privilegiadas sobre el host. La autenticación por roles está implementada de forma inicial; no publiques este servicio fuera de una red confiable sin TLS, operación segura de secretos y aislamiento adicional. Ver [cuentas y permisos](../docs/PLATFORM_ACCESS.md) y [confinamiento de contenedores](../docs/CONTAINER_SECURITY.md).

Consulta la [guía del dispositivo Modbus TCP](../docs/MODBUS.md) para configurar y probar el dispositivo desde Docker.

Consulta [la guía de acceso controlado a la red OT](../docs/ACCESS.md) antes de activar un reenvío de puerto.
