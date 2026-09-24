# 🔨 Anvil

> The forge where OT cyber ranges are shaped.

Anvil es un framework modular para construir y desplegar laboratorios OT/ICS
dockerizados desde un panel web. Permite diseñar escenarios, configurar
dispositivos simulados y desplegarlos en redes Docker aisladas.

El MVP simula PLCs Siemens S7 mediante `python-snap7` y proporciona estado en
tiempo real desde el navegador.

## Estado del proyecto

- Simulador PLC S7 parametrizable.
- Válvulas booleanas y sensores analógicos constantes o senoidales.
- API FastAPI con persistencia SQLite.
- Redes y contenedores independientes por escenario.
- Editor visual con Drawflow.
- Estado y actividad en vivo mediante WebSocket.
- Reconciliación del runtime y healthchecks Docker.
- Tests unitarios, smoke test S7 real y CI en GitHub Actions.

## Arquitectura

```text
┌─────────────────┐   HTTP/WS   ┌──────────────────┐   Docker API   ┌─────────────┐
│  Frontend       │ ──────────► │  Backend         │ ─────────────► │  PLCs       │
│  Vanilla JS +   │             │  FastAPI +       │                │  Python +   │
│  Drawflow       │ ◄────────── │  SQLite          │ ◄───────────── │  snap7      │
└─────────────────┘   eventos   └──────────────────┘   health       └─────────────┘
```

El backend sirve tanto la API como el frontend. Cada escenario obtiene una red
Docker propia y cada dispositivo se ejecuta en un contenedor etiquetado y
gestionado por Anvil.

## Inicio rápido con Docker CLI

Requisitos: Docker Engine con Compose y acceso al socket Docker.

```bash
git clone https://github.com/marcosfnmr/Anvil.git
cd Anvil

docker build -t anvil/plc-s7:dev ./plc
docker compose up -d --build
curl http://localhost:8000/api/ready
```

Abrir `http://localhost:8000/` para usar el editor o
`http://localhost:8000/docs` para consultar OpenAPI.

Para detener solamente el backend:

```bash
docker compose down
```

Los PLCs desplegados desde Anvil se detienen desde la interfaz antes de ejecutar
`docker compose down`.

## Pruebas

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt -r plc/requirements.txt

python -m unittest discover -s plc/tests -v
python -m unittest discover -s backend/tests -v
```

Prueba real del protocolo S7:

```bash
PLC_CONFIG="$(python -c 'import json; print(json.dumps(json.load(open("example_config.json"))))')"
docker run --rm -d --name anvil-smoke-plc -p 1102:102 \
  -e PLC_CONFIG="$PLC_CONFIG" anvil/plc-s7:dev
python integration/s7_smoke.py --port 1102
docker rm -f anvil-smoke-plc
```

## Estructura

```text
Anvil/
├── plc/                    # runtime del PLC S7
├── backend/                # API, orquestador y frontend
│   ├── devices/            # adaptadores de dispositivos
│   ├── models/             # contratos y persistencia
│   ├── routes/             # HTTP y WebSocket
│   ├── services/           # Docker, eventos y reconciliación
│   ├── static/             # editor web
│   └── tests/
├── integration/            # smoke tests contra runtimes reales
├── docs/
│   ├── DESIGN.md
│   ├── OPERATIONS.md
│   └── ROADMAP.md
└── docker-compose.yml
```

## Extensibilidad

Anvil utiliza registries independientes para los componentes del PLC, los
dispositivos del backend y los nodos del frontend. El contrato completo está en
[`docs/DESIGN.md`](docs/DESIGN.md).

## Seguridad

El backend monta `/var/run/docker.sock`, por lo que debe ejecutarse únicamente
en una máquina o red de laboratorio confiable. La versión actual no incorpora
autenticación ni está preparada para exponerse directamente a Internet.

Consulta [`docs/OPERATIONS.md`](docs/OPERATIONS.md) antes de desplegar o
actualizar una instancia y [`docs/ROADMAP.md`](docs/ROADMAP.md) para conocer los
siguientes pasos.

## Licencia

MIT.
