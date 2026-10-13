# Fichas de fidelidad de los emuladores

Este documento describe lo que implementa el checkout actual de Anvil y separa
las pruebas disponibles de las funciones que todavía no se han caracterizado.
“Compatible con un cliente” no significa equivalente a un PLC físico ni
certificado para operación industrial.

Es una ficha de alcance y evidencia, no sustituye las guías que explican cada
concepto desde cero. Para entender qué representa cada equipo, qué configura el
escenario y cómo se conecta con los demás, consulta [PLC S7](plc-s7.md),
[Modbus TCP/RTU](MODBUS.md), [elementos de campo](FIELD_OBJECTS.md), [HMI](hmi.md),
[SCADA](SCADA.md) y [zonas/rutas de red](NETWORK_ZONES.md). Esta página concentra
qué comportamientos se han probado y cuáles siguen sin caracterizarse.

## PLC Siemens S7

Para una explicación funcional orientada a quien empieza, consulta
[PLC S7 en Anvil](plc-s7.md): define el PLC, explica cliente/servidor S7,
memoria, configuración, conexiones y límites del modelo.

| Área | Implementación y límite |
|---|---|
| Servidor | Servidor S7 construido con `python-snap7`, TCP/102. La publicación al host está desactivada por defecto; si se solicita, Anvil crea un relay y binding dedicados. `integration/external_bundle_smoke.py` comprobó lectura/escritura de DB mediante Snap7 desde Linux del host contra el bundle Compose. Una sonda desde Python estándar de Windows/WSL llegó al handshake COTP, pero no probó leer/escribir DB S7 desde Windows. Otra máquina y publicación en una interfaz LAN siguen sin verificar. Véase [Acceso controlado](ACCESS.md). |
| Datos | DB heredados y áreas separadas M/I/Q registradas con Snap7. Canales de E/S BOOL/REAL calculan offsets por espacio por defecto; `io_channel` permite fijar byte y, para BOOL, bit con validación de solapes y tamaño. HMI/SCADA y el mapa API/UI leen las áreas; DB/M/Q pueden escribirse según permisos, mientras I se ofrece como entrada de solo lectura en la API. `integration/s7_smoke.py` verifica DB/M/Q de lectura-escritura e I de lectura con Snap7. Los componentes simulados heredados siguen asociados a DB y sus offsets no son editables. |
| Ejecución | Scan nominal configurable de 10–5000 ms y retardo fijo+jitter acotado por operación S7 de 0–10 000 ms cada uno; perfil `timeout` omite la respuesta a lecturas/escrituras de datos y deja abierta la sesión. Ejecuta ON/OFF con histéresis, AND/OR/XOR, TON/TOF, SR, CTU y secuenciador one-hot. La retención opcional conserva estados documentados de bloques en un volumen Docker; sin ella son volátiles al reiniciar. El ciclo usa el scheduler normal de Linux/Docker; no garantiza tiempo real. |
| Interconexión | Un `remote_input` consulta un DB remoto mediante lecturas GET periódicas; los canales M/I/Q pueden enlazarse al runtime de campo. PLC↔PLC no es un enlace cíclico general de E/S: `remote_input` sigue limitado a DB. No implementa PUT, Ladder/ST ni PROFINET I/O. |
| No implementa | CPU/firmware Siemens, temporización de hardware, comunicación de seguridad, mapa simbólico/etiquetas TIA ni compatibilidad verificada con herramientas de ingeniería Siemens. No ofrece lógica cíclica general de marcas/I/O ni runtime IEC 61131-3. |
| Evidencia | `integration/s7_smoke.py` prueba DB/M/I/Q y exige un mínimo medido cuando se configura retardo; CI inicia el servidor con 150 ms y requiere ≥140 ms. `integration/control_logic_smoke.py` verifica lógica/TON y `integration/s7_peer_route_smoke.py` la ruta PLC→PLC. La traza Snap7 registra área DB/M/I/Q en logs, API y panel de protocolo. Además, existe una cola durable de metadatos de escrituras/errores con confirmación de recogida; no conserva tramas ni sustituye una captura PCAP. |

## Modbus TCP

| Área | Implementación y límite |
|---|---|
| Servidor | PyModbus TCP en el puerto configurado (502 por defecto), Unit ID 0–255 y direcciones de protocolo base cero. También ofrece RTU sobre pseudo-terminal y túnel raw TCP sin MBAP; no emula cable ni señal eléctrica RS-485. No publica puertos del host por defecto. `integration/external_bundle_smoke.py` comprobó lectura y escritura de coil desde Linux del host contra el bundle Compose; una sonda desde Python estándar de Windows/WSL leyó la coil y comprobó ambos estados. Otra máquina y publicación en una interfaz LAN siguen sin verificar. Véase [Acceso controlado](ACCESS.md). |
| Mapa | Coils BOOL, discrete inputs BOOL, holding registers UINT16 e input registers UINT16 con espacios separados, valores iniciales y asignación automática de direcciones. |
| Acceso | Coils y holding registers admiten lectura/escritura; discrete inputs e input registers son solo lectura por protocolo. HMI/SCADA limitan escrituras a puntos marcados `read_write`. El cliente PyModbus estándar verifica FC01–06, FC15/16, FC22 (Mask Write Register) y FC23 (Read/Write Multiple Registers) en TCP. En RTU, el smoke con un cliente serie PyModbus verifica las mismas funciones a través del túnel raw. Este inventario describe las funciones probadas; no implica soporte de todos los códigos Modbus ni equivalencia frente a cualquier dispositivo. La intercepción de ejercicios tiene un alcance más estrecho: FC03/FC04 en respuestas y FC05/FC06/FC15/FC16/FC22 en solicitudes, según el [diseño de intercepción](PROTOCOL_INTERCEPTION_DESIGN.md). FC05 escribe una coil individual y FC15 modifica coils seleccionadas en una petición múltiple; no es MITM transparente. |
| Comportamiento | En TCP: retardo fijo+jitter determinista acotado de 0–10 000 ms cada uno, excepción Device Failure o desconexión inducida. RTU usa baudios nominales para temporización de tramas, sin línea eléctrica temporizada; sus fallos están restringidos por esquema. |
| No implementa | Diagnóstico completo de dispositivo, perfiles de fabricante ni equivalencia caracterizada exhaustivamente frente a hardware. RTU viaja por pseudo-terminal/túnel virtual, no puerto serie físico. |
| Evidencia | `integration/modbus_protocol_test.py` verifica TCP con cliente PyModbus independiente, incluidas direcciones límite, huecos, excepciones 2/4, retardo+jitter y desconexión inducida. `integration/modbus_rtu_smoke.py` verifica RTU con cliente serie en otro contenedor y cubre además las operaciones gestionadas de Anvil, HMI y adquisición SCADA. `integration/tcp_route_smoke.py`; `plc/tests/test_modbus_server.py`, `backend/tests/test_modbus.py`, `backend/tests/test_modbus_api.py`. |

