from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping


_DIGITS = (
    "không",
    "một",
    "hai",
    "ba",
    "bốn",
    "năm",
    "sáu",
    "bảy",
    "tám",
    "chín",
)
_SCALES = ("", "nghìn", "triệu", "tỷ")
_PROTECTED_RE = re.compile(
    r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    flags=re.IGNORECASE,
)
_SIGNED_NUMBER = r"[+-]?\d+(?:[.,]\d{1,2})?"
_PERCENT_RE = re.compile(rf"(?<![\w.,])({_SIGNED_NUMBER})(?![\d.,])\s*%")
_USD_RE = re.compile(
    r"\$([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?!\d|[.,]\d)"
)
_UNIT_RE = re.compile(
    rf"(?<![\w.,/:-])({_SIGNED_NUMBER})(?![\d.,])\s*"
    rf"(km/h|km|cm|mm|kg|mg|g|°c|°f)(?!\w)",
    flags=re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(
    r"(?<![\w./-])(\d{4})-(\d{2})-(\d{2})(?![\w./-])"
)
_CLOCK_TIME_RE = re.compile(
    r"(?<![\w:])([01]\d|2[0-3]):([0-5]\d)(?![\w:])"
)
_UNRESOLVED_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w./-])\d{4}-\d{2}-\d{2}(?![\w./-])"
    r"|(?<!\w)(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)"
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?!\w)"
    r"|(?<![\w./:-])[+-]?0\d+(?![\w./:-])"
    r"|(?<!\w)(?<![.,]\d)[+-]?\d+[.,]\d+(?!\w)(?![.,]\d)"
)
_INTEGER_RE = re.compile(r"(?<![\w@./:])[-+]?\d+(?![\w@./:-])")
_UNIT_SPOKEN = {
    "km/h": "ki lô mét trên giờ",
    "km": "ki lô mét",
    "cm": "xen ti mét",
    "mm": "mi li mét",
    "kg": "ki lô gam",
    "mg": "mi li gam",
    "g": "gam",
    "°c": "độ C",
    "°f": "độ F",
}


@dataclass(frozen=True, slots=True)
class PronunciationReplacement:
    category: str
    original: str
    spoken: str


@dataclass(frozen=True, slots=True)
class PronunciationResult:
    display_text: str
    tts_text: str
    replacements: tuple[PronunciationReplacement, ...]


def _read_two_digits(value: int) -> str:
    if value < 10:
        return _DIGITS[value]
    tens, ones = divmod(value, 10)
    if tens == 1:
        prefix = "mười"
    else:
        prefix = f"{_DIGITS[tens]} mươi"
    if ones == 0:
        return prefix
    if ones == 1 and tens >= 2:
        suffix = "mốt"
    elif ones == 4 and tens >= 2:
        suffix = "tư"
    elif ones == 5:
        suffix = "lăm"
    else:
        suffix = _DIGITS[ones]
    return f"{prefix} {suffix}"


def _read_three_digits(value: int, *, force_hundreds: bool = False) -> str:
    hundreds, remainder = divmod(value, 100)
    parts: list[str] = []
    if hundreds or force_hundreds:
        parts.extend((_DIGITS[hundreds], "trăm"))
    if remainder:
        if remainder < 10 and (hundreds or force_hundreds):
            parts.append("lẻ")
        parts.append(_read_two_digits(remainder))
    return " ".join(parts)


def integer_to_vietnamese(value: int) -> str:
    """Read a non-negative integer deterministically for Vietnamese TTS."""
    if value < 0:
        return f"âm {integer_to_vietnamese(-value)}"
    if value == 0:
        return _DIGITS[0]
    if value >= 1_000_000_000_000:
        return str(value)

    groups: list[int] = []
    remaining = value
    while remaining:
        groups.append(remaining % 1000)
        remaining //= 1000

    parts: list[str] = []
    highest = len(groups) - 1
    for index in range(highest, -1, -1):
        group = groups[index]
        if group == 0:
            continue
        force_hundreds = index < highest and group < 100
        spoken = _read_three_digits(group, force_hundreds=force_hundreds)
        scale = _SCALES[index]
        parts.append(f"{spoken} {scale}".strip())
    return " ".join(parts)


def number_token_to_vietnamese(token: str) -> str:
    """Read an integer or explicit decimal token without guessing locale dates/times."""
    raw = str(token).strip()
    if not raw:
        return raw
    sign = ""
    if raw[0] in "+-":
        sign = "âm " if raw[0] == "-" else "dương "
        raw = raw[1:]
    if re.fullmatch(r"\d+", raw):
        return f"{sign}{integer_to_vietnamese(int(raw))}"
    match = re.fullmatch(r"(\d+)[.,](\d+)", raw)
    if match is None:
        return f"{sign}{raw}" if sign else raw
    whole, fraction = match.groups()
    fraction_spoken = " ".join(_DIGITS[int(char)] for char in fraction)
    return f"{sign}{integer_to_vietnamese(int(whole))} phẩy {fraction_spoken}"


