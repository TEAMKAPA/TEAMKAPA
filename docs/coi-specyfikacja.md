# Specyfikacja COI — opracowanie zespołu

Streszczenie materiałów organizatora („Materiały do zadania dla Uczestników”) i decyzje
zespołu tam, gdzie materiały są niespójne. Kod: `backend/shared/schemas/coi_form.py`
(modele), `backend/shared/validators/form_rules.py` (reguły), wzorcowy formularz:
`tests/fixtures/sample_form.json`, oficjalny schemat: `docs/coi/importowanyPlikJsonSchemat.json`.

## Format pliku importu (`FormDefinition`)

```text
title                         ≤ 255 znaków; „Wniosek/Zgłoszenie/Deklaracja/Oświadczenie …”
tabs[]                        strony
  id (UUID), title, type      MAIN | FINISH
  questions[]                 wyłącznie sekcje
    x, y, rows, cols          grid-area: {y+1} / {x+1} / span {rows} / span {cols}
    field                     SECTION | REPEATING_SECTION (title, techName, states: SHOW)
      repetingSectionGrid[]   pola sekcji — x, y, rows, cols, field (pisownia schematu!)
flow.links[]                  id (UUID), source, target — przejścia między stronami
```

Każde pole (`field`) ma `id` (UUID) i `techName`; opcjonalnie `questionOrder`, `tooltipMessage`.
Dozwolone klucze zależą od typu:

| Typ | Klucze poza wspólnymi | Walidatory |
| --- | --- | --- |
| `TEXT_FIELD` | title, description, readOnly, defaultValue, defaultValueType, states | dowolne |
| `CHECKBOX` | title, description, readOnly, options / dictionaryName, states | REQUIRED |
| `NUMBER` | title, description, readOnly, states | REQUIRED |
| `SELECT` | title, description, readOnly, searchEnabled, options / dictionaryName, defaultValue, states | REQUIRED |
| `TEXTAREA` | title, description, readOnly, defaultValue, states | REQUIRED |
| `FILE_UPLOADER` | title, description, defaultValue, states | REQUIRED |
| `RADIO` | title, description, readOnly, options / dictionaryName, states | REQUIRED |
| `DATEPICKER` | title, description, readOnly, defaultValue*, states | REQUIRED |
| `TEXT_BLOCK` | title, description, states (tylko SHOW) | — |
| `CONSENT_SECTION` | selectAllEnabled, consentSectionConfig {consents[{description, required}], required, files} | — |
| `INFO_SECTION` | description (wymagany) | — |

\* dla danych inicjalnych (`PESEL.birthDate`).

Walidatory: `REQUIRED, EMAIL, PESEL, NIP, REGON, ZIP_CODE, EPUAP, ADE, SERIAL_NUMBER_ID_CARD`.
Pola do wypełnienia **muszą** mieć klucz `validation` (pusta tablica = pole opcjonalne).
Pole ze słownikiem ma `dictionaryName` i **pustą** tablicę `options`.

## Reguły formularza (sprawdzane przez `validate_form_rules`)

| Kod | Poziom | Reguła |
| --- | --- | --- |
| `TABS_MIN_TWO` | ERROR | co najmniej dwie strony |
| `FINISH_TAB_LAST` / `FINISH_TAB_SINGLE` | ERROR | ostatnia strona FINISH, jedyna; pozostałe MAIN |
| `FINISH_ONLY_INFO_SECTION` | ERROR | na FINISH wyłącznie pola INFO_SECTION |
| `TAB_EMPTY` / `SECTION_EMPTY` | ERROR / WARNING | strona ma ≥ 1 pole; sekcja bez pól |
| `TAB_TITLE_DUPLICATE`, `TECHNAME_DUPLICATE_IN_TAB`, `TECHNAME_DUPLICATE_IN_SECTION` | ERROR | unikalność wymagana przez adresowanie w JSONacie |
| `DUPLICATE_ID`, `OPTION_*_DUPLICATE` | ERROR | unikalne identyfikatory i opcje |
| `FLOW_*` | ERROR | przejścia między istniejącymi stronami, każda strona osiągalna z pierwszej i prowadząca do FINISH, FINISH bez wyjść |
| `STATE_UNKNOWN_TAB/SECTION/FIELD/OPTION` | ERROR | warunek JSONata wskazuje istniejące pole i istniejącą opcję |
| `STATE_SELF_REFERENCE`, `STATE_CYCLE` | ERROR | brak zależności od siebie i cykli |
| `STATE_FORWARD_REFERENCE`, `STATE_QUERY_UNPARSED` | WARNING | zależność od późniejszej strony; warunek do ręcznej weryfikacji |
| `INITIAL_DATA_UNKNOWN`, `INITIAL_DATA_NOT_READONLY` | ERROR | dane inicjalne: znane `ŹRÓDŁO.atrybut`, pole `readOnly` |
| `INITIAL_DATA_TYPE_MISMATCH`, `INITIAL_DATA_MISSING_TYPE` | WARNING | typ pola zgodny z katalogiem; brak `EXTERNAL_SYSTEM` |
| `INPUT_WITHOUT_TITLE`, `GRID_OVERLAP`, `QUESTION_ORDER_DUPLICATE` | WARNING | etykieta pola; układ siatki |

