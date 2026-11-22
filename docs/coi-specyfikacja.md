# Specyfikacja COI — opracowanie zespołu

Streszczenie materiałów organizatora („Materiały do zadania dla Uczestników”) i decyzje
zespołu tam, gdzie materiały są niespójne. Kod: `backend/shared/schemas/coi_form.py`
(modele), `backend/shared/validators/form_rules.py` (reguły), wzorcowy formularz:
`tests/fixtures/sample_form.json`, oficjalny schemat: `docs/coi/importowanyPlikJsonSchemat.json`.

**Najważniejsze źródło formatu:** konfiguracje wyeksportowane z systemu COI dla dwóch
wniosków referencyjnych (`tests/fixtures/coi_reference/`). Schemat importu nie ma kluczy
wymaganych ani zakazu kluczy dodatkowych — opisuje tylko typy. Tam, gdzie opisy różnią się
od plików z COI, obowiązuje plik z COI; testy pilnują, że oba wzorce przechodzą nasze modele
i reguły bez błędów, a wynik ma te same klucze w tej samej kolejności.

## Format pliku importu (`FormDefinition`)

```text
title                         ≤ 255 znaków; „Wniosek/Zgłoszenie/Deklaracja/Oświadczenie …”
tabs[]                        strony
  id (UUID), title, type      MAIN | FINISH
  questions[]                 wyłącznie sekcje
    x, y, rows, cols          grid-area: {y+1} / {x+1} / span {rows} / span {cols}
    minItemRows               opcjonalnie (występuje w eksporcie COI)
    field                     SECTION | REPEATING_SECTION (title, techName, states: SHOW)
      repeatingSectionGrid[]  pola sekcji — x, y, rows, cols, field
flow.links[]                  id (tekst: UUID albo np. "a16ec"), source, target (UUID stron)
```

Układ jak w COI: jedna kolumna — `x=0`, `cols=3`; pole zajmuje 1 wiersz, sekcja liczbę pól + 1;
`y` i `questionOrder` rosną od 0 (sekcje na stronie i pola w sekcji osobno).

**Każde pole, także sekcja, ma komplet kluczy wspólnych** (puste, gdy nie dotyczą typu):
`id` (UUID), `type`, `techName`, `title`, `description`, `options`, `validation`, `readOnly`,
`repeatingSectionGrid` (u pól pusta lista), `states`, `questionOrder`, `tooltipMessage`.
Opisy sekcji, bloków tekstu, pól wielowierszowych i zgód są dokumentem HTML
(`<html><head></head><body>…</body></html>`; pusty opis sekcji to sam szkielet).
Klucze dodatkowe zależą od typu:

| Typ | Klucze dodatkowe | Walidatory | Opcje |
| --- | --- | --- | --- |
| `TEXT_FIELD` | defaultValue, defaultValueType | dowolne | — |
| `CHECKBOX` | dictionaryName | REQUIRED | tak |
| `NUMBER` | — | REQUIRED | — |
| `SELECT` | dictionaryName, multiLevelTree, searchEnabled, defaultValue, defaultValueType | REQUIRED | tak |
| `TEXTAREA` | defaultValue, defaultValueType | REQUIRED | — |
| `FILE_UPLOADER` | defaultValue | REQUIRED | — |
| `RADIO` | dictionaryName | REQUIRED | tak |
| `DATEPICKER` | defaultValue*, defaultValueType | REQUIRED | — |
| `TEXT_BLOCK` | — (stany tylko SHOW) | — | — |
| `CONSENT_SECTION` | selectAllEnabled, consentSectionConfig {consents[{description, required}], required, files} | — | — |
| `INFO_SECTION` | — (description wymagany) | — | — |
| `SECTION` | — (stany tylko SHOW) | — | — |

\* dla danych inicjalnych (`PESEL.birthDate`). `defaultValue` to tekst albo lista
(wybór ze słownika: `["Polska"]`). Identyfikator opcji to jej nazwa
(`{"id": "Swoim", "name": "Swoim", "techName": "swoim"}`).

Walidatory: `REQUIRED, EMAIL, PESEL, NIP, REGON, ZIP_CODE, EPUAP, ADE, SERIAL_NUMBER_ID_CARD`.
Pola do wypełnienia **muszą** mieć klucz `validation` (pusta tablica = pole opcjonalne).
Pole ze słownikiem ma `dictionaryName` i **pustą** tablicę `options`.

## Reguły formularza (sprawdzane przez `validate_form_rules`)

