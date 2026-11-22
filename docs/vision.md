# Kanał wizualny: renderowanie stron, współrzędne i detektory OpenCV (#10)

Moduł `backend/worker/vision/render` zamienia dokument wejściowy (PDF, JPEG, PNG) na obrazy stron
z metadanymi. Dokumenty biurowe (DOC, DOCX, ODS, XLSX) są rozpoznawane, ale nie są renderowane
([Formaty biurowe poza kanałem wizualnym](#formaty-biurowe-poza-kanałem-wizualnym)). Wszystkie dalsze elementy kanału wizualnego (detektory OpenCV,
VLM, oznaczenia, fuzja z tekstem) opisują położenie w jednym, wspólnym układzie współrzędnych.

Detektory OpenCV (`backend/worker/vision/detect`, Etap C) wykrywają na obrazach stron elementy
formularza (kratki, checkboxy i ich grupy, linie, kropki, komórki, ramki sekcji); opis w
[Detektory OpenCV (Etap C)](#detektory-opencv-etap-c).

Dokument nie zawiera treści formularzy COI. Liczby dotyczące materiałów są wyłącznie zbiorcze.

## Spis treści

1. [Punkt wejścia](#punkt-wejścia)
2. [Układ współrzędnych](#układ-współrzędnych)
3. [Metadane stron i dokumentu](#metadane-stron-i-dokumentu)
4. [Ścieżki renderowania](#ścieżki-renderowania)
5. [Cięcie zszytych obrazów A4](#cięcie-zszytych-obrazów-a4)
6. [Kody błędów i ostrzeżeń](#kody-błędów-i-ostrzeżeń)
7. [Wpływ na pewność w Etapie E](#wpływ-na-pewność-w-etapie-e)
8. [Formaty biurowe poza kanałem wizualnym](#formaty-biurowe-poza-kanałem-wizualnym)
9. [Analiza stron przez Eden AI (Etap D)](#analiza-stron-przez-eden-ai-etap-d)
10. [Konfiguracja i limity](#konfiguracja-i-limity)
11. [Bezpieczeństwo](#bezpieczeństwo)
12. [Testy](#testy)
13. [Zbiór oznaczeń i ewaluacja (#12)](#zbiór-oznaczeń-i-ewaluacja-12)
14. [Detektory OpenCV (Etap C)](#detektory-opencv-etap-c)
15. [Znane ograniczenia](#znane-ograniczenia)

## Punkt wejścia

```python
from backend.worker.vision.config import load_render_config
from backend.worker.vision.render import render_document

doc = render_document(data, declared_kind, load_render_config())
for page in doc.pages:
    page.meta  # PageMeta: serializowalne metadane strony
    page.image  # numpy uint8, RGB, kształt (wysokość, szerokość, 3)
```

- `declared_kind` to rodzaj z kroku 1 (np. `detect.kind_from_extension(nazwa)`) albo `None`.
- Ścieżka jest wybierana według rodzaju **wykrytego z zawartości** (`render/detect.py`), a nie według
  rozszerzenia. Niezgodność to ostrzeżenie `DECLARED_KIND_MISMATCH` w `doc.meta.issues`, a politykę
  odrzucania plików ustala krok 1 (API).
- Każdy błąd dokumentu to `RenderError` ze stałym kodem (`errors.IssueCode`). Wyników częściowych nie ma.
  Błąd zawsze niesie w `detail` rodzaj deklarowany i wykryty (`declared=… detected=…`), a w `issues`
  problemy zebrane przed błędem (np. `DECLARED_KIND_MISMATCH`), żeby trafiły do raportu przeglądu.
- Renderowanie PDF i dekodowanie obrazów odbywa się domyślnie w procesie potomnym (`isolated=True`).

Moduł `render` nie zależy od detektorów ani VLM, więc można go przenieść do #7 lub podmienić.

## Układ współrzędnych

**Wyjście kanału wizualnego nigdy nie zawiera pikseli.** Prostokąty to `BoundingBox` z
`backend.shared.schemas.quality` (#17):

| Właściwość | Wartość |
|---|---|
| Pola | `x0, y0, x1, y1` (postać listy: `coords.bbox_as_list` → `[x0, y0, x1, y1]`) |
| Jednostki | ułamki szerokości (x) i wysokości (y) strony, 0..1 |
| Początek | lewy górny róg, oś y w dół |
| Strona | tak, jak jest **wyświetlana**: po `/Rotate` (PDF) i orientacji EXIF (obrazy) |
| Kolejność | `x0 ≤ x1`, `y0 ≤ y1` (walidowane w #17) |

### Konwencja pikseli

Prostokąt w pikselach opisuje **krawędzie** pikseli: piksel w kolumnie `j` zajmuje przedział
`[j, j + 1)`. Prostokąt obejmujący kolumny 10..19 to `x0 = 10, x1 = 20`, czyli w ułamkach
`10 / szerokość` i `20 / szerokość`. Dzięki temu ułamki nie zależą od rozdzielczości renderowania.

### Przeliczenia (`backend/worker/vision/coords.py`)

| Funkcja | Z | Do |
|---|---|---|
| `px_to_norm(box, w, h)` | krawędzie pikseli | ułamki (surowa krotka) |
| `norm_to_px(bbox, w, h)`, `norm_to_px_int(...)` | ułamki | piksele (zmiennoprzecinkowe / obejmujące całe piksele) |
| `pdf_rect_to_norm(rect_pt, geometry)` | punkty PDF (lewy dolny róg) | ułamki strony wyświetlanej |
| `rotate_norm(raw, rotation)` | ułamki strony nieobróconej | ułamki po obrocie zgodnie z ruchem wskazówek |
| `part_to_original(raw, origin)` | ułamki strony wyciętej | ułamki oryginalnego zszytego obrazu |

Funkcje `*_to_norm` zwracają surową krotkę, która może wyjść poza stronę. `BoundingBox` powstaje
dopiero przez jawną decyzję:

- `to_bbox(raw)`: tryb ścisły, współrzędna poza `[-1e-9, 1 + 1e-9]` to `CoordinateError`,
- `clip_to_page(raw)`: przycięcie z flagą `przycięto`, którą wywołujący musi zapisać jako problem.

**Przycięcie nigdy nie odbywa się po cichu.**

### PDF: CropBox, MediaBox i `/Rotate`

Sprawdzone empirycznie na PDFium (build 8146, potwierdzone na 8149) i pokryte testem spójności:

- renderowany jest widoczny obszar **CropBox ∩ MediaBox** (`PageGeometry.visible_box`),
- `/Rotate` działa **zgodnie z ruchem wskazówek zegara**; bitmapa ma rozmiar strony po obrocie
  (`PageGeometry.displayed_size_pt`),
- punkt `(u, v)` strony nieobróconej (ułamki, lewy górny róg) przechodzi w:
  90°: `(1 − v, u)`, 180°: `(1 − u, 1 − v)`, 270°: `(v, 1 − u)`.

`pdf_rect_to_norm` zakłada brak dodatkowej macierzy przekształcenia, tak jak dla bbox z PDFium,
Doclinga i pól AcroForm. #7 może tej funkcji używać do sprowadzenia swoich współrzędnych do wspólnego układu.

### Numeracja stron

Strony są numerowane **od 1** (`PageMeta.page`), zgodnie z `FieldSource.page` w #17.
Strony wycięte ze zszytego obrazu są kolejnymi stronami dokumentu.

## Metadane stron i dokumentu

Modele tymczasowe w `backend/worker/vision/models.py` (`TODO(#17)`):

**`PageMeta`** (strona):

| Pole | Opis |
|---|---|
| `page` | numer strony od 1 |
| `width_px`, `height_px` | rozmiar obrazu strony |
| `dpi`, `dpi_source` | `render` (PDF), `metadata` (obraz z wiarygodnym DPI) albo `unknown` (`dpi = None`) |
| `dpi_estimate`, `dpi_estimate_source` | estymata z szerokości A4 (`a4_width`), tylko gdy `dpi = None` |
| `rotation` | obrót wyświetlania: `/Rotate` albo kąt zawarty w orientacji EXIF |
| `exif_orientation` | orientacja EXIF 1–8 (tylko obrazy); 2, 4, 5, 7 zawierają też odbicie lustrzane |
| `source` | `native_pdf` (strona PDF), `image` (strona z pliku graficznego) |
| `geometry` | `PageGeometry` (MediaBox, CropBox, `/Rotate` w punktach); tylko PDF |
| `origin` | `PageOrigin`; tylko strony wycięte ze zszytego obrazu |
| `issues` | problemy strony (`RenderIssue`: kod, poziom, szczegóły bez treści dokumentu) |

**`PageOrigin`**: położenie strony w oryginalnym obrazie (`image_width_px`, `image_height_px`,
`offset_y_px`, `part_height_px`, `part`, `parts`), w pikselach oryginału po orientacji EXIF.
Pozostaje poprawne także po zmniejszeniu strony (`DPI_REDUCED`).

**`DocumentMeta`**: `sha256` pliku, `declared_kind`, `detected_kind`, `source_page_count` (strony
PDF albo `1` dla obrazu, niezależnie od cięcia), `renderer_versions` (pypdfium2, PDFium, Pillow,
libjpeg-turbo, zlib; potrzebne do manifestu zbioru oznaczeń #12) oraz `issues` dokumentu.

## Ścieżki renderowania

| Rodzaj wykryty | Ścieżka | `source` |
|---|---|---|
| PDF | `render/pdf.py`: pypdfium2, `scale = dpi / 72`, RGB, białe tło, wygląd pól AcroForm (`render_forms`) | `native_pdf` |
| JPEG, PNG | `render/image.py`: Pillow, orientacja EXIF, normalizacja trybów, ewentualne cięcie | `image` |
| DOC, DOCX, ODS, XLSX | nie są renderowane: `RenderError` z kodem `OFFICE_FORMAT_NOT_RENDERED` | – |

Normalizacja obrazów do RGB uint8: przezroczystość (RGBA, LA, paleta z przezroczystością) nakładana
na białe tło; 16 bit (`I;16`) skalowane do 8 bit; tryby `1`, `L`, `P`, `CMYK` konwertowane. Z obrazów
wieloklatkowych używana jest pierwsza klatka (`MULTI_FRAME_IMAGE`).

DPI obrazu z metadanych jest przyjmowane, gdy obie wartości mieszczą się w 1..2400 i różnią o mniej
niż 1%. Zaokrąglamy je do 0,01, bo PNG zapisuje gęstość w całych pikselach na metr
(200 DPI → 199,9996).

## Cięcie zszytych obrazów A4

W materiałach COI większość plików graficznych to strony A4 wyrenderowane w 200 DPI i **zszyte w pionie**
w jeden wysoki obraz. Bez cięcia taki obraz byłby jedną stroną o proporcjach np. 1:22, co uniemożliwia
detekcję i odczyt przez VLM.

Obraz jest cięty na `k` stron, gdy jednocześnie:

1. jest pionowy (`wysokość > szerokość`),
2. `wysokość = k · round(szerokość · √2) ± split_height_tolerance_px` dla `k ≥ 2`,
3. pas `± split_seam_margin_px` wokół każdej granicy stron **nie zawiera tuszu**, czyli żadnego piksela
   ciemniejszego niż `split_ink_threshold` w dowolnym kanale.

Granice stron to `round(i · wysokość / k)`, więc nadwyżka z tolerancji rozkłada się równo.
Każda strona dostaje `PageOrigin` i informacyjny kod `IMAGE_SPLIT_A4` (`part=i/k`).

Wysoki obraz (`wysokość / szerokość > tall_image_ratio`), który nie spełnia warunków, **nie jest cięty**.
Zostaje jedną stroną z ostrzeżeniem `TALL_IMAGE_UNSPLIT`, a szczegóły podają powód (np. `ink at seam y=…`).

Ograniczenia reguły: tylko pionowe strony A4 zszyte w pionie. Inne formaty papieru, strony poziome
i obrazy zszyte w poziomie nie są cięte (zawsze z jawnym ostrzeżeniem, gdy obraz jest wysoki).

## Kody błędów i ostrzeżeń

Kody są częścią kontraktu i się nie zmieniają. Szczegóły (`detail`) zawierają wyłącznie dane
techniczne: liczby, rodzaje, nazwy typów wyjątków, nigdy treść dokumentu.

**Błędy dokumentu** (`RenderError`, renderowanie przerwane):

| Kod | Kiedy |
|---|---|
| `UNSUPPORTED_FORMAT` | zawartość nie jest obsługiwanym formatem; m.in. DOCM/XLSM (makra), XLS, ODT, zaszyfrowany OOXML |
| `PDF_UNREADABLE` | PDF uszkodzony, bez stron albo z niepoprawną geometrią strony |
| `PDF_ENCRYPTED` | PDF chroniony hasłem |
| `IMAGE_UNREADABLE` | obraz uszkodzony, obcięty albo niezgodny z formatem wykrytym |
| `TOO_MANY_PAGES` | więcej stron niż `max_pages` (także po cięciu obrazu) |
| `IMAGE_TOO_LARGE` | obraz ponad `max_image_pixels` (sprawdzane przed dekodowaniem) albo strona PDF wymagałaby DPI < 36 |
| `PAGE_RENDER_TIMEOUT` | przekroczony `page_timeout_s` (proces potomny zabity) |
| `PAGE_RENDER_CRASHED` | proces renderowania zakończył się nagle (np. awaria biblioteki C) lub zgłosił nieoczekiwany wyjątek |
| `OFFICE_FORMAT_NOT_RENDERED` | dokument biurowy (DOC, DOCX, ODS, XLSX): brak kanału wizualnego |

**Issues strony lub dokumentu** (renderowanie kontynuowane):

| Kod | Poziom | Gdzie | Znaczenie |
|---|---|---|---|
| `DPI_REDUCED` | WARNING | strona | strona przekraczała `max_page_pixels` i została wyrenderowana lub zmniejszona w niższej rozdzielczości |
| `DPI_UNKNOWN` | INFO (z estymatą) / WARNING (bez) | strona | obraz bez wiarygodnego DPI |
| `TALL_IMAGE_UNSPLIT` | WARNING | strona | wysoki obraz, którego nie udało się bezpiecznie pociąć |
| `IMAGE_SPLIT_A4` | INFO | strona | strona wycięta ze zszytego obrazu |
| `MULTI_FRAME_IMAGE` | WARNING | dokument | obraz ma wiele klatek; użyto pierwszej |
| `DECLARED_KIND_MISMATCH` | WARNING | dokument | rodzaj deklarowany różni się od wykrytego z zawartości |

## Wpływ na pewność w Etapie E

Etap A tylko zapisuje problemy, a ich wpływ na pewność realizuje Etap E (fuzja, kontrola, pewność).
Ustalenia:

- **`DPI_REDUCED`** trafia do issues strony i **w Etapie E obniża pewność wszystkich elementów tej
  strony**: mniejsza rozdzielczość pogarsza wykrywanie kratek, checkboxów i drobnego druku.
- **`TALL_IMAGE_UNSPLIT`** działa tak samo: obniża pewność elementów tej strony, bo nienaturalne
  proporcje strony utrudniają detekcję i odczyt.
- `DPI_UNKNOWN` bez estymaty (WARNING) oznacza, że progi detektorów zależne od DPI są niepewne
  (sposób uwzględnienia ustalimy w Etapie C).
- `DECLARED_KIND_MISMATCH` i `MULTI_FRAME_IMAGE` trafiają do raportu przeglądu na poziomie dokumentu.

## Formaty biurowe poza kanałem wizualnym

Renderowanie DOC, DOCX, ODS i XLSX wymagałoby konwersji do PDF przez LibreOffice (MPL 2.0), który
**zespół odrzucił (2026-11-21)**. Moduł konwersji został usunięty, a `render_document` zgłasza dla tych
formatów `OFFICE_FORMAT_NOT_RENDERED` (czytelny błąd, nie awaria). Rozpoznawanie formatów z zawartości
(`render/detect.py`) zostaje: dzięki niemu dokument biurowy dostaje ten kod, a warianty z makrami
(DOCM, XLSM) są odrzucane jako `UNSUPPORTED_FORMAT`.

Skutki:

- **pliki DOC, DOCX, ODS i XLSX nie przechodzą przez kanał wizualny.** W materiałach COI to
  **40 z 81 plików** (w folderach formularzy 33 z 69 plików formularzy; pozostałe to wizualizacje
  formularza po cyfryzacji i materiały do zadania),
- pola takich dokumentów pochodzą wyłącznie z kanału tekstowego (#7, #8): **nie mają elementów
  OpenCV, bbox, potwierdzenia wizualnego ani podwójnego odczytu VLM**,
- zbiór oznaczeń #12 nie obejmuje tych formatów (`eval/visual/README.md`).

**Kod powodu w Etapie E: `NO_VISUAL_CHANNEL`** (zaakceptowany 2026-11-21).

| Kod | Gdzie | Kiedy | Skutek |
|---|---|---|---|
| `NO_VISUAL_CHANNEL` | `confidence_reasons` każdego pola | kanał wizualny nie przetworzył strony ani dokumentu, z którego pochodzi pole: `OFFICE_FORMAT_NOT_RENDERED` albo inny `RenderError` | obniżona pewność pola: **najwyżej poziom MEDIUM**, więc pole zawsze trafia do przejrzenia (wielkość obniżenia kalibrowana na `dev`) |

Kod jest jeden dla wszystkich przyczyn braku kanału wizualnego, a przyczyna jest zapisana osobno:
w raporcie przebiegu etap 3 ma `StageReport(status=SKIPPED, detail="OFFICE_FORMAT_NOT_RENDERED")`
(albo inny kod błędu renderera). Dzięki temu urzędnik widzi, dlaczego pole nie ma potwierdzenia wizualnego.

## Analiza stron przez Eden AI (Etap D)

Od 2026-11-21 VLM (#11) działa przez **Eden AI**: organizatorzy przekazali zespołowi token i dopuścili
jego użycie z modelami open-weight. Implementacja powstanie w Etapie D; poniżej zasady.

- **Obrazy stron formularzy, także materiałów COI, są wysyłane do Eden AI** i dalej do dostawcy
  wybranego modelu. W trybie przypisywania ról razem z obrazem wysyłana jest lista elementów
  wykrytych przez OpenCV i bloki tekstu strony.
- Dozwolone są **tylko modele open-weight z licencją permisywną**; model, dostawca w Eden AI i licencja
  wag trafiają do `docs/licenses.md`.
- Token tylko w zmiennej środowiskowej `EDENAI_API_KEY` (konfiguracja wspólna z LLM tekstowym,
  `backend/shared/config.py`). Nie trafia do repozytorium, logów, raportów ani nagranych odpowiedzi.
- Odpowiedzi są cache'owane w `dane/robocze/` po hashu (obraz strony, model, wersja promptu, schemat,
  parametry), więc ta sama strona z tym samym promptem nie jest wysyłana ponownie.
- Implementacja lokalna (Qwen3-VL na vLLM lub llama.cpp) zostaje jako awaryjna za tym samym interfejsem.
- Testy w CI nie łączą się z siecią: używają fake'a z nagranymi odpowiedziami bez treści COI.

## Konfiguracja i limity

DPI pochodzi z `RENDER_DPI` (`backend/shared/config.py`), pozostałe parametry z `VISION_*`
(`backend/worker/vision/config.py`):

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `RENDER_DPI` | 200 | DPI renderowania PDF (72–600) |
| `VISION_MAX_PAGES` | 50 | maks. liczba stron dokumentu (także po cięciu obrazu) |
| `VISION_MAX_PAGE_PIXELS` | 40 000 000 | maks. pikseli strony; powyżej `DPI_REDUCED` |
| `VISION_MAX_IMAGE_PIXELS` | 100 000 000 | maks. pikseli obrazu wejściowego (ochrona przed bombami dekompresyjnymi) |
| `VISION_PAGE_TIMEOUT_S` | 30 | limit czasu na stronę (proces potomny) |
| `VISION_WORKER_STARTUP_TIMEOUT_S` | 60 | limit czasu startu procesu potomnego |
| `VISION_RENDER_FORMS` | true | rysowanie wyglądu pól AcroForm |
| `VISION_SPLIT_SEAM_MARGIN_PX` | 20 | pas wokół granicy stron, który musi być pusty |
| `VISION_SPLIT_HEIGHT_TOLERANCE_PX` | 2 | tolerancja wysokości zszytego obrazu |
| `VISION_SPLIT_INK_THRESHOLD` | 200 | piksel ciemniejszy (w dowolnym kanale) to tusz |
| `VISION_TALL_IMAGE_RATIO` | 1.6 | powyżej tej proporcji obraz jest „wysoki” |
| `VISION_A4_RATIO_TOLERANCE` | 0.01 | tolerancja proporcji A4 dla `dpi_estimate` |

Niepoprawna wartość zmiennej kończy start procesu błędem walidacji (fail fast).

## Bezpieczeństwo

- **Izolacja procesu:** PDFium i dekodery obrazów działają w procesie potomnym (`spawn`). Proces
  nadrzędny czeka jednocześnie na wynik i na zakończenie procesu, więc zawieszenie daje
  `PAGE_RENDER_TIMEOUT`, a nagła śmierć procesu od razu `PAGE_RENDER_CRASHED`. Proces jest
  zawsze sprzątany.
- **Rozpoznanie formatu z zawartości** z limitami odczytu: kontener OLE2 rozpoznawany po katalogu
  strumieni (z wykrywaniem pętli), OOXML po `[Content_Types].xml`, ODF po `mimetype`. Warianty z makrami
  są odrzucane.
- **Dekoder Pillow ograniczony do wykrytego formatu**; limit pikseli sprawdzany przed dekodowaniem,
  a ostrzeżenie o bombie dekompresyjnej traktowane jako błąd. Globalny limit Pillow jest zmieniany
  pod blokadą i zawsze przywracany.
- **Pliki tylko w pamięci**: PDF i obrazy są przetwarzane z bajtów; renderer nic nie zapisuje na dysk.
- **Logi i szczegóły błędów bez treści dokumentów**: wyjątki z procesu potomnego przekazują tylko nazwę typu.

## Testy

Wszystkie testy (`tests/vision/`) działają na **danych syntetycznych** generowanych w kodzie i
uruchamiają się w CI bez dostępu do `dane/`:

- `synth.py`: ręczny generator PDF (MediaBox z przesunięciem, CropBox, `/Rotate`, kolorowe
  prostokąty, szyfrowanie RC4), niezależny od PDFium; generatory OLE2, OOXML, ODF i obrazów,
- `test_render_consistency.py`: **spójność bbox z rendererem (wymaganie #12)**: 4 DPI (72, 150, 200,
  300) × 4 obroty × 4 układy MediaBox/CropBox. Bbox zmierzony z pikseli zgadza się z
  `pdf_rect_to_norm` w tolerancji 1,5 px (zmierzony maksymalny błąd krawędzi: 1,0 px na 192 prostokątach),
- `synth_forms.py` (#12, B.5): syntetyczne strony formularza A4 z elementami wszystkich 9 kategorii
  i etykietami w formacie `teamkapa.vision.labels/1` (bez czcionek; tekst to szare paski) oraz
  deterministyczne degradacje: obrót (z przeliczeniem etykiet), rozmycie, szum ze stałym ziarnem,
  JPEG, słabszy kontrast, niższe DPI. Testy (`test_synth_forms.py`) sprawdzają determinizm, ciasność
  ramek (krawędź na linii), to, że każdy piksel tuszu należy do elementu albo „tekstu”, oraz zgodność
  etykiet z tuszem po obrocie (±1,5 px). Detektory z Etapu C używają tego generatora,
- `test_detect_*.py` (Etap C): preprocessing (kąt prostowania ±0,05°, przeliczenie ramek), linie,
  prostokąty (m.in. checkbox-glif z cieniem, kratki z przerwą, linie 1 px z brakującym pikselem
  w narożniku), kropki (także wielokropki; szum i kropki w tekście odrzucane), grupy, ID i pewność,
  determinizm między procesami, wyłączanie IPP, wejście kroku 3 na PNG i PDF;
  `test_detect_quality.py`: jakość na stronach syntetycznych o różnej jakości (tabela wyżej),
- `test_coords.py`: przeliczenia z wartościami wyliczonymi ręcznie,
- `test_render_pdf.py`, `test_render_image.py`, `test_render_document.py`,
  `test_isolation.py`, `test_detect.py`: determinizm (identyczny hash pikseli), błędy, limity,
  EXIF 1–8, tryby kolorów, cięcie, izolacja i sprzątanie.

## Sprawdzenie na materiałach COI (A.8)

Jednorazowe sprawdzenie lokalne na materiałach COI (2026-11-21; renderer z commitu `7754cb4`,
200 DPI, tryb izolowany). Materiały i skrypty nie są częścią repozytorium; poniżej wyłącznie liczby zbiorcze.

**Pliki i strony**

| Wynik | Plików | Stron |
|---|---|---|
| PDF wyrenderowane | 11 | 65 |
| PNG wyrenderowane | 9 | 33 |
| JPEG wyrenderowane | 18 | 66 |
| DOC/DOCX/ODS/XLSX: `OFFICE_CONVERSION_DISABLED` (kod z A.8; obecnie `OFFICE_FORMAT_NOT_RENDERED`) | 40 | – |
| JSON (pominięte) | 3 | – |
| **Razem** | **81** | **164** |

Żadnego błędu renderowania poza zamierzonym odrzuceniem formatów biurowych.

**PDF**: 0 formularzy AcroForm i XFA, 0 widgetów; warstwa tekstu na każdej stronie każdego
formularza; `/Rotate` = 0 i CropBox = MediaBox na wszystkich stronach; każda strona w 200 DPI ma
1654 × 2339 px.

**Obrazy**: 24 z 24 obrazów zszytych pocięte na tyle stron, ile ma odpowiadający PDF (1, 2, 6 lub 16);
3 skany (130 DPI z metadanych) bez cięcia; 0 × `TALL_IMAGE_UNSPLIT`, `DPI_REDUCED`, `MULTI_FRAME_IMAGE`.
`dpi_estimate` dla obrazów bez DPI: 200,06.

**Zgodność geometrii: render PDF (200 DPI) vs obrazy COI po cięciu** (32 strony × 3 formaty = 96 porównań):

| Miara | Wynik |
|---|---|
| Rozmiar strony identyczny | 96 / 96 |
| Przesunięcie | 0 lub 1 px (≈ 0,0004 wysokości strony) |
| Pokrycie tuszu z tolerancją 1 px (PNG, 32 strony) | min. 97,9%, średnio 99,7–99,9% |
| Pokrycie tuszu dokładne (IoU) | 0,71–0,80 |
| Średnia różnica jasności | 1,6–6,6 (na 255) |
| Piksele różniące się o więcej niż 64 | ≤ 3,0% |

Niższe IoU dokładne wynika z innego wygładzania krawędzi (obrazy COI powstały w innym programie,
a kreski tekstu mają 1–3 px), nie z różnic geometrii.

**Czasy** (`render_document` w trybie izolowanym, ze startem procesu ok. 0,5 s):

| Rodzaj | Plików | Stron | Łącznie | Maks. na plik |
|---|---|---|---|---|
| PDF | 11 | 65 | 7,7 s | 1,1 s |
| PNG | 9 | 33 | 7,8 s | 2,1 s |
| JPEG | 18 | 66 | 15,8 s | 2,2 s |

## Zbiór oznaczeń i ewaluacja (#12)

Narzędzia i zasady: `eval/visual/README.md` (pochodzenie zbioru, podział, metryki, ochrona
`holdout`) i `eval/visual/LABELING.md` (konwencje oznaczania). Materiały COI, obrazy stron, etykiety
i pełne raporty są wyłącznie w `dane/`.

**Stan na 2026-11-22:**

- Podział po dokumentach zarejestrowany przed Etapem C: `dev` = f02, f05, f07, f09; `holdout` = f01,
  f03, f04, f06, f08. Nie jest zamrożony; manifest pozostaje roboczy.
- **Oznaczone są 2 strony `dev` z pilota** (`f02-p01`, `f07-p01`): 65 elementów, 8 stron oceny
  razem z wariantami PNG, JPG i JPEG. Pozostałe 34 zestawy etykiet są odłożone (prawdopodobny powrót
  z pre-etykietami z warstwy wektorowej PDF). `holdout` jest nietknięty.
- Bazy (`baseline.json`) nie ma, bo wymaga pełnego pokrycia części. **Wyniki na tym zbiorze są
  wyłącznie wstępnym pomiarem**, bez skanów i z dwóch rodzajów wniosku.
- Kontrola miernika na stronach pilota: etykiety podane jako predykcje dają P = R = 1, także po
  przesunięciu o 1–2 px; przesunięcie o pół szerokości elementu daje 0, a usunięcie co 10. elementu
  R = 0,92. Ciasność ramek pilota: 95% krawędzi ramek najwyżej 1 px od linii w obrazie.

## Detektory OpenCV (Etap C)

Pakiet `backend/worker/vision/detect`, wejście `detect_page(RenderedPage)` i `detect_document`,
cienkie wejście kroku 3: `backend/worker/pipeline/step03_visual_analyze.analyze_visual` (render +
detekcja, wynik w kontrakcie 7.5). Podpięcie w pipeline (`stages.default_stages`, odwzorowanie na
`contracts.VisualAnalysis`) należy do #16.

**Zasada: detektory pracują wyłącznie na obrazie strony.** Nigdy nie korzystają z warstwy
wektorowej ani tekstowej PDF. Ten sam kod działa więc dla PDF, obrazów cyfrowych i skanów, a ocena
na etykietach nie jest cykliczna także wtedy, gdy etykiety powstaną z pre-etykiet z warstwy
wektorowej (decyzja z 2026-11-22).

### Kroki

| Krok | Moduł | Opis |
|---|---|---|
| Preprocessing | `preprocess.py` | szarość jako minimum kanałów RGB (kolorowe linie są ciemne), rozmycie σ = 0,1 mm, tusz: ciemniejszy od progu Otsu (w granicach 120–245 i ≤ poziom papieru − 20) **i** od średniej lokalnej (okno 7 mm) − 10; czulsza maska do kropek: papier − max(30, 5 × szum tła); prostowanie z profilu rzutu wierszy (±5°, krok 0,25°, potem 0,02°; obrót od 0,1°) |
| Linie | `lines.py` | otwarcie morfologiczne jądrem 8 mm (poziomo i pionowo), domykanie przerw do 0,8 mm; `text_line`: odcinek poziomy ≥ 15 mm, ≤ 0,6 mm grubości, w ≥ 50% długości nieleżący na krawędziach prostokątów |
| Prostokąty | `rects.py` | wnętrza z zamkniętych obszarów tła (struktura pogrubiona o 0,15 mm, bo linie 1 px mają przerwy na złączach), rozszerzane do zewnętrznej krawędzi obramowania; checkbox 2–6,5 mm (proporcja ≤ 1,35); kratki: ≥ 2 sąsiadujące komórki ≤ 10 mm tej samej wysokości (`cells` = liczba komórek); komórka ≥ 5 × 3 mm; ramka sekcji: obrys składowej zawierający ≥ 2 elementy (bez ramek wewnętrznych o tych samych elementach) |
| Kropki | `dots.py` | łańcuchy składowych ≤ 0,8 mm w wierszu, przerwy ≤ 2,5 mm, ≥ 8 kropek, ≥ 10 mm, odstępy regularne (także okresowe co 3: wielokropki) |
| Grupy | `groups.py` | kandydaci: checkboxy w wierszu (przerwa ≤ 70 mm), potem w kolumnie (≤ 12 mm); ramka z tekstem opcji; pewność 0,3, `GROUP_CANDIDATE`, `multi` nieznane |
| ID i pewność | `page.py` | ramki przeliczane z obrazu wyprostowanego do układu strony; ID i kody poniżej |

Wszystkie progi są w `DetectConfig` (`backend/worker/vision/config.py`, zmienne `VISION_DETECT_*`)
w milimetrach strony albo jako proporcje i są przeliczane na piksele według efektywnego DPI strony
(DPI renderu lub metadanych, oszacowanie z Etapu A, szerokość strony A4, domyślnie 200).
Wartości wynikają z typowych wymiarów pól formularzy A4, a nie z dopasowania do zbioru #12.

### ID, rola i pewność

- **ID** `cv-p{strona}-{nnn}`: elementy sortowane po (wiersz, x0, y1, x1, kategoria), gdzie
  wiersz = `floor(y0 / 2 mm)`. Elementy jednego wiersza są numerowane od lewej mimo różnic
  1–2 px w y0.
- **Rola** jest zawsze `unknown`, a `signature` i `office_only` nie są nadawane: wymagają tekstu
  (rozstrzyga VLM i fuzja, Etapy D i E). Miejsce na podpis z kropek to dla OpenCV `dotted_line`,
  a ramka urzędu to `table_cell`.
- **Pewność:** komórka i kratki 0,9 (kratki o nieregularnej szerokości 0,6 z `CELLS_IRREGULAR`),
  checkbox 0,85, ramka sekcji i kropki 0,8, linia 0,7, kandydat na grupę 0,3 (`GROUP_CANDIDATE`).
  Kody strony mnożą pewność: `LOW_CONTRAST` (różnica papier–tusz < 110) × 0,8,
  `SKEW_CORRECTED` × 0,95.

### Determinizm

Ten sam obraz i ta sama konfiguracja dają te same elementy, kolejność i ID: detekcja nie używa
losowości, a test porównuje wynik w dwóch osobnych procesach. Intel IPP jest na czas detekcji
wyłączany (`cv2.ipp.setUseIPP(False)`, stan sprzed wywołania jest przywracany), bo IPP wybiera
ścieżkę obliczeń zależnie od procesora. Licencja IPP: wyjątek do potwierdzenia przez zespół
(`docs/licenses.md`).

### Wyniki na stronach syntetycznych

Generator `tests/vision/synth_forms.py` (A4, 200 DPI, wszystkie kategorie). Kategorie zależne od
tekstu porównywane z postacią wizualną (podpis jako kropki, pole urzędu jako komórka).

| Warunki | Mikro-F1 | Uwagi |
|---|---|---|
| czyste strony; obrót 1° i −2°; rozmycie 1 px; szum σ = 20; JPEG q = 30; kontrast 0,4; 100 DPI | 1,000 | także liczba kratek 100% |
| skan (obrót 1,2°, rozmycie, szum, JPEG); obrót 3° z szumem σ = 25 i JPEG q = 30; obrót −4,5°; 100 DPI z rozmyciem i JPEG; rozmycie 2 px | 1,000 | |
| wyblakły i rozmyty druk (kontrast 0,35, rozmycie 1,5 px) | 0,89 | kropki znikają (F1 kropek 0) |
| 75 DPI | 0,97 | ramki sekcji i część linii (linie 1 px łączą się z sąsiednim tuszem) |
| szum σ = 40 | 0,96 | szum przerywa łańcuchy kropek (F1 kropek 0,82) |

Czas detekcji: ok. 0,2 s na stronę A4 przy 200 DPI (z prostowaniem ok. 0,3 s).

### Wstępny pomiar na pilocie (`dev`)

**Tylko wstępny pomiar:** 2 oznaczone strony `dev` (z dwóch rodzajów wniosku), 8 stron oceny razem
z wariantami PNG, JPG i JPEG, bez skanów, 260 elementów etykiet; `holdout` nie był używany.

| Kategoria | P | R | F1 |
|---|---|---|---|
| char_boxes (liczba kratek zgodna w 20/20) | 1,000 | 1,000 | 1,000 |
| checkbox | 1,000 | 1,000 | 1,000 |
| checkbox_group (kandydaci) | 1,000 | 1,000 | 1,000 |
| dotted_line | 0,923 | 1,000 | 0,960 |
| table_cell | 0,974 | 1,000 | 0,987 |
| section_frame | 1,000 | 1,000 | 1,000 |
| text_line (brak w etykietach) | 0,000 | – | – |
| office_only (wymaga tekstu) | – | 0,000 | – |
| **mikro** | **0,940** | **0,969** | **0,955** |

- PDF i warianty obrazowe: mikro-F1 0,955 w obu przekrojach. Wynik jest taki sam przy IoU 0,3,
  0,5 i 0,7 (ramki są ciasne).
- Wszystkie pozostałe błędy wynikają z braku tekstu: linie nad przypisami jako `text_line`
  (8 fałszywych alarmów) oraz pola urzędu wykryte jako `dotted_line` albo `table_cell` (8).
- Pomiar wykrył dwa błędy, poprawione na podstawie zasad, a nie strojenia progów: zbyt duży
  minimalny bok wnętrza (checkbox-glif 2,5 mm ma wnętrze 1,7 mm) i przerwy w liniach 1 px
  w obrazach PNG.

## Znane ograniczenia

- **Skany obrócone bez `/Rotate` lub bez orientacji EXIF** (np. kartka zeskanowana bokiem) nie są
  prostowane w Etapie A. Wykrycie orientacji treści i prostowanie (deskew) to preprocessing w Etapie C.
  Wtedy bbox będzie przeliczany z powrotem do układu strony wyrenderowanej.
- **DOC, DOCX, ODS i XLSX nie mają kanału wizualnego** (`OFFICE_FORMAT_NOT_RENDERED`): pola tych dokumentów
  dostają w Etapie E kod `NO_VISUAL_CHANNEL` i obniżoną pewność.
- **Perspektywa i zniekształcenia zdjęć** nie są korygowane; w materiałach COI nie ma zdjęć z aparatu.
- **Profile kolorów ICC** nie są stosowane przy konwersji do RGB.
- **DPI z metadanych bywa fikcyjne** (np. domyślne 72 lub 96 w zrzutach ekranu); przyjmujemy je, gdy jest
  spójne, a jego wiarygodność oceniają dalsze kroki.
- **Detektory OpenCV nie rozróżniają `signature` ani `office_only` i nie nadają ról** (wymagają
  tekstu: VLM i fuzja). Separatory przypisów są wykrywane jako `text_line`.
- **Granice odporności detektorów** (strony syntetyczne): kropki znikają przy wyblakłym
  i jednocześnie rozmytym druku oraz przy szumie σ ≥ 40; przy 75 DPI giną ramki sekcji i część linii.
- **Pomiar detektorów na COI jest wstępny** (2 strony `dev`); progi nie były strojone na tym zbiorze.
- **`/UserUnit`** (rzadki parametr PDF zmieniający rozmiar jednostki) nie jest uwzględniany w DPI.
- **Narzut izolacji** to ok. 0,5 s na dokument (start procesu), a przy dużych obrazach dochodzi
  przesyłanie pikseli między procesami.
