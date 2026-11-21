# syntax=docker/dockerfile:1
# Obraz workera: Debian slim (nie Alpine) — Docling, OpenCV, pypdfium2 i PyTorch publikują
# koła tylko dla glibc (manylinux); na musl wymagałyby kompilacji ze źródeł.
# Kontekst budowania: katalog główny repozytorium.
FROM python:3.14.10-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY backend/worker/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY backend/__init__.py backend/__init__.py
COPY backend/shared backend/shared
COPY backend/worker backend/worker

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["arq", "backend.worker.worker.WorkerSettings", "--check"]

CMD ["arq", "backend.worker.worker.WorkerSettings"]
