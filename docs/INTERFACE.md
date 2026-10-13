# Interfaz de Anvil

La interfaz dedica toda el área de trabajo a una tarea. Las vistas comparten el
escenario activo y sus cambios sin guardar; navegar no reinicia la configuración.

## Antes de conectar equipos: cuatro conceptos distintos

En Anvil, que dos elementos aparezcan cerca o unidos en el mapa no implica que
compartan todos sus datos. Una **conexión de proceso** representa una relación
física simplificada, como caudal desde una bomba hasta un depósito. Una
**conexión de señal** lleva un valor entre un puerto de un sensor/actuador de
campo y un punto de entrada/salida de PLC o Modbus. Una conexión **Ethernet**
describe pertenencia/conectividad de red entre nodos de software. El
**protocolo** (S7 o Modbus) es la petición que inicia una HMI, un SCADA u otro
cliente para leer o escribir un punto del servidor. Una línea de un tipo no
crea automáticamente las otras relaciones.

Ejemplo: para accionar una válvula con un PLC, primero se añade y configura la
válvula desde el catálogo de campo. Se conecta su puerto `command` a una salida
escribible del PLC mediante una conexión de señal; si la válvula participa en
un circuito, se conectan también sus puertos de proceso a la tubería/equipo
correspondiente. Después se configura en el PLC la lógica que gobierna la
salida, o se usa un control HMI permitido. La HMI necesita además su propio tag
con el destino y la dirección de esa salida. El PLC y la HMI requieren una
conexión de red válida para intercambiar datos. La guía del [mapa de proceso](PROCESS_MAP.md)
describe cómo crear y reconocer cada línea.

- **Escenarios:** crear, abrir, importar, exportar y clonar laboratorios. Se abre
  primero al entrar en Anvil.
- **Editor:** lienzo completo del escenario, red y acceso al catálogo. Selecciona
  un nodo y pulsa «Configurar dispositivos», o haz doble clic en él. Arrastrar un
  nodo sigue modificando su posición en el lienzo.
- **Catálogo:** tarjetas de dispositivos. Elegir una añade el dispositivo y abre
  su configuración. Los botones se bloquean durante el despliegue o la ejecución.
- **Configuración:** formulario completo, selector de dispositivo, señales y HMI.
  Los ajustes de protocolo y acceso externo están plegados. Para añadir una señal,
  selecciona su tipo y pulsa «Añadir señal».
  El bloque **Servicios y puertos** muestra el inventario devuelto por el
  escenario guardado: protocolo, transporte, hostname/IP, puerto del contenedor
  y, si se habilitó, binding de host. La API interna del SCADA aparece como
  «solo interno»; una HMI se describe como cliente sin listener OT propio. Si
  editas configuración, el inspector avisa que la lista corresponde al último
  guardado hasta guardar de nuevo. El estado real se comprueba con healthcheck y
  diagnóstico, no con esta lista declarativa.
  Al seleccionar una HMI desplegada, el inspector muestra lecturas actualizadas
  cada dos segundos y controles para puntos S7 BOOL y Modbus coil/holding
  register escribibles; las entradas se identifican como solo lectura. En la
  configuración de la HMI se pueden definir alarmas numéricas por tag y umbral;
  sus estados y últimas transiciones aparecen en la consola de operador.
  En el inspector S7 también se puede configurar el ciclo nominal del PLC entre
  10 y 5000 ms; los escenarios existentes mantienen el valor por defecto de 100 ms.
- **Telemetría:** señales, historial, protocolo, logs y actividad. Cada pestaña
  utiliza toda la vista. Las señales pueden filtrarse por dispositivo; las entradas
  remotas muestran también su calidad de comunicación.

Los controles de guardar, desplegar y detener siguen disponibles en la cabecera
del escenario. En móvil la navegación pasa a una fila superior. Cada vista dispone
de su propia zona de desplazamiento, sin dividir inspector y telemetría en mitades.

Se conservan los identificadores de los controles y los módulos de escenarios,
protocolos y despliegue. `workspace.js` organiza las vistas; `workspace.css` define
su distribución y apariencia. No cambia el contrato JSON ni la persistencia.

Pruebas de navegador: `integration/workspace_ui.cjs`,
`integration/s7_links_ui.cjs` e `integration/ui_smoke.cjs`. Necesitan Playwright y un
backend de pruebas, por defecto en `8004`. Con `ANVIL_AXE_PATH` realizan también
una auditoría automática de accesibilidad. Las pruebas de protocolos crean y limpian
sus propios escenarios; deben ejecutarse en un preview aislado.

## Mapa de componentes
El editor muestra los componentes de campo y las relaciones E/S, S7 y HMI.
Consulta [la guía del mapa](PROCESS_MAP.md) para moverlos, guardar la disposición
y observar sus estados en directo.

Los componentes de campo se configuran desde su propio inspector, aunque estén
conectados a un PLC. Selecciona el objeto y abre **Configurar**; para cambiar
qué valor intercambia con un controlador, usa la sección de conexiones de
señal. Los nodos PLC/Modbus se configuran por separado desde **Configurar
dispositivos**. Esta separación refleja sus papeles: el objeto modela el
instrumento o actuador, mientras que el PLC/servidor publica puntos de datos y
ejecuta el comportamiento que le corresponde.

## Presentación y vistas

### Actualización de la HMI y espacio del editor

La consola HMI actualiza sus lecturas cada dos segundos. Al refrescar, conserva
las tarjetas y actualiza sus valores en el lugar: no vacía el sinóptico, no
desmonta los controles ni mueve el foco. Si el operador está editando una
consigna sin escribirla aún, la lectura de fondo conserva ese valor local. Si
una lectura falla, Anvil mantiene la última lectura visible y señala el error.

Las líneas del mapa se anclan al borde más cercano de los dos objetos. Si la
topología cambia de horizontal a vertical, la curva cambia de orientación al
volver a dibujarse. El título y la descripción secundaria del editor ocupan
menos altura; los márgenes y el encabezado principal también se reducen para
dejar más lienzo visible en pantallas pequeñas. El botón de zonas Purdue sigue
en la barra del mapa y su panel se abre como menú flotante.

El isotipo y el favicon comparten una marca vectorial de yunque en el mismo
tono ámbar de Anvil; el navegador renderiza el SVG sin requerir fuentes ni
recursos de imagen adicionales.
