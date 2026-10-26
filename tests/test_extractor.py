import io
import unittest
import zipfile

from app.core.exceptions import AppError, NotFoundError
from app.services import extractor
from app.services.extractor import clean_file_name, extract_document
from tests.file_factory import EXPECTED_SAMPLE_OUTCOMES, SAMPLE_PAGE_1, SAMPLE_PAGE_2, make_docx, make_pdf
from tests.helpers import make_container, outcomes


def error_code(fn, *args) -> tuple[int, str]:
    with unittest.TestCase().assertRaises(AppError) as ctx:
        fn(*args)
    return ctx.exception.status_code, ctx.exception.code


class PdfTests(unittest.TestCase):
    def test_pages_and_text_are_extracted(self):
        result = extract_document("sample.pdf", make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2]))
        self.assertEqual((result.file_type, result.page_count), ("pdf", 2))
        self.assertEqual([p.page for p in result.pages], [1, 2])
        self.assertIn("Clause 3 — Duration", result.pages[0].text)  # the em dash survives
        self.assertIn("Clause 5 — Governing Law", result.pages[1].text)

    def test_uploaded_pdf_gives_the_same_review_as_the_seeded_sample(self):
        container = make_container()
        uploaded = container.document_service.upload("sample.pdf", make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2]))
        self.assertEqual((uploaded.extraction_status, uploaded.page_count, uploaded.section_count), ("success", 2, 5))
        report = container.review_service.create_review(uploaded.document_id)
        self.assertEqual(outcomes(report), EXPECTED_SAMPLE_OUTCOMES)
        r4 = next(r for r in report.rule_results if r.rule_id == "R4")
        self.assertEqual((r4.evidence.source.page, r4.evidence.source.section, r4.evidence.verified), (2, "Clause 4", True))

    def test_page_without_text_is_fine_if_other_pages_have_text(self):
        result = extract_document("a.pdf", make_pdf([SAMPLE_PAGE_1, " "]))
        self.assertEqual(len(result.pages), 2)

    def test_scanned_or_blank_pdf_is_rejected(self):
        self.assertEqual(error_code(extract_document, "scan.pdf", make_pdf([" "])), (422, "NO_EXTRACTABLE_TEXT"))

    def test_corrupt_pdf_is_rejected(self):
        self.assertEqual(error_code(extract_document, "bad.pdf", b"%PDF-1.4\nthis is not really a pdf"), (422, "FILE_UNREADABLE"))

    def test_too_many_pages(self):
        pdf = make_pdf([f"Clause {i} — Purpose\nSome text on this page." for i in range(extractor.MAX_PDF_PAGES + 1)])
        self.assertEqual(error_code(extract_document, "long.pdf", pdf), (422, "DOCUMENT_TOO_LONG"))


