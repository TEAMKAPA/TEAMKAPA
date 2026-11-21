# Licencje zależności kanału wizualnego (#10)

Zasada projektu: wyłącznie open source bez copyleftu (zakaz GPL, AGPL, LGPL), także w bibliotekach
binarnych dołączonych do kół (*wheels*). `pip-licenses` w CI widzi tylko metadane pakietu, więc
biblioteki dołączone sprawdzam ręcznie, opisaną niżej metodą.

Stan: 2026-11-21. Sprawdzone koła: `win_amd64` (środowisko deweloperskie) oraz `manylinux_2_28_x86_64`
i `manylinux_2_17_x86_64` (obraz workera `python:3.14.10-slim-trixie`).

## Podsumowanie

| Pakiet | Wersja | Licencja pakietu | Dołączone biblioteki | Wynik |
|---|---|---|---|---|
| pypdfium2 | 5.15.0 | BSD-3-Clause OR Apache-2.0 | PDFium 159.0.8146 (BSD-3) + zależności, wszystkie permisywne | OK |
| pillow | 13.0.0 | MIT-CMU | libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, FreeType, HarfBuzz, lcms2, zlib, liblzma, libzstd, brotli, libxcb, libXau: permisywne | OK, z uwagą o FriBiDi |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | OpenBLAS (BSD-3), LAPACK (BSD-3-Open-MPI), libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1), libquadmath (LGPL-2.1-or-later, tylko Linux) | **Dozwolony** (patrz niżej) |

## Szczegóły

### pypdfium2 5.15.0
- Wiązania: BSD-3-Clause OR Apache-2.0. Plik binarny `pdfium.dll` / `libpdfium.so`: PDFium (BSD-3-Clause),
  `version.json`: `origin=sourcebuild-toolchained`, `flags=[]` (bez V8/XFA).
- Licencje dołączone (`dist-info/licenses/BUILD_LICENSES/`): abseil (Apache-2.0), agg23 (BSD-like),
  fast_float, dragonbox (Apache-2.0 z wyjątkiem LLVM / Boost), FreeType (FTL; dual FTL/GPL-2, korzystamy z FTL),
  HarfBuzz (MIT), highway (Apache-2.0), ICU (Unicode-3.0), lcms2 (MIT), libjpeg-turbo (IJG/BSD-3),
  OpenJPEG (BSD-2), libpng, libtiff, simdutf, zlib, llvm-libc (Apache-2.0 z wyjątkiem LLVM).
- Pliki z katalogów `android/`, `cibw_musl/`, `pdfium-binaries_musl/` i `v8_xfa/` dotyczą innych platform
  lub wariantów i nie występują w kołach Windows ani glibc. Wzmianki o GPL w ICU, LLVM i dragonbox to
  teksty wyjątków lub not o zgodności, a nie licencje komponentów.

### pillow 13.0.0
- Koło Linux zawiera w `pillow.libs/` wyłącznie biblioteki permisywne (lista w tabeli).
- SBOM (`dist-info/sboms/pillow-13.0.0.cdx.json`) wymienia **libimagequant 4.4.1 (GPL-3.0-or-later)**
  i **FriBiDi 1.0.17 (LGPL-2.1-or-later)**, ale:
  - libimagequant **nie jest dołączony**: brak pliku `.so` i brak symboli `liq_*` w `_imaging*.so`;
    na Windows `PIL.features.check("libimagequant") == False`;
  - FriBiDi **nie jest dołączony**: raqm jest wkompilowany statycznie (MIT), a `libfribidi.so.0`
    ładowany jest dynamicznie z systemu tylko wtedy, gdy jest zainstalowany (`fribidi-shim`).
    Na Windows `PIL.features.check("fribidi") == False`.
- Uwaga dla obrazu workera (#14): nie instalować pakietu systemowego `libfribidi0`, żeby Pillow
  go nie załadował. Kanał wizualny nie używa renderowania tekstu (`ImageFont` z układem raqm).

### numpy 2.5.4: dozwolony

**Status: dozwolony, potwierdzone przez zespół i mentorów (2026-11-21).**

Uzasadnienie:
- licencja pakietu numpy: BSD-3-Clause (oraz 0BSD, MIT, Zlib, CC0-1.0 dla fragmentów),
- dołączony **libgfortran** (biblioteka uruchomieniowa GCC): GPL-3.0-or-later **WITH GCC Runtime Library
  Exception 3.1**; wyjątek pozwala używać jej w programach niebędących GPL bez obowiązku otwierania kodu
  (Windows: statycznie w `libscipy_openblas64_*.dll`; Linux: `numpy.libs/libgfortran-*.so.5`, linkowany przez OpenBLAS),
- dołączona **libquadmath**: LGPL-2.1-or-later, tylko Linux (`numpy.libs/libquadmath-*.so.0`), **linkowana
  dynamicznie i niemodyfikowana**, więc nie wymaga upublicznienia kodu aplikacji,
- pozostałe dołączone komponenty są permisywne: OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI).

Warunek utrzymania statusu: bibliotek z `numpy.libs/` nie modyfikujemy ani nie linkujemy statycznie z własnym kodem.

## Metoda (odtworzenie)

1. Instalacja przypiętych wersji w `.venv` (Windows) i pobranie kół Linux bez instalacji:
   `pip download --no-deps --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --python-version 3.14 --implementation cp --abi cp314 --abi none numpy==2.5.4 pillow==13.0.0 pypdfium2==5.15.0`.
2. Lista bibliotek dołączonych: pliki w `*.libs/` i `pypdfium2_raw/` w archiwum koła.
3. Licencje: `dist-info/licenses/**`, sekcje `Name/Files/License` w `LICENSE.txt` numpy,
   SBOM CycloneDX Pillow, `PIL.features.check(...)`.
4. Linkowanie statyczne: wyszukanie symboli (`liq_*`, `fribidi_*`) i nazw `lib*.so` w plikach `.so`.

## Narzędzia pomocnicze (nie są zależnościami rozwiązania)

| Narzędzie | Wersja | Licencja | Użycie |
|---|---|---|---|
| Label Studio (Community) | 1.23.2, obraz `heartexlabs/label-studio` przypięty digestem | Apache-2.0 (`LICENSE` w tagu `1.23.2`) | ręczne oznaczanie stron zbioru #12 przez człowieka |

- Label Studio **nie występuje** w `requirements*.txt` ani w obrazach projektu i nie jest
  rozpowszechniane z rozwiązaniem. Działa lokalnie w osobnym kontenerze (instrukcja, digest i ustawienia
  prywatności: `eval/visual/README.md`, `eval/visual/labelstudio/ls.env`).
- Składników obrazu Dockera (system bazowy, nginx, biblioteki Pythona) nie audytujemy pod kątem copyleftu,
  bo nie są częścią rozwiązania ani nie są z nim łączone. Do repozytorium trafia tylko konfiguracja
  oznaczania (`config.xml`) i ustawienia serwera.
