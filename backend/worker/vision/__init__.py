"""Kanał wizualny (EPIC #9): renderowanie stron, wspólny układ współrzędnych, detekcja elementów.

Moduły:
- ``config``: limity i parametry renderowania (z konfiguracji runtime),
- ``models``: tymczasowe modele wyjścia renderera (``TODO(#17)``),
- ``coords``: przeliczenia współrzędnych do ułamków strony (``BoundingBox`` z #17),
- ``errors``: stałe kody błędów i ostrzeżeń,
- ``render``: renderowanie dokumentów do obrazów stron.

Pakiet ``render`` nie zależy od reszty kanału wizualnego, żeby #7 mógł go przejąć lub podmienić.
"""
