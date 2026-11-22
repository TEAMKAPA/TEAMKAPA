"""Krok 10 pipeline'u: kontrola kompletności, źródeł i zgodności (Etap E, #9), cienkie wejście.

Kontrola (``backend.worker.vision.fusion.control``) łączy pola z elementami wizualnymi, sprawdza
kompletność i źródła, porównuje podwójny odczyt i daje dowody pola (``FieldEvidence``); lista
problemów i pętla naprawcza są w ``vision.fusion.repair``. **Oceny pól nie przejmuje:** pewność
liczy ``pipeline.scoring.assess_fields`` (zespół), a dowody dołącza do niej
``vision.fusion.confidence.apply_evidence`` (punkt podpięcia; propozycja zmiany w
``docs/vision.md``). Podpięcie w orkiestratorze należy do #16.
"""

from backend.worker.vision.fusion.control import ControlResult, control_fields
from backend.worker.vision.fusion.repair import (
    FieldRepairer,
    RepairOutcome,
    build_problems,
    mark_unresolved,
    run_control_with_repair,
)

__all__ = [
    "ControlResult",
    "FieldRepairer",
    "RepairOutcome",
    "build_problems",
    "control_fields",
    "mark_unresolved",
    "run_control_with_repair",
]
