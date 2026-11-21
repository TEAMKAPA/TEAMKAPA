"""Przygotowanie stron do ręcznego oznaczania (#12, Etap B).

Uruchomienie z katalogu repozytorium (``dane/coi`` tylko do odczytu, wyniki w ``dane/robocze/b``)::

    .venv\\Scripts\\python.exe -m eval.visual.prepare

Kroki:

1. Każdy folder ``dane/coi/<n>. …`` to dokument ``f<nn>`` (rodzaj wniosku). Wszystkie jego
   pliki przechodzą przez renderer z Etapu A (200 DPI, tryb izolowany; formaty biurowe nie są
   renderowane), a wynik trafia do manifestu: plik wyrenderowany, wykluczony (kod błędu renderera
   albo ``NOT_A_FORM``) lub referencyjny (oczekiwany JSON).
2. Strony natywnego PDF to strony do oznaczania (``f<nn>-p<mm>``). Strona obrazu, której
   geometria zgadza się ze stroną PDF (``geometry.compare_pages``), jest jej wariantem i korzysta
   z tych samych etykiet. Pozostałe strony obrazów (np. skany) dostają własne zestawy etykiet
   (``<file_id>-p<mm>``).
3. Zapis: obrazy stron do oznaczania (``pages/<label_id>.png``), zadania Label Studio
   (``tasks.json``; kolejność: pilot, holdout, dev, a w części dokumenty od najmniejszej liczby
   stron) oraz manifest pełny ze ścieżkami (``manifest.full.json``) i publiczny bez ścieżek
   (``manifest.public.json``).

Wynik jest deterministyczny (poza datą w manifeście). Jeśli obraz strony do oznaczania już istnieje
i jego piksele różnią się od nowego renderu, skrypt kończy się błędem, bo etykiety mogłyby przestać
pasować do obrazu (``--force`` nadpisuje świadomie). Na wyjściu wypisywane są wyłącznie liczby.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image as PILImage

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import RenderError
from backend.worker.vision.models import DocumentKind, ImageArray, PageSource, RenderedDocument
from backend.worker.vision.render import render_document
from backend.worker.vision.render.detect import detect_kind, kind_from_extension
from eval.visual import ls
from eval.visual.geometry import GeometryParams, compare_pages, image_sha256
from eval.visual.models import (
    MANIFEST_SCHEMA,
    FileOrigin,
    FileStatus,
    LabelingInfo,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    RendererInfo,
    Split,
    SplitDefinition,
    coi_doc_id,
    dump_json,
    dump_public_manifest,
    label_id_for,
    read_json,
    write_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DANE = REPO_ROOT / "dane"
DEFAULT_OUT = DEFAULT_DANE / "robocze" / "b"
DEFAULT_SPLIT = Path(__file__).resolve().parent / "split.json"
DPI = 200

COI_FOLDER = re.compile(r"^([1-9][0-9]*)\.\s")
NOT_A_FORM_MARKERS = ("wizualizacja",)
"""Pliki z wizualizacją formularza po cyfryzacji (materiał referencyjny COI), a nie formularze."""

GEOMETRY_MISMATCH = "GEOMETRY_MISMATCH"
REPRODUCE = (
    ".venv\\Scripts\\python.exe -m eval.visual.prepare",
    "Label Studio i oznaczanie: eval/visual/README.md",
)


class PrepareError(RuntimeError):
    """Niespójność danych wejściowych lub wyników; komunikat bez treści dokumentów."""


@dataclass
class _Document:
    doc_id: str
    folder: str
    split: Split
    files: list[ManifestFile] = field(default_factory=list)
    pages: list[ManifestPage] = field(default_factory=list)
    images: dict[str, ImageArray] = field(default_factory=dict)  # label_id → obraz do oznaczania


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _coi_folders(dane: Path) -> list[tuple[str, Path]]:
    coi = dane / "coi"
    if not coi.is_dir():
        raise PrepareError(f"brak katalogu {coi}")
    found = []
    for entry in coi.iterdir():
        match = COI_FOLDER.match(entry.name)
        if entry.is_dir() and match:
            found.append((match.group(1), entry))
    if not found:
        raise PrepareError("brak folderów dokumentów w dane/coi")
    return sorted(found, key=lambda item: int(item[0]))


def _is_not_a_form(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in NOT_A_FORM_MARKERS)


def _file_ids(doc_id: str, paths: list[Path]) -> dict[Path, str]:
    """Stałe ID plików: ``<doc_id>-<format>``; przy powtórzeniu formatu kolejne ``-2``, ``-3``.

    Formularze dostają ID przed plikami pomocniczymi, a w grupie decyduje nazwa (kolejność stała).
    """
    ids: dict[Path, str] = {}
    used: Counter[str] = Counter()
    for path in sorted(paths, key=lambda p: (_is_not_a_form(p), p.name)):
        fmt = path.suffix.lower().lstrip(".")
        used[fmt] += 1
        suffix = "" if used[fmt] == 1 else f"-{used[fmt]}"
        ids[path] = f"{doc_id}-{fmt}{suffix}"
    return ids


def _issue_codes(document: RenderedDocument) -> tuple[str, ...]:
    codes = {i.code.value for i in document.meta.issues}
    codes.update(i.code.value for page in document.pages for i in page.meta.issues)
    return tuple(sorted(codes))


def _render_file(
    path: Path, file_id: str, dane: Path, cfg: RenderConfig, isolated: bool
) -> tuple[ManifestFile, RenderedDocument | None]:
    data = path.read_bytes()
    fmt = path.suffix.lower().lstrip(".")
    common: dict[str, Any] = {
        "file_id": file_id,
        "origin": FileOrigin.COI,
        "format": fmt,
        "sha256": _sha256(data),
        "size_bytes": len(data),
        "path": path.relative_to(dane).as_posix(),
    }
    declared = kind_from_extension(path.name)
    if fmt == "json":
        entry = ManifestFile(
            **common,
            declared_kind=None,
            detected_kind=None,
            status=FileStatus.REFERENCE,
            code="REFERENCE_JSON",
        )
        return entry, None
    if _is_not_a_form(path):
        entry = ManifestFile(
            **common,
            declared_kind=declared,
            detected_kind=detect_kind(data).kind,
            status=FileStatus.EXCLUDED,
            code="NOT_A_FORM",
        )
        return entry, None
    try:
        document = render_document(data, declared, cfg, isolated=isolated)
    except RenderError as exc:
        entry = ManifestFile(
            **common,
            declared_kind=declared,
            detected_kind=detect_kind(data).kind,
            status=FileStatus.EXCLUDED,
            code=exc.code.value,
            issues=tuple(sorted({i.code.value for i in exc.issues})),
        )
        return entry, None
    sources = {page.meta.source for page in document.pages}
    if len(sources) != 1:
        raise PrepareError(f"{file_id}: strony z różnych źródeł")
    entry = ManifestFile(
        **common,
        declared_kind=declared,
        detected_kind=document.meta.detected_kind,
        status=FileStatus.RENDERED,
        page_count=len(document.pages),
        page_source=sources.pop(),
        issues=_issue_codes(document),
    )
    return entry, document


def _merge_versions(versions: dict[str, str], document: RenderedDocument) -> None:
    for name, version in document.meta.renderer_versions.items():
        if versions.setdefault(name, version) != version:
            raise PrepareError(f"różne wersje komponentu renderera: {name}")


def _label_page(
    label_id: str, file_id: str, page_no: int, document: RenderedDocument
) -> tuple[ManifestPage, ImageArray]:
    page = document.pages[page_no - 1]
    base = ManifestVariant(file_id=file_id, page=page_no, included=True)
    entry = ManifestPage(
        label_id=label_id,
        base_file_id=file_id,
        page=page_no,
        width_px=page.meta.width_px,
        height_px=page.meta.height_px,
        dpi=page.meta.dpi,
        image_sha256=image_sha256(page.image),
        variants=(base,),
    )
    return entry, page.image


def _add_variant(page: ManifestPage, variant: ManifestVariant) -> ManifestPage:
    variants = sorted((*page.variants, variant), key=lambda v: (v.check is not None, v.file_id))
    return ManifestPage.model_validate({**page.model_dump(), "variants": variants})


def _is_pdf(path: Path) -> bool:
    return detect_kind(path.read_bytes()).kind is DocumentKind.PDF


def _prepare_document(
    folder: str,
    path: Path,
    split: SplitDefinition,
    dane: Path,
    cfg: RenderConfig,
    isolated: bool,
    versions: dict[str, str],
) -> _Document:
    """Renderuje pliki dokumentu: najpierw PDF (strony do oznaczania), potem pozostałe formaty.

    Obrazy innych formatów są porównywane ze stronami PDF od razu i nie są przechowywane, chyba że
    dostają własny zestaw etykiet (np. skan o innej geometrii).
    """
    doc_id = coi_doc_id(folder)
    doc = _Document(doc_id=doc_id, folder=folder, split=split.split_of(doc_id))
    paths = [p for p in path.iterdir() if p.is_file()]
    ids = _file_ids(doc_id, paths)
    pdf: tuple[ManifestFile, RenderedDocument] | None = None
    pdf_pages: list[ManifestPage] = []
    own_pages: list[ManifestPage] = []
    params = GeometryParams()
    for file_path in sorted(paths, key=lambda p: (not _is_pdf(p), ids[p])):
        entry, document = _render_file(file_path, ids[file_path], dane, cfg, isolated)
        doc.files.append(entry)
        if document is None:
            continue
        _merge_versions(versions, document)
        if entry.page_source is PageSource.NATIVE_PDF:
            if pdf is not None:
                raise PrepareError(f"{doc_id}: więcej niż jeden PDF")
            pdf = (entry, document)
            for page_no in range(1, len(document.pages) + 1):
                label_page, image = _label_page(
                    label_id_for(doc_id, page_no), entry.file_id, page_no, document
                )
                pdf_pages.append(label_page)
                doc.images[label_page.label_id] = image
            continue
        if pdf is None:
            raise PrepareError(f"{doc_id}: brak PDF z warstwą stron do oznaczania")
        for page_no, page in enumerate(document.pages, start=1):
            if page_no <= len(pdf_pages):
                check = compare_pages(pdf[1].pages[page_no - 1].image, page.image, params)
                variant = ManifestVariant(
                    file_id=entry.file_id,
                    page=page_no,
                    check=check,
                    included=check.passed,
                    reason=None if check.passed else GEOMETRY_MISMATCH,
                )
                pdf_pages[page_no - 1] = _add_variant(pdf_pages[page_no - 1], variant)
                if check.passed:
                    continue
            label_page, image = _label_page(
                label_id_for(entry.file_id, page_no), entry.file_id, page_no, document
            )
            own_pages.append(label_page)
            doc.images[label_page.label_id] = image
    if pdf is None:
        raise PrepareError(f"{doc_id}: brak PDF z warstwą stron do oznaczania")
    doc.files.sort(key=lambda f: f.file_id)
    doc.pages = [*pdf_pages, *own_pages]
    return doc


def _git_state(repo: Path) -> tuple[str, bool]:
    git = shutil.which("git")
    if git is None:
        raise PrepareError("git niedostępny: wersja renderera musi być zapisana w manifeście")

    def run(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - stała lista argumentów, bez powłoki
            [git, "-C", str(repo), *args], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    commit = run("rev-parse", "HEAD")
    dirty = bool(run("status", "--porcelain"))  # dane/ jest wykluczony z Gita, więc nie wpływa
    return commit, dirty


def _write_page_image(path: Path, image: ImageArray, *, force: bool) -> bool:
    """Zapisuje obraz strony; zwraca ``True``, gdy plik powstał lub został nadpisany."""
    if path.exists():
        with PILImage.open(path) as existing:
            current = np.asarray(existing.convert("RGB"), dtype=np.uint8)
        if image_sha256(current) == image_sha256(image):
            return False
        if not force:
            raise PrepareError(
                f"{path.name}: istniejący obraz różni się od nowego renderu; etykiety mogą "
                "nie pasować (użyj --force tylko świadomie)"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.fromarray(image).save(path, format="PNG")
    return True


def _task_order(documents: list[_Document], split: SplitDefinition) -> list[tuple[_Document, str]]:
    by_label = {p.label_id: d for d in documents for p in d.pages}
    missing = [label_id for label_id in split.pilot if label_id not in by_label]
    if missing:
        raise PrepareError(f"strony pilota nie istnieją: {', '.join(missing)}")
    ordered = [(by_label[label_id], label_id) for label_id in split.pilot]
    for part in (Split.HOLDOUT, Split.DEV):
        docs = sorted(
            (d for d in documents if d.split is part), key=lambda d: (len(d.pages), d.doc_id)
        )
        for d in docs:
            ordered.extend((d, p.label_id) for p in d.pages if p.label_id not in split.pilot)
    return ordered


def _tasks(documents: list[_Document], split: SplitDefinition) -> list[dict[str, Any]]:
    pages = {p.label_id: p for d in documents for p in d.pages}
    tasks = []
    for order, (doc, label_id) in enumerate(_task_order(documents, split), start=1):
        tasks.append(
            {
                "data": {
                    ls.IMAGE_TAG: ls.image_url(label_id),
                    "label_id": label_id,
                    "doc_id": doc.doc_id,
                    "split": doc.split.value,
                    "page": pages[label_id].page,
                    "order": order,
                }
            }
        )
    return tasks


@dataclass(frozen=True)
class PrepareResult:
    manifest: Manifest
    tasks: list[dict[str, Any]]
    written_images: int


def prepare(
    dane: Path,
    out: Path,
    split_path: Path = DEFAULT_SPLIT,
    *,
    force: bool = False,
    isolated: bool = True,
    repo: Path = REPO_ROOT,
) -> PrepareResult:
    split = read_json(split_path, SplitDefinition)
    cfg = RenderConfig(dpi=DPI)  # jawnie: wynik nie zależy od zmiennych środowiskowych
    versions: dict[str, str] = {}
    documents = [
        _prepare_document(folder, path, split, dane, cfg, isolated, versions)
        for folder, path in _coi_folders(dane)
    ]
    unassigned = set(split.dev + split.holdout) - {d.doc_id for d in documents}
    if unassigned:
        raise PrepareError(f"dokumenty z podziału nie istnieją: {', '.join(sorted(unassigned))}")

    commit, dirty = _git_state(repo)
    manifest = Manifest(
        schema_version=MANIFEST_SCHEMA,
        created=date.today(),
        split_registered=split.registered,
        split_rule=split.rule,
        renderer=RendererInfo(
            commit=commit, dirty=dirty, dpi=DPI, versions=dict(sorted(versions.items()))
        ),
        labeling=LabelingInfo(
            tool=ls.LS_NAME,
            version=ls.LS_VERSION,
            image=ls.LS_IMAGE,
            config_sha256=ls.config_sha256(),
        ),
        documents=tuple(
            ManifestDocument(
                doc_id=d.doc_id,
                folder=d.folder,
                split=d.split,
                files=tuple(d.files),
                pages=tuple(d.pages),
            )
            for d in documents
        ),
        reproduce=REPRODUCE,
    )
    tasks = _tasks(documents, split)

    written = 0
    for d in documents:
        for label_id, image in d.images.items():
            written += _write_page_image(
                out / "pages" / ls.page_file_name(label_id), image, force=force
            )
    write_text(out / "manifest.full.json", dump_json(manifest))
    write_text(out / "manifest.public.json", dump_public_manifest(manifest))
    write_text(out / "tasks.json", json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    return PrepareResult(manifest=manifest, tasks=tasks, written_images=written)


def summary(result: PrepareResult) -> list[str]:
    """Podsumowanie wyłącznie liczbowe (bez nazw plików i treści)."""
    manifest = result.manifest
    files = Counter(f.status.value for d in manifest.documents for f in d.files)
    codes = Counter(f.code for d in manifest.documents for f in d.files if f.code is not None)
    lines = [
        f"dokumenty: {len(manifest.documents)}",
        "pliki: " + ", ".join(f"{k}={v}" for k, v in sorted(files.items())),
        "kody: " + ", ".join(f"{k}={v}" for k, v in sorted(codes.items())),
    ]
    for part in (Split.DEV, Split.HOLDOUT):
        docs = [d for d in manifest.documents if d.split is part]
        pages = [p for d in docs for p in d.pages]
        variants = [v for p in pages for v in p.variants if v.included]
        rejected = [v for p in pages for v in p.variants if not v.included]
        lines.append(
            f"{part.value}: dokumenty={len(docs)} zestawy_etykiet={len(pages)} "
            f"strony_oceny={len(variants)} warianty_odrzucone={len(rejected)}"
        )
    checks = [
        v.check
        for d in manifest.documents
        for p in d.pages
        for v in p.variants
        if v.check is not None and v.check.passed
    ]
    if checks:
        worst = min(min(c.variant_ink_near_base, c.base_ink_near_variant) for c in checks)
        shift = max(max(abs(c.shift_px[0]), abs(c.shift_px[1])) for c in checks)
        lines.append(
            f"warianty przyjęte: {len(checks)}, min. pokrycie tuszu {worst}, "
            f"maks. przesunięcie {shift} px"
        )
    lines.append(f"zadania: {len(result.tasks)}, nowe obrazy stron: {result.written_images}")
    lines.append(
        f"renderer: commit {manifest.renderer.commit[:7]}, "
        f"zmiany lokalne: {manifest.renderer.dirty}"
    )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Przygotowanie stron do oznaczania (#12)")
    parser.add_argument("--dane", type=Path, default=DEFAULT_DANE, help="katalog dane/")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="katalog wyników")
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT, help="plik podziału")
    parser.add_argument("--force", action="store_true", help="nadpisz zmienione obrazy stron")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.dane, args.out, args.split, force=args.force)
    except (PrepareError, ValueError, OSError) as exc:  # ValueError obejmuje błędy walidacji
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    for line in summary(result):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
