# syntax=docker/dockerfile:1
# Obraz API: Alpine — usługa ma wyłącznie lekkie zależności z gotowymi kołami musllinux.
# Kontekst budowania: katalog główny repozytorium.
FROM python:3.14.10-alpine3.24

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libmagic: walidacja MIME przesyłanych plików (python-magic, BSD).
RUN apk add --no-cache libmagic \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app

WORKDIR /app

COPY backend/api/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY backend/__init__.py backend/__init__.py
COPY backend/shared backend/shared
COPY backend/api backend/api

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