class DocxTests(unittest.TestCase):
    def test_paragraphs_and_tables(self):
        data = make_docx(SAMPLE_PAGE_1.split("\n"), table_rows=[["Party", "Role"], ["Company A", "Disclosing Party"]])
        result = extract_document("nda.docx", data)
        self.assertEqual((result.file_type, result.page_count, len(result.pages)), ("docx", None, 1))
        text = result.pages[0].text
        self.assertIn("Clause 2 — Confidentiality", text)
        self.assertIn("Company A | Disclosing Party", text)

    def test_uploaded_docx_gives_the_same_review(self):
        container = make_container()
        data = make_docx((SAMPLE_PAGE_1 + "\n" + SAMPLE_PAGE_2).split("\n"))
        uploaded = container.document_service.upload("nda.docx", data)
        self.assertEqual((uploaded.page_count, uploaded.section_count), (None, 5))
        report = container.review_service.create_review(uploaded.document_id)
        self.assertEqual(outcomes(report), EXPECTED_SAMPLE_OUTCOMES)
        self.assertIsNone(report.rule_results[0].evidence.source.page)  # DOCX has no pages

    def test_zip_that_is_not_a_word_document(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("hello.txt", "hi")
        self.assertEqual(error_code(extract_document, "x.docx", buffer.getvalue()), (422, "FILE_UNREADABLE"))

    def test_zip_bomb_guard(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<x/>" * 1000)
        original = extractor.MAX_DOCX_UNCOMPRESSED_BYTES
        extractor.MAX_DOCX_UNCOMPRESSED_BYTES = 1000
        try:
            self.assertEqual(error_code(extract_document, "x.docx", buffer.getvalue()), (422, "FILE_UNREADABLE"))
        finally:
            extractor.MAX_DOCX_UNCOMPRESSED_BYTES = original

    def test_empty_docx_has_no_text(self):
        self.assertEqual(error_code(extract_document, "e.docx", make_docx([""])), (422, "NO_EXTRACTABLE_TEXT"))


class FileValidationTests(unittest.TestCase):
    def test_unsupported_extensions(self):
        for name in ("notes.txt", "old.doc", "sheet.xlsx", "noextension", "archive.pdf.exe"):
            self.assertEqual(error_code(extract_document, name, b"%PDF-1.4"), (415, "UNSUPPORTED_FILE_TYPE"), name)

    def test_empty_file(self):
        self.assertEqual(error_code(extract_document, "a.pdf", b""), (422, "FILE_EMPTY"))

    def test_too_large(self):
        data = b"%PDF-" + b"0" * extractor.MAX_FILE_BYTES
        self.assertEqual(error_code(extract_document, "big.pdf", data), (413, "FILE_TOO_LARGE"))

    def test_content_must_match_the_extension(self):
        self.assertEqual(error_code(extract_document, "fake.pdf", make_docx(["Some text here for the test."])), (415, "UNSUPPORTED_FILE_TYPE"))
        self.assertEqual(error_code(extract_document, "fake.docx", make_pdf(["Some text here for the test."])), (415, "UNSUPPORTED_FILE_TYPE"))
        self.assertEqual(error_code(extract_document, "fake.pdf", b"just some plain text, not a pdf"), (415, "UNSUPPORTED_FILE_TYPE"))

    def test_extension_is_case_insensitive(self):
        self.assertEqual(extract_document("NDA.PDF", make_pdf([SAMPLE_PAGE_1])).file_type, "pdf")

    def test_file_names_are_cleaned(self):
        self.assertEqual(clean_file_name("..\\..\\secret\\nda.pdf"), "nda.pdf")
        self.assertEqual(clean_file_name("/etc/passwd/../nda.pdf"), "nda.pdf")
        self.assertEqual(clean_file_name("a\x00b\nc.pdf"), "abc.pdf")
        self.assertEqual(clean_file_name(""), "document")
        self.assertEqual(len(clean_file_name("x" * 500 + ".pdf")), 200)


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = make_container().document_service

    def test_text_preview(self):
        uploaded = self.service.upload("sample.pdf", make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2]))
        text = self.service.get_text(uploaded.document_id)
        self.assertEqual(text.file_name, "sample.pdf")
        self.assertEqual([p.page for p in text.pages], [1, 2])
        self.assertEqual([(s.label, s.title, s.page) for s in text.sections],
                         [("Clause 1", "Purpose", 1), ("Clause 2", "Confidentiality", 1), ("Clause 3", "Duration", 1),
                          ("Clause 4", "Return of Information", 2), ("Clause 5", "Governing Law", 2)])

    def test_seeded_sample_has_a_preview_too(self):
        self.assertEqual(len(self.service.get_text("doc_sample_nda").pages), 2)

    def test_unknown_document(self):
        with self.assertRaises(NotFoundError) as ctx:
            self.service.get_text("doc_missing")
        self.assertEqual(ctx.exception.code, "DOCUMENT_NOT_FOUND")

    def test_each_upload_gets_its_own_id_and_failed_uploads_store_nothing(self):
        a = self.service.upload("a.pdf", make_pdf([SAMPLE_PAGE_1]))
        b = self.service.upload("a.pdf", make_pdf([SAMPLE_PAGE_1]))
        self.assertNotEqual(a.document_id, b.document_id)
        with self.assertRaises(AppError):
            self.service.upload("bad.pdf", b"%PDF-1.4 broken")
        self.assertEqual(len(self.service._documents._items), 2 + 2)  # two uploads + two seeded samples


if __name__ == "__main__":
    unittest.main()
