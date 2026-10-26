"""Optional DOCX -> PDF conversion with LibreOffice (headless), so Word files get real page numbers.

Used only when DOCX_ENGINE=libreoffice. Any problem raises ConversionUnavailable and the caller
falls back to python-docx, so a missing LibreOffice never blocks an upload.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CONVERSION_TIMEOUT_SECONDS = 60
MAX_PDF_BYTES = 50 * 1024 * 1024


class ConversionUnavailable(Exception):
    """LibreOffice is missing, timed out, or could not convert the file."""


def find_libreoffice() -> str | None:
    return os.getenv("LIBREOFFICE_PATH") or shutil.which("soffice") or shutil.which("libreoffice")


def convert_docx_to_pdf(data: bytes) -> bytes:
    executable = find_libreoffice()
    if not executable:
        raise ConversionUnavailable("LibreOffice not found; set LIBREOFFICE_PATH")

    # Every conversion gets its own folder and LibreOffice profile (parallel-safe, nothing left behind).
    with tempfile.TemporaryDirectory(prefix="nda-conversion-") as folder:
        work = Path(folder).resolve()
        source, out_dir, profile = work / "source.docx", work / "output", work / "profile"
        out_dir.mkdir()
        source.write_bytes(data)
        command = [
            executable, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--nologo", "--nodefault",
            "--nofirststartwizard", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(out_dir), str(source),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=CONVERSION_TIMEOUT_SECONDS, check=False, shell=False)
        except subprocess.TimeoutExpired:
            raise ConversionUnavailable("conversion timed out") from None
        except OSError:
            raise ConversionUnavailable("could not start LibreOffice") from None

        pdf_path = out_dir / "source.pdf"
        if result.returncode != 0 or not pdf_path.is_file():
            raise ConversionUnavailable("conversion failed")
        if pdf_path.stat().st_size > MAX_PDF_BYTES:
            raise ConversionUnavailable("converted PDF is too large")
        pdf = pdf_path.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise ConversionUnavailable("converter did not produce a PDF")
        return pdf
