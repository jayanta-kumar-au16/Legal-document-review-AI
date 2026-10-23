import re
import unicodedata
from functools import lru_cache

_PUNCT_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)
_SENTENCE_BREAK = re.compile(r"(?<=[.;!?])\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace and unify curly quotes / dashes so PDF text compares reliably."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT_MAP)
    return re.sub(r"\s+", " ", text).strip()


def excerpt_in_text(excerpt: str, source: str) -> bool:
    """True when `excerpt` appears verbatim in `source` (after whitespace/quote normalization)."""
    needle = normalize_text(excerpt)
    return bool(needle) and needle in normalize_text(source)


def sentences_with_offsets(text: str) -> list[tuple[str, int]]:
    """Split into (sentence, start_offset). Offsets index into `text`."""
    out: list[tuple[str, int]] = []
    pos = 0
    for match in _SENTENCE_BREAK.finditer(text):
        out.append((text[pos : match.start()], pos))
        pos = match.end()
    if pos < len(text):
        out.append((text[pos:], pos))
    return out


def sentence_at(text: str, position: int) -> tuple[str, int]:
    """The sentence containing character `position`, with its start offset."""
    sentences = sentences_with_offsets(text)
    if not sentences:
        return text, 0
    chosen = sentences[0]
    for sentence, start in sentences:
        if start <= position:
            chosen = (sentence, start)
        else:
            break
    return chosen


def first_sentence(text: str, limit: int = 200) -> str:
    sentences = sentences_with_offsets(text)
    sentence = sentences[0][0] if sentences else text
    return sentence if len(sentence) <= limit else sentence[: limit - 3].rstrip() + "..."


@lru_cache(maxsize=256)
def _term_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


def find_term(text: str, terms: list[str]) -> re.Match[str] | None:
    """First whole-word, case-insensitive match of any term, or None."""
    if not terms:
        return None
    return _term_pattern(tuple(terms)).search(text)
