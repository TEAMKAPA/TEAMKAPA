# Zbiór oznaczeń i ewaluacja kanału wizualnego (#12)

Ręcznie oznaczone strony formularzy służą do pomiaru detekcji elementów wizualnych (OpenCV, VLM,
fuzja). Ten katalog zawiera kod, konfigurację narzędzia, podział i instrukcje. **Materiały COI,
obrazy stron i etykiety leżą wyłącznie w `dane/`** (poza repozytorium). Repozytorium jest publiczne,
więc trafiają do niego tylko kod, manifest bez ścieżek i liczby zbiorcze.

| Plik | Zawartość |
|---|---|
| `split.json` | podział `dev`/`holdout` po dokumentach, zarejestrowany 2026-11-21 przed Etapem C |
| `models.py` | formaty: podział, etykiety strony (`teamkapa.vision.labels/1`), manifest (`teamkapa.vision.manifest/1`) |
| `prepare.py` | render stron do oznaczania, zadania Label Studio, manifest |
| `geometry.py` | hash pikseli strony, zgodność geometrii wariantów formatu |
| `ls.py`, `labelstudio/config.xml`, `labelstudio/ls.env` | przypięta wersja Label Studio, konfiguracja oznaczania, ustawienia serwera |
| `convert_ls.py`, `preview.py` | eksport Label Studio → etykiety stron, kontrola zgodności z `LABELING.md`, nakładki |
| `match.py`, `metrics.py`, `report.py`, `run.py` | dopasowanie, metryki, raport, ewaluacja z bazą i ochroną `holdout` |
| `selfcheck.py` | kontrola miernika na oznaczonych stronach `dev` (etykiety jako predykcje z zaburzeniami) |
| `LABELING.md` | konwencje oznaczania (kategorie, ramki, role, przypadki graniczne) |

## Pochodzenie zbioru

- **Źródło:** materiały konkursowe COI, foldery `1.`–`9.` w `dane/coi/` (jeden folder = jeden rodzaj
  wniosku = dokument `f01`–`f09`). Folder `Materiały do zadania…` nie należy do zbioru.
- **Renderowanie:** renderer z Etapu A (`backend/worker/vision/render`), 200 DPI, tryb izolowany,
  bez formatów biurowych. Wersje komponentów i commit zapisuje manifest.
- **Strony do oznaczania:** wszystkie strony natywnych PDF (33 strony). Strony PNG, JPG i JPEG
  (w folderze 7 także JPE) mają geometrię PDF z dokładnością do 1 px, więc są **wariantami** strony PDF
  i korzystają z jej etykiet. Warunek sprawdza `geometry.compare_pages`: rozmiar identyczny, przesunięcie
  treści ≤ 1 px, tusz każdego obrazu w odległości ≤ 1 px od tuszu drugiego w co najmniej 97%
  (zmierzone minimum: 0,9786). Strona, która warunku nie spełnia (3 skany w folderze 5: 130 DPI,
  inna geometria), dostaje własny zestaw etykiet. Razem **36 zestawów etykiet obsługuje 132 strony oceny**.
- **Wykluczone:** formaty biurowe (DOC, DOCX, ODS, XLSX: `OFFICE_FORMAT_NOT_RENDERED`; zespół
  odrzucił LibreOffice, więc nie przechodzą przez kanał wizualny), wizualizacje
  formularza po cyfryzacji (`NOT_A_FORM`). Oczekiwane JSON-y (foldery 1 i 5) są w manifeście jako
  materiał referencyjny (`REFERENCE_JSON`).
- **Identyfikacja plików:** manifest w repozytorium nie zawiera ścieżek ani nazw plików. Plik opisują
  numer folderu (`folder`), format (rozszerzenie) i `sha256`. Pełne ścieżki są tylko w kopii
  `dane/robocze/b/manifest.full.json`.
- **Oznaczanie:** jedna osoba (`human-1`), ręcznie, w Label Studio, bez wstępnych oznaczeń z detektorów.

