# Anvil — Diseño técnico

> Este documento es el **contrato de referencia** del proyecto. Se pinea
> al principio de cada conversación con Codex para que el contexto sea
> siempre el mismo.

---

## 1. Stack

| Capa      | Tecnología                                       |
| --------- | ------------------------------------------------ |
| PLC       | Python 3.11 + python-snap7 (>= 3.0)              |
| Backend   | FastAPI + Pydantic + SQLModel + docker-py        |
| Frontend  | HTML + CSS + JavaScript vanilla + Drawflow (CDN) |
| Orquest.  | Docker Compose (dev) + docker-py (runtime)       |
| Persist.  | SQLite                                           |

Un único proceso FastAPI sirve la API y el frontend estático. Sin CORS.
Sin bundler. Sin node en el flujo de desarrollo.

---

## 2. Modelo de datos

### 2.1 Config del PLC (`PLC_CONFIG` env var)

Lo que recibe cada contenedor `plc-s7` al arrancar, como JSON en una env var:

```json
{
  "plc_id": "plc-01",
  "rack": 0,
  "slot": 1,
  "db_size": 32,
  "components": [
    {
      "type": "valve",
      "name": "valvula_principal",
      "db": 1,
      "byte_offset": 0,
      "bit_offset": 0,
      "initial_state": false
    },
    {
      "type": "analog_sensor",
      "name": "presion",
      "db": 1,
      "byte_offset": 2,
      "unit": "bar",
      "min_value": 0.0,
      "max_value": 10.0,
      "simulation": {
        "mode": "sine_wave",
        "period_seconds": 30,
        "noise": 0.1
      }
    }
  ]
}
```

**Tipos de componentes soportados en el MVP:**

- `valve` — booleano en un bit. Campos: `db`, `byte_offset`, `bit_offset`,
  `initial_state`.
- `analog_sensor` — REAL (4 bytes IEEE-754 big-endian). Campos: `db`,
  `byte_offset`, `unit`, `min_value`, `max_value`, `simulation`.
  - `simulation.mode`: `constant` | `sine_wave`
  - `simulation.period_seconds`: solo para `sine_wave`
  - `simulation.noise`: float 0.0–1.0

**Codificación S7 (big-endian):**

- `BOOL`: un bit dentro de un byte (`byte_offset.bit_offset`, ej. `0.0`)
- `INT`: 2 bytes con signo (`struct.pack('>h', ...)`)
- `REAL`: 4 bytes IEEE-754 (`struct.pack('>f', ...)`)

### 2.2 Escenario (persistido en SQLite)

```json
{
  "id": "uuid4",
  "name": "linea_embotellado",
  "network": {
    "subnet": "192.168.100.0/24"
  },
  "devices": [
    {
      "node_id": "node-1",
      "device_type": "plc-s7",
      "name": "plc-valvula",
      "ip": "192.168.100.10",
      "config": {
        "components": [
          { "type": "valve", "name": "valvula_1", "db": 1 },
          { "type": "analog_sensor", "name": "presion", "db": 1, "simulation": { "mode": "sine_wave", "period_seconds": 30 } }
        ]
      }
    }
  ],
  "status": "stopped",
  "created_at": "..."
}
```

**Cálculo de offsets:** el usuario NO especifica `byte_offset` ni
`bit_offset`. El backend los calcula automáticamente al persistir el
escenario, en orden de declaración de componentes por cada DB.

---

## 3. API del backend

```
POST   /api/scenarios                    crear
GET    /api/scenarios                    listar
GET    /api/scenarios/{id}               detalle
PUT    /api/scenarios/{id}               actualizar topología
DELETE /api/scenarios/{id}               borrar

POST   /api/scenarios/{id}/deploy        levantar contenedores
POST   /api/scenarios/{id}/stop          parar y limpiar
GET    /api/scenarios/{id}/status        estado de contenedores

WS     /api/scenarios/{id}/events        (Fase 4) status en vivo
```

El frontend estático se sirve desde `/`. La API está bajo `/api/*`.

---

## 4. Estructura del código

