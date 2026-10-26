import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.container import build_container
from app.core.config import Settings
from app.core.exceptions import AppError
from app.services import clause_extractor
from app.services.docx_converter import find_libreoffice
from tests.file_factory import SAMPLE_PAGE_1, SAMPLE_PAGE_2, make_docx, make_pdf
from tests.helpers import make_container

HAVE_SOFFICE = bool(find_libreoffice())


class FakeOllama:
    """A tiny local server that answers /api/chat like Ollama does."""

    def __init__(self, reply):
        outer = self
        self.reply, self.requests = reply, []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status, body = outer.reply
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def model_reply(clauses, done_reason="stop"):
    return 200, {"done_reason": done_reason, "message": {"content": json.dumps({"clauses": clauses})}}


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
    def build(self, server_url):
        container = build_container(Settings(ollama_base_url=server_url, ollama_timeout_seconds=5))
        return container, upload_sample(container)

    def run_with(self, reply):
        server = FakeOllama(reply)
        self.addCleanup(server.close)
        container, document_id = self.build(server.url)
        return server, container.document_service, document_id

    def test_verified_clauses_are_returned_with_pages_and_summary(self):
        text = "The Receiving Party shall protect all confidential information and shall not disclose it to third parties without prior written consent."
        server, service, document_id = self.run_with(model_reply([
            {"number": "2", "clause_type": "confidentiality", "clause_content": text, "summary": "No disclosure."}]))
        result = service.get_clauses(document_id, "llm")
        self.assertEqual((result.method, result.clause_count), ("llm", 1))
        clause = result.clauses[0]
        self.assertEqual((clause.clause_type, clause.summary, clause.source.page, clause.source.section), ("confidentiality", "No disclosure.", 1, "Clause 2"))
        sent = server.requests[0]
        self.assertEqual((sent["options"]["temperature"], sent["stream"]), (0, False))

    def test_invented_text_is_rejected(self):
        _, service, document_id = self.run_with(model_reply([
            {"number": "1", "clause_type": "purpose", "clause_content": "This text is not in the document.", "summary": "x"}]))
        with self.assertRaises(AppError) as ctx:
            service.get_clauses(document_id, "llm")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (502, "UNVERIFIED_CLAUSE"))

    def test_model_problems_become_clear_errors(self):
        cases = [
            (model_reply([], done_reason="length"), "MODEL_OUTPUT_TRUNCATED"),
            ((200, {"message": {"content": "not json"}}), "INVALID_MODEL_OUTPUT"),
            ((404, {"error": "model not found"}), "OLLAMA_REQUEST_FAILED"),
            (model_reply([{"number": None, "clause_type": "Bad Type", "clause_content": "Clause 1", "summary": ""}]), "INVALID_CLAUSE_TYPE"),
        ]
        for reply, code in cases:
            with self.subTest(code=code):
                _, service, document_id = self.run_with(reply)
                with self.assertRaises(AppError) as ctx:
                    service.get_clauses(document_id, "llm")
                self.assertEqual(ctx.exception.code, code)

    def test_ollama_not_running(self):
        container, document_id = self.build("http://127.0.0.1:9")
        with self.assertRaises(AppError) as ctx:
            container.document_service.get_clauses(document_id, "llm")
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (503, "OLLAMA_UNAVAILABLE"))

    def test_input_is_not_followed_as_instructions_it_is_sent_as_data(self):
        server, service, document_id = self.run_with(model_reply([]))
        service.get_clauses(document_id, "llm")
        messages = server.requests[0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("untrusted data", messages[0]["content"])
        self.assertIn('"pages"', messages[1]["content"])

    def test_timeout(self):
        orig = clause_extractor.urllib.request.OpenerDirector.open

        def slow(self, *a, **k):
            raise TimeoutError()

        clause_extractor.urllib.request.OpenerDirector.open = slow
        try:
            container, document_id = self.build("http://127.0.0.1:9")
            with self.assertRaises(AppError) as ctx:
                container.document_service.get_clauses(document_id, "llm")
            self.assertEqual((ctx.exception.status_code, ctx.exception.code), (504, "OLLAMA_TIMEOUT"))
        finally:
            clause_extractor.urllib.request.OpenerDirector.open = orig


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
