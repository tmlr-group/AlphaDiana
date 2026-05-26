"""TerminalBench-2 binary scorer — pass/fail from reward.txt string value."""
from __future__ import annotations

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer


@register_scorer("terminal_bench2")
class TerminalBench2Scorer(Scorer):
    """Binary pass/fail scorer for terminal-bench-2 tasks.

    Receives response.answer = stripped content of /logs/verifier/reward.txt.
    The reward.txt is written inside the Docker container by tests/test.sh
    and read by TerminalBench2DockerAgent after test execution.

    Scoring logic:
      - response.answer == "1"  → correct=True,  score=1.0
      - anything else (including "0", empty, None, missing file) → correct=False, score=0.0
    """

    @property
    def name(self) -> str:
        return "terminal_bench2"

    def score(self, task, response) -> ScoreResult:
        if response.answer is None:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected="1",
                predicted=None,
                rationale="No reward.txt value produced (answer is None — reward.txt missing or unreadable).",
            )
        predicted = str(response.answer).strip()
        correct = predicted == "1"
        return ScoreResult(
            correct=correct,
            score=1.0 if correct else 0.0,
            expected="1",
            predicted=predicted,
            rationale=(
                "reward.txt value '1' = pass"
                if correct
                else f"reward.txt value '{predicted}' != '1' = fail"
            ),
        )
