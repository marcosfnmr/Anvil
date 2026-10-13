ARG BACKEND_BASE_IMAGE=docker.io/library/python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
FROM ${BACKEND_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

ENV ANVIL_DATA_DIR=/data \
    ANVIL_DATABASE_URL=sqlite:////data/anvil.db \
    ANVIL_BACKEND_UID=65534 \
    ANVIL_BACKEND_GID=65534

RUN mkdir -p /data && chown 65534:65534 /data && chmod 0700 /data

EXPOSE 8000/tcp

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()"]

ENTRYPOINT ["python", "/app/entrypoint.py"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
