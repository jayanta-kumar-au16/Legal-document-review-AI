import re
from dataclasses import dataclass

_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}

_WORDS = "|".join(sorted([*_UNITS, *_TENS], key=len, reverse=True))
_COMPOUND = rf"(?:{'|'.join(_TENS)})[- ](?:{'|'.join(k for k, v in _UNITS.items() if v < 10)})"

# "one year", "24 months", "two (2) years", "twenty-four months", "24-month"
_DURATION = re.compile(
    rf"\b(?P<num>\d+|{_COMPOUND}|{_WORDS})\s*(?:\(\s*\d+\s*\)\s*)?[- ]?(?P<unit>year|month)s?\b",
    re.IGNORECASE,
)


def words_to_int(token: str) -> int | None:
    token = token.lower().strip()
    if token.isdigit():
        return int(token)
    if token in _UNITS:
        return _UNITS[token]
    if token in _TENS:
        return _TENS[token]
    parts = re.split(r"[-\s]", token)
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS and _UNITS[parts[1]] < 10:
        return _TENS[parts[0]] + _UNITS[parts[1]]
    return None


@dataclass(frozen=True)
class DurationMatch:
    months: int
    start: int
    end: int
    text: str


def find_durations(text: str) -> list[DurationMatch]:
    """All durations in `text`, normalized to months."""
    found: list[DurationMatch] = []
    for match in _DURATION.finditer(text):
        number = words_to_int(match.group("num"))
        if number is None:
            continue
        months = number * 12 if match.group("unit").lower() == "year" else number
        found.append(DurationMatch(months, match.start(), match.end(), match.group(0)))
    return found
