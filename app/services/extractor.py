"""Turn an uploaded PDF or DOCX into page text. Plain code, no LLM.

Every failure raises an AppError with a fixed code, so the API can say exactly what is wrong.
Nothing is written to disk: the file is parsed from memory and only its text is kept.
"""
import io
import logging
import re
import zipfile
from dataclasses import dataclass

from app.core.exceptions import (
    AppError,
    PayloadTooLargeError,
    UnprocessableError,
    UnsupportedMediaError,
)
from app.repositories.document_repo import PageText

logger = logging.getLogger("nda_review_api")

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_TEXT_CHARS = 1_000_000
MIN_TEXT_CHARS = 20
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # guards against zip bombs

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ExtractedDocument:
    file_name: str
    file_type: str  # "pdf" | "docx"
    pages: list[PageText]
    page_count: int | None  # None for DOCX read directly (Word files have no fixed pages)
    warnings: tuple[str, ...] = ()


def clean_file_name(raw: str) -> str:
    """Display-safe file name: no folders, no control characters, bounded length."""
    name = _CONTROL_CHARS.sub("", (raw or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:200] or "document"


def extract_document(raw_file_name: str, data: bytes, docx_engine: str = "python") -> ExtractedDocument:
    """`docx_engine`: "python" (python-docx, no pages) or "libreoffice" (DOCX -> PDF first, so pages exist)."""
    file_name = clean_file_name(raw_file_name)
    extension = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""

    if extension not in ("pdf", "docx"):
        raise UnsupportedMediaError(
            "UNSUPPORTED_FILE_TYPE", "Only PDF and DOCX files are supported.", {"file_name": file_name}
        )
    if not data:
        raise UnprocessableError("FILE_EMPTY", "The uploaded file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise PayloadTooLargeError("FILE_TOO_LARGE", "The file is larger than the 10 MB limit.")

    sniffed = "pdf" if data.startswith(_PDF_MAGIC) else "docx" if data.startswith(_ZIP_MAGIC) else None
    if sniffed != extension:
        raise UnsupportedMediaError(
            "UNSUPPORTED_FILE_TYPE",
            f"The file content does not look like a real {extension.upper()} file. "
            "Only text-based PDF and DOCX files are supported.",
        )

    warnings: list[str] = []
    try:
        if extension == "pdf":
            pages = _read_pdf(data)
        else:
            pages = None
            if docx_engine == "libreoffice":
                pages = _read_docx_via_libreoffice(data, warnings)
            if pages is None:
                pages = _read_docx(data)
    except AppError:
        raise
    except Exception as exc:  # corrupt, encrypted or malformed: report the type only (messages can echo content)
        logger.error("Could not read %s upload: %s", extension, type(exc).__name__)
        raise UnprocessableError(
            "FILE_UNREADABLE",
            f"The {extension.upper()} could not be read. It may be corrupted or password-protected.",
        ) from None

    pages = [PageText(page=p.page, text=_tidy(p.text)) for p in pages]
    total = sum(len(p.text) for p in pages)
    if total < MIN_TEXT_CHARS:
        raise UnprocessableError(
            "NO_EXTRACTABLE_TEXT",
            "No readable text was found. Scanned or image-only documents are not supported (there is no OCR in v1).",
        )
    if total > MAX_TEXT_CHARS:
        raise UnprocessableError("DOCUMENT_TOO_LONG", "The document has too much text for this prototype.")

    return ExtractedDocument(
        file_name=file_name,
        file_type=extension,
        pages=pages,
        page_count=len(pages) if pages and pages[0].page is not None else None,
        warnings=tuple(warnings),
    )


def _tidy(text: str) -> str:
    """Trim trailing spaces and collapse runs of blank lines. Keeps line breaks (headings rely on them)."""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _read_pdf(data: bytes) -> list[PageText]:
    try:
        import pdfplumber
    except ImportError:
        raise AppError(503, "EXTRACTOR_UNAVAILABLE", "PDF support is not installed. Run: pip install -r requirements.txt") from None

    pages: list[PageText] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise UnprocessableError(
                "DOCUMENT_TOO_LONG", f"The PDF has more than {MAX_PDF_PAGES} pages, which is more than this prototype handles."
            )
        for number, page in enumerate(pdf.pages, start=1):
            pages.append(PageText(page=number, text=page.extract_text() or ""))
    return pages


def _check_docx_archive(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if "word/document.xml" not in archive.namelist():
            raise UnprocessableError("FILE_UNREADABLE", "The file is a ZIP archive but not a Word (DOCX) document.")
        if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise UnprocessableError("FILE_UNREADABLE", "The DOCX expands to an unreasonable size and was rejected.")


def _read_docx(data: bytes) -> list[PageText]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise AppError(503, "EXTRACTOR_UNAVAILABLE", "DOCX support is not installed. Run: pip install -r requirements.txt") from None

    _check_docx_archive(data)
    document = Document(io.BytesIO(data))
    lines: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = []
                for cell in row.cells:
                    text = " ".join(cell.text.split())
                    if text and (not cells or cells[-1] != text):  # merged cells repeat their text
                        cells.append(text)
                if cells:
                    lines.append(" | ".join(cells))
    return [PageText(page=None, text="\n".join(lines))]


def _read_docx_via_libreoffice(data: bytes, warnings: list[str]) -> list[PageText] | None:
    """DOCX -> PDF -> pages. Returns None (caller falls back to python-docx) if LibreOffice cannot do it."""
    from app.services.docx_converter import ConversionUnavailable, convert_docx_to_pdf

    _check_docx_archive(data)  # validate before handing the file to another program
    try:
        pdf = convert_docx_to_pdf(data)
    except ConversionUnavailable as exc:
        logger.warning("LibreOffice conversion skipped: %s", exc)
        warnings.append("LibreOffice was not used for this DOCX (" + str(exc) + "), so page numbers are unavailable.")
        return None
    return _read_pdf(pdf)
