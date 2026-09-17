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
    EvoXEvaluationError,
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
        action="store_true",
        help="Verify that an upstream failed evaluation is rejected, not rewarded",
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
    if args.invalid_source:
        seed = "raise RuntimeError('invalid-source-smoke')\n"
    scorer = EvoXProblemsScorer()
    scorer.setup({})
    try:
        result = scorer.score(task, AgentResponse(answer=f"```python\n{seed}\n```"))
    except EvoXEvaluationError as exc:
        if not args.invalid_source or exc.stage != "result":
            raise
        print(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "revision": REVISION,
                    "evaluator": task.metadata["evaluator"],
                    "evaluator_digest": task.metadata["evaluator_digest"],
                    "input": "invalid-source",
                    "score": None,
                    "score_status": exc.error_type,
                    "failure_stage": exc.stage,
                },
                sort_keys=True,
            )
        )
        return
    status = infer_score_status(
        {
            "benchmark_name": "evox_problems",
            "score": result.score,
            "correct": result.correct,
            "score_metadata": result.metadata,
        }
    )
    if (
        args.invalid_source
        or status != "valid_scored"
        or not math.isfinite(result.score)
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
                "input": "invalid-source" if args.invalid_source else "upstream-seed",
                "evaluator_digest": task.metadata["evaluator_digest"],
                "correct": result.correct,
                "metrics": result.metadata.get("metrics", {}),
                "artifact_keys": result.metadata.get("artifact_keys", []),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
