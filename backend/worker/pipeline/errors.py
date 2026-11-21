"""Wyjątki pipeline'u — ich typ decyduje o statusie zadania (patrz ``backend.worker.tasks``)."""


class PipelineNotImplementedError(RuntimeError):
    """Brakuje etapu potrzebnego do analizy (np. ekstrakcji tekstu przed scaleniem #7)."""


class DocumentUnreadableError(RuntimeError):
    """Treści dokumentu nie da się odczytać (uszkodzony plik, chroniony hasłem itp.)."""


class PipelineTemporarilyUnavailableError(RuntimeError):
    """Błąd przejściowy (np. lokalny serwer modelu niedostępny) — zadanie zostanie ponowione."""


class PipelineStageError(RuntimeError):
    """Etap zakończył się błędem ostatecznym; komunikat można pokazać użytkownikowi."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"Etap „{stage}” nie powiódł się: {message}")
        self.stage = stage
