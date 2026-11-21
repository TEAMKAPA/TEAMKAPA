"""Katalog danych inicjalnych (zadanie rozszerzające) — 1:1 z materiału „Dane inicjalne”.

Pole zasilane z rejestru ma: ``readOnly: true``, ``defaultValue: "ŹRÓDŁO.atrybut"``,
``defaultValueType: "EXTERNAL_SYSTEM"``. Jedno pole = jedno źródło i jeden atrybut.

Wyjątek z praktyki COI (oba referencyjne formularze od organizatora): dane kontaktowe
i adresowe z profilu zalogowanego (MLPZ: adres, telefon, e-mail) są podpowiedzią, którą
użytkownik może poprawić — ``readOnly: false``. Dane tożsamości (imię, nazwisko, PESEL)
i dane z rejestrów pozostają tylko do odczytu.
"""

from dataclasses import dataclass
from enum import StrEnum

from backend.shared.schemas.coi_form import FieldType


class InitialDataSource(StrEnum):
    PESEL = "PESEL"  # Rejestr PESEL
    PESEL_ADDRESS = "PESEL_ADDRESS"  # Dane zameldowania
    MLPZ = "MLPZ"  # Dane zalogowanego użytkownika


@dataclass(frozen=True, slots=True)
class InitialDataAttribute:
    source: InitialDataSource
    attribute: str
    name: str
    # None: materiał nie podaje typu pola dla tego atrybutu.
    field_type: FieldType | None

    @property
    def editable(self) -> bool:
        """Czy pole może pozostać edytowalne (``readOnly: false``) — patrz opis modułu."""
        return self.source is InitialDataSource.MLPZ and self.attribute in _MLPZ_EDITABLE

    @property
    def reference(self) -> str:
        """Wartość do ``defaultValue``, np. ``PESEL.name``."""
        return f"{self.source}.{self.attribute}"


#: Dane kontaktowe i adresowe z profilu — w formularzach COI edytowalne.
_MLPZ_EDITABLE = frozenset(
    {"phoneNumber", "email", "country", "voivodeship", "district", "community", "place",
     "postCode", "street", "building", "apartment"}
)  # fmt: skip

_T = FieldType.TEXT_FIELD
_D = FieldType.DATEPICKER

_PESEL: list[tuple[str, str, FieldType | None]] = [
    ("pesel", "Pesel z PESEL", _T),
    ("name", "Imię z PESEL", _T),
    ("surname", "Nazwisko z PESEL", _T),
    ("birthDate", "Data urodzenia z PESEL", _D),
    ("citizenship", "Narodowość z PESEL", _T),
    ("motherName", "Imię matki z PESEL", _T),
    ("birthPlace", "Miejsce urodzenia z PESEL", _T),
    ("motherSurname", "Nazwisko matki z PESEL", _T),
    ("sex", "Płeć z PESEL", _T),
    ("fatherName", "Imię ojca z PESEL", _T),
    ("secondName", "Drugie imię z PESEL", _T),
    ("birthCountry", "Kraj urodzenia z PESEL", _T),
    ("foreignBirthUSC", "Zagraniczne miasto USC urodzenia z PESEL", _T),
    ("familyName", "Nazwisko rodowe z PESEL", _T),
    ("fatherSurname", "Nazwisko ojca z PESEL", _T),
    ("maritalStatus", "Stan cywilny z PESEL", None),
]

_PESEL_ADDRESS: list[tuple[str, str, FieldType | None]] = [
    ("temporaryAddressVoivodeship", "Tymczasowy adres zameldowania: województwo z PESEL", _T),
    ("temporaryAddressDistrict", "Tymczasowy adres zameldowania: region z PESEL", None),
    ("temporaryAddressCommunity", "Tymczasowy adres zameldowania: gmina z PESEL", _T),
    ("temporaryAddressPlace", "Tymczasowy adres zameldowania: Miejscowość z PESEL", _T),
    ("temporaryAddressStreetType", "Tymczasowy adres zameldowania: Typ ulicy z PESEL", _T),
    ("temporaryAddressStreet", "Tymczasowy adres zameldowania: Ulica z PESEL", _T),
    ("temporaryAddressHouseNumber", "Tymczasowy adres zameldowania: Numer domu z PESEL", _T),
    (
        "temporaryAddressApartmentNumber",
        "Tymczasowy adres zameldowania: Numer lokalu z PESEL",
        _T,
    ),
    ("temporaryAddressPostalCode", "Tymczasowy adres zameldowania: Kod pocztowy z PESEL", _T),
    ("permanentAddressVoivodeship", "Stały adres zameldowania: województwo z PESEL", _T),
    ("permanentAddressDistrict", "Stały adres zameldowania: region z PESEL", _T),
    ("permanentAddressCommunity", "Stały adres zameldowania: gmina z PESEL", _T),
    ("permanentAddressPlace", "Stały adres zameldowania: Miejscowość z PESEL", _T),
    ("permanentAddressStreetType", "Stały adres zameldowania: Typ ulicy z PESEL", _T),
    ("permanentAddressStreet", "Stały adres zameldowania: Ulica z PESEL", _T),
    ("permanentAddressPostalCode", "Stały adres zameldowania: Kod pocztowy z PESEL", _T),
    ("permanentAddressHouseNumber", "Stały adres zameldowania: Numer domu z PESEL", _T),
    ("permanentAddressApartmentNumber", "Stały adres zameldowania: Numer lokalu z PESEL", _T),
]

_MLPZ: list[tuple[str, str, FieldType | None]] = [
    ("name", "Imię z MLPZ", _T),
    ("secondName", "Drugie Imię z MLPZ", _T),
    ("surname", "Nazwisko z MLPZ", _T),
    ("pesel", "Wartość PESEL z MLPZ", _T),
    ("phoneNumber", "Numer telefonu z MLPZ", _T),
    ("email", "Adres email z MLPZ", _T),
    ("country", "Kraj z MLPZ", _T),
    ("voivodeship", "Województwo z MLPZ", _T),
    ("district", "Dzielnica z MLPZ", _T),
    ("community", "Gmina z MLPZ", _T),
    ("place", "Miejscowość z MLPZ", _T),
    ("postCode", "Kod pocztowy z MLPZ", _T),
    ("street", "Ulica z MLPZ", _T),
    ("building", "Nr budynku z MLPZ", _T),
    ("apartment", "Nr lokalu z MLPZ", _T),
    ("institutionName", "Nazwa Organizacji z MLPZ", _T),
    ("descriptiveOrganizationName", "Opisowa nazwa organizacji z MLPZ", _T),
]


def _build() -> dict[str, InitialDataAttribute]:
    catalog: dict[str, InitialDataAttribute] = {}
    for source, rows in (
        (InitialDataSource.PESEL, _PESEL),
        (InitialDataSource.PESEL_ADDRESS, _PESEL_ADDRESS),
        (InitialDataSource.MLPZ, _MLPZ),
    ):
        for attribute, name, field_type in rows:
            entry = InitialDataAttribute(source, attribute, name, field_type)
            catalog[entry.reference] = entry
    return catalog


#: Klucz: wartość ``defaultValue`` (np. ``"MLPZ.email"``).
INITIAL_DATA_CATALOG: dict[str, InitialDataAttribute] = _build()
