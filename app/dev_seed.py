"""Fictional sample NDA used to try the review endpoints before the upload endpoint exists."""
from app.repositories.document_repo import InMemoryDocumentRepository, PageText, StoredDocument

SAMPLE_DOCUMENT_ID = "doc_sample_nda"

_PAGE_1 = """NON-DISCLOSURE AGREEMENT
Fictional sample for software demonstration only
This Agreement is entered into between Company A ("Disclosing Party") and Company B ("Receiving Party").
Clause 1 — Purpose
The Receiving Party may use confidential information solely to evaluate a potential business relationship with the Disclosing Party.
Clause 2 — Confidentiality
The Receiving Party shall protect all confidential information and shall not disclose it to third parties without prior written consent.
Clause 3 — Duration
The obligations under this agreement shall continue for one year from the date of disclosure.
"""

_PAGE_2 = """Clause 4 — Return of Information
The Receiving Party shall return confidential information upon written request from the Disclosing Party.
Clause 5 — Governing Law
This agreement shall be governed by applicable law.
"""


def seed_sample_document(documents: InMemoryDocumentRepository) -> None:
    documents.add(
        StoredDocument(
            document_id=SAMPLE_DOCUMENT_ID,
            file_name="sample_nda.pdf",
            extraction_status="success",
            pages=[PageText(page=1, text=_PAGE_1), PageText(page=2, text=_PAGE_2)],
        )
    )
