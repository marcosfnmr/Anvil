# Backend de Anvil

API FastAPI de la fase 2. Persiste escenarios en SQLite y crea una red Docker
aislada por escenario. Los offsets S7 se calculan internamente y nunca forman
parte del contrato público de la API.

## Ejecutar con Docker CLI

La imagen del PLC debe existir antes de arrancar el backend:

```bash
docker build -t anvil/plc-s7:dev ./plc
docker compose build backend
docker compose up -d backend
curl http://localhost:8000/api/health
```

La documentación interactiva queda disponible en
`http://localhost:8000/docs`. Para detener el backend:

```bash
docker compose down
```

`docker-compose.yml` monta `/var/run/docker.sock` porque el backend crea y
elimina las redes y contenedores de cada escenario mediante la API de Docker.

## Pruebas

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
python -m unittest discover -s backend/tests -v
```
