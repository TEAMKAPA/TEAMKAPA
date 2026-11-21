# Konwencje oznaczania stron (#12)

Instrukcja dla osoby oznaczającej w Label Studio (konfiguracja: `labelstudio/config.xml`). Etykiety są
punktem odniesienia dla detektorów OpenCV, VLM i fuzji, więc liczy się **spójność**: ten sam
przypadek zawsze oznaczamy tak samo. Wątpliwy przypadek oznaczamy najlepiej, jak się da, i zaznaczamy
flagę `uncertain`.

Oznaczamy ręcznie. Nie korzystamy z wyników detektorów ani z żadnych wstępnych oznaczeń.

## Zasady ogólne

1. **Oznaczamy każdy element należący do jednej z 9 kategorii**, także zagnieżdżony (kropki w komórce
   tabeli, komórki w ramce sekcji, checkboxy w grupie). Jeden element na stronie to jedna ramka.
2. **Nie oznaczamy** tekstu, logo, ikon, pieczęci graficznych, separatorów sekcji i linii ozdobnych,
   linii nad przypisami, obramowania całej strony, a na skanach krawędzi kartki i cieni.
3. **Ramka obejmuje sam element wizualny**, bez etykiety, podpisu pod linią i tekstu opcji. Wyjątek:
   `checkbox_group` obejmuje checkboxy razem z tekstami ich opcji.
4. **Ramka jest ciasna**: od pierwszego do ostatniego piksela elementu, razem z liniami obramowania.
   Ramki przyciągają się do pikseli obrazu. Przybliżaj stronę (kółko myszy), żeby trafić w krawędzie.
5. **Bez obrotu ramek** (wyłączony w konfiguracji). Na krzywym skanie ramka obejmuje cały element.

## Kategorie

| Skrót | Kategoria | Co to jest | Ramka |
|---|---|---|---|
| 1 | `char_boxes` | ciąg kratek na pojedyncze znaki (PESEL, NIP, kod pocztowy, data, numer rachunku) | cała grupa kratek; **atrybut `cells`** = liczba kratek |
| 2 | `checkbox` | pojedynczy kwadrat do zaznaczenia, rysowany albo jako znak (np. ❑) | sam kwadrat |
| 3 | `checkbox_group` | co najmniej 2 checkboxy odpowiadające na jedno pytanie | checkboxy i teksty ich opcji; **atrybut `multi`** |
| 4 | `text_line` | linia ciągła do wpisywania, także z podkreślników | od początku do końca linii |
| 5 | `dotted_line` | ciąg kropek lub wielokropków do wpisywania | od pierwszej do ostatniej kropki |
| 6 | `table_cell` | zamknięty prostokąt na tekst: komórka tabeli albo samodzielna ramka pola | wzdłuż linii obramowania |
| 7 | `section_frame` | duża ramka obejmująca sekcję (zwykle z nagłówkiem) lub całą tabelę | zewnętrzne obramowanie |
| 8 | `signature` | miejsce na podpis: linia, kropki albo ramka | sam element, bez napisu „podpis…” |
| 9 | `office_only` | miejsce wypełniane przez urząd: ramka na pieczęć, pole „wypełnia urząd/organ” | sam element (ramka, linia lub kropki) |

### Przypadki graniczne

- **Grupa kratek:** kratki stykające się bokami. Przerwa albo wydrukowany myślnik rozdziela grupy, więc
  data `DD-MM-RRRR` to trzy grupy (2, 2, 4), a kod pocztowy `__-___` to dwie (2, 3).
- **Pojedyncza kratka na znak** (bez sąsiednich) to też `char_boxes` z `cells = 1`, jeśli wyraźnie
  służy do wpisania znaku, a nie do zaznaczenia. Gdy nie wiadomo: `checkbox` i `uncertain`.
- **Pierwszeństwo kategorii:** `signature` i `office_only` wygrywają z `dotted_line`, `text_line`
  i `table_cell`. Kropki pod napisem „podpis” to jeden element `signature`, a nie `dotted_line`.
- **Komórka scalona** to jedna komórka. Komórka nagłówka albo komórka z samą etykietą (np. lewa
  kolumna z nazwami pól) to też `table_cell`, ale z rolą `skip`.
- **Kropki lub linia w komórce:** oznaczamy oba elementy, komórkę i kropki. Komórce nie trzeba zmieniać
  roli: konwerter sam ustawi `skip` dla komórki, w której jest inny element do wpisywania.
- **Ramka z instrukcją albo adresem urzędu** (tekst w ramce, nic do wpisania): `table_cell` z rolą `skip`.
- **Tabela:** każda komórka osobno (`table_cell`) i obramowanie całej tabeli (`section_frame`).
- **Pojedynczy checkbox** bez grupy (np. oświadczenie, zgoda) to tylko `checkbox`, bez `checkbox_group`.
- **`multi`** w grupie: `single`, gdy opcje się wykluczają albo instrukcja każe wybrać jedną;
  `multi`, gdy można wybrać kilka; `unknown`, gdy z formularza nie da się tego ustalić.
- **Kilka linii kropek pod sobą** (np. trzy wiersze uzasadnienia) to osobne elementy, po jednym na wiersz.
- **Linia kropek przechodząca w kolejny wiersz tekstu** to osobne elementy w każdym wierszu.

## Role

Rolę ustawiasz **tylko wtedy, gdy jest inna niż domyślna**. Domyślne uzupełnia konwerter:

| Kategoria | Rola domyślna |
|---|---|
| `char_boxes`, `text_line`, `dotted_line`, `table_cell` | `input` |
| `checkbox`, `checkbox_group` | `choice` |
| `signature` | `e_signature` |
| `section_frame` | `section` |
| `office_only` | `skip` |

Typowe wyjątki:

- `skip` dla komórki z samą etykietą lub nagłówkiem, ramki z instrukcją lub adresem oraz checkboxa
  z legendy albo przykładu wypełniania (nie do zaznaczenia),
- `skip` dla elementów przykładu w instrukcji wypełniania (np. wzór wpisywania w kratki).

Pole wypełniane przez urząd oznaczamy kategorią `office_only` (rola `skip` jest wtedy domyślna).

## Flaga `uncertain`

Zaznacz, gdy nie masz pewności co do kategorii, roli albo granic elementu. Element z flagą jest w metrykach
traktowany jak „trudny” (jak *difficult* w PASCAL VOC): wykrycie go nie jest błędem, a pominięcie nie
obniża czułości. Liczbę takich elementów raportujemy osobno.

## Status strony (obowiązkowy)

Przed zapisaniem (**Submit**) wybierz:

- `complete`: strona oznaczona w całości,
- `empty`: strona sprawdzona, nie ma na niej żadnego elementu (np. strona z samym tekstem informacyjnym).

Strony oznaczonej częściowo nie zapisuj przez Submit. Label Studio przechowuje szkic, a pracę można
dokończyć później.

## Wskazówki

- Skróty: 1–9 wybierają kategorię przed narysowaniem ramki, Ctrl+Enter zapisuje stronę.
- Najpierw duże elementy (`section_frame`, tabele), potem drobne (kratki, checkboxy, kropki).
- Po każdej stronie przejrzyj listę regionów: czy każda grupa kratek ma `cells`, a każda grupa
  checkboxów `multi`.
- Sesje po ≤ 90 min. Czas pracy na stronie zapisuje Label Studio.