| Kod | Poziom | Reguła |
| --- | --- | --- |
| `TABS_MIN_TWO` | ERROR | co najmniej dwie strony |
| `FINISH_TAB_LAST` / `FINISH_TAB_SINGLE` | ERROR | ostatnia strona FINISH, jedyna; pozostałe MAIN |
| `FINISH_ONLY_INFORMATIONAL` | ERROR | na FINISH wyłącznie pola informacyjne (TEXT_BLOCK, INFO_SECTION) |
| `TAB_EMPTY` / `SECTION_EMPTY` | ERROR / WARNING | strona ma ≥ 1 pole; sekcja bez pól |
| `TAB_TITLE_DUPLICATE`, `TECHNAME_DUPLICATE_IN_TAB`, `TECHNAME_DUPLICATE_IN_SECTION` | ERROR | unikalność wymagana przez adresowanie w JSONacie |
| `DUPLICATE_ID`, `OPTION_*_DUPLICATE` | ERROR | unikalne identyfikatory i opcje |
| `FLOW_*` | ERROR | przejścia między istniejącymi stronami, każda strona osiągalna z pierwszej i prowadząca do FINISH, FINISH bez wyjść |
| `STATE_UNKNOWN_TAB/SECTION/FIELD/OPTION` | ERROR | warunek JSONata wskazuje istniejące pole i istniejącą opcję |
| `STATE_SELF_REFERENCE`, `STATE_CYCLE` | ERROR | brak zależności od siebie i cykli |
| `STATE_FORWARD_REFERENCE`, `STATE_QUERY_UNPARSED` | WARNING | zależność od późniejszej strony; warunek do ręcznej weryfikacji |
| `INITIAL_DATA_UNKNOWN`, `INITIAL_DATA_NOT_READONLY` | ERROR | dane inicjalne: znane `ŹRÓDŁO.atrybut`; tożsamość i rejestry `readOnly` (kontakt i adres z MLPZ mogą być edytowalne) |
| `INITIAL_DATA_TYPE_MISMATCH`, `INITIAL_DATA_MISSING_TYPE` | WARNING | typ pola zgodny z katalogiem; brak `EXTERNAL_SYSTEM` |
| `INPUT_WITHOUT_TITLE`, `GRID_OVERLAP`, `QUESTION_ORDER_DUPLICATE` | WARNING | etykieta pola; układ siatki |
| `OPTION_TECHNAME_MISSING` | WARNING | opcja bez `techName` (zdarza się w eksporcie COI) |

Warunek JSONata (z materiału): `"Opcja" in tabs['Strona' in title].questions['sekcja' in
field.techName].field.repeatingSectionGrid['pole' in field.techName].field.__userAnswer`
(sekcja powtarzająca: `…field.__userAnswer[$repeatingSectionIndex]['pole' in fieldTechName].answers`).

## Słowniki i dane inicjalne

- Słowniki tylko w SELECT / RADIO / CHECKBOX; Województwo, Powiat, Gmina, Miejscowość, Kraje
  tylko w SELECT; Numery kierunkowe przy telefonie. Propozycje nowych słowników — rozszerzenie.
- Nazwy słowników z plików COI: `Kraje` (SELECT kraju, `defaultValue: ["Polska"]`),
  `TERYT_4G_DOWODOWY` i `TERYT_DOWODOWY` (SELECT adresu, `multiLevelTree: true`,
  `searchEnabled: true`, widoczne tylko dla kraju „Polska”).
- Dane inicjalne: `readOnly: true`, `defaultValue: "ŹRÓDŁO.atrybut"`,
  `defaultValueType: "EXTERNAL_SYSTEM"`; źródła `PESEL`, `PESEL_ADDRESS`, `MLPZ` — pełny
  katalog atrybutów w `backend/shared/schemas/initial_data.py`. W plikach COI dane kontaktowe
  i adresowe z MLPZ (adres, telefon, e-mail) są podpowiedzią edytowalną (`readOnly: false`).

## Standardowe sekcje (dokument „Standardy i dobre praktyki”)

Kolejność stron w plikach COI: „Dane” → strona tematyczna (np. „Informacje o budynku”) →
„Kontakt z urzędem” / „Kontakt i zgody” → „Strona końcowa” (FINISH: sekcja
`podglad_uzupelnionego_wniosku` „Podgląd uzupełnionego wniosku” z TEXT_BLOCK `podglad_1`). Strona 1: **Urząd** (TEXT_FIELD readOnly z nazwą urzędu) → **Podmiot
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
| `repetingSectionGrid` (schemat) vs `repeatingSectionGrid` (pliki COI) | zapis jak w plikach COI; literówkę ze schematu przyjmujemy na wejściu |
| FINISH: tylko INFO_SECTION (reguły) vs TEXT_BLOCK (standardy i pliki COI) | jak w plikach COI: sekcja podglądu z TEXT_BLOCK; INFO_SECTION nadal dopuszczalny |
| `defaultValue: []` (reguły) vs `string` (schemat) | tekst albo lista — oba warianty są w plikach COI |
| `readOnly: true` dla danych inicjalnych (materiał) vs `readOnly: false` dla adresu i kontaktu z MLPZ (pliki COI) | jak w plikach COI — błąd tylko dla tożsamości i rejestrów |
| klucze spoza schematu | te z plików COI (`multiLevelTree`, `minItemRows`) i reguł (`searchEnabled`, `selectAllEnabled`, `consents[].required`); limity plików, placeholder i regex „Pattern” ze standardów trafiają do raportu jakości |
| niespójności w eksporcie COI (`validation: {}`, opcja bez `techName`) | przyjmujemy przy wczytaniu; zapisujemy `[]`, brak `techName` → ostrzeżenie |
| `CONSENT_SECTIOM`, `descripton` (literówki w opisie) | `CONSENT_SECTION`, `description` (jak w schemacie i regułach) |
| tagi stron `main`/`finish` (reguły) vs `MAIN`/`FINISH` (opis JSON) | `MAIN` / `FINISH` |
| pewność i źródła pól — brak w schemacie | osobny `QualityReport`; API zwraca `{form, report}`, `form` to czysty plik importu |

## Pytania do mentorów

Pisownię `repeatingSectionGrid`, TEXT_BLOCK na FINISH, nazwy słowników i szerokość siatki
rozstrzygnęły pliki referencyjne COI (patrz wyżej). Otwarte:

1. Nazwy słowników spoza wzorców (np. numery kierunkowe, powiaty) — czy jest pełna lista `dictionaryName`?
2. Jak zapisać limity FILE_UPLOADER (formaty, rozmiar, liczba plików), placeholder i walidator „Pattern” — w schemacie brak kluczy.
3. Czy przepływ stron może być warunkowy („strony alternatywne”) — `flow.links` nie ma warunku.
