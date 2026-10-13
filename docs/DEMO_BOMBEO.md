# Demo: estación de bombeo conectada

Duración recomendada: 8–10 minutos. Escenario importable:
[`examples/demo-bombeo-conectado.json`](../examples/demo-bombeo-conectado.json).

La historia: una instalación tiene un PLC de bombeo, un PLC de supervisión,
un equipo de dosificación Modbus y una HMI para consultar sus señales.
Es una demostración de comunicaciones y puntos de E/S. No es un modelo hidráulico:
la válvula no condiciona el caudal y la dosificación no consume el nivel disponible.

## Preparación

1. Abre `http://localhost:8003/` y entra en **Escenarios**.
2. Abre **DEMO · Estacion de bombeo conectada** si ya está guardado.
   En otra instalación, pulsa **Importar** y selecciona el JSON del ejemplo.
3. Si está detenido, pulsa **Desplegar**. Espera a que esté ejecutándose.
4. En **Telemetría → Señales**, confirma que hay lecturas y que las entradas
   remotas del PLC 2 tienen calidad `good`.
5. Revisa que `Valvula_entrada`, `Bomba_trasiego`, `Ventilador_manual` y
   `Dosificador_habilitado` estén en false. Si están en true, pulsa **Apagar**.
   Ajusta `Consigna_dosis` a 250 y `Lote_actual` a 7 si repetiste la demo.
6. Para una presentación limpia, usa zoom del navegador al 100 % y cierra
   paneles ajenos. No necesitas publicar puertos de los dispositivos al host.

El escenario usa `172.30.247.0/24`. Antes de importarlo en otra máquina, comprueba
que no solapa una red Docker desplegada; si la cambias, cambia también todas las IPs.

## Dispositivos

| Dispositivo | IP | Qué demuestra |
| --- | --- | --- |
| PLC 1 · Bombeo | 172.30.247.10 | Válvula y bomba escribibles; temperatura variable; alarma de solo lectura |
| PLC 2 · Supervision | 172.30.247.11 | Cuatro entradas remotas S7; ventilador manual independiente |
| Modbus · Dosificacion | 172.30.247.12 | Coil, holding registers e input register, unit ID 1, TCP/502 |
| HMI · Sala de control | 172.30.247.20 | Lectura conjunta de los tres equipos |
| Politica · Acceso API | 172.30.247.30 | Permisos de consola/HMI para TCP/102 y TCP/502 |

Los nodos HMI y política son auxiliares. La HMI realiza lecturas mediante la API;
la política se aplica a la API, no es un firewall de red entre contenedores.

## Guion paso a paso

### 1. Presenta la instalación — 1 minuto

En **Editor**, muestra los cinco dispositivos:

> «Esta estación reúne equipos S7 y Modbus en una red Docker aislada.
> Podemos configurar señales, accionar salidas y observar comunicaciones reales.»

Haz doble clic en PLC 1 y muestra las señales. Luego selecciona PLC 2 y muestra
una entrada remota: emisor, señal de origen y periodo de consulta de 200 ms.
Vuelve al editor. Los enlaces están configurados en las entradas; no hay cables
dibujados ni un programa Ladder ejecutándose.

### 2. Enseña algo que cambia solo — 1 minuto

Abre **Telemetría → Señales**, filtra por **PLC 1 · Bombeo** y muestra
`Temperatura_agua`: oscila entre 18 y 28 °C en un ciclo de 20 segundos.
Es una señal sintética; no depende del bombeo.

Muestra los permisos: temperatura y alarma solo lectura; válvula y orden de bomba
lectura/escritura. La alarma permanece en false; no hay umbral automático configurado.

### 3. Abre la válvula y arranca la bomba — 2 minutos

1. En `Valvula_entrada`, pulsa **Encender**. Cambia de false a true.
2. En la **orden BOOL** de `Bomba_trasiego`, pulsa **Encender**.
3. Muestra el caudal **REAL** de esa bomba: pasa de 0 a **60 L/min**.
4. Pulsa **Apagar** en la orden: el caudal vuelve a 0.
5. Arráncala de nuevo para la siguiente parte.

> «La orden se escribe por S7. El simulador calcula el caudal a partir del estado
> de la bomba: parado, cero; en marcha, 60 litros por minuto.»

