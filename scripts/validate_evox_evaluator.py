"""Smoke selected pinned SkyDiscover evaluators through Hebe's adapter.

Run only with a trusted checkout: the original evaluator executes Python source.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from alphadiana.analysis.io.status import infer_score_status
from alphadiana.harness.base import AgentResponse
from alphahebe.integrations.diana.benchmarks.evox_problems import (
    EvoXProblemsBenchmark,
    EvoXProblemsScorer,
)

REVISION = "700711ec4c37d0be677456f66d38461310c0bcf8"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sky_root", type=Path, help="clean SkyDiscover checkout")
    parser.add_argument(
        "--problem",
        choices=("circle_packing", "third_autocorr_ineq"),
        default="circle_packing",
    )
    parser.add_argument(
        "--invalid-source",
        nargs="?",
        const="exception",
        choices=("exception", "prose", "invalid-shapes"),
        help="Verify that the original evaluator's candidate-error verdict stays a low score",
    )
    args = parser.parse_args()
    root = args.sky_root.resolve()
    problem = f"benchmarks/math/{args.problem}"
    spec = {"id": args.problem, "path": problem, "mode": "train"}
    if args.problem == "circle_packing":
        spec["evaluator"] = f"{problem}/evaluator.py"

    task = EvoXProblemsBenchmark().load_tasks(
        {
            "repository_path": str(root),
            "revision": REVISION,
            "problems": [spec],
        }
    )[0]
    seed = (root / problem / "initial_program.py").read_text(encoding="utf-8")
    if args.invalid_source == "exception":
        seed = "raise RuntimeError('invalid-source-smoke')\n"
    elif args.invalid_source == "prose":
        seed = "This is a prose answer, not a Python program.\n"
    elif args.invalid_source == "invalid-shapes":
        seed = (
            "import numpy as np\ndef run_packing():\n"
            "    return np.zeros((1, 2)), np.ones(1), 1.0\n"
            if args.problem == "circle_packing"
            else "import numpy as np\ndef run():\n    return np.ones(1), 1.0, 1.0, 400\n"
        )
    failure_score = -1_000_000_000.0
    scorer = EvoXProblemsScorer()
    scorer.setup({"failure_score": failure_score})
    answer = seed if args.invalid_source == "prose" else f"```python\n{seed}\n```"
    result = scorer.score(task, AgentResponse(answer=answer))
    status = infer_score_status(
        {
            "benchmark_name": "evox_problems",
            "score": result.score,
            "correct": result.correct,
            "score_metadata": result.metadata,
        }
    )
    expected_failure_score = (
        failure_score
        if args.problem == "circle_packing" and args.invalid_source == "invalid-shapes"
        else 0.0
    )
    if (
        status != "valid_scored"
        or not math.isfinite(result.score)
        or (
            args.invalid_source
            and (
                result.correct
                or result.score != expected_failure_score
                or "candidate_error" not in result.metadata
            )
        )
    ):
        raise RuntimeError(
            f"Unexpected evaluator outcome: status={status}, score={result.score}"
        )
    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "revision": REVISION,
                "evaluator": task.metadata["evaluator"],
                "score": result.score,
                "score_status": status,
                "input": args.invalid_source or "upstream-seed",
                "evaluator_digest": task.metadata["evaluator_digest"],
                "correct": result.correct,
                "metrics": result.metadata.get("metrics", {}),
                "artifact_keys": result.metadata.get("artifact_keys", []),
                "candidate_error": result.metadata.get("candidate_error"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
