import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.container import build_container
from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.docx_converter import find_libreoffice
from tests.fake_openai import FakeOpenAI, clauses as clauses_reply, completion
from tests.file_factory import SAMPLE_PAGE_1, SAMPLE_PAGE_2, make_docx, make_pdf
from tests.helpers import make_container

HAVE_SOFFICE = bool(find_libreoffice())


def upload_sample(container):
    return container.document_service.upload("nda.pdf", make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2])).document_id


class ManualClauseTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        self.service = self.container.document_service

    def test_clauses_from_headings(self):
        result = self.service.get_clauses(upload_sample(self.container))
        self.assertEqual((result.method, result.clause_count), ("manual", 5))
        self.assertEqual([c.clause_type for c in result.clauses],
                         ["purpose", "confidentiality", "duration", "return_of_information", "governing_law"])
        fourth = result.clauses[3]
        self.assertEqual((fourth.status, fourth.source.page, fourth.source.section, fourth.summary), ("found", 2, "Clause 4", None))
        self.assertTrue(fourth.evidence)

    def test_docx_clauses_have_no_page(self):
        docx = make_docx((SAMPLE_PAGE_1 + "\n" + SAMPLE_PAGE_2).split("\n"))
        document_id = self.service.upload("nda.docx", docx).document_id
        result = self.service.get_clauses(document_id)
        self.assertEqual(result.clause_count, 5)
        self.assertIsNone(result.clauses[0].source.page)

    def test_no_headings_gives_a_warning_not_an_error(self):
        document_id = self.service.upload("plain.pdf", make_pdf(["Just a paragraph of text with no clause headings at all."])).document_id
        result = self.service.get_clauses(document_id)
        self.assertEqual(result.clause_count, 0)
        self.assertTrue(any("No 'Clause N" in w for w in result.warnings))

    def test_seeded_sample_works_and_unknown_document_404s(self):
        self.assertEqual(self.service.get_clauses("doc_sample_nda").clause_count, 5)
        with self.assertRaises(AppError) as ctx:
            self.service.get_clauses("doc_missing")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (404, "DOCUMENT_NOT_FOUND"))

    def test_bad_method(self):
        with self.assertRaises(AppError) as ctx:
            self.service.get_clauses("doc_sample_nda", "magic")
        self.assertEqual(ctx.exception.code, "INVALID_METHOD")