Warunek JSONata (z materiału): `"Opcja" in tabs['Strona' in title].questions['sekcja' in
field.techName].field.repeatingSectionGrid['pole' in field.techName].field.__userAnswer`
(sekcja powtarzająca: `…field.__userAnswer[$repeatingSectionIndex]['pole' in fieldTechName].answers`).

## Słowniki i dane inicjalne

- Słowniki tylko w SELECT / RADIO / CHECKBOX; Województwo, Powiat, Gmina, Miejscowość, Kraje
  tylko w SELECT; Numery kierunkowe przy telefonie. Propozycje nowych słowników — rozszerzenie.
- Dane inicjalne: `readOnly: true`, `defaultValue: "ŹRÓDŁO.atrybut"`,
  `defaultValueType: "EXTERNAL_SYSTEM"`; źródła `PESEL`, `PESEL_ADDRESS`, `MLPZ` — pełny
  katalog atrybutów w `backend/shared/schemas/initial_data.py`.

## Standardowe sekcje (dokument „Standardy i dobre praktyki”)

Kolejność stron: dane stron postępowania → przedmiot wniosku → kontakt, opłaty, zgody →
podsumowanie (FINISH). Strona 1: **Urząd** (TEXT_FIELD readOnly z nazwą urzędu) → **Podmiot
wniosku** (RADIO Swoim / Innej osoby / Osoby prawnej lub jednostki organizacyjnej; warunkowo
RADIO Pełnomocnik / Reprezentant) → **Twoje dane** (MLPZ, readOnly) → **Adres** → warunkowo
**Dane innej osoby**, **Dane podmiotu** (NIP, REGON, KRS), **Pełnomocnictwo**, **Opłata
skarbowa** (17 zł). Przedostatnia: **Dane do kontaktu** (opcjonalne) i **Zgoda**
(CONSENT_SECTION — nie checkbox ani radio). Podpis elektroniczny jest domyślny i nie jest polem.
Lista rozwijana przy > 5 opcjach, wyszukiwarka przy > 15; układ jednokolumnowy.

Te sekcje są powtarzalne — pipeline powinien wstawiać je z gotowych szablonów zamiast
generować modelem (taniej i bez halucynacji).

## Decyzje zespołu przy niespójnościach materiałów

| Niespójność | Decyzja |
| --- | --- |
| `repetingSectionGrid` (schemat) vs `repeatingSectionGrid` (przykłady JSONaty) | klucz w JSON-ie jak w schemacie; zapytania JSONata jak w przykładach; walidator akceptuje obie pisownie |
| FINISH: tylko INFO_SECTION (reguły) vs TEXT_BLOCK „Podsumowanie/Podgląd” (standardy) | reguły (zadanie obligatoryjne) — treść podsumowania w `description` pola INFO_SECTION |
| `defaultValue: []` (reguły) vs `string` (schemat) | zawsze string, jak w schemacie |
| klucze spoza schematu | tylko te z reguł: `searchEnabled`, `selectAllEnabled`, `consents[].required`; limity plików, placeholder i regex „Pattern” ze standardów trafiają do raportu jakości |
| `CONSENT_SECTIOM`, `descripton` (literówki w opisie) | `CONSENT_SECTION`, `description` (jak w schemacie i regułach) |
| tagi stron `main`/`finish` (reguły) vs `MAIN`/`FINISH` (opis JSON) | `MAIN` / `FINISH` |
| pewność i źródła pól — brak w schemacie | osobny `QualityReport`; API zwraca `{form, report}`, `form` to czysty plik importu |

## Pytania do mentorów

1. Która pisownia klucza pól sekcji jest właściwa: `repetingSectionGrid` czy `repeatingSectionGrid`?
2. Czy na stronie FINISH dopuszczalny jest TEXT_BLOCK (standardy), czy wyłącznie INFO_SECTION (reguły)?
   Czy INFO_SECTION na FINISH też musi być w sekcji?
3. Dokładne wartości `dictionaryName` dla słowników (Kraje, Województwo, …; w standardach pojawia się `TERYT_4G_DOWODOWY`).
4. Szerokość siatki (liczba kolumn) — jakie `x`/`cols` stosować przy układzie jednokolumnowym?
5. Jak zapisać limity FILE_UPLOADER (formaty, rozmiar, liczba plików), placeholder i walidator „Pattern” — w schemacie brak kluczy.
6. Czy przykładowe pliki JSON dla dwóch wizualizowanych wniosków są dostępne (zbiór testowy)?
7. Czy przepływ stron może być warunkowy („strony alternatywne”) — `flow.links` nie ma warunku.
