"""Metryki porównania z konfiguracją referencyjną COI (eval/compare_reference.py)."""

import copy

import pytest

from eval.compare_reference import compare_forms
from tests.coi_helpers import reference_forms


@pytest.mark.parametrize("name", sorted(reference_forms()))
def test_reference_compared_with_itself_is_perfect(name: str) -> None:
    form = reference_forms()[name]
    result = compare_forms(form, form)
    assert result.fields["f1"] == 1.0
    assert result.sections["f1"] == 1.0
    assert set(result.attributes.values()) == {1.0}
    assert result.missing == [] and result.extra == []


def test_missing_field_and_wrong_type_are_measured() -> None:
    expected = reference_forms()["wniosek_numer_porzadkowy"]
    predicted = copy.deepcopy(expected)
    grid = predicted["tabs"][0]["questions"][2]["field"]["repeatingSectionGrid"]
    removed = grid.pop()["field"]["techName"]
    grid[0]["field"]["type"] = "TEXTAREA"
    result = compare_forms(predicted, expected)
    assert result.fields["recall"] < 1.0
    assert result.fields["precision"] == 1.0
    assert result.attributes["type"] is not None and result.attributes["type"] < 1.0
    assert any(label.endswith(f".{removed}") for label in result.missing)
