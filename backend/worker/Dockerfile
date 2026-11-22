# syntax=docker/dockerfile:1
# Obraz workera: Debian slim (nie Alpine) — Docling, OpenCV, pypdfium2 i PyTorch publikują
# koła tylko dla glibc (manylinux); na musl wymagałyby kompilacji ze źródeł.
# Kontekst budowania: katalog główny repozytorium.
FROM python:3.14.10-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Pakiety systemowe dla OCR (Tesseract + język polski, Apache-2.0) oraz OpenCV (libglib2.0-0).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-pol \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY backend/worker/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Model embeddingów bazy wiedzy (RAG) wbudowany w obraz: worker działa w sieci bez internetu.
# intfloat/multilingual-e5-small (MIT), rewizja przypięta jak EMBEDDING_MODEL_REVISION
# w backend/shared/config.py. Tylko wagi safetensors i tokenizer (~490 MB).
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-small
ARG EMBEDDING_MODEL_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3
ENV HF_HOME=/opt/huggingface
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download('${EMBEDDING_MODEL}', revision='${EMBEDDING_MODEL_REVISION}', \
allow_patterns=['config.json', 'model.safetensors', 'tokenizer.json', \
'tokenizer_config.json', 'special_tokens_map.json', 'sentencepiece.bpe.model'])" \
    && chmod -R a+rX /opt/huggingface
# Po zbudowaniu: żadnych pobrań w czasie działania (tylko pliki z obrazu).
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY backend/__init__.py backend/__init__.py
COPY backend/shared backend/shared
COPY backend/worker backend/worker

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["arq", "backend.worker.worker.WorkerSettings", "--check"]

# Własna konfiguracja logów: arq nie loguje argumentów zadań (nazwy plików, użytkownik).
CMD ["arq", "backend.worker.worker.WorkerSettings", "--custom-log-dict", "backend.worker.logging_config.LOG_CONFIG"]
