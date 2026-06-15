#!/usr/bin/env python3
"""Run the official QJL evaluation from a clean verifier workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core import evaluate_candidate_isolated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--comparison")
    parser.add_argument("--output", default="/artifacts/official_eval.json")
    args = parser.parse_args()

    baseline_payload = json.loads(
        Path("/verifier/seed_candidate.json").read_text(encoding="utf-8")
    )
    baseline_result = evaluate_candidate_isolated(
        baseline_payload,
        artifact_dir=Path("/artifacts/logs"),
        label="official_baseline",
    )
    if not baseline_result.get("correct"):
        result = {
            **baseline_result,
            "failure_reason": (
                "official_same_seed_baseline_failed: "
                f"{baseline_result.get('failure_reason')}"
            ),
            "baseline_result": baseline_result,
        }
    else:
        os.environ["QJL_BASELINE_RUNTIME_MS"] = str(
            baseline_result["metrics"]["runtime_ms"]
        )
        comparison_result = None
        if args.comparison:
            comparison_payload = json.loads(
                Path(args.comparison).read_text(encoding="utf-8")
            )
            comparison_result = evaluate_candidate_isolated(
                comparison_payload,
                artifact_dir=Path("/artifacts/logs"),
                label="official_best",
            )
        payload = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        result = evaluate_candidate_isolated(
            payload,
            artifact_dir=Path("/artifacts/logs"),
            label="official_final",
        )
        result["baseline_result"] = baseline_result
        result["comparison_result"] = comparison_result
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
