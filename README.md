# TEAMKAPA — COI „Cyfryzacja formularzy”

Usługa REST, która z blankietu urzędowego (PDF, DOC/DOCX, ODS, XLSX, JPG/PNG) generuje JSON
konfiguracji e-formularza zgodny ze schematem COI. Każde pole ma źródło w dokumencie i ocenę
pewności — urzędnik sprawdza tylko pola niepewne. Modele językowe to wyłącznie modele
z otwartymi wagami, wołane przez API Eden AI (patrz [Modele językowe](#modele-językowe)).

> Stan: infrastruktura (#14), modele COI (#17), REST API (#13), kolejka z bazą (#15), ekstrakcja
> tekstu (#7) i integracja pipeline'u (#16) z etapami 05–09 (#6, #8) gotowe. Kanał wizualny
> (#10/#11) w trakcie — do tego czasu pola mają pewność najwyżej MEDIUM.

## Architektura uruchomieniowa

| Usługa | Obraz | Rola | Port na hoście |
| --- | --- | --- | --- |
| `migrate` | obraz `api` | jednorazowo stosuje migracje schematu (`db/migrations`) | — |
| `api` | `python:3.14.10-alpine3.24` | REST API (FastAPI), JWT, walidacja plików | `8000` |
| `worker` | `python:3.14.10-slim-trixie` | pipeline przetwarzania (arq) | — |
| `postgres` | `pgvector/pgvector:0.8.7-pg18-trixie` | zadania, wyniki, zatwierdzone formularze | — |
| `valkey` | `valkey/valkey:9.0.8-alpine3.24` | kolejka zadań batch | — |
| `llm-gateway` | `nginxinc/nginx-unprivileged:1.31.7-alpine3.24` | jedyne wyjście do internetu: zapytania do Eden AI, dokłada klucz API | — |
| `frontend` *(profil `frontend`)* | `node:24.22.3-alpine3.24` → `nginxinc/nginx-unprivileged:1.31.7-alpine3.24` | interfejs urzędnika | `8080` |

**Dlaczego Alpine dla API, a slim dla workera:** API ma tylko lekkie zależności z gotowymi kołami
dla musl, więc Alpine daje najmniejszy obraz. Worker używa Docling, OpenCV, pypdfium2 i PyTorch,
które publikują koła wyłącznie dla glibc (manylinux) — na Alpine wymagałyby kompilacji ze źródeł.

**Bezpieczeństwo kontenerów:**
- wszystkie obrazy mają przypięte wersje (bez `latest`) i działają jako użytkownik bez roota
  (`api`/`worker`: UID 10001, `postgres`, `valkey`, `nginx`: użytkownicy obrazu),
- system plików kontenerów jest tylko do odczytu (`read_only`), pliki tymczasowe trafiają do `tmpfs`,
  odebrane są wszystkie capabilities i włączone `no-new-privileges`,
- `worker`, `postgres` i `valkey` są tylko w sieci wewnętrznej bez dostępu do internetu;
  jedynym wyjściem jest `llm-gateway`, który przepuszcza wyłącznie `GET /v3/models`
  i `POST /v3/chat/completions` do `api.edenai.run` (z weryfikacją TLS) — przetwarzanie nie
  może wysłać danych nigdzie indziej, a worker nie zna klucza API,
- sekrety podawane są wyłącznie w runtime (`.env` lub Docker secrets w `/run/secrets`),
  nigdy w obrazach ani repozytorium (`.dockerignore`, `.gitignore`, Gitleaks w CI).

## Wymagania

- Docker z Compose v2 (x86-64; GPU nie jest potrzebne)
- klucz API Eden AI (`EDENAI_API_KEY`) i dostęp do internetu z hosta — albo `LLM_DEMO_MODE=true`
- do pracy lokalnej bez Dockera: Python 3.14

## Budowanie obrazów

```bash
docker compose build                        # api, worker
docker compose --profile frontend build     # + frontend
```

## Uruchomienie

```bash
cp .env.example .env
# ustaw JWT_SECRET_KEY (min. 32 znaki), POSTGRES_PASSWORD i EDENAI_API_KEY:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d                       # api, worker, postgres, valkey, llm-gateway
docker compose --profile frontend up -d    # + frontend na http://localhost:8080

curl http://localhost:8000/health          # {"status":"ok","version":"0.4.0"}

# token JWT dla użytkownika (sekret jest tylko w kontenerze)
TOKEN=$(docker compose exec -T api python -m backend.api.token_cli --sub urzednik)
curl -H "Authorization: Bearer $TOKEN" -F "file=@wniosek.pdf"      http://localhost:8000/api/v1/forms/analyze
```

Wywołania LLM korzystają z Eden AI API (`EDENAI_API_KEY`, `EDENAI_BASE_URL`, `LLM_MODEL`).
Bez klucza API można uruchomić tryb demonstracyjny (`LLM_DEMO_MODE=true`) — wynik jest jawnie
oznaczony w raporcie (`demo_mode`) i ma pewność LOW.

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
| `JOB_QUEUE_TTL_SECONDS` | `3600` | Jak długo plik czeka w pamięci kolejki na workera (łącznie z ponowieniami). |
| `JOB_RESULT_TTL_SECONDS` | `3600` | Jak długo arq trzyma status w Valkey (źródłem prawdy jest PostgreSQL). |
| `JOB_TIMEOUT_SECONDS` | `1800` | Maksymalny czas analizy jednego pliku (potem `PIPELINE_TIMEOUT`, bez ponowienia). |
| `WORKER_MAX_JOBS` | `1` | Liczba równoległych analiz w jednym workerze. |
| `MAX_JOB_TRIES` | `3` | Łączna liczba prób analizy pliku (błędy przejściowe, awarie workera). |
| `RETRY_BACKOFF_SECONDS` | `30` | Opóźnienie pierwszego ponowienia; kolejne ×2 (30 s, 60 s, …). |
| `RESULT_RETENTION_DAYS` | `30` | Po ilu dniach usuwane są zadania i wygenerowane wyniki; zatwierdzone formularze zostają. |
| `STALE_JOB_GRACE_SECONDS` | `120` | Po jakim czasie zadanie bez śladu w kolejce jest uznawane za utracone. |
| `DB_POOL_MAX_SIZE` | `5` | Maksymalna liczba połączeń z bazą na proces. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | — (wymagane) | Dane bazy; z nich compose składa `DATABASE_URL`. |
| `VALKEY_URL` | `redis://valkey:6379/0` | Adres kolejki. |
| `EDENAI_API_KEY` | — | Klucz API Eden AI. W compose zna go wyłącznie `llm-gateway`; bezpośrednio do `api.edenai.run` jest wymagany. |
| `EDENAI_BASE_URL` | `https://api.edenai.run/v3` | Adres Eden AI API. W compose worker używa bramki `http://llm-gateway:8080/v3` (ustawia ją `docker-compose.yml`). |
| `LLM_MODEL` / `VLM_MODEL` | `deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct` | Model `dostawca/model` z katalogu Eden AI — **musi mieć otwarte wagi** (np. `openai/gpt-4o-mini` jest blokowany). |
| `VLM_BASE_URL` | `http://llm-gateway:8080/v3` | Adres API modelu wizyjnego (kanał wizualny, #11). |
| `LLM_MAX_TOKENS` | `8192` | Limit długości odpowiedzi modelu. |
| `LLM_ZERO_DATA_RETENTION` | `true` | Tylko dostawcy, którzy nie zapisują zapytań ani odpowiedzi. |
| `LLM_TIMEOUT_SECONDS` | `300` | Limit czasu jednego wywołania modelu (generowanie JSON-a). |
| `LLM_DEMO_MODE` | `false` | Tryb demonstracyjny bez modelu: deterministyczne odpowiedzi etapów 06–08; raport ma `demo_mode: true`, wszystkie pola pewność LOW — nie do importu. |
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
| `POST` | `/api/v1/forms/batch` | tak | Wiele plików (`files`) → `202 {batch_id, status_url, jobs, rejected}`. |
| `GET` | `/api/v1/batches/{batch_id}` | tak | Stan paczki: liczniki statusów, zadania, odrzucone pliki, `finished`. |
| `GET` | `/api/v1/jobs` | tak | Moje zadania od najnowszych; filtry `status`, `batch_id`; `limit`/`offset`. |
| `GET` | `/api/v1/jobs/{job_id}` | tak | Status `QUEUED` / `IN_PROGRESS` / `SUCCEEDED` / `FAILED`, próby, postęp, wynik albo błąd. |
| `PUT` | `/api/v1/jobs/{job_id}/approval` | tak | Zatwierdzenie (poprawionego) formularza COI — walidacja modeli i reguł. |
| `GET` | `/api/v1/jobs/{job_id}/approval` | tak | Zatwierdzony formularz z ostrzeżeniami reguł. |
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
Valkey (bez zapisu na dysk) z TTL, a worker usuwa go po ostatecznym zakończeniu analizy.
W bazie i w argumentach zadań są wyłącznie dane pochodne: metadane (nazwa, rozmiar, SHA-256,
właściciel), wygenerowany i zatwierdzony JSON.

**Stan zadań i niezawodność:** źródłem prawdy jest PostgreSQL (tabele `jobs`, `batches`,
`approved_forms`; migracje w `db/migrations`, stosowane przez usługę `migrate`). Błąd
przejściowy pipeline'u (np. serwer modelu niedostępny) lub chwilowa niedostępność bazy
powodują ponowienie z wykładniczym opóźnieniem, łącznie do `MAX_JOB_TRIES` prób. Zadanie
przerwane zamknięciem workera przejmuje kolejny worker; po twardej awarii zadanie wraca do
kolejki po wygaśnięciu blokady. Zadania porzucone przez kolejkę (limit prób po awariach,
restart Valkey) co 5 minut uzgadnia worker — trafiają do `FAILED` z kodem `JOB_FAILED` lub
`JOB_LOST`. Codziennie usuwane są zadania starsze niż `RESULT_RETENTION_DAYS`.

**Zatwierdzanie:** `PUT /jobs/{id}/approval` przyjmuje plik importu COI po korekcie
urzędnika. Problemy reguł COI poziomu ERROR blokują zapis (422 `FORM_RULES_VIOLATED`
z listą problemów), ostrzeżenia są zwracane w `warnings`. Zatwierdzone formularze zostają
po retencji zadań (katalog wzorców).

**Błędy** mają jeden format: `{"error": {"code": "...", "message": "...", "details": ...}}`.

| Kod HTTP | `error.code` |
| --- | --- |
| 400 | `FILENAME_MISSING`, `FILENAME_TOO_LONG`, `FILENAME_FORBIDDEN_CHARS`, `FILE_EMPTY`, `TOO_MANY_FILES` |
| 401 | `AUTH_REQUIRED`, `TOKEN_INVALID`, `TOKEN_EXPIRED` |
| 404 | `JOB_NOT_FOUND`, `BATCH_NOT_FOUND`, `APPROVAL_NOT_FOUND` |
| 409 | `JOB_NOT_SUCCEEDED` |
| 413 | `FILE_TOO_LARGE`, `REQUEST_TOO_LARGE` |
| 415 | `EXTENSION_NOT_ALLOWED`, `MIME_MISMATCH` |
| 422 | `REQUEST_INVALID`, `BATCH_ALL_REJECTED`, `DOCUMENT_UNREADABLE`, `FORM_RULES_VIOLATED` |
| 500 | `PIPELINE_STAGE_FAILED`, `PIPELINE_ERROR`, `JOB_FAILED`, `JOB_LOST`, `FILE_EXPIRED`, `FILE_CORRUPTED` |
| 501 | `PIPELINE_NOT_IMPLEMENTED` |
| 503 | `QUEUE_UNAVAILABLE`, `DATABASE_UNAVAILABLE`, `PIPELINE_UNAVAILABLE` |
| 504 | `PIPELINE_TIMEOUT` |

## Modele językowe

Zgodnie z doprecyzowaniem regulaminu przez mentorów używamy **wyłącznie modeli z otwartymi
wagami**, możliwych do postawienia na własnej infrastrukturze, wołanych przez API Eden AI
(`/v3/chat/completions`, zgodne z OpenAI). Zasady wymuszane w kodzie
(`backend/worker/clients/model_api.py`):

- przed analizą model jest sprawdzany w publicznym katalogu `/v3/models` — bez
  `open_weights: true` analiza kończy się błędem `PIPELINE_STAGE_FAILED` (np. GPT, Claude
  są blokowane),
- zapytanie wskazuje konkretny model `dostawca/model` i nie zawiera `fallbacks` — Eden AI
  nie przełączy go na inny model,
- `zero_data_retention` — tylko dostawcy, którzy nie zapisują zapytań i odpowiedzi,
- do modelu trafia wyłącznie treść **pustego blankietu** (bez danych obywateli),
- raport jakości zawiera zużycie (`usage`: wywołania, tokeny, koszt w USD) — koszt analizy
  jednego formularza.

Domyślny model: `deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct` (wagi
[`Qwen/Qwen3-VL-30B-A3B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct),
Apache 2.0; tekst i obraz, tryb JSON). Wybór modelu ostatecznie rozstrzyga skrypt ewaluacji
(#8, #11).

## Pipeline analizy

`backend/worker/pipeline/orchestrator.py` prowadzi analizę jednego pliku:

1. **Odczyt** — równolegle kanał tekstowy (`TextExtractor`, krok 02, #7) i wizualny
   (`VisualAnalyzer`, krok 03, #10/#11). Kontrakty: `pipeline/contracts.py`, rejestr
   implementacji: `pipeline/stages.py`.
2. **Etapy 05–09** (#6, #8) — typ wniosku, struktura, pola, zależności, reguły i słowniki.
   Wywołania modelu działają w wątku, z limitem `LLM_TIMEOUT_SECONDS`.
3. **Normalizacja** (`pipeline/normalize.py`) — wynik etapów → plik importu COI. Źródła pól,
   propozycje słowników i konflikty reguł trafiają do raportu, a każda poprawka (pole poza
   sekcją, klucz niedozwolony dla typu, nazwa techniczna, dane inicjalne bez źródła) jest
   w nim odnotowana.
4. **Kontrola** — reguły COI i ocena pewności pól (`pipeline/scoring.py`): nazwane sygnały
   (`source_found`, `source_element`, `ocr`, `rules`, `domain_rules`, `visual_match`), poziom
   HIGH / MEDIUM / LOW. Pole odczytane bez kanału wizualnego ma najwyżej MEDIUM.

Raport zawiera też przebieg etapów (`stages`: OK / SKIPPED / DEGRADED, czas). Błędy:
niedostępny serwer modelu → ponowienie; awaria kanału wizualnego → analiza na tekście
(`DEGRADED`, niższa pewność); błąd etapu → `PIPELINE_STAGE_FAILED` z nazwą etapu.

**Podłączenie nowego etapu** (#7, #10/#11): zaimplementuj protokół z `contracts.py`
i zwróć implementację w `stages.default_stages()`.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check . && ruff format --check .
mypy
pytest
```

Testy integracyjne (prawdziwy PostgreSQL, Valkey i worker arq — w tym scenariusze awarii
workera i ponowień) uruchamiają się, gdy ustawisz:

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_USER=coi -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=coi_test pgvector/pgvector:0.8.7-pg18-trixie
docker run -d --rm -p 6379:6379 valkey/valkey:9.0.8-alpine3.24
export POSTGRES_TEST_URL=postgresql://coi:test@localhost:5432/coi_test
export VALKEY_TEST_URL=redis://localhost:6379/15
pytest
```

Każdy test integracyjny dostaje własny schemat bazy z zastosowanymi migracjami.

CI (`.github/workflows/ci.yml`) uruchamia: skan sekretów (Gitleaks), ruff, mypy, testy (z
prawdziwą libmagic, PostgreSQL i Valkey), kontrolę licencji (blokuje GPL/AGPL/LGPL) oraz
budowanie obrazów z weryfikacją, że nie działają jako root i nie zawierają `.env`.

## Licencje

Projekt: MIT. Wszystkie zależności mają licencje permisywne; celowo pominięte są m.in. PyMuPDF
(AGPL), Marker/Surya (GPL), python-stdnum (LGPL), psycopg (LGPL — używamy asyncpg, Apache 2.0)
i Redis 8 (AGPL — używamy Valkey, BSD).
