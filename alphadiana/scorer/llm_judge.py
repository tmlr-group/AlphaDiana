"""LLM-as-a-judge scorer that uses an OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
import re

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.judge_config import JudgeConfig
from alphadiana.scorer.registry import register_scorer

logger = logging.getLogger(__name__)

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
        self._judge_model: str = ""
        self._api_base: str = ""
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
        provider = JudgeConfig.resolve(
            model=config.get("judge_model"),
            base_url=config.get("api_base"),
            api_key=config.get("api_key"),
        )
        self._judge_model, self._api_base, self._api_key = (
            provider.model,
            provider.base_url,
            provider.api_key,
        )
        self._timeout = int(config.get("timeout", self._timeout))

    def _call_llm(self, question: str, expected: str, predicted: str) -> dict:
        """Send the judging prompt to the LLM and parse the response.

        Retries on 429 / 5xx with exponential backoff (up to 2 retries).
        """
        import random
        import time

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

        max_retries = 2
        for attempt in range(max_retries + 1):
            resp = _requests.post(
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            if resp.status_code in (429, 502, 503, 504) and attempt < max_retries:
                delay = min(2.0 * (2**attempt), 30.0)
                jitter = random.uniform(0, delay * 0.3)
                # Respect Retry-After header if present.
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(float(retry_after), delay)
                    except ValueError:
                        pass
                logger.warning(
                    "LLM judge attempt %d/%d got %d, retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    resp.status_code,
                    delay + jitter,
                )
                time.sleep(delay + jitter)
                continue
            resp.raise_for_status()
            break

        data = resp.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Judge did not finish with a complete response")
        content = choice["message"]["content"]
        return self._parse_judge_response(content)

    @staticmethod
    def _parse_judge_response(text: str) -> dict:
        """Parse the JSON response from the judge LLM."""
        # Strip optional markdown code fences.
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        result = json.loads(text)
        if (
            not isinstance(result, dict)
            or type(result.get("correct")) is not bool
            or not isinstance(result.get("rationale"), str)
        ):
            raise ValueError(
                "Judge must return a boolean correct and a string rationale"
            )
        return {"correct": result["correct"], "rationale": result["rationale"]}

    def score(self, task, response) -> ScoreResult:
        if response.answer is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted=None,
                rationale="No answer produced (answer is None).",
            )
        expected_raw = str(task.ground_truth)
        predicted_raw = str(response.answer)
        question = task.problem

        try:
            judge_result = self._call_llm(question, expected_raw, predicted_raw)
        except Exception as exc:
            logger.warning("LLM judge scoring failed: %s", exc, exc_info=True)
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
