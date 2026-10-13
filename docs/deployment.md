# Bundles y despliegue en otra máquina

Anvil ofrece exportación JSON de escenarios, perfiles de dispositivo y un
bundle Compose de despliegue. Estas opciones transportan distintas cosas; un
bundle no es una imagen autocontenida ni una promesa de compatibilidad con
cualquier host.

## Elegir formato

| Artefacto | Uso | No incluye |
| --- | --- | --- |
| JSON de escenario | Editar o importar topología/configuración | Runtime Docker e identidad de usuario |
| Perfil de dispositivo | Reutilizar un PLC o Modbus | Identidad de nodo destino/puertos publicados |
| Bundle de despliegue | Compose, definición, lock de imágenes y reasignación de red | Secretos, daemon Docker o capas de imágenes |
| Bundle de evidencias | Compartir logs, auditoría y artefactos del escenario | Garantía de autoría sin custodiar el hash por separado |

## Procedimiento de transferencia

1. Detén el escenario y revisa que la exportación incluya la versión esperada.
2. Exporta el escenario o genera el bundle con la UI/API.
3. Copia el archivo por un canal controlado y verifica tamaño/hash por un canal
   independiente si el resultado se usará como evidencia.
4. En el destino instala Docker/Compose y construye o carga cada imagen requerida.
5. Si el escenario original o alguna red del host seguirá activa, elige una
   subred IPv4 libre por zona y reasigna el bundle para que pueda convivir.
6. Revisa `ANVIL_ALLOWED_IMAGES`, límites de recursos, puertos publicados,
   políticas y rutas antes de desplegar.
7. Despliega el bundle y valida los protocolos desde el cliente.

El ZIP contiene `docker-compose.yml`, `scenario.json`, `.env`,
`network-addresses.json` y `readdress.py`. Compose usa las variables del `.env`
para cada subred, IP estática y puerto host publicado. Para mover el ejemplo de
una zona y conservar el offset de cada dirección:

```bash
python3 readdress.py --zone default=10.250.0.0/24
```

En topologías con varias zonas, repite `--zone ZONE_ID=IPv4_CIDR` por cada
segmento que deba cambiar. El script valida que las redes no se solapen y que
cada IP resultante sea utilizable; solo escribe `.env` si toda la reasignación es
válida. Para cambiar un puerto host en conflicto, edita su variable
`ANVIL_BUNDLE_HOST_PORT_*` en `.env`. Los puertos siguen cerrados por defecto.

No transfieras `ANVIL_BOOTSTRAP_TOKEN`, contraseñas, cookies, claves privadas ni
ficheros `.env` con el bundle. Los endpoints de exportación y esquema exacto se
describen en `/docs` y [OPERATIONS.md](OPERATIONS.md).

### Publicación externa optativa de S7 o Modbus TCP

Si un PLC S7 o un dispositivo Modbus TCP tiene `external_access=true`, el bundle
incluye un contenedor relay por dispositivo y el binding solicitado. El relay
se conecta a un bridge uplink de usuario, dedicado al proyecto Compose, y a la zona privada OT del dispositivo;
solo él publica el puerto. El destino S7/Modbus no recibe una publicación directa.
El origen permitido del relay es el gateway del bridge Docker, que se detecta
desde la ruta por defecto del contenedor. Compose debe admitir `gw_priority`
para seleccionar el uplink como la ruta por defecto. El binding se restringe a
`external_host_ip` y usa loopback por defecto; conserva ese valor para clientes
del mismo host. Cambia la variable host port en `.env` si el puerto ya está
ocupado y revisa la IP de escucha antes de permitir tráfico desde otra máquina.

El bundle no incluye capas de imágenes. `images.txt` lista también la imagen del
relay; ejecuta `export-images.sh` en el equipo que tenga todas las imágenes y
transfiere el TAR y su lock. Tras cargarlas, comprueba sus IDs con
`check-images.sh` y valida el YAML con `docker compose config` antes de iniciar.
La publicación remota requiere firewall del host y una prueba desde el cliente
real; el bundle no añade autenticación S7/Modbus ni allowlist de clientes final.

## Verificar evidencias

Un ZIP de evidencia incluye inventario SHA-256 de archivos. La utilidad
`integration/verify_evidence_bundle.py` verifica inventario, tamaños y hashes.
Si no se conserva por separado el hash del manifiesto, una persona con capacidad
de reemplazar ambos puede recalcularlos; para autenticar origen hace falta una
firma/clave confiable, todavía fuera del alcance descrito.
