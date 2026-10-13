# 🔨 Anvil

> The forge where OT cyber ranges are shaped.

**Manual completo:** consulta [la documentación de Anvil](docs/index.md) para
instalación, recorridos guiados, dispositivos, protocolos, operación, API,
seguridad y desarrollo. El sitio navegable se genera con `mkdocs build`.

Anvil es un framework modular para construir y desplegar laboratorios OT/ICS dockerizados desde un panel web. Permite diseñar escenarios, configurar dispositivos simulados y desplegarlos en redes Docker aisladas.

Anvil simula PLCs Siemens S7 mediante `python-snap7` y dispositivos Modbus TCP en contenedores independientes. El panel observa estado, valores, logs y permite operar los puntos con permisos configurados.

## Estado del proyecto

- Simulador PLC S7 parametrizable.
- Válvulas y motores booleanos, entradas digitales y alarmas; bombas con caudal derivado, y sensores analógicos constantes o senoidales.
- API FastAPI con persistencia SQLite.
- Redes y contenedores independientes por escenario.
- Editor visual con Drawflow.
- Enlaces S7 entre PLCs: entradas remotas BOOL/REAL, lectura TCP desde el receptor y diagnóstico de datos obsoletos. Consulta [la guía de enlaces](docs/S7_LINKS.md).
- Estado y logs en vivo mediante WebSocket.
- Mapa de memoria S7 (`DBX`/`DBD`) y consola para leer puntos y operar salidas permitidas desde la API de Anvil.
- Escritura de válvulas limitada a puntos BOOL marcados R/W.
- Periodo de scan S7 configurable por PLC (10–5000 ms, 100 ms por defecto).
- Bloques PLC por scan configurables desde el inspector: ON/OFF con histéresis, lógica BOOL AND/OR/XOR, SR, temporizadores TON/TOF, CTU por flanco ascendente y secuenciador one-hot (ver [lógica PLC](docs/PLC_LOGIC.md)). No es un runtime Ladder/ST completo.
- Nodo SCADA independiente que sondea S7/Modbus TCP, conserva tendencias acotadas y evalúa alarmas por umbral.
- Historial reciente de cambios y traza semántica de operaciones S7.
- Reconciliación del runtime, healthchecks Docker, tests y CI.
- Ciclo de vida por nodo desde el inspector: iniciar, detener y reiniciar contenedores individuales; acciones registradas en la auditoría del escenario.

La HMI consulta en vivo puntos S7/Modbus seleccionados desde el inspector y permite escribir BOOL/REAL S7, coils y holding registers mediante las operaciones gestionadas de Anvil cuando el mapa, rol y política autorizan esa operación. Las escrituras se originan dentro del contenedor HMI. El SCADA adquiere puntos seleccionados de forma autónoma en su contenedor, conserva muestras e historial en SQLite con retención limitada por escenario, presenta tendencias y alarmas, y permite operar las mismas salidas autorizadas mediante Anvil. Las entradas alimentadas por campo, discrete inputs e input registers aparecen como solo lectura en las operaciones gestionadas. Esta autorización de API/HMI/SCADA no se aplica automáticamente a un cliente S7/Modbus que se conecte directamente al servidor: las escrituras que permite el protocolo requieren restringir quién alcanza el puerto mediante red, firewall y publicación externa, desactivada por defecto. Por defecto el escenario usa una red plana; opcionalmente se pueden crear zonas Purdue con redes Docker internas separadas. Las rutas TCP explícitas permiten servicios S7/Modbus entre SCADA/HMI y PLCs remotos mediante gateways de flujo único; no hay routing L3 ni ACL general entre nodos de una misma zona. El modo de acceso externo está desactivado por defecto; si se activa, escucha en loopback salvo que se configure una IPv4 concreta del host. Gateway de catálogo sigue siendo representativo sin routing; la estación de participante y la estación de ingeniería quedan aisladas por defecto. La estación de ingeniería tiene terminal no-root opt-in para roles de operación; no es TIA Portal ni una réplica de hardware.

La traza S7 se basa en callbacks del simulador (cliente, DB, bytes, valor observado y resultado); no es un PCAP ni una captura completa de tramas. El historial de señales de la vista Telemetría vive en el navegador; el histórico SCADA separado se persiste en SQLite.

## Arquitectura

```text
┌─────────────────┐   HTTP/WS   ┌──────────────────┐   Docker API   ┌─────────────┐
│  Frontend       │ ──────────► │  Backend         │ ─────────────► │  PLCs       │
│  Vanilla JS +   │             │  FastAPI +       │                │  Python +   │
│  Drawflow       │ ◄────────── │  SQLite          │ ◄───────────── │  snap7      │
└─────────────────┘ estado/logs └──────────────────┘ health/logs    └─────────────┘
```

El backend sirve tanto la API como el frontend. Cada escenario obtiene una red Docker propia y cada dispositivo se ejecuta en un contenedor etiquetado y gestionado por Anvil. Las operaciones de consola se ejecutan desde el contenedor PLC hacia su propio servidor S7, manteniendo aislada la red OT.

