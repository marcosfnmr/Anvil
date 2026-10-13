ARG PLC_BASE_IMAGE=docker.io/library/python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
FROM ${PLC_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN mkdir -p /var/lib/anvil/retention /var/lib/anvil/protocol-audit && chown -R 65534:65534 /var/lib/anvil

EXPOSE 102/tcp

HEALTHCHECK --interval=5s --timeout=4s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import socket; connection = socket.create_connection(('127.0.0.1', 102), 3); connection.close()"]

CMD ["python", "server.py"]
