"""Utilities for extracting and normalizing competition-math answers."""

from __future__ import annotations

import json
import re


_ANSWER_RE = re.compile(
    r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is|=)\s*(.+)",
    re.IGNORECASE,
)
_SIMPLE_FRAC_RE = re.compile(
    r"\\(?:d|t)?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}"
)
_SIMPLE_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]+)\}")
_NUMERIC_LITERAL_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
_NUMERIC_FRACTION_RE = re.compile(
    r"\(?-?\d+(?:\.\d+)?\)?/\(?-?\d+(?:\.\d+)?\)?"
)
_JSON_METADATA_KEYS = {
    "cost",
    "duration",
    "elapsed",
    "error",
    "exit_code",
    "prompt_tokens",
    "status",
    "tokens",
}


def _contains_json_metadata_key(value) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in _JSON_METADATA_KEYS for key in value):
            return True
        return any(_contains_json_metadata_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_json_metadata_key(item) for item in value)
    return False


def _looks_like_json_metadata(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) > 1 and all(line[0] in "[{" for line in lines):
        parsed_lines = []
        for line in lines:
            try:
                parsed_lines.append(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
        return any(_contains_json_metadata_key(item) for item in parsed_lines)

    if stripped[0] not in "[{":
        return False
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return _contains_json_metadata_key(parsed)


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


def extract_answer_candidate(text: str, *, source: str = "assistant_text") -> str:
    """Extract the final-answer candidate from free-form model output."""
    if source not in {"assistant_text", "model_text"}:
        return ""
    if not text or not text.strip():
        return ""
    if _looks_like_json_metadata(text):
        return ""

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


def is_numeric_literal_answer(text: str) -> bool:
    """Return True only for pure numeric literals, not symbolic expressions."""
    candidate = extract_answer_candidate(text)
    candidate = candidate.strip().rstrip(".")
    candidate = _strip_wrappers(candidate)
    candidate = _normalize_latex(candidate)
    candidate = candidate.replace(",", "")
    candidate = re.sub(r"\s+", "", candidate)
    if not candidate or "=" in candidate:
        return False
    if any(ch in candidate for ch in ("+", "*", "^", "_")):
        return False
    if re.search(r"[A-Za-z\\]", candidate):
        return False
    return bool(
        _NUMERIC_LITERAL_RE.fullmatch(candidate)
        or _NUMERIC_FRACTION_RE.fullmatch(candidate)
    )
