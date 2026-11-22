"""Etap E.4: etykieta z kilku odczytów (warstwa tekstowa wygrywa, skan: głosowanie 2 z 3)."""

from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.labels import LabelReading, resolve_label


def r(source: str, text: str) -> LabelReading:
    return LabelReading(source, text)  # type: ignore[arg-type]


def test_native_text_layer_wins_and_reports_variant() -> None:
    decision = resolve_label(
        [r("text_layer", "Numer PESEL"), r("vlm", "Adres e-mail")], native_text=True
    )
    assert (decision.text, decision.source, decision.codes) == (
        "Numer PESEL",
        "text_layer",
        (codes.LABEL_VARIANT,),
    )
    same = resolve_label([r("text_layer", "Numer PESEL"), r("vlm", "PESEL")], native_text=True)
    assert same.codes == () and same.agreeing == 2


def test_scan_majority_two_of_three() -> None:
    decision = resolve_label(
        [r("ocr", "Imię i nazwisko"), r("ocr2", "Imie i nazwlsko"), r("vlm", "Data urodzenia")],
        native_text=False,
    )
    assert (decision.text, decision.source, decision.codes) == ("Imię i nazwisko", "ocr", ())
    assert (decision.agreeing, decision.reads) == (2, 3)


def test_scan_text_comes_from_ocr_even_if_vlm_agrees() -> None:
    decision = resolve_label(
        [r("vlm", "Imię i nazwisko"), r("ocr", "Imie i nazwisko")], native_text=False
    )
    assert (decision.text, decision.source) == ("Imie i nazwisko", "ocr")


def test_scan_without_majority_is_disagreement() -> None:
    decision = resolve_label(
        [r("ocr", "Imię"), r("ocr2", "Data urodzenia"), r("vlm", "Adres zamieszkania")],
        native_text=False,
    )
    assert decision.text is None and decision.codes == (codes.LABEL_DISAGREEMENT,)
    two = resolve_label([r("ocr", "Imię"), r("vlm", "Numer telefonu")], native_text=False)
    assert two.codes == (codes.LABEL_DISAGREEMENT,)


def test_single_reading() -> None:
    single = resolve_label([r("ocr", "Numer PESEL"), r("vlm", "  ")], native_text=False)
    assert (single.text, single.codes) == ("Numer PESEL", (codes.LABEL_SINGLE_READ,))
    vlm_only = resolve_label([r("vlm", "Numer PESEL")], native_text=False)
    assert vlm_only.text is None and vlm_only.codes == (codes.LABEL_DISAGREEMENT,)
    assert resolve_label([], native_text=False).reads == 0


def test_native_without_text_layer_falls_back_to_vote() -> None:
    decision = resolve_label([r("ocr", "Kod pocztowy"), r("vlm", "Kod pocztowy")], native_text=True)
    assert (decision.text, decision.codes) == ("Kod pocztowy", ())
