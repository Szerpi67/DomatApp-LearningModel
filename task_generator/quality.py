from __future__ import annotations

from fractions import Fraction
import re


_COMPARISON_EXPRESSION = re.compile(
    r"^\s*(\d+\s*/\s*\d+|\d+)\s*(<=|>=|≤|≥|<|>|=|≠)\s*(\d+\s*/\s*\d+|\d+)\s*$"
)
_ABANDONED_DRAFT = re.compile(
    r"[?.!]\s*(?:nie|a raczej|poprawka|chodziło mi|miałem na myśli)\b",
    re.IGNORECASE,
)


def is_statement_well_formed(statement: str) -> bool:
    """Reject unfinished self-corrections and overly long closed-task prompts."""
    normalized = " ".join(statement.split())
    if not normalized or len(normalized.split()) > 18:
        return False
    if _ABANDONED_DRAFT.search(normalized) or normalized.count("?") > 1:
        return False
    return True


def has_single_true_comparison(
    option_texts: list[str], correct_texts: list[str]
) -> bool | None:
    """Check simple numeric comparison options; return None for other task formats."""
    matches = [_COMPARISON_EXPRESSION.fullmatch(text) for text in option_texts]
    if not all(matches):
        return None

    true_texts = []
    try:
        for text, match in zip(option_texts, matches):
            left = _parse_fraction(match.group(1))
            right = _parse_fraction(match.group(3))
            operator = match.group(2)
            is_true = {
                "<": left < right,
                ">": left > right,
                "=": left == right,
                "≠": left != right,
                "<=": left <= right,
                "≤": left <= right,
                ">=": left >= right,
                "≥": left >= right,
            }[operator]
            if is_true:
                true_texts.append(text)
    except (ValueError, ZeroDivisionError):
        return False

    return len(true_texts) == 1 and true_texts[0] in correct_texts


def _parse_fraction(value: str) -> Fraction:
    parts = value.replace(" ", "").split("/", maxsplit=1)
    if len(parts) == 1:
        return Fraction(int(parts[0]))
    return Fraction(int(parts[0]), int(parts[1]))