## Podział `dev` / `holdout`

| Część | Dokumenty | Zestawy etykiet | Strony oceny | Rola |
|---|---|---|---|---|
| `dev` | f02, f05, f07, f09 | 24 (21 stron PDF + 3 skany) | 84 | strojenie progów i kalibracja |
| `holdout` | f01, f03, f04, f06, f08 | 12 | 48 | wyłącznie raport końcowy |

- Podział jest **po dokumentach**: wszystkie strony i warianty formatu dokumentu są w jednej części,
  bo te same strony występują w kilku formatach (podział po plikach dałby wyciek).
- Do zamrożenia (przed Etapem C) podział może się zmienić wyłącznie na podstawie liczebności etykiet,
  nigdy na podstawie wyników detektorów. Zamrożenie zapisuje manifest (`split_frozen`, hashe etykiet).
- **Zasady dla `holdout`:** progi, wagi i prompty stroi się tylko na `dev`. Przy strojeniu nie
  ogląda się stron ani etykiet `holdout`. Ewaluacja `holdout` wymaga jawnej flagi i jest odnotowywana
  (Etap B.4).

## Odtworzenie

Z katalogu repozytorium, w środowisku `.venv` (`requirements-dev.txt`):

```powershell
.venv\Scripts\python.exe -m eval.visual.prepare
```

Wynik w `dane/robocze/b/`:

| Plik | Zawartość |
|---|---|
| `pages/<label_id>.png` | obrazy stron do oznaczania (36) |
| `tasks.json` | zadania Label Studio w kolejności oznaczania |
| `manifest.full.json` | manifest ze ścieżkami plików (tylko lokalnie) |
| `manifest.public.json` | manifest bez ścieżek (kandydat do `eval/visual/manifest.json`) |

Ponowne uruchomienie daje te same obrazy i manifest (poza datą). Jeśli obraz strony różni się
od nowego renderu (np. po zmianie renderera), skrypt kończy się błędem, bo etykiety mogłyby przestać
pasować do obrazu.

## Label Studio: instalacja i uruchomienie

**Wersja:** Label Studio **1.23.2** (wydanie z 2026-09-29), edycja Community, licencja **Apache-2.0**
(plik `LICENSE` w tagu `1.23.2` repozytorium `HumanSignal/label-studio`). Obraz przypięty digestem
listy manifestów:

```text
heartexlabs/label-studio:1.23.2@sha256:afcc516a22775a39d0d66f4c2ddc95d01be3e3d3862b21fcf4f9de34b4ad4e12
```

Label Studio to narzędzie człowieka do oznaczania, a **nie zależność rozwiązania**: nie występuje
w `requirements*.txt` ani w obrazach projektu i nie jest rozpowszechniane z rozwiązaniem
(`docs/licenses.md`). Działa w osobnym kontenerze, bo wymaga innego Pythona niż projekt (3.14).

Wymagania: Docker Desktop, około 1 GB miejsca na obraz. Komendy w PowerShell, z katalogu repozytorium:

```powershell
docker pull heartexlabs/label-studio:1.23.2@sha256:afcc516a22775a39d0d66f4c2ddc95d01be3e3d3862b21fcf4f9de34b4ad4e12
New-Item -ItemType Directory -Force dane\robocze\labelstudio | Out-Null
docker run -d --name ls-teamkapa `
  -p 127.0.0.1:8080:8080 `
  --env-file eval\visual\labelstudio\ls.env `
  -v "${PWD}\dane\robocze\labelstudio:/label-studio/data" `
  -v "${PWD}\dane\robocze\b\pages:/label-studio/files/pages:ro" `
  heartexlabs/label-studio:1.23.2@sha256:afcc516a22775a39d0d66f4c2ddc95d01be3e3d3862b21fcf4f9de34b4ad4e12
