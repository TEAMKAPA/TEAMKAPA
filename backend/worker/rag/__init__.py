"""Baza wiedzy (RAG): katalog typów dokumentów i zatwierdzonych formularzy w pgvector.

- ``embedder`` — wektory dokumentów (multilingual-e5-small, lokalnie w workerze),
- ``catalog`` — nasze typy dokumentów (formularze od organizatora) i ich wzorce COI,
- ``knowledge`` — wyszukiwanie kNN, zapis wektora zadania, zasilanie katalogu,
- ``retrieval`` — decyzja o typie (próg podobieństwa / wybór modelu z top-k / nowy typ)
  i wzorzec podobnego formularza dla zapytań etapów 06–07.
"""
