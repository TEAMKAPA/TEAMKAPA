# Baza wiedzy (RAG)

Rozpoznawanie typu dokumentu i wzorzec podobnego formularza dla modelu językowego —
zgodnie z planem: *regex → hash → kNN embeddingów do katalogu → model wybiera z top-5 lub
„nowy typ”*, z pętlą zwrotną z formularzy zatwierdzonych przez urzędników.

## Elementy

| Element | Gdzie | Rola |
|---|---|---|
| Model embeddingów | `backend/worker/rag/embedder.py` | `intfloat/multilingual-e5-small` (MIT, 384 wymiary), lokalnie na CPU, wbudowany w obraz workera |
| Katalog typów | `backend/worker/rag/catalog/types.json` | 9 formularzy od organizatora: tytuł, słowa kluczowe, sygnatura dokumentu; konfiguracje COI typów 1 i 5 w `catalog/forms/` |
| Baza wiedzy | PostgreSQL + pgvector, `db/migrations/002_knowledge_base.sql` | `document_types`, `knowledge_examples` (wektor, SHA-256, konfiguracja COI), `jobs.embedding` |
| Wyszukiwanie | `backend/worker/rag/knowledge.py` | zasilenie katalogu przy starcie workera, kNN (`<=>`, indeks HNSW), dopasowanie po skrócie |
| Decyzja | `backend/worker/rag/retrieval.py` | kaskada progów, wybór modelu w strefie niepewności, wzorzec dla zapytań |
| Pętla zwrotna | `backend/shared/db/repository.py` (`save_approval`) | zatwierdzony formularz → przykład w bazie wiedzy (nowy typ dla nierozpoznanych) |

W bazie są wyłącznie dane pochodne: wektor, skrót SHA-256 i konfiguracja COI. Treść
dokumentów użytkowników nie jest zapisywana; sygnatury w katalogu to fragmenty publicznych
formularzy urzędowych.

## Przebieg (etap 05)

1. **Sygnatura** dokumentu: tytuł i początek treści (nagłówek, adresat, pierwsze etykiety),
   bez linii do wypełnienia i pustych komórek arkuszy; do 2000 znaków (~512 tokenów e5).
2. **Wektor** e5 (prefiks `query:`, średnia po tokenach, normalizacja L2) zapisany przy
   zadaniu — przy zatwierdzeniu trafi do bazy bez ponownego liczenia (API nie ma modelu).
3. **Hash**: ten sam plik był już zatwierdzony → typ i formularz z zatwierdzenia.
4. **kNN**: najbliższe przykłady (najlepszy na typ), top-5 w raporcie.
5. **Decyzja**:
   - podobieństwo ≥ `RAG_ACCEPT_SIMILARITY` (0,95) → typ przyjęty,
   - < `RAG_REJECT_SIMILARITY` (0,90) → nowy typ (bez wzorca),
   - pomiędzy → model językowy wybiera spośród kandydatów albo „nowy typ”; dostaje początek
     dokumentu wzorcowego każdego kandydata (dla typów z zatwierdzeń — pola wzorca) i regułę
     „dokładnie ta sama sprawa, nie podobna dziedzina”. W trybie demo: nowy typ.
6. **Wzorzec**: konfiguracja COI wybranego typu (strony tematyczne) trafia do zapytań etapów
   06–07; pola zgodne ze wzorcem dostają sygnał pewności `example_match`.

Raport (`report.retrieval`) podaje metodę (`hash`/`knn`/`llm`/`new`/`lexical`), podobieństwo,
kandydatów i źródło wzorca. Gdy baza wiedzy jest niedostępna albo `RAG_ENABLED=false`, typ
rozpoznaje dopasowanie słów kluczowych do katalogu (ostrzeżenie `KNOWLEDGE_BASE_UNAVAILABLE`).

## Pętla zwrotna

`PUT /api/v1/jobs/{id}/approval` w jednej transakcji zapisuje zatwierdzenie i przykład w bazie
wiedzy: wektor dokumentu z analizy + zatwierdzona konfiguracja. Dokument rozpoznany jako typ
z katalogu wzbogaca ten typ; nierozpoznany zakłada nowy typ (kod z tytułu formularza). Kolejny
podobny dokument dostaje zatwierdzony formularz jako wzorzec, a ten sam plik — po skrócie.

## Kalibracja i wyniki

`python eval/rag_classification.py --source "<Dane do KAPAForms>"` — 9 formularzy × 5 formatów
(PDF, DOC, DOCX, ODS, XLSX):

| Miara | Wynik |
|---|---|
| top-1 (typ obecny w katalogu) | **42/42** |
| podobieństwo do własnego typu | min 0,953, mediana 0,994 |
| najwyższe podobieństwo do obcego typu (leave-one-type-out) | 0,926 |
| margines top-1 − top-2 | min 0,064 |

`python eval/compare_reference.py --rag [--exclude-type TYP] --reference <COI JSON> <plik>` —
generowanie z prawdziwym modelem (Qwen3-VL-30B, Eden AI), F1 pól / sekcji:

| Scenariusz | Wniosek 1 (numer porządkowy, PDF) | Wniosek 5 (nadpłata, PDF) |
|---|---|---|
| bez RAG | 0,97 / 0,88 | 0,91 / 0,89 |
| RAG, typ z wzorcem w bazie | 0,97 / 0,94 | **0,98 / 1,00** |
| RAG, typ usunięty z bazy | 0,97 / 0,88 — nowy typ (0,895) | 0,90 / 0,89 — model: nowy typ (0,907) |

Uwaga: dla typów 1 i 5 wzorcem jest konfiguracja referencyjna, z którą porównujemy — ten
wiersz pokazuje scenariusz po zatwierdzeniu formularza danego typu (pętla zwrotna), a nie
uogólnienie. Wiersz „typ usunięty z bazy” sprawdza, że dokument spoza bazy nie dostaje cudzego
wzorca. Koszt: ~0,003 USD na formularz.

## Utrzymanie

- Nowy typ do katalogu: wpis w `types.json` (kod, tytuł, słowa kluczowe, `source_folder`,
  opcjonalnie `form`) i `python -m backend.worker.rag.build_catalog --source <katalog>`;
  worker przelicza wektory zmienionych sygnatur przy starcie.
- Zmiana modelu embeddingów wymaga nowej migracji (wymiar `vector(384)`) i przeliczenia bazy.
- Progi kalibrować ponownie przy rozszerzeniu katalogu (`eval/rag_classification.py`).
