#!/usr/bin/env python3
"""
extract_gpqa_entropy_by_harness.py — Extract combined cross-harness GPQA entropy-token dataset.

Output:
    data/gpqa_entropy_by_harness.csv — harness,task_id,correct,mean_entropy,n_tokens

Run:
    python3 analyze_tools/extract_gpqa_entropy_by_harness.py
    (from repo root)
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

# Allow import of sibling modules (consistent with plot_figures.py pattern)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compute_trajectory_stats import GPQA_RUNS, load_gpqa_records

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def is_valid(record: dict) -> bool:
    """Check if a record has valid scoring (handles all harness conventions)."""
    if record.get("score_status") == "valid_scored":
        return True
    # DirectLLM records may not have score_status but still have correct/score
    if "score" in record and isinstance(record.get("correct"), bool):
        return True
    return False


def extract_mean_entropy(record: dict) -> float:
    """Extract mean_entropy from token_entropy_stats, returning NaN if missing."""
    tes = record.get("token_entropy_stats")
    if not isinstance(tes, dict):
        return float("nan")
    val = tes.get("mean")
    if val is None:
        return float("nan")
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def extract_n_tokens(record: dict) -> int:
    """Extract n_tokens from token_entropy_stats, returning 0 if missing."""
    tes = record.get("token_entropy_stats")
    if not isinstance(tes, dict):
        return 0
    val = tes.get("n_tokens", 0)
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def main() -> None:
    rows: list[dict[str, object]] = []
    harness_order = ["openclaw", "opencode", "zeroclaw", "directllm"]

    for harness in harness_order:
        records = load_gpqa_records(harness)
        harness_count = 0

        for task_id, record in records.items():
            if not is_valid(record):
                continue

            correct = 1 if record.get("correct") else 0
            mean_entropy = extract_mean_entropy(record)
            n_tokens = extract_n_tokens(record)

            # Skip invalid records: missing token counts or NaN entropy
            if n_tokens <= 0:
                continue
            if math.isnan(mean_entropy):
                continue

            rows.append({
                "harness": harness,
                "task_id": task_id,
                "correct": correct,
                "mean_entropy": mean_entropy,
                "n_tokens": n_tokens,
            })
            harness_count += 1

        print(f"  {harness}: {harness_count} records extracted")

    out_path = DATA_DIR / "gpqa_entropy_by_harness.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["harness", "task_id", "correct", "mean_entropy", "n_tokens"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} total rows to {out_path}")


if __name__ == "__main__":
    main()
