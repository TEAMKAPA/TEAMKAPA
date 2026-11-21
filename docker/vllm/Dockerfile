# syntax=docker/dockerfile:1
# Cienka nakładka na oficjalny obraz vLLM: oryginał działa jako root.
# Dodajemy użytkownika bez uprawnień i katalog cache wag z właściwym właścicielem
# (nazwany wolumen dziedziczy właściciela katalogu z obrazu).
FROM vllm/vllm-openai:v0.19.1

RUN groupadd --system --gid 10001 vllm \
    && useradd --system --uid 10001 --gid vllm --home-dir /models --shell /usr/sbin/nologin vllm \
    && mkdir -p /models \
    && chown -R vllm:vllm /models

ENV HOME=/models \
    HF_HOME=/models/huggingface \
    VLLM_CACHE_ROOT=/models/vllm-cache

USER 10001:10001
