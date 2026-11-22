# Licencje zależności kanału wizualnego (#10)

Zasada projektu: wyłącznie open source bez copyleftu (zakaz GPL, AGPL, LGPL), także w bibliotekach
binarnych dołączonych do kół (*wheels*). `pip-licenses` w CI widzi tylko metadane pakietu, więc
biblioteki dołączone sprawdzam ręcznie, opisaną niżej metodą.

Stan: 2026-11-22 (opencv-python-headless), 2026-11-21 (pozostałe). Sprawdzone koła: `win_amd64` (środowisko deweloperskie) oraz `manylinux_2_28_x86_64`
i `manylinux_2_17_x86_64` (obraz workera `python:3.14.10-slim-trixie`).

## Podsumowanie

| Pakiet | Wersja | Licencja pakietu | Dołączone biblioteki | Wynik |
|---|---|---|---|---|
| pypdfium2 | 5.15.2 | BSD-3-Clause OR Apache-2.0 | PDFium 159.0.8149 (BSD-3) + zależności, wszystkie permisywne | OK |
| pillow | 13.0.0 | MIT-CMU | libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, FreeType, HarfBuzz, lcms2, zlib, liblzma, libzstd, brotli, libxcb, libXau: permisywne | OK, z uwagą o FriBiDi |
| opencv-python-headless | 5.1.0.95 | MIT (opakowanie), Apache-2.0 (OpenCV) | FFmpeg (LGPL-2.1-or-later, dynamicznie), **Intel IPP ICV (Intel Simplified Software License, statycznie)**, Intel ITT (GPL-2.0-only OR BSD-3-Clause), OpenSSL 1.1.1w (OpenSSL/SSLeay), libgfortran i libquadmath jak w numpy, reszta permisywna | **Wyjątek do potwierdzenia przez zespół** (patrz niżej) |
| rapidfuzz | 3.14.6 | MIT | brak (Linux: tylko biblioteki systemowe C/C++; Windows: runtime MSVC `msvcp140`) | OK |
| httpx | 0.28.1 | BSD-3-Clause | czysty Python; zależności: httpcore, h11, anyio, idna (BSD/MIT), certifi (MPL-2.0, patrz niżej) | OK |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | OpenBLAS (BSD-3), LAPACK (BSD-3-Open-MPI), libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1), libquadmath (LGPL-2.1-or-later, tylko Linux) | **Dozwolony** (patrz niżej) |

## Szczegóły

### pypdfium2 5.15.2
- Wiązania: BSD-3-Clause OR Apache-2.0. Plik binarny `pdfium.dll` / `libpdfium.so`: PDFium (BSD-3-Clause),
  `version.json`: `origin=sourcebuild-toolchained`, `flags=[]` (bez V8/XFA).
- Licencje dołączone (`dist-info/licenses/BUILD_LICENSES/`): abseil (Apache-2.0), agg23 (BSD-like),
  fast_float, dragonbox (Apache-2.0 z wyjątkiem LLVM / Boost), FreeType (FTL; dual FTL/GPL-2, korzystamy z FTL),
  HarfBuzz (MIT), highway (Apache-2.0), ICU (Unicode-3.0), lcms2 (MIT), libjpeg-turbo (IJG/BSD-3),
  OpenJPEG (BSD-2), libpng, libtiff, simdutf, zlib, llvm-libc (Apache-2.0 z wyjątkiem LLVM),
  brotli (MIT), libyuv (BSD-3-Clause).
- Pliki z katalogów `android/`, `cibw_musl/`, `pbin_musl/` i `v8_xfa/` dotyczą innych platform
  lub wariantów i nie występują w kołach Windows ani glibc. Wzmianki o GPL w ICU, LLVM i dragonbox to
  teksty wyjątków lub not o zgodności, a nie licencje komponentów.
- **Aktualizacja z 5.15.0 do 5.15.2** (2026-11-21, przy scaleniu z `main`): PDFium build 8146 → 8149,
  ten sam pojedynczy plik binarny, nadal `flags=[]`. Nowe pliki licencji to brotli (MIT) i libyuv
  (BSD-3-Clause). Oba komponenty były już w pliku binarnym Linux wersji 5.15.0, a teraz są
  udokumentowane. Liczba wzmianek „GPL” w plikach binarnych się nie zmieniła (teksty wyjątków).
  Wynik: OK.

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

### opencv-python-headless 5.1.0.95: wyjątek do potwierdzenia

