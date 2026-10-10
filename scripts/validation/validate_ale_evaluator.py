"""Replay a trusted C++ candidate through the pinned official ALE public judge.

This is evaluator validation, not fresh model generation or private evaluation.
Install the clean pinned ALE checkout and Hebe before running this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

import ale_bench
from alphadiana.results.status import infer_score_status
from alphadiana.harness.base import AgentResponse
from alphahebe.integrations.diana.benchmarks.ale_bench import (
    ALEBenchBenchmark,
    ALEBenchScorer,
)

REVISION = "f7d927906dc1dcd860ee086e4560d576438b1354"
DATA_SHA256 = "f557d24377e00d105d6d2a0c5c5413166b6fe87662bdea5b4f163e0b64f21e1b"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ale_root", type=Path, help="clean installed ALE checkout")
    parser.add_argument(
        "data_root", type=Path, help="cached ALE data, including lite ID list"
    )
    parser.add_argument("candidate", type=Path, help="trusted ahc027 C++20 source")
    args = parser.parse_args()
    root = args.ale_root.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    )
    if revision != REVISION or dirty:
        raise ValueError("ALE checkout must be clean at the pinned revision")
    if not Path(ale_bench.__file__).resolve().is_relative_to(root):
        raise ValueError(
            "Installed ALE does not resolve to the specified source checkout"
        )
    data_root = args.data_root.resolve()
    data_digest = hashlib.sha256((data_root / "ahc027.zip").read_bytes()).hexdigest()
    if data_digest != DATA_SHA256:
        raise ValueError("ahc027 data differs from the verified snapshot")
    os.environ["ALE_BENCH_DATA"] = str(data_root)
    source = args.candidate.read_text(encoding="utf-8")
    image = subprocess.check_output(
        ["docker", "image", "inspect", "ale-bench:cpp20-202301", "--format", "{{.Id}}"],
        text=True,
    ).strip()
    task = ALEBenchBenchmark().load_tasks(
        {"problem_ids": ["ahc027"], "lite_version": True}
    )[0]
    scorer = ALEBenchScorer()
    scorer.setup(
        {"evaluation_mode": "public", "num_workers": 1, "reuse_containers": False}
    )
    result = scorer.score(task, AgentResponse(answer=f"```cpp\n{source}\n```"))
    status = infer_score_status(
        {
            "benchmark_name": "ale_bench",
            "score": result.score,
            "correct": result.correct,
            "score_metadata": result.metadata,
        }
    )
    if (
        status != "valid_scored"
        or not result.correct
        or not math.isfinite(result.score)
    ):
        raise RuntimeError(
            f"Expected an accepted candidate: {result.rationale}; {result.metadata}"
        )
    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "revision": revision,
                "image": image,
                "data_sha256": data_digest,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "public_seed_count": task.metadata["public_seed_count"],
                "public_seeds_digest": task.metadata["public_seeds_digest"],
                "score": result.score,
                "score_status": status,
                "metadata": result.metadata,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