La bomba responde a su propio mando. Abrir o cerrar la válvula no modifica su caudal.
El caudal no es una rampa ni una simulación física de tuberías.

### 4. Muestra la comunicación PLC a PLC — 1 minuto

Filtra por **PLC 2 · Supervision**:

- `Valvula_recibida`: true.
- `Bomba_recibida`: true.
- `Caudal_recibido`: 60 L/min.
- `Temperatura_recibida`: cambia siguiendo al emisor, con el retraso de consulta.

> «Este PLC consulta al primero por S7/TCP. Recibe BOOL y REAL y publica la
> calidad de las lecturas. Sus entradas remotas son de solo lectura.»

Activa `Ventilador_manual` para enseñar una salida local del receptor. Explica
que es manual: las entradas remotas todavía no ejecutan reglas que lo activen.

### 5. Opera el equipo Modbus — 1 minuto

Filtra por **Modbus · Dosificacion**:

1. Pulsa **Encender** en `Dosificador_habilitado`: escritura de una coil.
2. Introduce **400** en `Consigna_dosis` y pulsa **Escribir**: holding register.
3. Introduce **8** en `Lote_actual` y pulsa **Escribir**.
4. Muestra `Nivel_disponible`, **75 %**, de solo lectura: input register.

> «Un mismo panel opera dos protocolos y respeta los permisos de cada punto.»

El nivel es un valor inicial constante y la coil no ejecuta un proceso de dosificación.
Cada espacio Modbus tiene sus propias direcciones: coil 0, holding 0 y 1, input 0.
Que coil e input compartan el número 0 no es una colisión.

### 6. Consulta todo desde la HMI — 1 minuto

En **Configuración**, selecciona **HMI · Sala de control** y pulsa **Leer valores**.
Muestra los valores de ambos PLCs y del equipo Modbus: válvula abierta, bomba
en marcha, caudal, temperatura y nueva consigna 400.

> «La HMI consulta los equipos configurados y refresca la lectura mientras está
> seleccionada. Los puntos de salida aparecen con controles; las entradas e
> input registers permanecen en solo lectura.»

Puedes escribir desde la fila de una salida S7, coil o holding register. La
pantalla no es un sinóptico del proceso: la consigna Modbus solo cambia el
registro configurado y no altera la variable física de esta demostración.

### 7. Enseña trazabilidad — 1 minuto

Vuelve a **Telemetría**:

- **Historial**: muestra cambios observados y órdenes realizadas.
- **Protocolo**: muestra operaciones S7, incluidas lecturas procedentes del PLC receptor.
- **Logs**: muestra los mensajes reales de los contenedores.
- **Actividad**: muestra eventos del despliegue.

La pestaña Protocolo es un diagnóstico S7; no es una captura PCAP ni un analizador
completo de ambos protocolos.

Para descargar PCAP del enlace de un dispositivo, abre la pestaña **PCAP**, elige
el nodo y pulsa **Iniciar captura**; al terminar, usa **Detener y descargar PCAP**.

### 8. Cierre y reinicio — 30 segundos

Apaga válvula, bomba, ventilador y coil. Restablece consigna 250 y lote 7 si vas
a repetir. Pulsa **Detener** cuando termines.

> «Anvil permite montar un laboratorio OT con protocolos reales, señales
> simuladas, permisos y trazabilidad, sin necesitar hardware para esta demo.»

Al detener y desplegar de nuevo, los dispositivos recuperan sus valores iniciales.
El escenario conserva la configuración y las referencias entre dispositivos.

## Extra opcional: permisos de consola

Hazlo al final, solo en este escenario:

1. Detén la demo.
2. En **Politica · Acceso API**, deja permitido únicamente el puerto 102.
3. Guarda y despliega de nuevo.
4. Las operaciones Modbus desde la consola serán rechazadas por la política.
   La lectura conjunta HMI también puede fallar porque incluye el equipo Modbus.
5. Detén, restaura **102 y 502**, guarda y despliega para recuperar la demo.

Explica que esto demuestra permisos de la API. No demuestra segmentación física
de red ni bloqueo de la comunicación directa entre PLCs.
