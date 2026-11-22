"""Odwzorowanie między modelami zespołu a modelami Etapu E (tylko odczyt modeli zespołu).

- ``field_refs``: pola formularza po normalizacji (``pipeline.normalize.NormalizedForm``, kroki
  06–09) → ``FieldRef`` (pole widziane przez kontrolę),
- ``text_blocks``: elementy tekstu z kroku 02 (``contracts.ExtractedDocument``) → bloki tekstu
  trybu 1 VLM i kontroli, **z tymi samymi ID**: dzięki temu ``label_block_id`` elementu
  wizualnego wskazuje ten sam blok, który krok 07 podaje jako ``element_id`` pola,
- ``acroform_blocks``: pola AcroForm z kroku 02 (kanał ``ACROFORM``) do kontroli kompletności.

Kod zespołu nie jest modyfikowany; odwzorowanie wyników kanału wizualnego na
``contracts.VisualAnalysis`` to propozycja dla #16/#17 (``docs/vision.md``).
"""

from backend.shared.schemas.quality import SourceChannel
from backend.worker.pipeline.contracts import ExtractedDocument
from backend.worker.pipeline.normalize import NormalizedForm
from backend.worker.vision.fusion.models import FieldRef
from backend.worker.vision.vlm.roles import TextBlock


def field_refs(normalized: NormalizedForm) -> list[FieldRef]:
    """Pola (bez sekcji) w kolejności formularza, ze źródłem z kroku 07."""
    refs: list[FieldRef] = []
    for _, _, leaf in normalized.form.iter_fields():
        if leaf is None:
            continue
        source = normalized.sources.get(leaf.id)
        refs.append(
            FieldRef(
                field_id=leaf.id,
                tech_name=leaf.tech_name,
                title=leaf.title,
                field_type=leaf.type,
                validators=tuple(str(v.value) for v in leaf.validation),
                page=source.page if source else None,
                source_text=source.text if source else None,
                text_element_id=source.element_id if source else None,
                origin=source.origin if source else None,
            )
        )
    return refs


def text_blocks(extracted: ExtractedDocument) -> list[TextBlock]:
    """Elementy tekstu z pozycją (bez pól AcroForm) jako bloki tekstu, z ID z kroku 02."""
    return [
        TextBlock(id=e.element_id, page=e.page, text=e.text, bbox=e.bbox, confidence=e.confidence)
        for e in extracted.elements
        if e.channel is not SourceChannel.ACROFORM and e.text.strip()
    ]


def acroform_blocks(extracted: ExtractedDocument) -> list[TextBlock]:
    return [
        TextBlock(id=e.element_id, page=e.page, text=e.text, bbox=e.bbox, confidence=e.confidence)
        for e in extracted.elements
        if e.channel is SourceChannel.ACROFORM
    ]
