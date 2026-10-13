# Documento de Requisitos — Plataforma Cyber Range OT

**Proyecto:** Plataforma de diseño y despliegue de entornos industriales (OT) emulados sobre contenedores
**Versión:** 1.0 (borrador)
**Tipo de documento:** Especificación de Requisitos del Sistema (SRS)
**Estado:** Para revisión

---

## 1. Introducción

### 1.1 Propósito
Este documento define los requisitos funcionales y no funcionales de una plataforma tipo *cyber range* orientada a tecnología de operación (OT). La plataforma permite diseñar visualmente —mediante un editor por nodos— un entorno industrial completo (PLC, SCADA, HMI, sensores, actuadores, válvulas, proceso físico y red), desplegarlo automáticamente sobre redes Docker y configurarlo con la mayor fidelidad posible respecto a un entorno OT real.

El objetivo de fidelidad es doble: (a) servir como banco de pruebas de ciberseguridad industrial (entrenamiento, investigación, pruebas de herramientas) y (b) alcanzar un nivel de realismo tal que un componente emulado de la plataforma pueda **sustituir temporalmente a un dispositivo real** en un entorno OT, respondiendo al mismo protocolo y lógica de proceso.

### 1.2 Alcance
La plataforma cubre:
- Diseño gráfico del entorno industrial mediante nodos y conexiones.
- Biblioteca de componentes OT emulados y configurables.
- Emulación de protocolos industriales (Modbus, S7/S7comm como mínimo).
- Simulación del proceso físico asociado (dinámica de tanques, caudales, presiones, etc.).
- Orquestación y despliegue automático en contenedores y redes Docker segmentadas.
- Monitorización, captura de tráfico y registro de eventos.
- Módulo de escenarios de ataque/defensa (*red team* / *blue team*).
- Gestión de usuarios, proyectos y plantillas.

Queda fuera del alcance en esta versión: emulación de hardware físico a nivel de señal eléctrica, certificación de seguridad funcional (SIL/IEC 61508) y despliegue sobre hardware industrial real (ver sección 13, fuera de alcance).

### 1.3 Definiciones y acrónimos

| Término | Definición |
|---------|------------|
| OT | Operational Technology — tecnología que controla procesos físicos industriales |
| PLC | Controlador Lógico Programable |
| SCADA | Supervisory Control and Data Acquisition |
| HMI | Interfaz Hombre-Máquina |
| RTU | Remote Terminal Unit |
| IED | Intelligent Electronic Device |
| Modbus | Protocolo industrial (TCP/RTU) basado en registros y bobinas |
| S7comm | Protocolo propietario de comunicación de PLC Siemens S7 |
| OPC UA | Open Platform Communications Unified Architecture |
| Modelo Purdue | Modelo de referencia de segmentación de redes ICS por niveles (0–5) |
| PDU | Protocol Data Unit — unidad de datos de protocolo |
| Nodo | Elemento gráfico del editor que representa un componente, conexión o lógica |
| Topología | Conjunto de nodos y conexiones que define un escenario |
| Escenario | Topología + configuración + condiciones de proceso + eventos |

### 1.4 Referencias
- IEC 62443 (seguridad en sistemas de automatización industrial).
- Modelo de referencia Purdue (ISA-95 / ISA-99).
- Especificación Modbus Application Protocol v1.1b3.
- Documentación de protocolos S7comm / ISO-TSAP (RFC 1006).

---

## 2. Descripción general

### 2.1 Visión del sistema
La plataforma es una aplicación web de tipo "diseñar y desplegar". El usuario construye una maqueta industrial arrastrando y conectando nodos en un lienzo. Cada nodo representa un dispositivo OT, un bloque de proceso físico, un segmento de red o un elemento de lógica. Al pulsar *desplegar*, el motor de orquestación traduce la topología a contenedores Docker interconectados por redes virtuales segmentadas, inicia los emuladores correspondientes y expone los puntos de interacción (HMI web, consolas, interfaces de protocolo, captura de tráfico).

### 2.2 Actores / interesados

| Actor | Descripción | Necesidad principal |
|-------|-------------|---------------------|
| Diseñador de escenarios | Construye topologías y configura dispositivos | Editor flexible y biblioteca completa |
| Instructor / operador de range | Lanza ejercicios y supervisa participantes | Control de despliegue, monitorización, inyección de eventos |
| Participante (alumno / analista) | Interactúa con el entorno (ataca o defiende) | Acceso realista a dispositivos y red |
| Investigador / ingeniero OT | Usa componentes como sustitutos de equipos reales | Fidelidad de protocolo y exportabilidad de configuración |
| Administrador de plataforma | Gestiona usuarios, recursos y seguridad | Control de accesos, cuotas, aislamiento |