## SCADA y HMI

- SCADA sondea puntos S7/Modbus seleccionados, conserva un buffer acotado en el
  runtime y soporta reglas de umbral. El backend persiste muestras y estado de
  alarmas en SQLite, ofrece histórico CSV y reconocimiento; la recogida depende
  del buffer y puede perder muestras si el backend queda fuera más tiempo que
  este. Las alarmas numéricas tienen un retardo de activación configurable; se
  valida con las muestras del sondeo, por lo que la precisión depende de ese
  periodo. Los inicios pendientes se guardan en SQLite, pero al recrear el
  runtime la condición y calidad deben confirmarse otra vez. No es un
  historiador industrial de alta disponibilidad.
- La HMI de operador consulta valores por S7/Modbus, permite escribir puntos
  seleccionados autorizados, configurar alarmas numéricas o BOOL propias y ver su
  historial persistido de transiciones. Su sinóptico es parte de la aplicación
  web Anvil; no es un runtime HMI comercial ni un cliente gráfico externo de un
  PLC físico. Las alarmas propias permiten retardo de activación configurable,
  medido con las lecturas válidas del sondeo de la HMI; una lectura inválida
  reinicia una condición aún pendiente y puede marcar como obsoleta una alarma
  activa. No hay expresiones complejas ni temporización industrial independiente
  del ciclo de sondeo.
- Ambos runtimes usan clientes de protocolo dentro de contenedores y permanecen
  limitados por los mapas de puntos, permisos y rutas declaradas de Anvil.

## Proceso y nodos auxiliares

- El runtime de campo calcula un balance determinista de volumen para los
  elementos actualmente conectados; no es un simulador hidráulico distribuido.
  Presión es una aproximación hidrostática, no un solver de red.
- El catálogo distingue motor on/off, bomba y variador de frecuencia. El VFD
  acepta consigna REAL 0–100 %, aplica retardo/fallo, informa velocidad y caudal,
  y puede alimentar tubería o depósito. Su caudal es lineal con la consigna;
  no emula motor eléctrico, rampa/inercia, frecuencia de salida, curvas de par
  ni protecciones de hardware.
- `gateway` y `firewall` del catálogo son representaciones o controles de la
  API; no filtran tráfico IP entre contenedores. Los gateways TCP internos
  reenvían un único destino/puerto con dos interfaces de red y sin `NET_ADMIN`.
- `attacker-station` es una estación de participante aislada por defecto. Si se
  habilita, se conecta solo a la red interna de su zona y usa la imagen de cliente
  Snap7/PyModbus; no publica puertos ni permite egress. Un participante con
  membresía puede usar la terminal WebSocket de Anvil, ejecutada como UID sin
  privilegios y limitada por sesión/duración. Las sesiones y roles se auditan;
  los comandos no se guardan. Los leases de terminal coordinan las cuotas entre
  workers que comparten SQLite; réplicas con bases separadas no comparten cuotas.
- `engineering-station` es un host de ingeniería aislado por defecto. Al
  habilitar `engineering_access_enabled`, se conecta únicamente a la red Docker
  interna de su zona; no publica puertos ni obtiene salida externa. El terminal
  requiere rol diseñador, instructor o administrador, usa UID 65534 y no guarda
  comandos. `ANVIL_ENGINEERING_IMAGE` permite seleccionar una imagen que incluya
  `/bin/sh` y las herramientas de ingeniería necesarias. El test local usa la
  imagen SCADA como entorno de cliente Snap7; no equivale a una estación Siemens
  ni incluye TIA Portal.

## Cómo ampliar esta ficha

Para declarar una nueva función como compatible, añade una prueba de protocolo
con cliente independiente, registra versión de librería/imagen y anota aquí los
rangos, funciones, excepciones y límites que cubre. Los smokes locales usan
clientes estándar y Docker Engine local; no equivalen a interoperabilidad frente
a hardware industrial. El bundle Compose con relay de ingreso sí se verificó
desde Linux del host para lectura/escritura S7 DB y Modbus coil; Windows/WSL solo
verificó handshake COTP S7 y lectura/escritura Modbus coil. No se ha probado el
acceso desde una segunda máquina ni el binding en una interfaz LAN. El modo
sustituto solo publica S7/Modbus cuando se activa explícitamente y usa loopback
por defecto. Anvil no gestiona el firewall del host: el operador debe restringir
la IP origen y el puerto publicado.
