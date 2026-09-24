# Backend de Anvil

FastAPI sirve la API, el editor estático y el canal WebSocket de Anvil. Persiste
los escenarios en SQLite y crea una red Docker aislada para cada escenario.

## Ejecutar

La imagen del PLC debe existir antes de arrancar el backend:

```bash
docker build -t anvil/plc-s7:dev ./plc
docker compose up -d --build
curl http://localhost:8000/api/health
curl http://localhost:8000/api/ready
```

- `/api/health` comprueba que el proceso HTTP responde.
- `/api/ready` comprueba acceso a SQLite y al daemon Docker.
- `/docs` muestra OpenAPI.
- `/api/scenarios/{id}/events` entrega estado en vivo por WebSocket.

Al arrancar, el backend sincroniza los estados persistidos con Docker. Si
`ANVIL_CLEANUP_ORPHANS=true`, también elimina únicamente contenedores y redes
con etiquetas de Anvil cuyo escenario ya no existe.

## Pruebas

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
python -m unittest discover -s backend/tests -v
```

## Seguridad

El socket Docker montado en el contenedor otorga un nivel de acceso equivalente
al administrador del host. No publiques este servicio fuera de una red
confiable sin añadir autenticación, TLS y aislamiento adicional.
