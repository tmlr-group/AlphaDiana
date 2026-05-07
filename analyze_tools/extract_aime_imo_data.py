#!/usr/bin/env python3
"""
extract_aime_imo_data.py -- extract AIME 2026 and IMO-AnswerBench direct metrics.

Run:
    python3 analyze_tools/extract_aime_imo_data.py

Outputs (analyze_tools/data/):
    aime2026_direct_summary.csv  — per-task AIME 2026 results
    imo_answerbench_direct_summary.csv — per-task IMO-AnswerBench results
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
DATA_DIR.mkdir(exist_ok=True)

AIME_DIR = Path("/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1")
IMO_DIR = Path("/path/to/xxx/alphadiana_results/phase9_directllm_qwen35_27b_imo_answerbench_logprobs")

ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def load_task_records(run_dir: Path) -> dict[str, dict]:
    tasks_dir = run_dir / "tasks"
    records: dict[str, dict] = {}
    if not tasks_dir.exists():
        return records
    for path in sorted(tasks_dir.glob("*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, list):
            if not data:
                continue
            record = data[-1]
        else:
            record = data
        task_id = record.get("task_id") or path.stem
        records[task_id] = record
    return records


def is_valid_scored(record: dict) -> bool:
    if record.get("score_status") == "valid_scored":
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


def extract_aime() -> list[dict]:
    records = load_task_records(AIME_DIR)
    out = []
    correct = 0
    total_score = 0.0
    valid_count = 0
    for task_id, record in records.items():
        if not is_valid_scored(record):
            continue
        valid_count += 1
        is_correct = bool(record.get("correct"))
        score = record.get("score", 0) or 0
        if is_correct:
            correct += 1
        total_score += score
        s = record.get("token_entropy_stats", {})
        out.append({
            "task_id": task_id,
            "correct": 1 if is_correct else 0,
            "score": score,
            "ground_truth": record.get("ground_truth", ""),
            "predicted": str(record.get("predicted", "")),
            "mean_entropy": s.get("mean", float("nan")),
            "n_tokens": s.get("n_tokens", 0),
            "wall_time_sec": record.get("wall_time_sec", 0),
            "traj_length": len(record.get("trajectory", [])),
        })
    print(f"AIME 2026: {correct}/{valid_count} correct (pass rate: {correct/valid_count*100:.1f}%)")
    print(f"AIME 2026: avg score = {total_score/valid_count:.1f} (total: {total_score}/{valid_count})")
    return out


def extract_imo() -> list[dict]:
    records = load_task_records(IMO_DIR)
    out = []
    correct = 0
    valid_count = 0
    for task_id, record in records.items():
        if not is_valid_scored(record):
            continue
        valid_count += 1
        is_correct = bool(record.get("correct"))
        if is_correct:
            correct += 1
        s = record.get("token_entropy_stats", {})
        out.append({
            "task_id": task_id,
            "correct": 1 if is_correct else 0,
            "score": record.get("score", 0),
            "ground_truth": record.get("ground_truth", ""),
            "predicted": str(record.get("predicted", "")),
            "mean_entropy": s.get("mean", float("nan")),
            "n_tokens": s.get("n_tokens", 0),
            "wall_time_sec": record.get("wall_time_sec", 0),
            "traj_length": len(record.get("trajectory", [])),
        })
    print(f"IMO-AnswerBench: {correct}/{valid_count} correct (accuracy: {correct/valid_count*100:.1f}%)")
    return out


def main() -> None:
    print("Extracting AIME 2026 and IMO-AnswerBench data...")

    # AIME 2026
    aime_rows = extract_aime()
    aime_fields = [
        "task_id", "correct", "score", "ground_truth", "predicted",
        "mean_entropy", "n_tokens", "wall_time_sec", "traj_length",
    ]
    aime_path = DATA_DIR / "aime2026_direct_summary.csv"
    with aime_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=aime_fields)
        writer.writeheader()
        writer.writerows(aime_rows)
    print(f"  wrote {aime_path} ({len(aime_rows)} rows)")

    # IMO-AnswerBench
    imo_rows = extract_imo()
    imo_fields = [
        "task_id", "correct", "score", "ground_truth", "predicted",
        "mean_entropy", "n_tokens", "wall_time_sec", "traj_length",
    ]
    imo_path = DATA_DIR / "imo_answerbench_direct_summary.csv"
    with imo_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=imo_fields)
        writer.writeheader()
        writer.writerows(imo_rows)
    print(f"  wrote {imo_path} ({len(imo_rows)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()