```

- Port jest dostępny **tylko z tego komputera** (`127.0.0.1`).
- Baza (SQLite), eksporty i ustawienia projektu są w `dane/robocze/labelstudio/`, a obrazy stron są
  podłączone tylko do odczytu. Nic z tego nie trafia do Gita.
- Zatrzymanie i ponowne uruchomienie: `docker stop ls-teamkapa`, `docker start ls-teamkapa`.
  Usunięcie kontenera (`docker rm ls-teamkapa`) nie usuwa danych z `dane/robocze/labelstudio/`.

### Prywatność: co jest wyłączone po stronie serwera

Ustawienia są w `labelstudio/ls.env` i wynikają ze źródeł wersji 1.23.2:

| Kanał | Domyślnie w 1.23.2 | Ustawienie |
|---|---|---|
| Sentry serwera (raporty błędów) | **włączony**: domyślny DSN `*.ingest.sentry.io` w `core/settings/label_studio.py` | `LABEL_STUDIO_SENTRY_DSN=` (pusty DSN: `init_sentry` nie startuje) |
| Sentry przeglądarki | **włączony**: skrypt `browser.sentry-cdn.com` w `templates/base.html` | `LABEL_STUDIO_FRONTEND_SENTRY_DSN=` |
| Telemetria: Google Tag Manager i `labelstud.io/track` na stronach logowania, zdarzenia `__lsa` | włączona (`COLLECT_ANALYTICS=True`) | `LABEL_STUDIO_COLLECT_ANALYTICS=false` |
| Sprawdzanie najnowszej wersji (PyPI) | włączone (`LATEST_VERSION_CHECK=True`) | `LABEL_STUDIO_LATEST_VERSION_CHECK=false` |
| Flagi funkcji | lokalnie (`FEATURE_FLAGS_OFFLINE=True`) | ustawione jawnie |
| Wskazówki pobierane przez serwer z `raw.githubusercontent.com` (`core/views.py`, `heidi_tips`) | włączone, bez flagi | blokada ruchu wychodzącego: `HTTP(S)_PROXY=http://127.0.0.1:9`, `NO_PROXY=localhost,127.0.0.1` |

`get_env` (`core/utils/params.py`) czyta najpierw `LABEL_STUDIO_<NAZWA>` i używa pustej wartości
dosłownie, więc pusty DSN nie jest zastępowany domyślnym. Nieistniejący serwer proxy sprawia, że
każde połączenie HTTP(S) procesu serwera poza `localhost` kończy się błędem (także gdyby któryś
kanał pozostał włączony).

### Weryfikacja po uruchomieniu

1. **Zmienne w kontenerze:**
   ```powershell
   docker exec ls-teamkapa sh -c "env | grep -E 'SENTRY|ANALYTICS|VERSION_CHECK|FEATURE_FLAGS|PROXY' | sort"
   ```
   Oczekiwane: oba `*SENTRY_DSN=` puste, `COLLECT_ANALYTICS=false`, `LATEST_VERSION_CHECK=false`,
   proxy `http://127.0.0.1:9`.
2. **Blokada ruchu wychodzącego serwera:**
   ```powershell
   docker exec ls-teamkapa python -c "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=5)"
   ```
   Oczekiwany **błąd** połączenia (odmowa połączenia z proxy). Brak błędu oznacza, że blokada nie działa:
   zatrzymaj kontener i zgłoś to.
3. **Ustawienia widoczne w stronie:** po zalogowaniu otwórz źródło strony (Ctrl+U) i znajdź
   `sentry_dsn: null` oraz `collect_analytics: false`. Źródło strony logowania nie może zawierać
   `googletagmanager`.
4. **DevTools (F12 → Network):** w trakcie sesji wszystkie żądania mają iść do `127.0.0.1:8080`.
   **Ta kontrola obejmuje wyłącznie ruch przeglądarki** (strona, skrypty, obrazy). Nie pokazuje połączeń
   wychodzących z serwera w kontenerze; te zamykają ustawienia serwera i blokada proxy z punktów 1–2.

