# 🔨 Anvil

> The forge where OT cyber ranges are shaped.

Anvil es un framework modular para desplegar entornos OT/ICS dockerizados desde
un panel web. Arrastras dispositivos a un canvas, configuras sus componentes
virtuales, y con un clic se despliegan como contenedores aislados listos para
pentesting educativo.

Empezamos con PLCs Siemens S7 simulados. La arquitectura está pensada para
escalar a otros dispositivos y protocolos (Modbus, EtherNet/IP, HMIs...) sin
tocar el orquestador.

---

## 🎯 MVP

- **Dispositivo:** PLC Siemens S7 (simulado con `python-snap7`)
- **Componentes virtuales:** válvula (booleana), sensor analógico (REAL)
- **Panel web:** canvas con drag & drop y deploy en un clic
- **Backend:** FastAPI que orquesta Docker
- **Frontend:** vanilla JS + Drawflow, sin build step

---

## 🏗️ Arquitectura

```
┌─────────────────┐   HTTP/WS   ┌──────────────────┐   Docker API   ┌─────────────┐
│  Frontend       │ ──────────► │  Backend         │ ─────────────► │  PLCs       │
│  (vanilla JS +  │             │  (FastAPI +      │                │  (Python +  │
│   Drawflow CDN) │ ◄────────── │   docker-py)     │ ◄───────────── │   snap7)    │
└─────────────────┘   estado    └──────────────────┘   status       └─────────────┘
```

Ver [`docs/DESIGN.md`](docs/DESIGN.md) para el diseño detallado (schemas,
API, patrones de extensibilidad).

---

## 🚀 Empezar

_Instrucciones en desarrollo. Ver el plan por fases en `docs/DESIGN.md`._

```bash
# En construcción
docker compose up --build
```

---

## 📁 Estructura

```
anvil/
├── plc/                  # imagen Docker del PLC S7 (Fase 1)
├── backend/              # FastAPI + frontend estático (Fases 2-3)
│   └── static/           # HTML + JS + CSS del panel
├── docs/
│   └── DESIGN.md         # diseño técnico y contratos
└── docker-compose.yml    # orquestación de dev
```

---

## 🧩 Extensibilidad

Anvil se apoya en cuatro registries independientes para minimizar el
acoplamiento entre capas:

1. **`COMPONENT_REGISTRY`** (PLC) — tipos de componentes simulados
2. **`DEVICE_REGISTRY`** (backend) — tipos de dispositivo dockerizable
3. **`NODE_REGISTRY`** (frontend) — tipos de nodo del canvas
4. **Discriminated unions** (Pydantic) — validación automática de configs

Añadir un componente, dispositivo o nodo nuevo es siempre crear un archivo y
registrarlo. Ver ejemplos en `docs/DESIGN.md`.

---

## 📜 Licencia

MIT.
