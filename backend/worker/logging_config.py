"""Konfiguracja logowania workera (``arq ... --custom-log-dict``).

arq na poziomie INFO loguje argumenty i wyniki zadań — w tym nazwy plików (mogą zawierać
dane osobowe) i identyfikator użytkownika. Dlatego logi arq są od poziomu WARNING, a zadanie
loguje samo, wyłącznie identyfikator zadania, typ i rozmiar pliku oraz wynik.
"""

from typing import Any

LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "loggers": {
        "arq": {"level": "WARNING"},
        "backend": {"level": "INFO"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}
