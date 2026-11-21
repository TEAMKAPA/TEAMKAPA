"""Unit tests for domain validators, COI dictionary mapping, and conflict overrides."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.shared.validators.domain import (  # noqa: E402
    VALIDATORS,
    validate_date,
    validate_email,
    validate_nip,
    validate_pesel,
    validate_phone,
    validate_postal_code,
    validate_regon,
    validate_value,
)
from backend.worker.pipeline.step09_rules_dicts import (  # noqa: E402
    COI_DICTIONARIES,
    apply_rules_and_dicts,
    infer_domain_rule,
    match_coi_dictionary,
    run_step09,
)


class TestDomainValidators:
    @pytest.mark.parametrize(
        "pesel",
        [
            "90051212349",  # 1990-05-12
            "00222900016",  # 2000-02-29 (leap year, 2000s encoding)
            "99923100007",  # 1899-12-31 (1800s encoding)
            " 90051212349 ",
        ],
    )
    def test_pesel_valid(self, pesel: str) -> None:
        assert validate_pesel(pesel) is True

    @pytest.mark.parametrize(
        "pesel",
        [
            "90051212340",  # bad checksum
            "23222900015",  # valid checksum, invalid non-leap date 2023-02-29
            "90131212345",  # invalid month 13
            "9005121234",  # too short
            "900512123490",  # too long
            "9005121234a",  # non-digit
            "",
        ],
    )
    def test_pesel_invalid(self, pesel: str) -> None:
        assert validate_pesel(pesel) is False

    @pytest.mark.parametrize(
        "nip",
        [
            "5261040828",
            "526-10-40-828",
            "526 104 08 28",
        ],
    )
    def test_nip_valid(self, nip: str) -> None:
        assert validate_nip(nip) is True

    @pytest.mark.parametrize(
        "nip",
        [
            "5261040829",  # bad checksum
            "1234567890",  # checksum == 10 (must be rejected)
            "0000000000",  # all zeros
            "526104082",  # too short
            "52610408281",  # too long
            "526104082a",  # non-digit
            "",
        ],
    )
    def test_nip_invalid(self, nip: str) -> None:
        assert validate_nip(nip) is False

    @pytest.mark.parametrize(
        "regon",
        [
            "123456785",  # valid 9-digit
            "100000050",  # valid 9-digit where checksum == 10 -> 0
            "12345678512347",  # valid 14-digit
            "123 456 785",
        ],
    )
    def test_regon_valid(self, regon: str) -> None:
        assert validate_regon(regon) is True

    @pytest.mark.parametrize(
        "regon",
        [
            "123456786",  # bad 9-digit checksum
            "12345678512348",  # bad 14-digit checksum
            "12345678612347",  # bad first 9 digits in 14-digit REGON
            "000000000",  # all zeros
            "00000000000000",  # all zeros 14
            "12345678",  # wrong length
            "123456785123",  # wrong length
            "12345678a",
            "",
        ],
    )
    def test_regon_invalid(self, regon: str) -> None:
        assert validate_regon(regon) is False

    @pytest.mark.parametrize("code", ["00-001", "30-059", " 80-233 "])
    def test_postal_code_valid(self, code: str) -> None:
        assert validate_postal_code(code) is True

    @pytest.mark.parametrize(
        "code",
        ["00001", "0-0001", "00-0001", "AB-CDE", "00 001", ""],
    )
    def test_postal_code_invalid(self, code: str) -> None:
        assert validate_postal_code(code) is False

    @pytest.mark.parametrize(
        "phone",
        [
            "500600700",
            "500 600 700",
            "500-600-700",
            "(22) 123-45-67",
            "+48 500 600 700",
            "+48500600700",
            "0048500600700",
        ],
    )
    def test_phone_valid(self, phone: str) -> None:
        assert validate_phone(phone) is True

    @pytest.mark.parametrize(
        "phone",
        [
            "12345",
            "+48 123",
            "500-abc-700",
            "+1234567890123456",
            "(22 123 45 67",
            "",
        ],
    )
    def test_phone_invalid(self, phone: str) -> None:
        assert validate_phone(phone) is False

    @pytest.mark.parametrize(
        "email",
        [
            "jan.kowalski@example.com",
            "anna+test@urzad.gov.pl",
            "user_1@sub.domain.org",
        ],
    )
    def test_email_valid(self, email: str) -> None:
        assert validate_email(email) is True

    @pytest.mark.parametrize(
        "email",
        [
            "plainaddress",
            "jan..kowalski@example.com",
            ".jan@example.com",
            "jan@example",
            "jan@-example.com",
            "jan@example.123",
            "jan kowalski@example.com",
            "",
        ],
    )
    def test_email_invalid(self, email: str) -> None:
        assert validate_email(email) is False

    @pytest.mark.parametrize(
        "date_str",
        [
            "2025-03-15",
            "15-03-2025",
            "15.03.2025",
            "15/03/2025",
            "2024-02-29",
        ],
    )
    def test_date_valid(self, date_str: str) -> None:
        assert validate_date(date_str) is True

    @pytest.mark.parametrize(
        "date_str",
        [
            "2025-02-29",  # non-leap year
            "31.04.2025",  # April has 30 days
            "2025-13-01",
            "2025-3-5",
            "not-a-date",
            "",
        ],
    )
    def test_date_invalid(self, date_str: str) -> None:
        assert validate_date(date_str) is False

    def test_validators_registry_and_helper(self) -> None:
        expected_keys = {
            "pesel",
            "nip",
            "regon",
            "postal_code",
            "phone",
            "email",
            "date",
        }
        assert set(VALIDATORS.keys()) == expected_keys
        assert validate_value("pesel", "90051212349") is True
        assert validate_value("postal_code", "00-950") is True
        with pytest.raises(KeyError):
            validate_value("nonexistent", "123")


class TestStep09RulesAndDicts:
    def test_box_count_inference(self) -> None:
        pesel = infer_domain_rule("Numer identyfikacyjny", box_count=11)
        nip = infer_domain_rule("Numer", box_count=10)
        postal5 = infer_domain_rule("Kod", box_count=5)
        postal6 = infer_domain_rule("Kod", box_count=6)
        regon9 = infer_domain_rule("REGON", box_count=9)
        regon14 = infer_domain_rule("REGON", box_count=14)

        assert pesel is not None and pesel.validator == "pesel"
        assert pesel.max_length == 11
        assert nip is not None and nip.validator == "nip"
        assert nip.max_length == 10
        assert postal5 is not None and postal5.validator == "postal_code"
        assert postal5.max_length == 6
        assert postal6 is not None and postal6.validator == "postal_code"
        assert regon9 is not None and regon9.max_length == 9
        assert regon14 is not None and regon14.max_length == 14

    def test_official_six_coi_dictionaries(self) -> None:
        assert set(COI_DICTIONARIES.keys()) == {
            "Województwo",
            "Powiat",
            "Gmina",
            "Miejscowość",
            "Kraje",
            "Numery kierunkowe",
        }
        assert match_coi_dictionary("Województwo zamieszkania") == "Województwo"
        assert match_coi_dictionary("Powiat") == "Powiat"
        assert match_coi_dictionary("Gmina / Dzielnica") == "Gmina"
        assert match_coi_dictionary("Miejscowość") == "Miejscowość"
        assert match_coi_dictionary("Kraj urodzenia") == "Kraje"
        assert match_coi_dictionary("Obywatelstwo") == "Kraje"
        assert match_coi_dictionary("Numer kierunkowy telefonu") == "Numery kierunkowe"

    @pytest.mark.parametrize(
        "label,expected_dict",
        [
            ("Województwo", "Województwo"),
            ("Powiat", "Powiat"),
            ("Gmina", "Gmina"),
            ("Miejscowość", "Miejscowość"),
            ("Kraj urodzenia", "Kraje"),
        ],
    )
    def test_five_coi_dicts_strictly_require_dropdown(self, label: str, expected_dict: str) -> None:
        result = apply_rules_and_dicts([{"id": "f1", "label": label, "type": "radio"}])
        field = result["fields"][0]
        assert field["dictionary"] == expected_dict
        assert field["type"] == "dropdown"
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["attribute"] == "type"
        assert result["conflicts"][0]["llm_value"] == "radio"
        assert result["conflicts"][0]["rule_value"] == "dropdown"

    def test_area_code_allows_dropdown_radio_checkbox_and_overrides_text(self) -> None:
        fields = [
            {"id": "d1", "label": "Numer kierunkowy", "type": "dropdown"},
            {"id": "d2", "label": "Numer kierunkowy", "type": "radio"},
            {"id": "d3", "label": "Numer kierunkowy", "type": "checkbox"},
            {"id": "d4", "label": "Numer kierunkowy", "type": "text"},
        ]
        result = apply_rules_and_dicts(fields)
        by_id = {f["id"]: f for f in result["fields"]}

        assert by_id["d1"]["type"] == "dropdown"
        assert by_id["d2"]["type"] == "radio"
        assert by_id["d3"]["type"] == "checkbox"
        assert by_id["d4"]["type"] == "dropdown"
        for field in by_id.values():
            assert field["dictionary"] == "Numery kierunkowe"

        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["field_id"] == "d4"

    def test_new_dictionary_proposal_for_unmatched_closed_list(self) -> None:
        fields = [
            {
                "id": "stan_cywilny",
                "label": "Stan cywilny",
                "type": "radio",
                "options": ["Kawaler/Panna", "Żonaty/Zamężna", "Rozwiedziony/a"],
            },
            {
                "id": "wojewodztwo",
                "label": "Województwo",
                "type": "dropdown",
                "options": ["Mazowieckie", "Małopolskie"],
            },
        ]
        result = apply_rules_and_dicts(fields)

        assert len(result["dictionary_proposals"]) == 1
        proposal = result["dictionary_proposals"][0]
        assert proposal["field_id"] == "stan_cywilny"
        assert proposal["name"] == "Stan cywilny"
        assert proposal["values"] == [
            "Kawaler/Panna",
            "Żonaty/Zamężna",
            "Rozwiedziony/a",
        ]
        assert proposal["structure"]["key_type"] == "code"
        assert len(proposal["structure"]["entries"]) == 3
        assert "Stan cywilny" in proposal["usage"]

    def test_deterministic_rule_overrides_llm_conflicts(self) -> None:
        raw_form: dict[str, Any] = {
            "form_id": "test_form",
            "fields": [
                {
                    "id": "pesel",
                    "label": "Numer PESEL",
                    "type": "number",
                    "validator": "nip",
                    "max_length": 20,
                    "dictionary": "Kraje",
                    "box_count": 11,
                },
                {
                    "id": "custom_field",
                    "label": "Rodzaj sprawy",
                    "type": "text",
                    "dictionary": "NieistniejacySlownik",
                },
            ],
        }
        result = run_step09(raw_form)

        # Input must not be mutated.
        assert raw_form["fields"][0]["type"] == "number"

        pesel_field = result["fields"][0]
        assert pesel_field["type"] == "text"
        assert pesel_field["validator"] == "pesel"
        assert pesel_field["max_length"] == 11
        assert pesel_field["dictionary"] is None

        pesel_conflicts = {
            c["attribute"]: (c["llm_value"], c["rule_value"])
            for c in result["conflicts"]
            if c["field_id"] == "pesel"
        }
        assert pesel_conflicts == {
            "type": ("number", "text"),
            "validator": ("nip", "pesel"),
            "max_length": (20, 11),
            "dictionary": ("Kraje", None),
        }

        custom_field = result["fields"][1]
        assert custom_field["dictionary"] is None
        custom_conflicts = [c for c in result["conflicts"] if c["field_id"] == "custom_field"]
        assert len(custom_conflicts) == 1
        assert custom_conflicts[0]["rule"] == "coi_dictionary_whitelist"

    def test_run_step09_supports_coi_form_definition_from_step08(self) -> None:
        coi_form: dict[str, Any] = {
            "title": "Wniosek o wydanie dowodu osobistego",
            "tabs": [
                {
                    "id": "tab-1",
                    "title": "Dane wnioskodawcy",
                    "type": "MAIN",
                    "questions": [
                        {
                            "x": 0,
                            "y": 0,
                            "rows": 1,
                            "cols": 12,
                            "field": {
                                "id": "sec-1",
                                "type": "SECTION",
                                "techName": "twoje_dane",
                                "title": "Twoje dane",
                                "repeatingSectionGrid": [
                                    {
                                        "x": 0,
                                        "y": 0,
                                        "rows": 1,
                                        "cols": 12,
                                        "field": {
                                            "id": "f-pesel",
                                            "type": "NUMBER",
                                            "techName": "pesel",
                                            "title": "Numer PESEL",
                                            "validation": ["REQUIRED", "NIP"],
                                        },
                                    },
                                    {
                                        "x": 0,
                                        "y": 1,
                                        "rows": 1,
                                        "cols": 12,
                                        "field": {
                                            "id": "f-woj",
                                            "type": "RADIO",
                                            "techName": "wojewodztwo",
                                            "title": "Województwo",
                                            "validation": ["REQUIRED"],
                                            "options": [
                                                {
                                                    "id": "o1",
                                                    "name": "Mazowieckie",
                                                    "techName": "mazowieckie",
                                                }
                                            ],
                                        },
                                    },
                                    {
                                        "x": 0,
                                        "y": 2,
                                        "rows": 1,
                                        "cols": 12,
                                        "field": {
                                            "id": "f-powod",
                                            "type": "RADIO",
                                            "techName": "powod_wniosku",
                                            "title": "Powód złożenia wniosku",
                                            "validation": ["REQUIRED"],
                                            "options": [
                                                {
                                                    "id": "p1",
                                                    "name": "Pierwszy dowód",
                                                    "techName": "pierwszy",
                                                },
                                                {
                                                    "id": "p2",
                                                    "name": "Utrata dowodu",
                                                    "techName": "utrata",
                                                },
                                            ],
                                        },
                                    },
                                    {
                                        "x": 0,
                                        "y": 3,
                                        "rows": 1,
                                        "cols": 12,
                                        "field": {
                                            "id": "f-zgoda",
                                            "type": "CONSENT_SECTION",
                                            "techName": "zgoda_1",
                                            "title": "Wyrażam zgodę",
                                            "consentSectionConfig": {
                                                "consents": [
                                                    {
                                                        "description": "Zgoda",
                                                        "required": True,
                                                    }
                                                ],
                                                "required": True,
                                                "files": [],
                                            },
                                        },
                                    },
                                ],
                            },
                        }
                    ],
                },
                {
                    "id": "tab-finish",
                    "title": "Podsumowanie",
                    "type": "FINISH",
                    "questions": [
                        {
                            "x": 0,
                            "y": 0,
                            "rows": 1,
                            "cols": 12,
                            "field": {
                                "id": "info-1",
                                "type": "INFO_SECTION",
                                "techName": "podglad",
                                "description": "Sprawdź dane przed wysłaniem.",
                            },
                        }
                    ],
                },
            ],
            "flow": {"links": [{"id": "l1", "source": "tab-1", "target": "tab-finish"}]},
        }

        result = run_step09(coi_form)

        # Input must remain untouched.
        orig_grid = coi_form["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"]
        assert orig_grid[0]["field"]["type"] == "NUMBER"

        grid = result["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"]
        by_tech = {c["field"]["techName"]: c["field"] for c in grid}

        # PESEL overridden from NUMBER/NIP -> TEXT_FIELD/PESEL.
        assert by_tech["pesel"]["type"] == "TEXT_FIELD"
        assert by_tech["pesel"]["validation"] == ["REQUIRED", "PESEL"]

        # Województwo overridden from RADIO -> SELECT + dictionaryName + empty options.
        assert by_tech["wojewodztwo"]["type"] == "SELECT"
        assert by_tech["wojewodztwo"]["dictionaryName"] == "Województwo"
        assert by_tech["wojewodztwo"]["options"] == []

        # CONSENT_SECTION stays untouched.
        assert by_tech["zgoda_1"]["type"] == "CONSENT_SECTION"

        # Unmatched closed-list field produces a new dictionary proposal.
        assert len(result["dictionary_proposals"]) == 1
        assert result["dictionary_proposals"][0]["name"] == "Powód złożenia wniosku"
        assert result["dictionary_proposals"][0]["values"] == [
            "Pierwszy dowód",
            "Utrata dowodu",
        ]

        # Conflicts recorded for PESEL (type + validation) and Województwo (type).
        conflict_pairs = {(c["field_id"], c["attribute"]) for c in result["conflicts"]}
        assert ("pesel", "type") in conflict_pairs
        assert ("pesel", "validation") in conflict_pairs
        assert ("wojewodztwo", "type") in conflict_pairs