### Konfiguracja projektu (jednorazowo)

1. Otwórz <http://127.0.0.1:8080> i załóż konto lokalne. Użyj fikcyjnego adresu, np.
   `annotator@example.com`, i hasła tylko do tej instancji. Konto istnieje wyłącznie w bazie
   w `dane/robocze/labelstudio/`.
2. **Create Project**, nazwa bez treści formularzy (np. `TEAMKAPA 12`).
3. **Labeling Setup** → **Custom template** → widok kodu → wklej całą zawartość
   `eval/visual/labelstudio/config.xml` → **Save**.
4. **Settings → Cloud Storage → Add Source Storage**, typ **Local files**:
   ścieżka `/label-studio/files/pages`, filtr plików `.*\.png$`. Zapisz, ale **nie uruchamiaj
   synchronizacji (Sync)**. Magazyn jest potrzebny tylko po to, żeby Label Studio mogło serwować obrazy.
5. **Import** → wskaż `dane/robocze/b/tasks.json` → **Import**. Powinno powstać 36 zadań w kolejności
   oznaczania (kolumny `order`, `label_id`, `split`).
6. **Settings → Annotation**: kolejność zadań **Sequential**.

Jeżeli kreator magazynu wymusza synchronizację, wybierz tryb, w którym każdy plik PNG jest zadaniem,
i **nie importuj** wtedy `tasks.json`, żeby zadania się nie podwoiły. Zadania utworzone w ten sposób
nie mają pól `label_id`, `doc_id`, `split` i `page`, a konwerter ich wymaga, więc taki projekt trzeba
założyć od nowa z importem `tasks.json`.

### Eksport

**Export → JSON** (nie JSON-MIN). Plik zapisz w `dane/robocze/labelstudio/exports/`, a kopię z folderu
Pobrane usuń. Eksport zawiera też czas pracy nad zadaniem (`lead_time`), więc nie trzeba mierzyć go ręcznie.
Label Studio zapisuje własną kopię w `dane/robocze/labelstudio/export/`; konwertujemy plik z `exports/`.

## Konwersja i kontrola nakładek

```powershell
.venv\Scripts\python.exe -m eval.visual.convert_ls dane\robocze\labelstudio\exports\<plik>.json
```

Wynik w `dane/robocze/labels/` (poza repozytorium):

| Plik | Zawartość |
|---|---|
| `<label_id>.json` | etykiety strony (`teamkapa.vision.labels/1`), tylko strony bez błędów |
| `preview/<label_id>.png` | nakładka: ramki w kolorach kategorii z Label Studio, numer elementu, atrybuty, legenda |
| `convert_report.md`, `convert_report.json` | błędy, ostrzeżenia, role wywnioskowane, czas na element, ciasność ramek |

- **Błąd** (strona nie dostaje pliku etykiet, poprzedni zostaje bez zmian, kod wyjścia 1): niezgodność
  z `LABELING.md` albo z manifestem, opisana w raporcie z identyfikatorem i położeniem regionu
  w pikselach. Na nakładce region z błędem ma czerwoną ramkę `ERR`. Poprawka: w Label Studio,
  potem **Update**, nowy eksport i ponowna konwersja.
- **Ostrzeżenie** (strona jest konwertowana): krawędź ramki komórki, kratek, sekcji lub checkboxa dalej
  niż 2 px od linii w obrazie, niezapisany szkic, nieprawdopodobne `cells` przy fladze `uncertain`.
- **Automatycznie, z wpisem w raporcie:** przycięcie ramki wystającej poza stronę o ≤ 0,5 px, rola
  `skip` dla komórki z innym elementem do wypełnienia, role domyślne.
- ID elementów (`gt-p{strona}-{nnn}`) nadaje kolejność czytania (od góry, potem od lewej). Ten sam
  eksport daje identyczne pliki. Oznaczający jest zapisany jako `human-<id użytkownika LS>`,
  bez adresu e-mail.
