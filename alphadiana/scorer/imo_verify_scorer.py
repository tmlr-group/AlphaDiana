"""IMO-AnswerBench scorer with multi-valued answer and symbolic guard.

Extends math_verify with two fixes for competition math:
1. Multi-valued ground truth: splits on commas and requires the prediction
   to match ALL values, not just one.
2. Symbolic guard: refuses numeric fallback when the expression contains
   free variables (letters other than common constants like e, pi, i).
"""

from __future__ import annotations

import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.math_answer import normalize_math_text, parse_numeric_answer


def _wrap_boxed(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(r"\boxed{"):
        return stripped
    return r"\boxed{" + stripped + "}"


_FREE_VAR_RE = re.compile(r"[a-df-hj-np-tv-z]", re.IGNORECASE)
_KNOWN_CONSTANTS = {"e", "i"}


def _has_free_variables(text: str) -> bool:
    """Return True if text contains alphabetic chars that look like free variables."""
    cleaned = re.sub(r"\\(frac|sqrt|left|right|lfloor|rfloor|lceil|rceil|log|ln|sin|cos|tan|mathbb|infty|cup|cap|cdot|times|pm|mp|geq|leq|neq|text|mathrm|operatorname)\b", "", text)
    cleaned = re.sub(r"[{}()\\$\s\d.,+\-*/=^_|!<>\[\]]", "", cleaned)
    for ch in cleaned:
        if ch.lower() not in _KNOWN_CONSTANTS:
            return True
    return False


def _math_verify_equal(expected: str, predicted: str) -> bool | None:
    try:
        from math_verify import verify, parse
        from math_verify.parser import LatexExtractionConfig, ExprExtractionConfig
    except ImportError:
        return None

    cfg = [LatexExtractionConfig(), ExprExtractionConfig()]
    try:
        gold = parse(_wrap_boxed(expected), extraction_config=cfg)
        pred = parse(_wrap_boxed(predicted), extraction_config=cfg)
        if not gold or not pred:
            return None
        return verify(gold, pred)
    except Exception:
        return None


def _split_multi_valued(text: str) -> list[str]:
    """Split ground truth on commas that separate distinct values.

    Handles cases like "$P(x)=-1, P(x)=x+1$" and "$-2/3, 0, 2/3$".
    Does NOT split commas inside braces/parens.
    """
    cleaned = text.strip().strip("$").strip()
    parts = []
    depth = 0
    current: list[str] = []
    for ch in cleaned:
        if ch in "({[":
            depth += 1
            current.append(ch)
        elif ch in ")}]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    last = "".join(current).strip()
    if last:
        parts.append(last)
    return parts if len(parts) > 1 else [text]


def _single_match(expected: str, predicted: str) -> tuple[bool, str]:
    """Match a single expected value against predicted. Returns (matched, method)."""
    sym = _math_verify_equal(expected, predicted)
    if sym is not None:
        return sym, "math_verify"

    exp_norm = normalize_math_text(expected)
    pred_norm = normalize_math_text(predicted)
    if exp_norm == pred_norm:
        return True, "normalized_string"

    if _has_free_variables(expected) or _has_free_variables(predicted):
        return False, "blocked_free_var"

    exp_num = parse_numeric_answer(expected)
    pred_num = parse_numeric_answer(predicted)
    if exp_num is not None and pred_num is not None:
        if abs(exp_num - pred_num) < 1e-9:
            return True, "numeric"

    return False, "no_match"


@register_scorer("imo_verify")
class ImoVerifyScorer(Scorer):

    @property
    def name(self) -> str:
        return "imo_verify"

    def score(self, task, response) -> ScoreResult:
        if response.answer is None:
            return ScoreResult(
                correct=False, score=0.0,
                expected=str(task.ground_truth), predicted=None,
                rationale="No answer produced.",
            )

        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)

        gt_parts = _split_multi_valued(expected_raw)

        if len(gt_parts) == 1:
            matched, method = _single_match(expected_raw, predicted_raw)
            return ScoreResult(
                correct=matched,
                score=1.0 if matched else 0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=f"Single-value: {method}.",
                metadata={"method": method},
            )

        pred_parts = _split_multi_valued(predicted_raw)

        matched_gt = 0
        for gt_val in gt_parts:
            for pred_val in pred_parts:
                ok, _ = _single_match(gt_val, pred_val)
                if ok:
                    matched_gt += 1
                    break

        all_matched = matched_gt == len(gt_parts)
        return ScoreResult(
            correct=all_matched,
            score=1.0 if all_matched else 0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale=f"Multi-value: {matched_gt}/{len(gt_parts)} ground truth values matched.",
            metadata={"method": "multi_value", "matched": matched_gt, "total": len(gt_parts)},
        )
