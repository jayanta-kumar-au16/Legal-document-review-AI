import unittest

from app.repositories.document_repo import PageText
from app.services.segmenter import segment_document
from app.utils.numbers import find_durations, words_to_int
from app.utils.text import excerpt_in_text, find_term, normalize_text


class DurationTests(unittest.TestCase):
    def months(self, text):
        return [d.months for d in find_durations(text)]

    def test_formats(self):
        self.assertEqual(self.months("continue for one year from disclosure"), [12])
        self.assertEqual(self.months("for 24 months"), [24])
        self.assertEqual(self.months("for two (2) years"), [24])
        self.assertEqual(self.months("twenty-four months"), [24])
        self.assertEqual(self.months("a 36-month period"), [36])
        self.assertEqual(self.months("Five Years"), [60])

    def test_no_duration(self):
        self.assertEqual(self.months("obligations survive indefinitely"), [])

    def test_words_to_int(self):
        self.assertEqual(words_to_int("thirty-six"), 36)
        self.assertIsNone(words_to_int("banana"))


class TextTests(unittest.TestCase):
    def test_excerpt_matching_ignores_whitespace_and_curly_quotes(self):
        source = "The Receiving Party\nshall  protect “Confidential Information”."
        self.assertTrue(excerpt_in_text('shall protect "Confidential Information".', source))

    def test_excerpt_not_in_text(self):
        self.assertFalse(excerpt_in_text("something else", "The Receiving Party shall protect."))
        self.assertFalse(excerpt_in_text("   ", "anything"))

    def test_find_term_is_whole_word(self):
        self.assertIsNone(find_term("Indiana law applies", ["India"]))
        self.assertIsNotNone(find_term("laws of India apply", ["India"]))

    def test_normalize(self):
        self.assertEqual(normalize_text("a \n\t b"), "a b")


class SegmenterTests(unittest.TestCase):
    def seg(self, text, page=1):
        return segment_document([PageText(page=page, text=text)])

    def test_keyword_and_numbered_headings(self):
        segments = self.seg("Clause 1 — Purpose\nText A.\nSection 2.1: Term\nText B.\n3. Governing Law\nText C.\nARTICLE IV. Liability\nText D.")
        self.assertEqual([(s.label, s.title) for s in segments],
                         [("Clause 1", "Purpose"), ("Section 2.1", "Term"), ("3", "Governing Law"), ("ARTICLE IV", "Liability")])

    def test_references_and_sentences_are_not_headings(self):
        segments = self.seg("Clause 1 — Purpose\nSection 5 of this Agreement shall apply.\n1. The Receiving Party shall comply.\nMore text.")
        self.assertEqual(len(segments), 1)
        self.assertIn("Section 5 of this Agreement", segments[0].body)

    def test_text_before_first_heading_is_skipped(self):
        segments = self.seg("NON-DISCLOSURE AGREEMENT\nBetween A and B\nClause 1 — Purpose\nBody")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].body, "Body")

    def test_clause_spanning_pages_reports_the_right_page(self):
        segments = segment_document([
            PageText(page=1, text="Clause 1 — Duration\nFirst sentence on page one."),
            PageText(page=2, text="Second sentence on page two."),
        ])
        body = segments[0].body
        self.assertEqual(segments[0].heading_page, 1)
        self.assertEqual(segments[0].page_at(body.index("First")), 1)
        self.assertEqual(segments[0].page_at(body.index("Second")), 2)

    def test_docx_style_pages_without_page_numbers(self):
        segments = segment_document([PageText(page=None, text="Clause 1 — Purpose\nBody text.")])
        self.assertIsNone(segments[0].heading_page)
        self.assertIsNone(segments[0].page_at(0))


if __name__ == "__main__":
    unittest.main()