### 2.3 Contexto operativo
La plataforma se despliega sobre uno o varios hosts con Docker. Puede operar aislada (laboratorio cerrado) o con salida controlada a redes externas. El aislamiento entre escenarios y entre niveles de la red simulada es un requisito central de seguridad.

---

## 3. Requisitos funcionales

### 3.1 Editor visual por nodos

- **RF-01** El sistema debe ofrecer un lienzo de diseño donde el usuario pueda crear, mover, conectar y eliminar nodos mediante interacción de arrastrar y soltar.
- **RF-02** Cada nodo debe exponer un panel de propiedades editable (parámetros de red, protocolo, direcciones de memoria, lógica, etc.) coherente con el tipo de componente.
- **RF-03** Las conexiones entre nodos deben poder representar al menos: (a) enlaces de red lógicos, (b) enlaces de proceso físico (tubería, señal de sensor/actuador) y (c) relaciones de control. El sistema debe distinguirlos visualmente.
- **RF-04** El editor debe validar la topología antes del despliegue (p. ej. conflictos de IP, nodos sin red asignada, lazos de control incompletos) y mostrar errores y advertencias accionables.
- **RF-05** El usuario debe poder guardar, versionar, duplicar y exportar/importar topologías en un formato declarativo legible (p. ej. JSON o YAML).
- **RF-06** El sistema debe permitir agrupar nodos en zonas/subredes que representen niveles del modelo Purdue (nivel 0 proceso, nivel 1 control, nivel 2 supervisión, nivel 3 operaciones, DMZ).
- **RF-07** El sistema debe ofrecer plantillas predefinidas de escenarios (p. ej. planta de tratamiento de agua, línea de embotellado, subestación eléctrica) como punto de partida.

### 3.2 Biblioteca de componentes OT

- **RF-08** La plataforma debe incluir, como mínimo, los siguientes tipos de nodo configurables: PLC, SCADA, HMI, RTU/gateway, sensor (analógico y digital), actuador, válvula (on/off y proporcional), variador/motor, switch/router de red y host genérico (estación de ingeniería, jump host).
- **RF-09** Cada PLC debe permitir definir su lógica de control de forma configurable (p. ej. lenguaje tipo IEC 61131-3 — Ladder / ST — o equivalente del emulador utilizado) y asociar entradas/salidas a sensores y actuadores de la topología.
- **RF-10** Cada dispositivo debe permitir configurar su mapa de memoria / direccionamiento del protocolo (registros Modbus holding/input, coils, discrete inputs; DB, marcas, entradas/salidas en S7) de forma explícita y editable.
- **RF-11** Los sensores deben generar valores derivados del modelo de proceso físico (no valores aleatorios), y los actuadores deben influir de vuelta sobre dicho modelo, cerrando el lazo de control.
- **RF-12** Cada HMI debe ser accesible como interfaz gráfica (preferentemente web) que muestre el estado del proceso en tiempo real y permita operar sobre él (consignas, arranques/paradas, reconocimiento de alarmas).
- **RF-13** El SCADA debe poder adquirir datos de múltiples dispositivos por sus protocolos nativos, presentar tendencias/alarmas y actuar sobre el proceso.
- **RF-14** La biblioteca debe ser extensible: debe existir un mecanismo documentado para añadir nuevos tipos de nodo/dispositivo (plugin, plantilla de contenedor + descriptor de propiedades).

### 3.3 Emulación de protocolos industriales

- **RF-15** El sistema debe emular, como mínimo, **Modbus (TCP y RTU)** y **S7 / S7comm**, respondiendo con fidelidad a nivel de función (function codes de Modbus; servicios de lectura/escritura de S7).
- **RF-16** Los emuladores deben comportarse como esclavos/servidores reales: responder a clientes externos estándar (p. ej. herramientas de ingeniería, escáneres, clientes `pymodbus`/`snap7`) sin que estos distingan trivialmente el emulador de un equipo real.
- **RF-17** El sistema debe permitir configurar parámetros de comportamiento del protocolo: unit/slave ID, puertos, tiempos de respuesta, latencias, errores inducidos y excepciones.
- **RF-18** La arquitectura de protocolos debe ser modular y preparada para incorporar en fases posteriores: OPC UA, DNP3, IEC 60870-5-104, EtherNet/IP, Profinet e IEC 61850 (ver sección 13).
- **RF-19** El sistema debe reproducir, de forma configurable, características de temporización realistas: tiempo de ciclo del PLC (scan cycle), periodos de sondeo del SCADA y retardos de red.

