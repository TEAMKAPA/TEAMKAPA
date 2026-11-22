"""Fuzja OpenCV i VLM, kontrola kompletności i źródeł, pewność pól, iteracja naprawcza (Etap E).

Moduły:

- ``codes``: kody powodów, poziom ważności, skutek dla poziomu pewności i akcja naprawcza,
- ``config``: progi ``VISION_FUSION_*``; progi poziomów z ``CONFIDENCE_HIGH`` i ``CONFIDENCE_LOW``,
- ``models``: element po fuzji, pole wejściowe, dowody pola, problem do naprawy, pasma pewności,
- ``elements``: fuzja elementów strony (krok 4),
- ``evidence``: kontrola elementów dodanych przez VLM na pikselach strony,
- ``labels``: rozstrzyganie etykiet z kilku odczytów (Docling, OCR, VLM),
- ``linking``: pole ↔ element wizualny, kompletność, źródła w tekście strony,
- ``confidence``: sygnały i bramki poziomu pola, punkt podpięcia do ``pipeline.scoring``,
- ``repair``: lista problemów, protokół ``FieldRepairer`` (propozycja dla #8), pętla z limitem,
- ``adapters``: odwzorowanie na kontrakty zespołu (``contracts``, ``quality``; propozycja #16/#17).

Modele są tymczasowe (``TODO(#17)``); kod zespołu (``pipeline/*``) nie jest modyfikowany.
"""
