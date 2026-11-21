# TEAMKAPA — COI „Cyfryzacja formularzy”

Minimalny szkielet repozytorium projektu hackathonowego COI „Cyfryzacja formularzy” (Python 3.14 + FastAPI + Docker).

## 1. Budowanie obrazu Docker

Docelowa instrukcja budowania obrazu zostanie uzupełniona po dodaniu pliku `Dockerfile` w kolejnych etapach prac:

```bash
docker build -t teamkapa-cyfryzacja-formularzy .
```

## 2. Uruchamianie aplikacji

Przed uruchomieniem skopiuj plik `.env.example` do `.env` i dostosuj zmienne środowiskowe:

```bash
cp .env.example .env
```

Docelowe uruchomienie kontenera po dodaniu pliku `Dockerfile` oraz warstwy aplikacyjnej FastAPI:

```bash
docker run --rm --env-file .env -p 8000:8000 teamkapa-cyfryzacja-formularzy
```

## 3. Opis konfiguracji

Parametry konfiguracyjne definiowane są w pliku `.env` (szablon: `.env.example`):

| Zmienna | Domyślna wartość | Opis |
| --- | --- | --- |
| `MAX_FILE_SIZE_MB` | `20` | Maksymalny dopuszczalny rozmiar przesyłanego pliku w megabajtach (MB). |
| `JWT_ENABLED` | `true` | Flaga włączająca lub wyłączająca uwierzytelnianie JWT dla API. |
| `JWT_SECRET_KEY` | `changeme` | Tajny klucz używany do podpisywania i weryfikacji tokenów JWT (wymaga zmiany w środowisku produkcyjnym). |
| `JWT_ALGORITHM` | `HS256` | Algorytm kryptograficzny używany do podpisywania tokenów JWT. |

## 4. Opis REST API

Dokumentacja endpointów REST API oraz interaktywna specyfikacja OpenAPI/Swagger zostaną uzupełnione po zaimplementowaniu warstwy aplikacji FastAPI w katalogu `backend/api/`.