### 3.4 Simulación del proceso físico

- **RF-20** El sistema debe incluir un motor de simulación de proceso que calcule la evolución de las variables físicas (nivel, caudal, presión, temperatura, estado de equipos) en función del tiempo y de las acciones de los actuadores.
- **RF-21** El usuario debe poder definir el modelo de proceso asociado a la topología (ecuaciones, bloques, o parámetros de modelos predefinidos como tanques, bombas, tuberías, reactores).
- **RF-22** El motor de proceso debe sincronizarse con los PLC de modo que la lógica de control emulada actúe realmente sobre la física simulada (lazo cerrado determinista y reproducible).
- **RF-23** El sistema debe permitir inyectar perturbaciones en el proceso (fugas, fallos de sensor, bloqueo de válvula, cambios de demanda) para generar condiciones anómalas.

### 3.5 Orquestación y despliegue en Docker

- **RF-24** El sistema debe traducir automáticamente una topología a un despliegue de contenedores Docker, un contenedor (o conjunto) por nodo activo, sin intervención manual del usuario.
- **RF-25** El sistema debe crear redes Docker virtuales segmentadas que reproduzcan la segmentación de la topología (subredes por zona/nivel Purdue), con enrutamiento y aislamiento configurables entre segmentos.
- **RF-26** El sistema debe asignar a cada dispositivo direcciones IP, nombres de host y puertos coherentes con la configuración del nodo, y garantizar que no haya conflictos.
- **RF-27** El sistema debe permitir operaciones de ciclo de vida sobre el escenario completo o por nodo: desplegar, iniciar, detener, reiniciar, pausar y destruir.
- **RF-28** El estado de despliegue (contenedores activos, salud, uso de recursos) debe reflejarse en tiempo real en la interfaz, mapeado sobre los nodos del lienzo.
- **RF-29** El sistema debe soportar el despliegue repetido y reproducible de un mismo escenario (idempotencia): dos despliegues de la misma topología producen el mismo entorno.
- **RF-30** El sistema debe permitir exportar el despliegue como artefactos independientes (p. ej. `docker-compose` y ficheros de configuración) para su ejecución fuera de la plataforma.

### 3.6 Fidelidad y sustituibilidad (requisito diferencial)

- **RF-31** La configuración de un dispositivo emulado debe poder exportarse e importarse en un formato que facilite su correspondencia con la configuración de un equipo real equivalente.
- **RF-32** El sistema debe permitir que un dispositivo emulado quede expuesto en una interfaz de red accesible externamente (modo *sustituto*), de forma que un maestro/cliente real del entorno OF/OT pueda comunicarse con él como si fuera el equipo original.
- **RF-33** El sistema debe documentar, por cada emulador, su nivel de fidelidad y sus desviaciones conocidas respecto al dispositivo real (limitaciones, funciones no soportadas).

### 3.7 Monitorización, captura y registro

- **RF-34** El sistema debe permitir capturar el tráfico de red de cualquier segmento o enlace del escenario (p. ej. exportable en formato PCAP).
- **RF-35** El sistema debe registrar eventos de proceso, de protocolo y de ciclo de vida de contenedores, con marca de tiempo y trazabilidad por escenario.
- **RF-36** El sistema debe ofrecer una vista de telemetría en tiempo real de las variables de proceso y del estado de los dispositivos.
- **RF-37** Los registros y capturas deben poder asociarse a un escenario y exportarse para análisis posterior.

### 3.8 Módulo de escenarios de ataque/defensa

- **RF-38** El sistema debe permitir definir y lanzar escenarios de ejercicio con objetivos, cronología de eventos e inyección de incidentes (ataques de protocolo, manipulación de registros, denegación de servicio, man-in-the-middle).
- **RF-39** El sistema debe permitir habilitar puntos de acceso para participantes (p. ej. una estación atacante preconfigurada dentro de un segmento concreto).
- **RF-40** El sistema debe permitir evaluar el estado del proceso frente a condiciones de éxito/fallo del ejercicio (p. ej. "el atacante consiguió abrir la válvula", "la defensa detectó la escritura no autorizada").

### 3.9 Gestión de usuarios y proyectos

- **RF-41** El sistema debe soportar autenticación de usuarios y control de acceso basado en roles (administrador, instructor, diseñador, participante).
- **RF-42** El sistema debe aislar los proyectos/escenarios por usuario u organización, evitando interferencias entre despliegues concurrentes.
- **RF-43** El sistema debe permitir gestionar una biblioteca compartida de plantillas y componentes.

---

## 4. Requisitos no funcionales

