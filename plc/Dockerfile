FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

EXPOSE 102/tcp

HEALTHCHECK --interval=2s --timeout=1s --start-period=2s --retries=10 \
  CMD ["python", "-c", "import socket; connection = socket.create_connection(('127.0.0.1', 102), 1); connection.close()"]

CMD ["python", "server.py"]
