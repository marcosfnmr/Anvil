# Mantener esta documentación

## Fuente de verdad

- `README.md`: descripción corta, arquitectura y acceso al material.
- `docs/index.md`: puerta de entrada del manual.
- Guías de usuario/operación: tareas concretas respaldadas por UI/API/runtime.
- `docs/DESIGN.md`: contratos de arquitectura.
- `docs/SRS_TRACEABILITY.md`: cobertura, evidencia y límites por requisito.
- `docs/SRS_REQUIREMENTS_v1.md`: documento fuente preservado, no editar como si
  fuera la matriz de implementación.

La documentación desplegada se genera de estos Markdown mediante MkDocs
Material. No edites manualmente `site/`; el siguiente build lo reemplaza.

## Reglas para añadir una función

Una función debe documentar, según corresponda:

1. Qué problema resuelve y desde qué pantalla se usa.
2. Requisitos y build/runtime necesarios.
3. Propiedades, rangos, defaults, direcciones, permisos y valores de error.
4. Qué cambia al guardar, desplegar, pausar, detener o recrear.
5. Endpoint o smoke reproducible que demuestre el comportamiento.
6. Diferencias conocidas frente a hardware/protocolos reales.
7. Cómo deshacer o limpiar la demo sin perder datos de otros escenarios.

## Explicar conceptos, no solo opciones de pantalla

Una guía de dispositivo o subsistema debe poder leerse sin conocer de antemano
el vocabulario de automatización. Antes de enumerar campos de configuración,
explica el propósito del equipo en una instalación y cómo se relaciona con los
demás. Para cada concepto técnico relevante, deja claro:

1. **Qué es y para qué sirve** en un sistema industrial.
2. **Qué representa Anvil**: procesos, contenedores, clientes/servidores,
   memoria, señales o estado que realmente implementa.
3. **Qué partes se simulan y cuáles no**, incluidos los límites de protocolo,
   temporización y hardware que se hayan verificado.
4. **Qué configura la persona** y qué valores/tipos/unidades/direcciones
   significan; distingue defaults y permisos cuando existan.
5. **Cómo intercambia datos** con otros elementos: quién inicia la comunicación,
   qué destino/protocolo/punto necesita y qué conexión de proceso, señal o red
   debe configurarse por separado.
6. **Cómo recorrer y verificar el flujo completo**, desde una medida u orden
   hasta el consumidor, indicando qué estados/error revisar y una prueba
   reproducible.
7. **Qué otros conceptos dependen de este equipo** y dónde se explican. Los
   términos especializados deben definirse en contexto y enlazarse al glosario
   o a una ficha de referencia, no dejarse como nombres de campos sin explicar.
8. **Qué evidencia respalda cada afirmación** y en qué entorno se obtuvo;
   distingue tests unitarios, pruebas de integración, despliegue local y
   validación contra hardware.

Por ejemplo, la página de PLC debe definir qué hace un PLC, explicar que S7 es
la interfaz de comunicación y no el programa de control, describir las partes
que el runtime reproduce, las áreas y bloques configurables y las relaciones
con campo, otros PLC, HMI y SCADA. Aplicar el mismo criterio a Modbus, objetos
de campo, proceso, HMI/SCADA y redes evita presentar etiquetas de UI como si
fueran conceptos autoexplicativos.

Mantén separadas las relaciones que suelen confundirse: **proceso** describe
el flujo/magnitud modelados; **señal** lleva un valor entre un puerto de campo
y un punto de E/S; **protocolo** permite a un cliente pedir un dato a un
servidor; y **red/ruta** permite alcanzar ese servicio. Una línea visual del
mapa solo tiene el efecto que declara su tipo.

Antes de dar por terminada una ficha, comprueba que un lector pueda contestar
sin inferencias: qué es el elemento, qué hace en una instalación, qué ejecuta
Anvil, qué propiedades controla, qué relaciones necesita para intercambiar
datos, cómo verificar el recorrido y qué afirmaciones no deben hacerse por los
límites del simulador. Si hay un flujo que atraviesa varios capítulos, enlázalos
en el orden causal (fuente → controlador → actuador → cliente) y conserva una
explicación corta en el punto donde el concepto se introduce por primera vez.

Actualiza `mkdocs.yml` si la guía nueva debe aparecer en el índice. Comprueba
links de navegación y construye el sitio. Para nuevos requisitos, conserva el
texto original y registra cobertura/evidencia en la matriz SRS.

## Build y preview local

```bash
python3 -m venv .venv-docs
.venv-docs/bin/python -m pip install -r requirements-docs.txt
.venv-docs/bin/python tools/build_docs.py
docker compose -p anvil-docs -f docker-compose.docs.yml up -d
```

El build estricto comprueba la navegación y anclas; el helper valida y copia a
`site/` los archivos de código/ejemplos enlazados fuera de `docs/`, para que la
matriz de trazabilidad y las demos sigan siendo navegables en el preview local.
El build debe terminar sin errores de MkDocs y sin warnings de navegación,
enlaces locales o anclas. Las advertencias informativas que emita una dependencia
de terceros (por ejemplo, el aviso de compatibilidad de Material para MkDocs
2.0) deben registrarse como tales y no confundirse con errores del contenido.
El Compose documental sirve el contenido
estático en `http://localhost:8009/`, enlazado a loopback y separado del stack de
Anvil. Reconstruye el sitio antes de recargar el preview. El servidor de preview
de MkDocs es de desarrollo y no debe exponerse como servidor de producción.

## Calidad y precisión

Evita frases como “reproduce un PLC real” sin describir el protocolo y la
frontera exacta de compatibilidad. Distingue pruebas unitarias, integración
Docker y pruebas con hardware. Una prueba contra un runtime del simulador no
verifica interoperabilidad con equipo físico. No incluyas claves, contraseñas,
IP públicas, rutas de usuario ni datos de escenarios de terceros.