### 4.1 Fidelidad / realismo
- **RNF-01** Los emuladores de protocolo deben superar pruebas de interoperabilidad con clientes/maestros estándar del sector sin modificaciones en dichos clientes.
- **RNF-02** El comportamiento temporal (ciclos, latencias) debe ser configurable dentro de rangos realistas y reproducible entre ejecuciones.

### 4.2 Rendimiento y escalabilidad
- **RNF-03** El sistema debe soportar escenarios de, al menos, decenas de dispositivos simultáneos por host, con un objetivo de escalado a cientos mediante múltiples hosts/orquestación distribuida.
- **RNF-04** El despliegue de un escenario de tamaño medio (p. ej. 20–30 nodos) debe completarse en un tiempo acotado y comunicado en la interfaz (objetivo orientativo: pocos minutos).
- **RNF-05** La interfaz de diseño debe mantener respuesta fluida con topologías de al menos 100 nodos.

### 4.3 Usabilidad
- **RNF-06** Un usuario con conocimientos OT básicos debe poder construir y desplegar un escenario de plantilla sin escribir código.
- **RNF-07** Los mensajes de error de validación y despliegue deben ser claros y orientados a la resolución.

### 4.4 Seguridad y aislamiento
- **RNF-08** Los escenarios deben ejecutarse aislados entre sí y respecto al host; ningún tráfico malicioso del ejercicio debe poder escapar sin configuración explícita.
- **RNF-09** El sistema debe aplicar el principio de mínimo privilegio en los contenedores y en el acceso al demonio Docker.
- **RNF-10** El acceso externo (modo sustituto, salida a Internet) debe estar deshabilitado por defecto y requerir activación explícita.
- **RNF-11** Las credenciales, configuraciones y capturas deben almacenarse de forma protegida.

### 4.5 Portabilidad y mantenibilidad
- **RNF-12** La plataforma debe ejecutarse sobre hosts Linux con Docker, sin dependencia de hardware industrial específico.
- **RNF-13** La arquitectura debe ser modular (editor, orquestador, catálogo de emuladores, motor de proceso) con interfaces bien definidas que permitan sustituir o ampliar cada módulo.
- **RNF-14** Cada tipo de componente/emulador debe estar empaquetado como imagen de contenedor versionada y reproducible.

### 4.6 Observabilidad y fiabilidad
- **RNF-15** El sistema debe detectar y reportar contenedores caídos o en mal estado y permitir su recuperación sin destruir el escenario completo.
- **RNF-16** Las operaciones de despliegue deben ser atómicas en lo posible: un fallo parcial debe dejar el escenario en un estado conocido y recuperable.

---

## 5. Arquitectura de referencia (orientativa)

La arquitectura propuesta (no prescriptiva) se organiza en capas:

1. **Capa de presentación** — Aplicación web con el editor por nodos, paneles de configuración, vistas de telemetría y acceso a HMIs.
2. **Capa de aplicación / API** — Servicio que gestiona proyectos, valida topologías, traduce la topología a un plan de despliegue y expone una API.
3. **Capa de orquestación** — Componente que interactúa con Docker (SDK/API) para crear redes, contenedores, volúmenes y gestionar el ciclo de vida.
4. **Capa de emulación** — Catálogo de imágenes: emuladores de PLC, servidores de protocolo (Modbus, S7), SCADA, HMI, motor de proceso, herramientas de red y ataque.
5. **Capa de datos** — Persistencia de proyectos, plantillas, configuraciones, usuarios, registros y capturas.

El **mapeo topología → despliegue** es el núcleo del sistema: cada tipo de nodo se asocia a una imagen y a una plantilla de configuración parametrizable; cada zona a una red Docker; cada enlace a reglas de conectividad.

---

## 6. Restricciones técnicas

- **RT-01** El despliegue de los entornos se realiza obligatoriamente sobre **contenedores y redes Docker**.
- **RT-02** La emulación de protocolo debe basarse preferentemente en componentes maduros y de fidelidad contrastada (p. ej. runtimes de PLC de código abierto, librerías de protocolo consolidadas) antes que en reimplementaciones propias, salvo necesidad justificada.
- **RT-03** El formato de topología debe ser declarativo, versionable y legible por humanos.
- **RT-04** La plataforma no debe requerir hardware OT real para funcionar.

---

## 7. Interfaces

- **IF-01** Interfaz de usuario web (editor, configuración, monitorización).
- **IF-02** API de la plataforma para automatización (crear/desplegar/destruir escenarios programáticamente).
- **IF-03** Interfaces de protocolo industrial expuestas por cada dispositivo emulado (Modbus/TCP, S7, etc.).
- **IF-04** Interfaz con el demonio Docker (orquestación).
- **IF-05** Interfaz de exportación de artefactos (topología, `docker-compose`, configuraciones, PCAP, registros).

