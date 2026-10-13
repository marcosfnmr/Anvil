# Imágenes base de contenedores

Los Dockerfiles mantenidos por Anvil usan referencias OCI fijadas por digest de
índice multi-plataforma. Así, `linux/amd64` y `linux/arm64` resuelven la variante
correspondiente del mismo índice inmutable:

| Familia | Referencia predeterminada |
| --- | --- |
| Python 3.11 slim | `docker.io/library/python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce` |
| Debian Bookworm slim | `docker.io/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587` |

Los digest se contrastaron con el índice publicado por Docker Hub usando
`docker buildx imagetools inspect` el 2026-10-08. Las pruebas
`backend/tests/test_container_bases.py` exigen que cada `FROM` predeterminado
termine en un digest SHA-256.

Para revisar si existe una actualización, consulta el índice oficial:

```sh
docker buildx imagetools inspect docker.io/library/python:3.11-slim
docker buildx imagetools inspect docker.io/library/debian:bookworm-slim
```

Una actualización deliberada debe cambiar conjuntamente los valores por
defecto pertinentes en los Dockerfiles, revisar las notas de seguridad/upstream,
ejecutar `python -m unittest backend.tests.test_container_bases` (o el descubrimiento
normal de backend) y reconstruir todas las imágenes afectadas. Las referencias
están en `ARG` para que un empaquetador pueda derivar una imagen desde una base
aprobada; CI y el Compose normal usan los defaults fijados. No reemplaces esos
defaults por tags flotantes.

CI inventaría las siete imágenes construidas en el job mediante `dpkg-query` y
`pip inspect --local`, genera un SBOM SPDX 2.3 por imagen y añade
`provenance.json` con commit, ID de imagen, plataforma, referencia base y hashes
de Dockerfiles, locks y SBOM. El workflow adjunta los ficheros como artefacto de
Actions durante 30 días. Para generarlos localmente después de construir las
etiquetas `*:ci` que usa CI:

```sh
python integration/container_sbom.py --output-dir artifacts/container-supply-chain
```

El manifiesto actual es un registro de trazabilidad sin firma criptográfica; no
es una attestation verificable ni acredita por sí mismo la integridad del runner.
Las dependencias Python se inventarían desde las imágenes terminadas y los
paquetes Debian desde su base instalada. La herramienta no declara cobertura de
archivos fuente ni de componentes descargados fuera de esos inventarios.

Fijar la imagen base hace repetible la selección del sistema base, pero por sí
solo no garantiza una reconstrucción idéntica: las dependencias, el frontend,
la plataforma del builder y otros insumos también deben fijarse y verificarse.
