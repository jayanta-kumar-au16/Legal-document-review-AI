"""Optional on-disk copy of uploaded documents (set DOCUMENT_STORAGE_DIR to switch it on).

Layout (one folder per document):
    <dir>/<document_id>/original.pdf|docx   the uploaded file
    <dir>/<document_id>/extracted.json      the extracted text (reloaded at startup)
    <dir>/<document_id>/clauses_<method>.json   the last clause extraction result

Folders are written under a temporary name and renamed when complete, so a crash never leaves
a half-written document behind. Files that cannot be read at startup are skipped.
"""
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

from app.core.exceptions import AppError
from app.repositories.document_repo import StoredDocument

logger = logging.getLogger("nda_review_api")
_SAFE_ID = re.compile(r"^doc_[0-9a-f]{12}$")


class DiskDocumentStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, document: StoredDocument, original: bytes) -> None:
        if not _SAFE_ID.match(document.document_id):
            raise ValueError("unsafe document id")
        staging = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))
            (staging / f"original.{document.file_type or 'bin'}").write_bytes(original)
            (staging / "extracted.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")
            staging.rename(self.root / document.document_id)
            staging = None
        except OSError:
            logger.error("Could not save document %s to disk", document.document_id)
            raise AppError(500, "STORAGE_FAILURE", "Could not save the document. Please retry.") from None
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def save_clauses(self, document_id: str, method: str, result: dict) -> None:
        folder = self.root / document_id
        if not _SAFE_ID.match(document_id) or not folder.is_dir():
            return
        temp = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, prefix=".clauses-", suffix=".json", delete=False) as out:
                temp = Path(out.name)
                json.dump(result, out, ensure_ascii=False, indent=2)
            temp.replace(folder / f"clauses_{method}.json")
            temp = None
        except OSError:
            logger.error("Could not save clause result for %s", document_id)
            raise AppError(500, "STORAGE_FAILURE", "Could not save the clause result. Please retry.") from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def load_all(self) -> list[StoredDocument]:
        documents = []
        if not self.root.is_dir():
            return documents
        for folder in sorted(self.root.iterdir()):
            path = folder / "extracted.json"
            if not (folder.is_dir() and _SAFE_ID.match(folder.name) and path.is_file()):
                continue
            try:
                document = StoredDocument.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Skipping unreadable stored document %s", folder.name)
                continue
            if document.document_id == folder.name:
                documents.append(document)
        return documents
