"""Konwersja DOC/DOCX/ODS/XLSX do PDF za interfejsem ``OfficeConverter``.

Domyślnie konwersja jest **wyłączona** (``VISION_OFFICE_ENABLED=false``): LibreOffice (MPL 2.0)
czeka na decyzję mentorów. Przy wyłączonej konwersji dokumenty biurowe nie przechodzą przez kanał
wizualny, a renderer zwraca czytelny błąd ``OFFICE_CONVERSION_DISABLED`` zamiast awarii.

``LibreOfficeConverter`` (gdy włączony):
- uruchamia ``soffice --headless --convert-to pdf`` jako niezmodyfikowany proces zewnętrzny,
- dla każdego żądania tworzy katalog tymczasowy (profil, wejście, wyjście) usuwany w ``finally``,
- w świeżym profilu wyłącza wykonywanie makr i blokuje niezaufane linki zewnętrzne,
- egzekwuje limit czasu i po jego przekroczeniu zabija całe drzewo procesów,
- nie przekazuje wyjścia LibreOffice do logów (mogłoby zawierać dane dokumentu).
"""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import DocumentKind

Runner = Callable[[list[str], float, Path], int]
"""Uruchamia komendę w katalogu ``cwd`` z limitem czasu; zwraca kod wyjścia.
Po przekroczeniu czasu zgłasza ``subprocess.TimeoutExpired`` (proces musi być już zabity)."""

INPUT_STEM = "input"
"""Stała nazwa pliku wejściowego: oryginalna nazwa (mogąca zawierać treść) nie trafia dalej."""

_KILL_WAIT_S = 10.0

_PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
  </item>
</oor:items>
"""


class OfficeConverter(Protocol):
    def convert_to_pdf(self, data: bytes, kind: DocumentKind) -> bytes: ...

    def versions(self) -> dict[str, str]: ...


class DisabledOfficeConverter:
    """Konwersja wyłączona flagą konfiguracyjną: zawsze czytelny błąd, nigdy awaria."""

    def convert_to_pdf(self, data: bytes, kind: DocumentKind) -> bytes:
        raise RenderError(
            IssueCode.OFFICE_CONVERSION_DISABLED,
            f"kind={kind.value}: office conversion disabled (VISION_OFFICE_ENABLED=false)",
        )

    def versions(self) -> dict[str, str]:
        return {}


class LibreOfficeConverter:
    def __init__(self, soffice_path: str, timeout_s: float, runner: Runner | None = None) -> None:
        self.soffice_path = soffice_path
        self.timeout_s = timeout_s
        self._runner = runner if runner is not None else run_with_timeout

    def versions(self) -> dict[str, str]:
        # Wersja nie jest odpytywana (wymagałaby uruchomienia LibreOffice); w manifeście #12
        # podawana jest wersja pakietu z obrazu workera.
        return {"libreoffice": "external"}

    def build_command(self, profile_dir: Path, source: Path, out_dir: Path) -> list[str]:
        return [
            self.soffice_path,
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nologo",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]

    def convert_to_pdf(self, data: bytes, kind: DocumentKind) -> bytes:
        if not kind.is_office:
            raise ValueError(f"nie jest dokumentem biurowym: {kind}")
        tmp = Path(tempfile.mkdtemp(prefix="vision-office-"))
        try:
            profile, in_dir, out_dir = tmp / "profile", tmp / "in", tmp / "out"
            for d in (profile / "user", in_dir, out_dir):
                d.mkdir(parents=True)
            (profile / "user" / "registrymodifications.xcu").write_text(
                _PROFILE_XCU, encoding="utf-8"
            )
            source = in_dir / f"{INPUT_STEM}.{kind.value}"
            source.write_bytes(data)
            command = self.build_command(profile, source, out_dir)
            try:
                code = self._runner(command, self.timeout_s, tmp)
            except subprocess.TimeoutExpired:
                raise RenderError(
                    IssueCode.OFFICE_CONVERSION_TIMEOUT, f"timeout={self.timeout_s:g}s"
                ) from None
            except FileNotFoundError:
                raise RenderError(IssueCode.OFFICE_CONVERSION_FAILED, "soffice not found") from None
            if code != 0:
                raise RenderError(IssueCode.OFFICE_CONVERSION_FAILED, f"exitcode={code}")
            result = out_dir / f"{INPUT_STEM}.pdf"
            if not result.is_file():
                raise RenderError(IssueCode.OFFICE_CONVERSION_FAILED, "no output pdf")
            return result.read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def default_office_converter(cfg: RenderConfig) -> OfficeConverter:
    if cfg.office_enabled:
        return LibreOfficeConverter(cfg.soffice_path, cfg.office_timeout_s)
    return DisabledOfficeConverter()


def run_with_timeout(command: list[str], timeout_s: float, cwd: Path) -> int:
    """Proces bez wejścia i bez przechwytywania wyjścia; po limicie czasu zabija drzewo procesów."""
    env = dict(os.environ, HOME=str(cwd), TMPDIR=str(cwd), TMP=str(cwd), TEMP=str(cwd))
    devnull = subprocess.DEVNULL
    if sys.platform == "win32":
        process = subprocess.Popen(  # noqa: S603 - lista argumentów, bez powłoki
            command, cwd=cwd, env=env, stdin=devnull, stdout=devnull, stderr=devnull,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )  # fmt: skip
    else:
        # własna grupa procesów: killpg zabija też procesy potomne (soffice -> soffice.bin)
        process = subprocess.Popen(  # noqa: S603 - lista argumentów, bez powłoki
            command, cwd=cwd, env=env, stdin=devnull, stdout=devnull, stderr=devnull,
            start_new_session=True,
        )  # fmt: skip
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        raise


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - stała komenda systemowa
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_KILL_WAIT_S)