def _usd_to_vietnamese(token: str) -> str:
    raw = token.strip()
    sign = ""
    if raw[0] in "+-":
        sign = "âm " if raw[0] == "-" else "dương "
        raw = raw[1:]
    whole, separator, fraction = raw.partition(".")
    whole_spoken = integer_to_vietnamese(int(whole.replace(",", "")))
    if not separator:
        return f"{sign}{whole_spoken}"
    fraction_spoken = " ".join(_DIGITS[int(char)] for char in fraction)
    return f"{sign}{whole_spoken} phẩy {fraction_spoken}"


def _date_to_vietnamese(match: re.Match[str]) -> str:
    year, month, day = (int(part) for part in match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return match.group(0)
    return (
        f"ngày {integer_to_vietnamese(day)} "
        f"tháng {integer_to_vietnamese(month)} "
        f"năm {integer_to_vietnamese(year)}"
    )


def _time_to_vietnamese(match: re.Match[str]) -> str:
    hour, minute = (int(part) for part in match.groups())
    spoken = f"{integer_to_vietnamese(hour)} giờ"
    if minute:
        spoken = f"{spoken} {integer_to_vietnamese(minute)} phút"
    return spoken


def _replacement_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    prefix = r"(?<!\w)" if term and term[0].isalnum() else ""
    suffix = r"(?!\w)" if term and term[-1].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}", flags=re.IGNORECASE)


def _mask_protected(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"\ue000{chr(0xE100 + len(protected))}\ue001"
        protected[key] = match.group(0)
        return key

    return _PROTECTED_RE.sub(replace, text), protected


def _mask_protected_with_pattern(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"\ue002{chr(0xE200 + len(protected))}\ue003"
        protected[key] = match.group(0)
        return key

    return pattern.sub(replace, text), protected


def _restore_protected(text: str, protected: Mapping[str, str]) -> str:
    restored = text
    for key, value in protected.items():
        restored = restored.replace(key, value)
    return restored


def shape_spoken_phrasing(text: str) -> str:
    """Clean punctuation spacing for TTS without changing display text or semantics."""
    working, protected = _mask_protected(str(text or ""))
    working = re.sub(r"[ \t]+", " ", working)
    working = re.sub(r"\s+([,.;:!?])", r"\1", working)
    # Do not split punctuation inside numeric tokens such as 08:05, 1,234 or $12,50.
    working = re.sub(r"(?<!\d)([,;:!?])(?=[^\s,.;:!?])", r"\1 ", working)
    return _restore_protected(working.strip(), protected)


def normalize_pronunciation(
    text: str,
    pronunciation_map: Mapping[str, str] | None = None,
    *,
    normalize_numbers: bool = True,
) -> PronunciationResult:
    """Create TTS-safe spoken text while preserving the original display subtitle.

    Only explicit mappings and unambiguous token classes are changed. URLs and
    email addresses are protected so numeric normalization cannot corrupt them.
    """
    display_text = str(text or "")
    working, protected = _mask_protected(shape_spoken_phrasing(display_text))
    replacements: list[PronunciationReplacement] = []

    for source, target in sorted(
        ((str(source), str(target)) for source, target in (pronunciation_map or {}).items()),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if not source or not target.strip() or source.casefold() == target.casefold():
            continue
        pattern = _replacement_pattern(source)

        def replace_mapping(match: re.Match[str], *, spoken: str = target) -> str:
            original = match.group(0)
            replacements.append(PronunciationReplacement("mapping", original, spoken))
            return spoken

        working = pattern.sub(replace_mapping, working)

    if normalize_numbers:
        def substitute(
            pattern: re.Pattern[str],
            category: str,
            converter: Callable[[re.Match[str]], str],
            value: str,
        ) -> str:
            def replace(match: re.Match[str]) -> str:
                spoken = converter(match)
                if spoken == match.group(0):
                    return match.group(0)
                replacements.append(
                    PronunciationReplacement(category, match.group(0), spoken)
                )
                return spoken

            return pattern.sub(replace, value)

        working = substitute(
            _PERCENT_RE,
            "percent",
            lambda match: f"{number_token_to_vietnamese(match.group(1))} phần trăm",
            working,
        )
        working = substitute(
            _USD_RE,
            "currency",
            lambda match: f"{_usd_to_vietnamese(match.group(1))} đô la Mỹ",
            working,
        )
        working = substitute(
            _UNIT_RE,
            "unit",
            lambda match: (
                f"{number_token_to_vietnamese(match.group(1))} "
                f"{_UNIT_SPOKEN[match.group(2).casefold()]}"
            ),
            working,
        )
        working = substitute(
            _ISO_DATE_RE,
            "date",
            _date_to_vietnamese,
            working,
        )
        working = substitute(
            _CLOCK_TIME_RE,
            "time",
            _time_to_vietnamese,
            working,
        )
        working, unresolved_numbers = _mask_protected_with_pattern(
            working,
            _UNRESOLVED_NUMERIC_TOKEN_RE,
        )
        working = substitute(
            _INTEGER_RE,
            "number",
            lambda match: number_token_to_vietnamese(match.group(0)),
            working,
        )
        working = _restore_protected(working, unresolved_numbers)

    tts_text = _restore_protected(working, protected)
    tts_text = re.sub(r"[ \t]+", " ", tts_text).strip()
    return PronunciationResult(
        display_text=display_text,
        tts_text=tts_text,
        replacements=tuple(replacements),
    )