---

## 8. Datos

- **DT-01** Modelo de topología (nodos, conexiones, zonas, propiedades).
- **DT-02** Configuraciones por dispositivo (red, protocolo, mapa de memoria, lógica).
- **DT-03** Modelos de proceso físico.
- **DT-04** Catálogo de componentes y plantillas.
- **DT-05** Datos de usuarios, roles y proyectos.
- **DT-06** Registros de eventos, telemetría y capturas de tráfico.

---

## 9. Supuestos y dependencias

- **SU-01** Se dispone de uno o varios hosts con Docker correctamente instalado y recursos suficientes (CPU, RAM, red).
- **SU-02** El entorno de ejecución es un laboratorio controlado; cualquier salida a producción o a redes reales es responsabilidad del operador y se realiza bajo configuración explícita.
- **SU-03** El uso de la plataforma para pruebas de ciberseguridad se realiza con autorización y en entornos legítimos.

---

## 10. Criterios de aceptación (alto nivel)

- **CA-01** Un usuario puede diseñar por nodos un escenario con al menos PLC + SCADA + HMI + sensores + válvulas, desplegarlo en Docker y verlo operativo.
- **CA-02** Un cliente Modbus estándar externo lee y escribe correctamente registros del PLC emulado; un cliente S7 estándar interactúa con el PLC S7 emulado.
- **CA-03** Al actuar sobre un actuador desde el HMI/SCADA, el proceso físico simulado reacciona de forma coherente y los sensores reflejan el cambio.
- **CA-04** El tráfico del escenario puede capturarse y exportarse en PCAP, y los eventos quedan registrados.
- **CA-05** Dos despliegues de la misma topología generan entornos equivalentes (reproducibilidad).
- **CA-06** Los escenarios se ejecutan aislados y sin fuga de tráfico por defecto.
- **CA-07** La configuración de un dispositivo emulado puede exportarse para su correspondencia con un equipo real (modo sustituto demostrable en, al menos, un protocolo).

---

## 11. Priorización (MoSCoW)

| Prioridad | Requisitos representativos |
|-----------|----------------------------|
| **Must** | RF-01..06, RF-08..13, RF-15..17, RF-20..22, RF-24..29, RF-34..36, RNF-01, RNF-03, RNF-08..10 |
| **Should** | RF-07, RF-14, RF-18, RF-19, RF-30..33, RF-38..40, RNF-02, RNF-13, RNF-15 |
| **Could** | RF-41..43, plantillas avanzadas, orquestación distribuida |
| **Won't (esta versión)** | Protocolos avanzados completos (IEC 61850, Profinet), simulación de señal eléctrica, alta disponibilidad |

---

## 12. Fases sugeridas (orientativo, no contractual)

1. **Fase 1 — Núcleo:** editor por nodos + orquestador Docker + PLC Modbus + motor de proceso + HMI básico.
2. **Fase 2 — Fidelidad:** emulación S7, segmentación Purdue, temporización realista, captura PCAP.
3. **Fase 3 — Cyber range:** SCADA completo, módulo de ataque/defensa, telemetría avanzada.
4. **Fase 4 — Escala y sustituibilidad:** modo sustituto, exportación de configuraciones, orquestación multi-host, protocolos adicionales.

---

## 13. Fuera de alcance (esta versión)

- Emulación eléctrica/física a nivel de señal analógica real.
- Soporte completo y certificado de todos los protocolos industriales existentes.
- Integración directa con hardware OT real en producción.
- Alta disponibilidad y tolerancia a fallos a nivel de plataforma.
- Certificaciones de seguridad funcional.

---

## 14. Riesgos y mitigaciones (resumen)

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Fidelidad insuficiente de emuladores | Alto | Apoyarse en runtimes/librerías consolidadas; pruebas de interoperabilidad (RNF-01) |
| Fuga de tráfico malicioso del range | Alto | Aislamiento por defecto, redes segmentadas, salida bajo demanda (RNF-08, RNF-10) |
| Complejidad del mapeo topología→Docker | Medio | Diseño modular, formato declarativo, despliegue idempotente (RF-29) |
| Escalado de recursos | Medio | Orquestación distribuida en fase posterior (RNF-03) |
| Uso indebido de la plataforma | Alto | Control de acceso, entorno controlado, uso autorizado (SU-03) |

---

*Documento preparado como base de trabajo. Los valores numéricos orientativos (tiempos, tamaños) deben ajustarse tras validar el entorno de ejecución y las expectativas de los interesados.*
