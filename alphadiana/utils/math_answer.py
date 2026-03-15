"""Utilities for extracting and normalizing competition-math answers."""

from __future__ import annotations

import re


_ANSWER_RE = re.compile(
    r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is|=)\s*(.+)",
    re.IGNORECASE,
)
_SIMPLE_FRAC_RE = re.compile(
    r"\\(?:d|t)?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}"
)
_SIMPLE_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]+)\}")


def extract_boxed(text: str) -> str | None:
    r"""Extract the content of the last \boxed{...}, handling nested braces."""
    idx = text.rfind(r"\boxed{")
    if idx == -1:
        return None
    start = idx + len(r"\boxed{")
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
        pos += 1
    if depth == 0:
        return text[start : pos - 1]
    return None


def extract_answer_candidate(text: str) -> str:
    """Extract the final-answer candidate from free-form model output."""
    boxed = extract_boxed(text)
    if boxed is not None:
        return boxed.strip()

    matches = list(_ANSWER_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()

    stripped = text.strip()

    # If the text is short (likely already a direct answer), return last line as-is.
    if len(stripped) <= 50:
        lines = [l.strip() for l in stripped.splitlines() if l.strip()]
        return lines[-1] if lines else stripped

    # Fallback for long outputs (e.g. agentic multi-paragraph text without \boxed{}):
    # extract the last standalone integer from the text.
    number_matches = list(re.finditer(r'\b(\d+)\b', text))
    if number_matches:
        return number_matches[-1].group(1)

    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    return lines[-1] if lines else stripped


def _strip_outer_pair(text: str, left: str, right: str) -> str:
    if text.startswith(left) and text.endswith(right):
        return text[len(left) : -len(right)].strip()
    return text


def _strip_wrappers(text: str) -> str:
    updated = text.strip()
    while True:
        previous = updated
        updated = _strip_outer_pair(updated, "$", "$")
        updated = _strip_outer_pair(updated, r"\(", r"\)")
        updated = _strip_outer_pair(updated, r"\[", r"\]")
        updated = _strip_outer_pair(updated, "{", "}")
        if updated == previous:
            return updated


def _normalize_latex(text: str) -> str:
    text = text.replace(r"\left", "")
    text = text.replace(r"\right", "")
    text = text.replace(r"\!", "")
    text = text.replace(r"\,", "")
    text = text.replace(r"\%", "%")
    while True:
        updated = _SIMPLE_FRAC_RE.sub(r"\1/\2", text)
        if updated == text:
            break
        text = updated
    while True:
        updated = _SIMPLE_SQRT_RE.sub(r"sqrt(\1)", text)
        if updated == text:
            break
        text = updated
    return text


def normalize_math_text(text: str) -> str:
    """Normalize common math-answer surface forms for string comparison."""
    text = extract_answer_candidate(text)
    text = text.strip().rstrip(".")
    text = _strip_wrappers(text)
    text = _normalize_latex(text)
    if "=" in text:
        rhs = text.rsplit("=", 1)[-1].strip()
        if rhs:
            text = rhs
    text = text.strip()
    text = re.sub(r"\s+", "", text)
    return text.lower()


def parse_numeric_answer(text: str) -> float | None:
    """Parse a numeric answer from common math benchmark output formats."""
    normalized = normalize_math_text(text)
    if not normalized:
        return None

    normalized = normalized.replace(",", "")

    if normalized.endswith("%"):
        try:
            return float(normalized[:-1]) / 100.0
        except ValueError:
            pass

    frac_match = re.fullmatch(r"\(?(-?\d+(?:\.\d+)?)\)?/\(?(-?\d+(?:\.\d+)?)\)?", normalized)
    if frac_match:
        numer = float(frac_match.group(1))
        denom = float(frac_match.group(2))
        if denom != 0:
            return numer / denom
        return None

    try:
        return float(normalized)
    except ValueError:
        pass

    matches = re.findall(
        r"-?\d+(?:\.\d+)?(?:/-?\d+(?:\.\d+)?)?%?",
        normalized,
    )
    if not matches:
        return None

    candidate = matches[-1]
    if candidate.endswith("%"):
        try:
            return float(candidate[:-1]) / 100.0
        except ValueError:
            return None

    frac_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)", candidate)
    if frac_match:
        numer = float(frac_match.group(1))
        denom = float(frac_match.group(2))
        if denom != 0:
            return numer / denom
        return None

    try:
        return float(candidate)
    except ValueError:
        return None