**Status: wyjątek dla Intel IPP ICV zaakceptowany przez właściciela #10 (2026-11-22), do potwierdzenia
przez zespół i mentorów.** Pakiet jest już zależnością zespołu (#7, `extractors/ocr.py`), więc decyzja
dotyczy całego projektu. GPL ani AGPL nie występują.

Koła: `cp37-abi3-manylinux_2_28_x86_64` i `cp37-abi3-win_amd64`. Biblioteki dołączone:

| Komponent | Gdzie | Licencja | Ocena |
|---|---|---|---|
| OpenCV 5.1.0 | `cv2.abi3.so` / `cv2.pyd` | Apache-2.0 | OK |
| **Intel IPP ICV 2026.1.0** (+ IPP IW) | statycznie w `cv2` (oba koła; `getBuildInformation`: `Intel IPP: 2026.1.0`, `Custom HAL: ipp`) | **Intel Simplified Software License** (własnościowa, bezpłatne użycie i rozpowszechnianie binariów, bez copyleftu; nie open source) | **wyjątek** |
| Intel ITT 3.25.4 | statycznie w `cv2` | `GPL-2.0-only OR BSD-3-Clause` (SPDX w `3rdparty/ittnotify` OpenCV 5.x) | OK, korzystamy z BSD-3-Clause |
| FFmpeg (libavcodec, libavformat, libavutil, libswscale, libswresample) | Linux: `opencv_python_headless.libs/*.so`; Windows: `opencv_videoio_ffmpeg510_64.dll` | LGPL-2.1-or-later (napis licencji w plikach; konfiguracja bez `--enable-gpl` i `--enable-nonfree`) | dozwolone: biblioteki osobne, ładowane dynamicznie, niemodyfikowane |
| OpenSSL 1.1.1w | statycznie w `libavformat` (Linux) | OpenSSL License + SSLeay (permisywna) | licencja OK; **wersja bez wsparcia od 2023 r.** (uwaga poniżej) |
| libgfortran, libquadmath, OpenBLAS | Linux, `*.libs/` | jak w numpy | OK (jak numpy) |
| libvpx, libaom, dav1d, libavif, libpng, zlib, libjpeg-turbo, libtiff, OpenJPEG, libwebp, protobuf, flatbuffers, quirc, KAZE/AKAZE, MLAS, CLapack, openh264 (Windows) | statycznie lub `.libs/` | BSD, MIT, Apache-2.0, zlib, ISC | OK |
| Qt 5, libxcb, LGPL-3 i MPL (libunistring, libsrt itd.) | tylko koła nie-headless lub macOS | – | nie dotyczy |

Uwagi:
- IPP nie da się usunąć konfiguracją w czasie działania (licencja dotyczy rozpowszechniania koła).
  Alternatywy przy odrzuceniu wyjątku: własna kompilacja OpenCV z `WITH_IPP=OFF` (#14) albo
  detektory bez OpenCV (numpy + scipy.ndimage); obie wymagają decyzji zespołu, bo OpenCV używa też #7.
- Detektory kanału wizualnego wyłączają IPP w czasie działania (`cv2.ipp.setUseIPP(False)`): IPP wybiera
  ścieżkę obliczeń zależnie od procesora, a wynik detekcji ma być identyczny na każdej maszynie.
- OpenSSL 1.1.1w dotyczy wyłącznie obsługi protokołów sieciowych w FFmpeg (`cv2.VideoCapture` z adresem
  URL). Rozwiązanie nie używa wideo ani FFmpeg; nie wolno przekazywać do `cv2` adresów sieciowych.

### rapidfuzz 3.14.6 i httpx 0.28.1 (#11)

- **rapidfuzz** (porównanie etykiet w podwójnym odczycie): MIT. Koło `manylinux_2_28` zawiera wyłącznie
  własne rozszerzenia C++ linkowane z bibliotekami systemowymi (`libc`, `libstdc++`, `libgcc_s`, `libm`),
  bez dołączonych bibliotek i bez napisów GPL; koło Windows dodatkowo `msvcp140` (runtime Microsoft
  Visual C++, rozpowszechnianie dozwolone przez licencję runtime).
- **httpx** (klient VLM): BSD-3-Clause, czysty Python. Był już zależnością przechodnią (`huggingface_hub`
  przez Docling), więc jawne przypięcie nie dodaje pakietów. Jego zależność **certifi** (pakiet CA,
  MPL-2.0) również już była w obrazie (Docling, `requests`). MPL-2.0 to słaby copyleft na poziomie
  pliku: używamy pliku bez zmian, więc nie powstają obowiązki dla naszego kodu; CI (`pip-licenses`)
  blokuje wyłącznie GPL, AGPL i LGPL.

### Model VLM: Qwen3-VL-30B-A3B-Instruct przez Eden AI (#11)

| Pole | Wartość |
|---|---|
| Model (wagi) | `Qwen/Qwen3-VL-30B-A3B-Instruct`, open-weight |
| Licencja wag | **Apache-2.0** (karta modelu i API Hugging Face: `license: apache-2.0`) |
| Identyfikator w Eden AI | `deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct` (dostawca wykonujący: **DeepInfra**) |
| Katalog Eden AI (`GET /v3/models`, 2026-11-22) | wejście tekst i obraz, `open_weights: true`, `supports_response_schema: true`, `zero_data_retention: true`, region `us`, kwantyzacja `fp8`, 0,15 USD / 1 mln tokenów wejścia, 0,60 USD / 1 mln tokenów wyjścia |

- **Przepływ danych:** obrazy i tekst stron formularzy trafiają do Eden AI (`https://api.edenai.run/v3`),
  a stamtąd do DeepInfra, który uruchamia model. Organizatorzy dopuścili wysyłanie stron COI do Eden AI
  z modelami open-weight (decyzja z 2026-11-21, brief 7.4).
- Lista dozwolonych modeli jest w kodzie (`backend/worker/vision/vlm/config.py`); identyfikator spoza
  listy kończy start błędem walidacji konfiguracji. Dodanie modelu wymaga sprawdzenia licencji wag tutaj.
- Wariant lokalny (awaryjny) to te same wagi uruchomione przez człowieka na vLLM lub llama.cpp; kod
  niczego nie pobiera.

## Metoda (odtworzenie)

1. Instalacja przypiętych wersji w `.venv` (Windows) i pobranie kół Linux bez instalacji:
   `pip download --no-deps --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --python-version 3.14 --implementation cp --abi cp314 --abi none numpy==2.5.4 pillow==13.0.0 pypdfium2==5.15.2`.
2. Lista bibliotek dołączonych: pliki w `*.libs/` i `pypdfium2_raw/` w archiwum koła.
3. Licencje: `dist-info/licenses/**`, sekcje `Name/Files/License` w `LICENSE.txt` numpy,
   SBOM CycloneDX Pillow, `PIL.features.check(...)`.
4. Linkowanie statyczne: wyszukanie symboli (`liq_*`, `fribidi_*`) i nazw `lib*.so` w plikach `.so`.
5. OpenCV (koła `--abi abi3`): sekcje „is redistributed within” w `cv2/LICENSE-3RD-PARTY.txt`,
   napisy licencji i flagi `--enable-*` konfiguracji FFmpeg w plikach binarnych, wersja OpenSSL
   (`OpenSSL 1.1.1w`), symbole `__itt_*`, `cv2.getBuildInformation()` (IPP, ITT, zależności 3rdparty).

## PyTorch w wersji CPU (bez bibliotek NVIDIA)

Docling zależy od PyTorch. Domyślne koła `torch` z PyPI dla Linuksa to wersja CUDA, która
dociąga ~4 GB bibliotek `nvidia-*` (cuBLAS, cuDNN, NCCL… — licencja własnościowa NVIDIA, nie
open source) i `triton`. Rozwiązanie działa bez GPU, więc obraz workera i CI instalują
najpierw `backend/worker/requirements-torch-cpu.txt` (indeks `download.pytorch.org/whl/cpu`,
`torch==2.15.0+cpu`, `torchvision==0.29.2+cpu`, BSD-3-Clause), a dopiero potem
`requirements.txt` — pip uznaje torch za spełniony i nie pobiera wersji CUDA. Sprawdzone:
po tej kolejności w środowisku nie ma żadnego pakietu `nvidia-*` ani `triton`.

## Modele wbudowane w obraz workera

| Model | Rewizja | Licencja | Użycie |
|---|---|---|---|
| `intfloat/multilingual-e5-small` (Hugging Face) | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | MIT (karta modelu) | wektory dokumentów bazy wiedzy (RAG, `docs/rag.md`) |
| `docling-project/docling-layout-heron` (Hugging Face) | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | Apache-2.0 (karta modelu: `license: apache-2.0`) | Docling: układ strony PDF i obrazów (krok 02) |
| `docling-project/docling-models` (Hugging Face) | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` (tag `v2.3.0`) | CDLA-Permissive-2.0 (karta modelu: `license: cdla-permissive-2.0`) | Docling: TableFormer — struktura tabel w PDF i obrazach (krok 02) |

- Pobierane przy budowaniu obrazu (`backend/worker/Dockerfile`), w czasie działania
  `HF_HUB_OFFLINE=1` (worker nie ma internetu). e5: wyłącznie `model.safetensors`, `config.json`
  i pliki tokenizera (~470 MB). Docling: dwa repozytoria (~510 MB) w katalogu
  `DOCLING_ARTIFACTS_PATH=/opt/docling/models`, w układzie jak `docling-tools models download`;
  rewizje to te, które `docling==2.151.1` pobiera domyślnie (layout z gałęzi `main` —
  przypięty do commita, który była nią 2026-11-22). Bez nich ekstrakcja PDF/JPG/PNG w kontenerze kończyła się błędem
  „Cannot find an appropriate cached snapshot folder”. Wariant ONNX układu strony pomijamy —
  obraz nie ma `onnxruntime`, Docling używa silnika `transformers`.
- CDLA-Permissive-2.0 (Linux Foundation) to licencja permisywna dla danych/modeli: pozwala
  używać i rozpowszechniać wyniki bez warunku udostępniania zmian — brak copyleftu.
- Uruchamiany przez `transformers` i `torch` (CPU), które obraz już zawiera dla Docling —
  bez nowych zależności Pythona.

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
