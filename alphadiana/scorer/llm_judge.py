"""LLM-as-a-judge scorer that uses an OpenAI-compatible API."""

from __future__ import annotations

import json
import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

_JUDGE_SYSTEM_PROMPT = """\
You are a precise grading assistant. You will be given a question, the expected \
(ground-truth) answer, and a student's predicted answer. Determine whether the \
predicted answer is correct.

Respond ONLY with a JSON object (no markdown fences) containing exactly two keys:
  "correct": true or false,
  "rationale": a brief explanation of your judgement.
"""

_JUDGE_USER_TEMPLATE = """\
Question:
{question}

Expected answer:
{expected}

Predicted answer:
{predicted}
"""


@register_scorer("llm_judge")
class LLMJudgeScorer(Scorer):
    """Scorer that delegates grading to an LLM via an OpenAI-compatible chat
    completions endpoint."""

    def __init__(self) -> None:
        self._judge_model: str = "gpt-4o"
        self._api_base: str = "https://api.openai.com/v1"
        self._api_key: str = ""
        self._timeout: int = 60

    @property
    def name(self) -> str:
        return "llm_judge"

    def setup(self, config: dict) -> None:
        if _requests is None:
            raise RuntimeError(
                "The 'requests' package is required for LLMJudgeScorer. "
                "Install it with: pip install requests"
            )
        self._judge_model = config.get("judge_model", self._judge_model)
        self._api_base = config.get("api_base", self._api_base).rstrip("/")
        self._api_key = config.get("api_key", self._api_key)
        self._timeout = int(config.get("timeout", self._timeout))

    def _call_llm(self, question: str, expected: str, predicted: str) -> dict:
        """Send the judging prompt to the LLM and parse the response."""
        if _requests is None:
            raise RuntimeError("requests is not available")

        user_content = _JUDGE_USER_TEMPLATE.format(
            question=question,
            expected=expected,
            predicted=predicted,
        )

        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": self._judge_model,
            "messages": [
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
        }

        resp = _requests.post(
            f"{self._api_base}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        return self._parse_judge_response(content)

    @staticmethod
    def _parse_judge_response(text: str) -> dict:
        """Parse the JSON response from the judge LLM."""
        # Strip optional markdown code fences.
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: look for "correct": true/false anywhere in the text.
            correct_match = re.search(r'"correct"\s*:\s*(true|false)', text, re.IGNORECASE)
            correct = correct_match.group(1).lower() == "true" if correct_match else False
            return {"correct": correct, "rationale": text}

        return {
            "correct": bool(result.get("correct", False)),
            "rationale": str(result.get("rationale", "")),
        }

    def score(self, task, response) -> ScoreResult:
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)
        question = task.problem

        try:
            judge_result = self._call_llm(question, expected_raw, predicted_raw)
        except Exception as exc:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected_raw,
                predicted=predicted_raw,
                rationale=f"LLM judge call failed: {exc}",
                metadata={"error": str(exc)},
            )

        correct = judge_result["correct"]
        return ScoreResult(
            correct=correct,
            score=1.0 if correct else 0.0,
            expected=expected_raw,
            predicted=predicted_raw,
            rationale=judge_result.get("rationale", ""),
        )
