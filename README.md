# TEAMKAPA — COI „Cyfryzacja formularzy”

Usługa REST, która z blankietu urzędowego (PDF, DOC/DOCX, ODS, XLSX, JPG/PNG) generuje JSON
konfiguracji e-formularza zgodny ze schematem COI. Każde pole ma źródło w dokumencie i ocenę
pewności — urzędnik sprawdza tylko pola niepewne. Wszystkie modele AI działają lokalnie.

> Stan: infrastruktura (#14), modele COI (#17) i REST API z kolejką (#13) gotowe; pipeline
> analizy (#16) w trakcie — do tego czasu analiza kończy się błędem 501 `PIPELINE_NOT_IMPLEMENTED`.

## Architektura uruchomieniowa

| Usługa | Obraz | Rola | Port na hoście |
| --- | --- | --- | --- |
| `api` | `python:3.14.10-alpine3.24` | REST API (FastAPI), JWT, walidacja plików | `8000` |
| `worker` | `python:3.14.10-slim-trixie` | pipeline przetwarzania (arq) | — |
| `postgres` | `pgvector/pgvector:0.8.7-pg18-trixie` | wzorce, słowniki, metadane zadań | — |
| `valkey` | `valkey/valkey:9.0.8-alpine3.24` | kolejka zadań batch | — |
| `llm` *(profil `gpu`)* | `vllm/vllm-openai:v0.19.1` + użytkownik bez roota | lokalny LLM tekstowy | — |
| `vlm` *(profil `gpu`)* | `vllm/vllm-openai:v0.19.1` + użytkownik bez roota | lokalny model wizyjny | — |
| `frontend` *(profil `frontend`)* | `node:24.22.3-alpine3.24` → `nginxinc/nginx-unprivileged:1.31.7-alpine3.24` | interfejs urzędnika | `8080` |

**Dlaczego Alpine dla API, a slim dla workera:** API ma tylko lekkie zależności z gotowymi kołami
dla musl, więc Alpine daje najmniejszy obraz. Worker używa Docling, OpenCV, pypdfium2 i PyTorch,
które publikują koła wyłącznie dla glibc (manylinux) — na Alpine wymagałyby kompilacji ze źródeł.

**Bezpieczeństwo kontenerów:**
- wszystkie obrazy mają przypięte wersje (bez `latest`) i działają jako użytkownik bez roota
  (`api`/`worker`/`llm`/`vlm`: UID 10001, `postgres`, `valkey`, `nginx`: użytkownicy obrazu),
- system plików kontenerów jest tylko do odczytu (`read_only`), pliki tymczasowe trafiają do `tmpfs`,
  odebrane są wszystkie capabilities i włączone `no-new-privileges`,
- `worker`, `postgres` i `valkey` są tylko w sieci wewnętrznej bez dostępu do internetu —
  przetwarzanie fizycznie nie może wołać zewnętrznych API; `llm`/`vlm` mają wyjście wyłącznie
  do jednorazowego pobrania wag,
- sekrety podawane są wyłącznie w runtime (`.env` lub Docker secrets w `/run/secrets`),
  nigdy w obrazach ani repozytorium (`.dockerignore`, `.gitignore`, Gitleaks w CI).

## Wymagania

- Docker z Compose v2
- do profilu `gpu`: GPU NVIDIA + NVIDIA Container Toolkit (szacunkowo 48 GB VRAM albo 2×24 GB
  dla obu modeli w FP8; przy 24 GB — kwantyzacja 4-bit)
- do pracy lokalnej bez Dockera: Python 3.14

## Budowanie obrazów

```bash
docker compose build                                  # api, worker
docker compose --profile gpu --profile frontend build # wszystkie obrazy
```

## Uruchomienie

```bash
cp .env.example .env
# ustaw JWT_SECRET_KEY (min. 32 znaki) i POSTGRES_PASSWORD:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d                       # api, worker, postgres, valkey
docker compose --profile gpu up -d         # + lokalne LLM i VLM (vLLM)
docker compose --profile frontend up -d    # + frontend na http://localhost:8080

curl http://localhost:8000/health          # {"status":"ok","version":"0.2.0"}

# token JWT dla użytkownika (sekret jest tylko w kontenerze)
TOKEN=$(docker compose exec -T api python -m backend.api.token_cli --sub urzednik)
curl -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf"      http://localhost:8000/api/v1/forms/analyze
```

Bez GPU można wskazać dowolny lokalny serwer zgodny z API OpenAI (np. Ollama, llama.cpp)
przez `LLM_BASE_URL` / `VLM_BASE_URL` — kontrakt klienta jest ten sam.

API przy starcie waliduje konfigurację i kończy działanie z czytelnym błędem, jeśli np.
`JWT_SECRET_KEY` jest pusty, krótszy niż 32 znaki lub ma wartość przykładową z `.env.example`.

## Konfiguracja

Wszystkie parametry pochodzą ze zmiennych środowiskowych (szablon: `.env.example`).
Sekret można też podać jako Docker secret — plik `/run/secrets/<nazwa_zmiennej>`.

| Zmienna | Domyślnie | Opis |
| --- | --- | --- |
| `MAX_FILE_SIZE_MB` | `20` | Maksymalny rozmiar przesyłanego pliku (1–500 MB). |
| `JWT_ENABLED` | `true` | Uwierzytelnianie JWT; `false` tylko do lokalnego developmentu. |
| `JWT_SECRET_KEY` | — (wymagany) | Klucz weryfikacji tokenów, min. 32 znaki. |
| `JWT_ALGORITHM` | `HS256` | `HS256`, `HS384` lub `HS512`. |
| `JWT_EXPIRE_MINUTES` | `60` | Czas życia tokenu wystawianego skryptem CLI (1–1440). |
| `JWT_LEEWAY_SECONDS` | `30` | Tolerancja różnicy zegarów przy sprawdzaniu ważności tokenu. |
| `ANALYZE_WAIT_SECONDS` | `60` | Ile `POST /forms/analyze` czeka na wynik, zanim zwróci 202 z `job_id`. |
| `ANALYZE_MAX_WAIT_SECONDS` | `300` | Górny limit parametru `?wait=` (proxy frontendu ma timeout 300 s). |
| `MAX_BATCH_FILES` | `20` | Maksymalna liczba plików w jednym żądaniu batch. |
| `JOB_QUEUE_TTL_SECONDS` | `3600` | Jak długo plik czeka w pamięci kolejki na workera; potem zadanie wygasa. |
| `JOB_RESULT_TTL_SECONDS` | `3600` | Jak długo wynik zadania jest dostępny w `GET /jobs/{id}`. |
| `JOB_TIMEOUT_SECONDS` | `1800` | Maksymalny czas analizy jednego pliku. |
| `WORKER_MAX_JOBS` | `1` | Liczba równoległych analiz w jednym workerze (ograniczona przez GPU). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | — (wymagane) | Dane bazy; z nich compose składa `DATABASE_URL`. |
| `VALKEY_URL` | `redis://valkey:6379/0` | Adres kolejki. |
| `LLM_BASE_URL` / `LLM_MODEL` | `http://llm:8000/v1` / — | Serwer i nazwa lokalnego LLM tekstowego. |
| `VLM_BASE_URL` / `VLM_MODEL` | `http://vlm:8001/v1` / — | Serwer i nazwa lokalnego modelu wizyjnego. |
| `LLM_GPU_MEMORY_UTILIZATION` / `VLM_GPU_MEMORY_UTILIZATION` | `0.45` | Ułamek pamięci GPU na model (suma < 1 na jednej karcie). |
| `HF_TOKEN` | puste | Tylko gdy model na Hugging Face wymaga akceptacji licencji. |
| `ACCURACY_MODE` | `max` | `max` — podwójny odczyt każdej strony; `balanced` — tylko stron o niskiej pewności. |
| `RENDER_DPI` | `200` | Rozdzielczość renderowania stron (72–600). |
| `CONFIDENCE_HIGH` / `CONFIDENCE_LOW` | `0.85` / `0.5` | Progi pewności pola: zielone / żółte / czerwone (do kalibracji). |
| `API_PORT` / `FRONTEND_PORT` | `8000` / `8080` | Porty publikowane na hoście. |
| `TMPFS_SIZE` | `512m` | Rozmiar `tmpfs` na pliki tymczasowe w `api` i `worker`. |

## REST API

Interaktywna dokumentacja OpenAPI: `http://localhost:8000/api/docs`, specyfikacja:
`/api/openapi.json` (dostępne też przez frontend pod `/api/...`).

| Metoda | Ścieżka | JWT | Opis |
| --- | --- | --- | --- |
| `POST` | `/api/v1/forms/analyze` | tak | Jeden plik (`file`) → `200 {form, report}` albo `202` z `job_id`. |
| `POST` | `/api/v1/forms/batch` | tak | Wiele plików (`files`) → `202 {jobs, rejected}`. |
| `GET` | `/api/v1/jobs/{job_id}` | tak | Status `QUEUED` / `IN_PROGRESS` / `SUCCEEDED` / `FAILED`, postęp, wynik albo błąd. |
| `GET` | `/health` | nie | Liveness probe. |
| `GET` | `/ready` | nie | Readiness probe — sprawdza kolejkę (503, gdy niedostępna). |

**Uwierzytelnianie:** nagłówek `Authorization: Bearer <token>`. API tylko weryfikuje tokeny
(podpis, algorytm z `JWT_ALGORITHM`, wymagane `exp` i `sub`). Token wystawia
`python -m backend.api.token_cli --sub <użytkownik> [--minutes N]` uruchomione w kontenerze
`api`. Użytkownik widzi wyłącznie swoje zadania — cudze zwracają 404.

**Długie analizy:** `analyze` czeka na wynik najwyżej `ANALYZE_WAIT_SECONDS` (parametr
`?wait=` od 0 do `ANALYZE_MAX_WAIT_SECONDS`). Gdy analiza trwa dłużej, zwraca **202**
z `job_id`, nagłówkami `Location` i `Retry-After`; wynik i postęp (`progress`: etap,
krok / liczba kroków) są pod `GET /api/v1/jobs/{job_id}`.

**Walidacja plików:** dozwolone PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG, PNG; typ MIME wykryty
z treści (libmagic) musi pasować do rozszerzenia; nazwa ≤ 250 znaków bez `; : > < / \ | ' * % $`;
rozmiar ≤ `MAX_FILE_SIZE_MB`. W batch poprawne pliki trafiają do kolejki, a odrzucone są
zwracane w `rejected` z kodem i komunikatem.

**Plik nie jest utrwalany:** API trzyma go w pamięci (lub `tmpfs`), przekazuje do pamięci
Valkey (bez zapisu na dysk) z TTL, a worker usuwa go po analizie. W argumentach i wynikach
zadań są tylko metadane (nazwa, rozmiar, SHA-256, właściciel).

**Błędy** mają jeden format: `{"error": {"code": "...", "message": "...", "details": ...}}`.

| Kod HTTP | `error.code` |
| --- | --- |
| 400 | `FILENAME_MISSING`, `FILENAME_TOO_LONG`, `FILENAME_FORBIDDEN_CHARS`, `FILE_EMPTY`, `TOO_MANY_FILES` |
| 401 | `AUTH_REQUIRED`, `TOKEN_INVALID`, `TOKEN_EXPIRED` |
| 404 | `JOB_NOT_FOUND` |
| 413 | `FILE_TOO_LARGE`, `REQUEST_TOO_LARGE` |
| 415 | `EXTENSION_NOT_ALLOWED`, `MIME_MISMATCH` |
| 422 | `REQUEST_INVALID`, `BATCH_ALL_REJECTED`, `DOCUMENT_UNREADABLE` |
| 500 | `PIPELINE_ERROR`, `JOB_FAILED`, `FILE_EXPIRED`, `FILE_CORRUPTED` |
| 501 | `PIPELINE_NOT_IMPLEMENTED` |
| 503 | `QUEUE_UNAVAILABLE` |

## Development

```bash
python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check . && ruff format --check .
mypy
pytest
```

CI (`.github/workflows/ci.yml`) uruchamia: skan sekretów (Gitleaks), ruff, mypy, testy,
kontrolę licencji (blokuje GPL/AGPL/LGPL) oraz budowanie obrazów z weryfikacją, że nie działają
jako root i nie zawierają `.env`.

## Licencje

Projekt: MIT. Wszystkie zależności mają licencje permisywne; celowo pominięte są m.in. PyMuPDF
(AGPL), Marker/Surya (GPL), python-stdnum (LGPL), psycopg (LGPL — używamy asyncpg, Apache 2.0)
i Redis 8 (AGPL — używamy Valkey, BSD).