- Konwerter odrzuca regiony z pre-anotacji (`origin` inny niż `manual`, `predictions`).
- Nakładki stron `holdout` ogląda tylko oznaczający. Przy strojeniu (Etap C i dalej) ich się nie ogląda.

## Pilot

1. Oznacz według `LABELING.md` dwie pierwsze strony z kolejki: `f02-p01` i `f07-p01` (`dev`). Razem
   obejmują prawie wszystkie kategorie.
2. Każdą stronę zakończ statusem strony (`complete` albo `empty`) i **Submit**.
3. Wyeksportuj JSON do `dane/robocze/labelstudio/exports/`.
4. Konwersja, nakładki i przegląd konwencji (wykonane 2026-11-22). Wynik pilota: 65 regionów, średnio
   72 s na element, 95% krawędzi ramek najwyżej 1 px od linii. Konwerter wykrył status `empty`
   na oznaczonej stronie, literówkę w `cells` i przypadkowe kliknięcie; reguły dodane do `LABELING.md`.

## Stan oznaczania (decyzja z 2026-11-22)

**Oznaczone są tylko 2 strony `dev` z pilota (`f02-p01`, `f07-p01`; 65 elementów, 8 stron oceny
z wariantami formatu). Dalsze oznaczanie jest odłożone.** Wrócimy do niego, prawdopodobnie z
pre-etykietami z warstwy wektorowej PDF (do poprawy przez człowieka), co zmieni zasadę „bez
pre-anotacji” i wymaga osobnej decyzji. Do tego czasu:

- podział nie jest zamrożony, a manifest (`dane/robocze/b/`) pozostaje roboczy (B.6 odłożony),
- nie powstaje baza (`baseline.json`): `run.py --write-baseline` wymaga pełnego pokrycia części,
- `holdout` jest nietknięty: nie ma etykiet, podglądów ani obliczeń,
- każdy wynik na tym zbiorze jest **wyłącznie wstępnym pomiarem** (raport oznacza niepełne pokrycie).

Kolejność i szacunek poniżej dotyczą wznowienia oznaczania.

## Kolejność oznaczania i szacunek czasu

Kolejka w `tasks.json`: pilot (`f02-p01`, `f07-p01`), cały `holdout` (od dokumentów z najmniejszą
liczbą stron), potem reszta `dev` z f09 (16 stron) na końcu. Po `holdout`, a przed resztą `dev`,
dochodzą własne skany i zdjęcia wydruków stron `holdout` (pomiar jakości skanów na `holdout`).
Po kilku dniach ponowne oznaczenie 3 stron mierzy zgodność oznaczającego z samym sobą.

Szacunek przed pilotem: około 7,3 h oznaczania (38 s na element). Pilot zmierzył 72 s na element
(`lead_time` liczy cały czas otwarcia zadania, razem z przerwami i nauką narzędzia), a f07-p01 ma
46 elementów wobec szacowanych ~20. Po pilocie: **pozostałe 34 zestawy to około 10–15 h** oznaczania
(~650–750 elementów po 50–72 s), czyli 7–10 sesji po ≤ 90 min, plus ~1 h kontroli nakładek.
Szacunek przeliczam po `holdout`. Zmęczenie pogarsza dokładność ramek, więc sesje krótkie.

## Metryki i ewaluacja

```powershell
.venv\Scripts\python.exe -m eval.visual.run --system opencv --predictions <katalog>
.venv\Scripts\python.exe -m eval.visual.selfcheck
```

- **Predykcje:** plik `<file_id>-p<NN>.json` (`teamkapa.vision.predictions/1`: system, strona,
  rozmiar, lista `VisualElement` z kontraktu 7.5) dla każdej ocenianej strony pliku, czyli każdego
  włączonego wariantu oznaczonego zestawu. Brak pliku, plik strony spoza manifestu, inny system albo
  rozmiar to błąd (kod 2), a nie pominięcie. Pliki stron nieocenianych są pomijane i liczone w raporcie.