## Inicio rápido con Docker CLI

Requisitos: Docker Engine 28 o posterior, Docker Compose v2 y acceso al socket
Docker. Engine 28 habilita el gateway `isolated` usado por las redes de
escenario para quitar la dirección bridge del host.

```bash
git clone https://github.com/marcosfnmr/Anvil.git
cd Anvil

docker build -t anvil/plc-s7:dev ./plc
docker compose up -d --build
curl http://localhost:8000/api/ready
```

Abrir `http://localhost:8000/` para usar el editor o `http://localhost:8000/docs` para consultar OpenAPI.

Para detener solamente el backend:

```bash
docker compose down
```

Los PLCs desplegados desde Anvil se detienen desde la interfaz antes de ejecutar `docker compose down`.

## Pruebas

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt -r plc/requirements.txt

python -m unittest discover -s plc/tests -v
python -m unittest discover -s backend/tests -v
```

Prueba integral de API, despliegue, lectura/escritura S7 y traza de protocolo contra el backend de Docker Compose:

```bash
export ANVIL_TEST_EMAIL=admin@example.test
read -r -s -p 'Contraseña de Anvil: ' ANVIL_TEST_PASSWORD; echo
export ANVIL_TEST_PASSWORD
docker cp integration/platform_smoke.py anvil-backend-1:/tmp/platform_smoke.py
docker exec -e ANVIL_TEST_EMAIL -e ANVIL_TEST_PASSWORD anvil-backend-1 python /tmp/platform_smoke.py
```

El helper hace login o bootstrap con el token que ya tenga el contenedor. Para
otros smokes autenticados, consulta [VALIDATION_LOCAL.md](docs/VALIDATION_LOCAL.md).

Consulta [la guía de acceso controlado a OT](docs/ACCESS.md) y [la guía de Modbus TCP](docs/MODBUS.md) para construir la imagen, configurar el escenario y ejecutar una prueba del protocolo desde Docker CLI.
Consulta también las [fichas de fidelidad de los emuladores](docs/DEVICE_FIDELITY.md) para conocer qué funciones están implementadas, qué pruebas las respaldan y qué diferencias conocidas hay frente a equipos reales.
Para añadir un nuevo tipo de dispositivo integrado, consulta la [guía de extensión de adaptadores](docs/EXTENDING_DEVICES.md); Anvil no carga plugins de terceros en ejecución.
Consulta [la guía SCADA](docs/SCADA.md) para configuración, tendencias, alarmas y su prueba Docker.
Para segmentar equipos en redes aisladas consulta [zonas Purdue](docs/NETWORK_ZONES.md).
Los eventos operativos persistentes y su exportación se describen en [auditoría por escenario](docs/AUDIT.md).
Los ejercicios de cronología, incidentes de protocolo acotados y objetivos están descritos en [ejercicios OT](docs/EXERCISES.md); el motor limita acciones a salidas permitidas y nodos del escenario.

Para diseñar primero la planta con sensores y actuadores independientes y
conectar después sus señales a los PLCs, consulta [elementos de campo](docs/FIELD_OBJECTS.md).

## Estructura

```text
Anvil/
├── plc/                    # runtime del PLC y captura semántica S7
├── backend/                # API, orquestador y frontend
│   ├── devices/            # adaptadores de dispositivos
│   ├── models/             # contratos y persistencia
│   ├── routes/              # HTTP y WebSocket
│   ├── services/            # Docker, E/S, eventos y logs
│   ├── static/              # editor web
│   └── tests/
├── integration/             # smoke tests contra runtimes reales
└── docs/                    # diseño, operación y hoja de ruta
```

## Seguridad

Los contenedores de escenario se ejecutan sin root, con FS raíz de solo lectura, capacidades reducidas y límites de recursos. El backend monta un gateway del Docker API, que conserva autoridad elevada sobre el host; ejecuta Anvil solo en una máquina o red de laboratorio confiable. Hay autenticación local básica con roles y aislamiento por escenario, pero aún no está preparada para exposición directa a Internet. Consulta [cuentas y permisos](docs/PLATFORM_ACCESS.md) y [confinamiento de contenedores](docs/CONTAINER_SECURITY.md).

Consulta [`docs/OPERATIONS.md`](docs/OPERATIONS.md) antes de desplegar o actualizar una instancia y [`docs/ROADMAP.md`](docs/ROADMAP.md) para conocer los siguientes pasos.

La trazabilidad del borrador de requisitos de la plataforma Cyber Range OT,
incluidos los límites actuales y el plan por etapas, está en
[`docs/SRS_TRACEABILITY.md`](docs/SRS_TRACEABILITY.md).
El texto fuente v1.0 preservado y verificado contra el adjunto está en
[`docs/SRS_REQUIREMENTS_v1.md`](docs/SRS_REQUIREMENTS_v1.md).

## Licencia

MIT.