```
anvil/
├── plc/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── config_schema.py
│   └── components/
│       ├── __init__.py        # COMPONENT_REGISTRY + register_component()
│       ├── base.py            # Component (ABC)
│       ├── valve.py
│       └── analog_sensor.py
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── models/
│   │   ├── scenario.py        # Pydantic + SQLModel
│   │   └── db.py
│   ├── devices/
│   │   ├── __init__.py        # DEVICE_REGISTRY + register_device()
│   │   ├── base.py            # Device (ABC) con build_container_config()
│   │   └── plc_s7.py
│   ├── services/
│   │   ├── docker_manager.py  # wrapper docker-py
│   │   └── scenario_service.py
│   ├── routes/
│   │   ├── scenarios.py
│   │   └── health.py
│   └── static/
│       ├── index.html
│       ├── style.css
│       ├── app.js
│       ├── api.js
│       ├── canvas.js
│       ├── inspector.js
│       ├── nodes/
│       │   ├── registry.js
│       │   └── plc-s7.js
│       └── components/
│           ├── registry.js
│           ├── valve.js
│           └── analog_sensor.js
│
├── docker-compose.yml
└── docs/
    └── DESIGN.md
```

---

## 5. Patrones de extensibilidad

### 5.1 Registry de componentes (PLC)

```python
# plc/components/__init__.py
COMPONENT_REGISTRY = {}

def register_component(type_name):
    def decorator(cls):
        COMPONENT_REGISTRY[type_name] = cls
        return cls
    return decorator

# plc/components/valve.py
from . import register_component
from .base import Component

@register_component("valve")
class Valve(Component):
    def initialize(self, db): ...
    def simulate(self, db, dt): ...
```

**Añadir un motor:** crear `components/motor.py`, decorar, listo. Nada más
cambia.

### 5.2 Registry de dispositivos (backend)

```python
# backend/devices/__init__.py
DEVICE_REGISTRY = {}

def register_device(type_name):
    def decorator(cls):
        DEVICE_REGISTRY[type_name] = cls
        return cls
    return decorator

# backend/devices/plc_s7.py
@register_device("plc-s7")
class PlcS7Device(Device):
    image = "anvil/plc-s7:latest"
    exposed_ports = [102]

    def build_container_config(self, node_data) -> dict:
        return {
            "image": self.image,
            "environment": {"PLC_CONFIG": json.dumps(node_data["config"])},
            "networking_config": ...,
        }
```

### 5.3 Registry de nodos (frontend)

```javascript
// backend/static/nodes/registry.js
export const NODE_REGISTRY = {};
export function registerNodeType(type, def) {
  NODE_REGISTRY[type] = def;
}

// backend/static/nodes/plc-s7.js
import { registerNodeType } from './registry.js';
registerNodeType('plc-s7', {
  label: 'PLC S7',
  icon: '🏭',
  color: '#3498db',
  inspectorFields: [
    { name: 'name', type: 'text', label: 'Nombre' },
    { name: 'components', type: 'component-list', allowed: ['valve', 'analog_sensor'] }
  ]
});
```

### 5.4 Discriminated union (Pydantic)

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field

class ValveComponent(BaseModel):
    type: Literal["valve"]
    name: str
    db: int
    byte_offset: int
    bit_offset: int
    initial_state: bool = False

class AnalogSensorComponent(BaseModel):
    type: Literal["analog_sensor"]
    name: str
    db: int
    byte_offset: int
    unit: str
    min_value: float
    max_value: float
    simulation: SimulationConfig

Component = Annotated[
    Union[ValveComponent, AnalogSensorComponent],
    Field(discriminator="type"),
]
```

Añadir un tipo = añadir la clase y meterla en la Union.

---

## 6. Plan de fases

- **Fase 1** — Simulador PLC parametrizable (imagen Docker que arranca
  snap7.Server con `PLC_CONFIG`)
- **Fase 2** — Backend orquestador (FastAPI + SQLite + docker-py)
- **Fase 3** — Frontend canvas (Drawflow + registries)
- **Fase 4** — Estado en vivo (WebSocket)

Cada fase entrega algo verificable de forma independiente.

---

## 7. Restricciones y decisiones ya cerradas

- `PLC_CONFIG` se pasa **solo como env var** (JSON string). No volúmenes.
- El backend calcula offsets automáticamente. El usuario nunca los ve.
- Un solo servicio FastAPI sirve API + frontend. Sin CORS.
- Sin build step en el frontend. Vanilla JS + CDN.
- Docker socket montado en el backend (`/var/run/docker.sock`).
- Red por escenario, no compartida.