class LlmClauseTests(unittest.TestCase):
    def build(self, url):
        container = build_container(Settings(llm_provider="openai", openai_api_key="sk-test", openai_base_url=url,
                                             openai_timeout_seconds=5, openai_max_retries=0))
        return container, upload_sample(container)

    def run_with(self, *reply):
        server = FakeOpenAI()
        server.reply(*reply)
        self.addCleanup(server.close)
        container, document_id = self.build(server.url)
        return server, container.document_service, document_id

    def test_verified_clauses_are_returned_with_pages_and_summary(self):
        text = "The Receiving Party shall protect all confidential information and shall not disclose it to third parties without prior written consent."
        server, service, document_id = self.run_with(clauses_reply([
            {"number": "2", "clause_type": "confidentiality", "clause_content": text, "summary": "No disclosure."}]))
        result = service.get_clauses(document_id, "llm")
        self.assertEqual((result.method, result.clause_count), ("llm", 1))
        clause = result.clauses[0]
        self.assertEqual((clause.clause_type, clause.summary, clause.source.page, clause.source.section),
                         ("confidentiality", "No disclosure.", 1, "Clause 2"))
        body = server.requests[0]["body"]
        self.assertEqual((body["temperature"], body["response_format"]["json_schema"]["strict"]), (0, True))

    def test_invented_text_is_rejected(self):
        _, service, document_id = self.run_with(clauses_reply([
            {"number": "1", "clause_type": "purpose", "clause_content": "This text is not in the document.", "summary": "x"}]))
        with self.assertRaises(AppError) as ctx:
            service.get_clauses(document_id, "llm")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (502, "UNVERIFIED_CLAUSE"))

    def test_model_problems_become_clear_errors(self):
        cases = [
            (completion("{}", finish_reason="length"), 502, "LLM_OUTPUT_TRUNCATED"),
            (completion("not json"), 502, "INVALID_MODEL_OUTPUT"),
            ((401, {}), 502, "LLM_AUTH_FAILED"),
            ((429, {}), 503, "LLM_RATE_LIMITED"),
            (clauses_reply([{"number": None, "clause_type": "Bad Type", "clause_content": "Clause 1", "summary": ""}]), 502, "INVALID_CLAUSE_TYPE"),
        ]
        for reply, status, code in cases:
            with self.subTest(code=code):
                _, service, document_id = self.run_with(reply)
                with self.assertRaises(AppError) as ctx:
                    service.get_clauses(document_id, "llm")
                self.assertEqual((ctx.exception.status_code, ctx.exception.code), (status, code))

    def test_service_not_reachable(self):
        container, document_id = self.build("http://127.0.0.1:9/v1")
        with self.assertRaises(AppError) as ctx:
            container.document_service.get_clauses(document_id, "llm")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (503, "LLM_UNAVAILABLE"))

    def test_llm_not_configured(self):
        service = make_container().document_service
        with self.assertRaises(AppError) as ctx:
            service.get_clauses("doc_sample_nda", "llm")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (503, "LLM_NOT_CONFIGURED"))

    def test_document_text_is_sent_as_data(self):
        server, service, document_id = self.run_with(clauses_reply([]))
        service.get_clauses(document_id, "llm")
        messages = server.requests[0]["body"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("untrusted data", messages[0]["content"])
        self.assertIn('"pages"', messages[1]["content"])


class DiskStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def container(self):
        return build_container(Settings(document_storage_dir=str(self.folder)))

    def test_documents_survive_a_restart(self):
        first = self.container()
        document_id = upload_sample(first)
        saved = self.folder / document_id
        self.assertTrue((saved / "original.pdf").read_bytes().startswith(b"%PDF-"))
        self.assertTrue((saved / "extracted.json").is_file())

        second = self.container()
        self.assertEqual(len(second.document_service.get_text(document_id).pages), 2)
        report = second.review_service.create_review(document_id)  # a reloaded document can be reviewed
        self.assertEqual(len(report.rule_results), 5)

    def test_clause_results_are_saved_per_method(self):
        container = self.container()
        document_id = upload_sample(container)
        container.document_service.get_clauses(document_id, "manual")
        saved = json.loads((self.folder / document_id / "clauses_manual.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["clause_count"], 5)
        self.assertFalse(list((self.folder / document_id).glob(".clauses-*")))

    def test_failed_upload_leaves_no_folder_and_corrupt_files_are_skipped(self):
        container = self.container()
        with self.assertRaises(AppError):
            container.document_service.upload("bad.pdf", b"%PDF-1.4 broken")
        self.assertEqual([p for p in self.folder.iterdir()], [])
        broken = self.folder / "doc_000000000000"
        broken.mkdir()
        (broken / "extracted.json").write_text("{not json", encoding="utf-8")
        self.container()  # starts fine

    def test_nothing_is_written_when_storage_is_off(self):
        container = build_container(Settings())
        upload_sample(container)
        self.assertEqual(list(self.folder.iterdir()), [])


@unittest.skipUnless(HAVE_SOFFICE, "LibreOffice not installed")
class LibreOfficeTests(unittest.TestCase):
    def test_docx_gets_page_numbers(self):
        container = build_container(Settings(docx_engine="libreoffice"))
        docx = make_docx((SAMPLE_PAGE_1 + "\n" + SAMPLE_PAGE_2).split("\n"))
        uploaded = container.document_service.upload("nda.docx", docx)
        self.assertGreaterEqual(uploaded.page_count, 1)
        self.assertEqual(uploaded.section_count, 5)
        clauses = container.document_service.get_clauses(uploaded.document_id)
        self.assertEqual(clauses.clause_count, 5)
        self.assertEqual(clauses.clauses[0].source.page, 1)


class FallbackTests(unittest.TestCase):
    def test_missing_libreoffice_falls_back_to_python_docx_with_a_warning(self):
        import os
        old = os.environ.get("LIBREOFFICE_PATH")
        os.environ["LIBREOFFICE_PATH"] = "/nonexistent/soffice"
        try:
            container = build_container(Settings(docx_engine="libreoffice"))
            docx = make_docx((SAMPLE_PAGE_1 + "\n" + SAMPLE_PAGE_2).split("\n"))
            uploaded = container.document_service.upload("nda.docx", docx)
        finally:
            if old is None:
                os.environ.pop("LIBREOFFICE_PATH")
            else:
                os.environ["LIBREOFFICE_PATH"] = old
        self.assertIsNone(uploaded.page_count)
        self.assertEqual(uploaded.section_count, 5)
        self.assertTrue(uploaded.warnings and "LibreOffice" in uploaded.warnings[0])

    def test_unknown_engine_is_rejected_at_startup(self):
        with self.assertRaises(ValueError):
            build_container(Settings(docx_engine="word"))


if __name__ == "__main__":
    unittest.main()