- **Dopasowanie** w obrębie kategorii: zachłanne po malejącym IoU, IoU ≥ 0,5 (raport także dla 0,3
  i 0,7), remis rozstrzygają ID. Cienkie linie (`text_line`, `dotted_line`, poziome `signature`
  i `office_only`) rozszerzane pionowo do 1% wysokości strony. `checkbox_group` po składzie
  (Jaccard członków ≥ 0,5), a bez członków po IoU. Etykiety `uncertain` są „trudne”.
- **Metryki:** P, R, F1 per kategoria, mikro i makro, 95% przedziały Wilsona; przekroje per format
  (PDF, obraz będący wariantem strony PDF, obraz z własnymi etykietami, czyli skany) i per dokument,
  makro-F1 po dokumentach; macierz pomyłek kategorii (dopasowanie bez względu na kategorię);
  liczba kratek (zgodność, MAE), role (trafność, macierz pomyłek), `multi` grup.
- **Pola i ablacja** (`metrics.ablation`): P/R miejsc pól z wyniku pipeline'u z kanałem wizualnym
  i bez niego. Polem jest element z rolą `input`, `choice` albo `e_signature`, a grupa wyboru to jedno
  pole. W Etapie B tylko mechanizm i testy; liczby w Etapach D i E.
- **Ochrona `holdout`:** domyślnie `--split dev`. `holdout` wymaga `--split holdout --final`, a każde
  uruchomienie trafia do `dane/robocze/eval/holdout_runs.log` (data, commit, system, katalog). Przy
  `dev` skrypt nie otwiera etykiet ani predykcji `holdout`.
- **Baza:** `--write-baseline <plik>` (tylko przy pełnym pokryciu części), `--baseline <plik>` porównuje
  mikro-P/R/F1 i F1 per kategoria z tolerancją (domyślnie 0,02) i zwraca kod 1 przy regresji.
- **Raport:** `dane/robocze/eval/<split>-<system>.{json,md}` z sekcją „Znane ograniczenia”.
- **Testy CI** działają na danych syntetycznych: `tests/vision/test_eval_*.py` (konwerter, dopasowanie,
  metryki, `run.py` z ochroną `holdout`) i `tests/vision/synth_forms.py` (strony formularza z etykietami
  i degradacjami: obrót, rozmycie, szum, JPEG, kontrast, niższe DPI).
- **Kontrola miernika** (`selfcheck`, tylko `dev`) na 2 stronach pilota (8 stron oceny): etykiety
  bez zmian i przesunięte o 1 lub 2 px w obu osiach dają P = R = F1 = 1; przesunięte o pół szerokości
  elementu: 0; bez co 10. elementu: P = 1, R = 0,92.

## Znane ograniczenia zbioru

- **Oznaczone są 2 strony `dev` (pilot)**, z dwóch dokumentów i bez skanów; wyniki to wstępny pomiar.
- Jeden oznaczający: brak zgodności między oznaczającymi; szum etykiet szacuje powtórne oznaczenie 3 stron.
- Warianty formatu jednej strony nie są niezależnymi próbkami. Wyniki raportuje się per dokument i per
  format (PDF, obraz cyfrowy, skan).
- Prawdziwe skany są tylko w jednym dokumencie (`dev`), zdjęć z aparatu nie ma. Pomiar na `holdout`
  wymaga własnych skanów i zdjęć wydruków.
- Formaty biurowe są poza zbiorem (brak kanału wizualnego; ich pola dostają w Etapie E kod
  `NO_VISUAL_CHANNEL`, `docs/vision.md`).
- Etykiety wariantów nie są korygowane o przesunięcie ≤ 1 px (około 0,0004 wysokości strony).
- Folder 9 to około 60% elementów `dev`, a folder 8 około 2/3 `holdout` (szacunek przed oznaczeniem),
  stąd raport per dokument i makro-średnie.
