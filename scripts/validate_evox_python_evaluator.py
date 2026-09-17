"""Smoke the pinned SkyDiscover circle-packing evaluator through Hebe's adapter.

Run only with a trusted checkout: the original evaluator executes Python source.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from alphadiana.harness.base import AgentResponse
from alphahebe.integrations.diana.benchmarks.evox_problems import (
    EvoXProblemsBenchmark,
    EvoXProblemsScorer,
)

REVISION = "700711ec4c37d0be677456f66d38461310c0bcf8"
PROBLEM = "benchmarks/math/circle_packing"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sky_root", type=Path, help="clean SkyDiscover checkout")
    args = parser.parse_args()
    root = args.sky_root.resolve()

    task = EvoXProblemsBenchmark().load_tasks(
        {
            "repository_path": str(root),
            "revision": REVISION,
            "problems": [
                {
                    "id": "circle_packing",
                    "path": PROBLEM,
                    "evaluator": f"{PROBLEM}/evaluator.py",
                    "mode": "train",
                }
            ],
        }
    )[0]
    seed = (root / PROBLEM / "initial_program.py").read_text(encoding="utf-8")
    scorer = EvoXProblemsScorer()
    scorer.setup({})
    result = scorer.score(task, AgentResponse(answer=f"```python\n{seed}\n```"))
    if "error" in result.metadata or not math.isfinite(result.score):
        raise RuntimeError(
            f"Original evaluator did not return a finite score: {result.rationale}"
        )
    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "revision": REVISION,
                "evaluator": task.metadata["evaluator"],
                "score": result.score,
                "correct": result.correct,
                "metrics": result.metadata.get("metrics", {}),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
