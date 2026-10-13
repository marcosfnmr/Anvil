# Mapa de proceso OT

## Antes de dibujar conexiones

Un **nodo** (PLC, Modbus, HMI o SCADA) ejecuta un servicio en la red. Un
**objeto de campo** (sensor, válvula, bomba o depósito) representa el proceso
y no tiene IP propia. Por eso el mapa muestra relaciones de distinta clase:
red para alcanzar servicios, señal para intercambiar un valor entre campo y
E/S, y proceso para mover caudal o una medida física. Ninguna clase sustituye
a las otras. El [glosario](glossary.md) define estos términos y el
[tutorial](tutorial.md) recorre un circuito completo.

## Cómo leer el mapa

El mapa reúne objetos que en una instalación pueden vivir en redes y capas
distintas. Una línea solo tiene significado según su tipo: Ethernet describe
conectividad de red; señal transporta un valor entre un punto de controlador y
un puerto de campo; proceso representa caudal o una magnitud simulada; y la
relación de control resume qué sensor/entrada y actuador/salida participan en
reglas PLC. No todas las líneas son cables físicos equivalentes ni crean una
comunicación protocolaria.

Un PLC o equipo Modbus es un nodo software con IP y runtime Docker. Una válvula,
bomba, sensor o depósito es un objeto de campo sin IP propia; su inspector
configura el comportamiento de proceso. Primero se pueden colocar los objetos
libres y después añadir controladores, canales y conexiones. La HMI/SCADA se
configura aparte como cliente que elige destino y puntos; dibujar una línea no
configura automáticamente sus tags.

El lienzo reúne PLCs, equipos Modbus, HMI y objetos físicos independientes:
sensores, motores, bombas, válvulas y depósitos. Los objetos físicos no tienen
IP propia. Un runtime de campo por escenario calcula sus estados y conecta los
puertos físicos con las señales de los controladores.

- **Configurar** abre el inspector del objeto seleccionado; las válvulas,
  bombas, sensores y depósitos se configuran independientemente del PLC.
- **Conexiones de señal** asocian un puerto de objeto con un canal PLC o punto
  Modbus. Se muestran en verde y respetan tipo, dirección y permisos.
- **Conexiones físicas** representan caudal entre bomba, depósito y válvula,
  o nivel desde depósito a sensor. Se muestran con línea azul discontinua.
  Son distintas del cableado de señales del PLC.
- **Relaciones de control** conectan en ámbar la entrada/sensor y la salida/actuador
  que gobiernan los bloques ON/OFF, TON y lógica booleana de un PLC S7. Se derivan
  de los componentes y del cableado de campo; no crean una conexión de red ni
  sustituyen los enlaces de señal.
- **Switch Ethernet** representa la conmutación L2 del bridge Docker interno
  de su zona; no es un contenedor adicional. Configura su capacidad y conecta
  todos los equipos de red de la zona a él con enlaces Ethernet dorados. La API
  valida la capacidad y que no queden equipos sin enlace. Los puertos comparten
  el mismo segmento: no hay VLAN, STP, filtrado por puerto ni routing IP. Sin un
  switch, los enlaces Ethernet siguen siendo anotaciones de topología. Para
  cruces entre zonas se define una ruta explícita.
- El panel de conexiones del inspector crea y elimina ambos tipos de relación.
  La API valida puertos, tipos, unidades, duplicados y entradas con múltiples
  fuentes antes de guardar.
- **Centrar**, zoom, arrastre del fondo y modo **Mover** recorren el mapa sin
  alterar conexiones ni configuración.
- Al abrir un escenario, el mapa se encuadra automáticamente debajo de la barra
  de controles. **Centrar** repite el ajuste después de añadir o mover objetos.

## Modelo físico disponible

El primer modelo acoplado incluye una bomba de caudal configurable, un depósito
con capacidad, altura, nivel inicial y caudal de salida, una válvula on/off con caudal
máximo y un sensor analógico conectado al nivel. El nivel cambia según el
balance de caudal (L/min) y el tiempo transcurrido; se limita entre 0 y la
capacidad. La salida PLC acciona bomba y válvula y la entrada analógica recibe
el nivel calculado. El mapa expone nivel, litros y caudales durante el despliegue.

El depósito admite además una perturbación de fuga constante (`leak_flow_lpm`,
0 por defecto). La fuga se suma al caudal de salida, reduce el volumen según el
tiempo transcurrido y cesa cuando el depósito llega a cero. El inspector muestra
el caudal perdido mientras haya líquido. Es un modelo determinista de balance,
sin propagación hidráulica ni cálculo de presión en tuberías.

También se configura una demanda inicial (`demand_flow_lpm`, 0 por defecto) y se
puede cambiar en ejecución desde el inspector. La operación se audita; el mapa
muestra la demanda efectiva, la fuga y la salida combinada. Así se pueden
demostrar cambios de demanda sin editar ni redeplegar el escenario.

El depósito calcula presión manométrica estática a partir de la altura de
líquido (`9.80665 × fracción de llenado × altura`, kPa). Las bombas y el
variador también tienen una cabeza máxima configurada; las tuberías pueden
aplicar una pérdida nominal que escala con el cuadrado del caudal y las válvulas
limitan el caudal. Es una aproximación concentrada para cadenas y ramas
explícitas, no un solver hidráulico general: no usa diámetro, longitud,
rugosidad, elevación ni viscosidad.

El modelo es determinista en el caudal de proceso y sirve para demostraciones y
pruebas funcionales. No es un simulador hidráulico de ingeniería: no modela
presión de red, geometría de tubería, transitorios, cavitación, curvas de bomba
ni límites de scan de un PLC físico. El catálogo sí incluye tuberías,
distribuidores y colectores; sus relaciones se configuran como conexiones de
proceso. Los ciclos de retorno, múltiples fuentes convergentes sin un colector
explícito y una resolución general de redes hidráulicas quedan fuera del modelo.

## Otras líneas y lecturas

- **S7 · lectura**, azul: entradas remotas entre PLCs. Verde indica lectura
  vigente; ámbar indica enlace obsoleto o error.
- **HMI · datos**, violeta discontinua: equipos configurados como fuentes HMI.
  Expresa configuración y no garantiza sondeo continuo.
- Lecturas sin respuesta, obsoletas o con escenario detenido se indican como
  tales; la interfaz no inventa valores en vivo.

## Verificación

Prueba del proceso acoplado contra PLC S7 real en Docker:

```bash
.venv/bin/python integration/process_loop_smoke.py --base-url http://127.0.0.1:8005
```

Prueba de aislamiento de red y clientes S7/Modbus:

```bash
.venv/bin/python integration/docker_network_smoke.py --base-url http://127.0.0.1:8005
```

Este smoke se autentica automáticamente; configura `ANVIL_TEST_EMAIL` y
`ANVIL_TEST_PASSWORD` como explica [VALIDATION_LOCAL.md](VALIDATION_LOCAL.md).
