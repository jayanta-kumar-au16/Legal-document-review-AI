"""Step 1: cut the extracted text into clause segments. Plain code, no LLM."""
import re
from dataclasses import dataclass

from app.repositories.document_repo import PageText

# "Clause 3 - Duration", "Section 2.1: Term", "ARTICLE IV. Governing Law", "Clause 5"
_KEYWORD_HEADING = re.compile(
    r"^(?P<label>(?i:clause|section|article)\s+(?:\d+|[IVXLC]+)(?:\.\d+)*)\b"
    r"(?P<sep>\s*[—–:.\-]\s*|\s+)?(?P<title>.*)$"
)
# "3. Duration", "4.1) Return of information"
_NUMBERED_HEADING = re.compile(r"^(?P<label>\d+(?:\.\d+)*)[.)]\s+(?P<title>[A-Z][^\n]*)$")

_MAX_TITLE_LEN = 80
_MAX_NUMBERED_TITLE_LEN = 60


@dataclass(frozen=True)
class Segment:
    index: int
    label: str  # "Clause 3" - used as the section reference
    title: str  # "Duration"
    heading_page: int | None
    body: str  # clause text without the heading line, whitespace-normalized
    line_starts: tuple[tuple[int, int | None], ...]  # (offset in body, page of that line)

    def page_at(self, offset: int) -> int | None:
        """Page on which the character at `offset` of `body` sits."""
        page = self.heading_page
        for start, line_page in self.line_starts:
            if start <= offset:
                page = line_page
            else:
                break
        return page


class _Builder:
    def __init__(self, index: int, label: str, title: str, page: int | None):
        self.index, self.label, self.title, self.page = index, label, title, page
        self._parts: list[str] = []
        self._starts: list[tuple[int, int | None]] = []
        self._length = 0

    def add(self, line: str, page: int | None) -> None:
        if self._parts:
            self._length += 1  # the joining space
        self._starts.append((self._length, page))
        self._parts.append(line)
        self._length += len(line)

    def build(self) -> Segment:
        return Segment(
            index=self.index,
            label=self.label,
            title=self.title,
            heading_page=self.page,
            body=" ".join(self._parts),
            line_starts=tuple(self._starts),
        )


def _match_heading(line: str) -> tuple[str, str] | None:
    """Return (label, title) if `line` looks like a clause heading, else None."""
    match = _KEYWORD_HEADING.match(line)
    if match:
        label = re.sub(r"\s+", " ", match.group("label"))
        title = match.group("title").strip()
        separator = match.group("sep") or ""
        if not title:
            return label, ""
        if len(title) > _MAX_TITLE_LEN:
            return None
        if not separator.strip():
            # "Section 5 of this Agreement shall apply..." is a reference, not a heading.
            if not title[0].isupper() or title.endswith("."):
                return None
        return label, title

    match = _NUMBERED_HEADING.match(line)
    if match:
        title = match.group("title").strip()
        if len(title) <= _MAX_NUMBERED_TITLE_LEN and not title.endswith((".", ";", ",", ":")):
            return match.group("label"), title
    return None


def segment_document(pages: list[PageText]) -> list[Segment]:
    """Split pages into clause segments. Text before the first heading (title, parties) is skipped."""
    segments: list[Segment] = []
    current: _Builder | None = None
    for page in pages:
        for raw_line in page.text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            heading = _match_heading(line)
            if heading:
                if current:
                    segments.append(current.build())
                current = _Builder(len(segments) + 1, heading[0], heading[1], page.page)
            elif current:
                current.add(line, page.page)
    if current:
        segments.append(current.build())
    return segments
