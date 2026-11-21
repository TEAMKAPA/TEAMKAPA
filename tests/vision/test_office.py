"""Konwersja dokumentów biurowych: flaga, komenda, profil bez makr, limity, sprzątanie."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import DocumentKind
from backend.worker.vision.render.office import (
    DisabledOfficeConverter,
    LibreOfficeConverter,
    default_office_converter,
    run_with_timeout,
)
from tests.vision.synth import SynthPage, make_minimal_docx, make_pdf

FAKE_PDF = make_pdf([SynthPage()])


class FakeRunner:
    """Zapisuje wywołania; opcjonalnie tworzy wynikowy PDF albo symuluje błąd."""

    def __init__(self, *, code: int = 0, write_pdf: bool = True, exc: Exception | None = None):
        self.code, self.write_pdf, self.exc = code, write_pdf, exc
        self.calls: list[tuple[list[str], float, Path]] = []
        self.seen_profile: str | None = None
        self.seen_inputs: list[str] = []

    def __call__(self, command: list[str], timeout_s: float, cwd: Path) -> int:
        self.calls.append((command, timeout_s, cwd))
        xcu = cwd / "profile" / "user" / "registrymodifications.xcu"
        self.seen_profile = xcu.read_text(encoding="utf-8") if xcu.exists() else None
        self.seen_inputs = sorted(p.name for p in (cwd / "in").iterdir())
        if self.exc is not None:
            raise self.exc
        if self.write_pdf:
            out_dir = Path(command[command.index("--outdir") + 1])
            (out_dir / "input.pdf").write_bytes(FAKE_PDF)
        return self.code


def test_disabled_converter_gives_readable_error() -> None:
    with pytest.raises(RenderError) as info:
        DisabledOfficeConverter().convert_to_pdf(b"x", DocumentKind.DOCX)
    assert info.value.code is IssueCode.OFFICE_CONVERSION_DISABLED
    assert "VISION_OFFICE_ENABLED=false" in info.value.detail and "kind=docx" in info.value.detail


def test_default_converter_follows_flag() -> None:
    assert isinstance(default_office_converter(RenderConfig()), DisabledOfficeConverter)
    enabled = default_office_converter(
        RenderConfig(office_enabled=True, soffice_path="/opt/lo/soffice", office_timeout_s=42)
    )
    assert isinstance(enabled, LibreOfficeConverter)
    assert (enabled.soffice_path, enabled.timeout_s) == ("/opt/lo/soffice", 42)


def test_successful_conversion_command_and_profile() -> None:
    runner = FakeRunner()
    converter = LibreOfficeConverter("soffice", 120, runner=runner)
    assert converter.convert_to_pdf(b"docx-bytes", DocumentKind.DOCX) == FAKE_PDF

    [(command, timeout_s, cwd)] = runner.calls
    assert timeout_s == 120
    assert command[0] == "soffice"
    for flag in ("--headless", "--norestore", "--nolockcheck", "--nodefault", "--invisible"):
        assert flag in command
    assert command[command.index("--convert-to") + 1] == "pdf"
    [profile_arg] = [a for a in command if a.startswith("-env:UserInstallation=file:")]
    assert "profile" in profile_arg
    # stała nazwa wejścia z rozszerzeniem rodzaju wykrytego
    assert command[-1].endswith("input.docx") and runner.seen_inputs == ["input.docx"]
    # profil z wyłączonymi makrami istniał w chwili uruchomienia
    assert runner.seen_profile is not None
    assert '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value>' in (
        runner.seen_profile
    )
    assert "BlockUntrustedRefererLinks" in runner.seen_profile
    assert not cwd.exists()  # katalog tymczasowy usunięty


TIMEOUT, FAILED = IssueCode.OFFICE_CONVERSION_TIMEOUT, IssueCode.OFFICE_CONVERSION_FAILED


@pytest.mark.parametrize(
    ("runner", "code", "detail"),
    [
        (FakeRunner(exc=subprocess.TimeoutExpired("soffice", 1)), TIMEOUT, "timeout=1s"),
        (FakeRunner(exc=FileNotFoundError()), FAILED, "soffice not found"),
        (FakeRunner(code=77), FAILED, "exitcode=77"),
        (FakeRunner(write_pdf=False), FAILED, "no output pdf"),
    ],
)
def test_conversion_failures_clean_up(runner: FakeRunner, code: IssueCode, detail: str) -> None:
    converter = LibreOfficeConverter("soffice", 1, runner=runner)
    with pytest.raises(RenderError) as info:
        converter.convert_to_pdf(b"x", DocumentKind.ODS)
    assert (info.value.code, info.value.detail) == (code, detail)
    [(_, _, cwd)] = runner.calls
    assert not cwd.exists()


def test_non_office_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="biurowym"):
        LibreOfficeConverter("soffice", 1, runner=FakeRunner()).convert_to_pdf(
            b"x", DocumentKind.PDF
        )


def test_run_with_timeout_kills_slow_process(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_timeout([sys.executable, "-c", "import time; time.sleep(60)"], 0.5, tmp_path)
    assert time.monotonic() - start < 20


def test_run_with_timeout_returns_exit_code(tmp_path: Path) -> None:
    assert run_with_timeout([sys.executable, "-c", "raise SystemExit(3)"], 30, tmp_path) == 3


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice (soffice) niedostępny")
def test_libreoffice_integration() -> None:  # pragma: no cover - zależne od środowiska
    pdf = LibreOfficeConverter("soffice", 120).convert_to_pdf(
        make_minimal_docx(), DocumentKind.DOCX
    )
    assert pdf.startswith(b"%PDF-")
