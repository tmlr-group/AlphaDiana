"""Append-only JSONL storage for evaluation results."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.base import ScoreResult


class ResultStore:
    """Append-only JSONL result storage.

    Each line in the file is a JSON object representing the outcome of
    evaluating one benchmark task.
    """

    def __init__(self, output_dir: str, run_id: str) -> None:
        os.makedirs(output_dir, exist_ok=True)
        self.path = f"{output_dir}/{run_id}.jsonl"

    def append(
        self,
        task: BenchmarkTask,
        response: AgentResponse,
        score: ScoreResult,
    ) -> None:
        """Append a single result record to the JSONL file."""
        record = {
            "task_id": task.task_id,
            "problem": task.problem,
            "ground_truth": task.ground_truth,
            "predicted": response.answer,
            "correct": score.correct,
            "score": score.score,
            "rationale": score.rationale,
            "trajectory": response.trajectory,
            "token_usage": response.token_usage,
            "wall_time_sec": response.wall_time_sec,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def load(self) -> list[dict]:
        """Read and parse all JSONL lines from the results file."""
        if not os.path.exists(self.path):
            return []
        results: list[dict] = []
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results
