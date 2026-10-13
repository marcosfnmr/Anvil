ARG DOCKER_PROXY_BASE_IMAGE=docker.io/library/python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
FROM ${DOCKER_PROXY_BASE_IMAGE}
WORKDIR /app
COPY proxy.py ./
COPY runtime_exec_policy.json ./
EXPOSE 12375
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:12375/health', timeout=4)"]
CMD ["python", "proxy.py"]
