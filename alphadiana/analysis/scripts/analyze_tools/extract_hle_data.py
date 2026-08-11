#!/usr/bin/env python3
"""
extract_hle_data.py -- extract HLE metrics from Direct/OpenCode/ZeroClaw results.

Run:
    python3 analyze_tools/extract_hle_data.py

Outputs (analyze_tools/data/):
    hle_accuracy_by_harness.csv  — correct/valid/errors per harness
    hle_entropy_by_outcome.csv   — per-task entropy + token count + outcome
    hle_operational_tax.csv      — behavioral vs deployable accuracy per harness
    hle_paired_net_gain.csv      — rescue/regression vs Direct (on shared task_ids)
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
DATA_DIR.mkdir(exist_ok=True)
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", REPO_ROOT / "results")).expanduser()

ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}

RUNS = {
    "directllm": {
        "dir": RESULTS_DIR / "phase9_directllm_qwen35_27b_hle_logprobs",
        "expected": 323,
    },
    "opencode": {
        "dir": RESULTS_DIR / "20260426-hle-opencode-qwen35_27b-v01",
        "expected": 591,
    },
    "zeroclaw": {
        "dir": RESULTS_DIR / "20260426-hle-zeroclaw-qwen35_27b-v01",
        "expected": 591,
    },
}


def load_task_records(run_dir: Path) -> dict[str, dict]:
    """Load all task records from tasks/ directory."""
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


def load_logprobs_entropy(harness: str, run_dir: Path, task_id: str) -> dict | None:
    """Load per-token logprobs and compute entropy stats."""
    # Try logprobs_int16 first (OpenCode, ZeroClaw)
    lp_dir = run_dir / "logprobs_int16"
    if lp_dir.exists():
        lp_path = lp_dir / f"{task_id}.jsonl"
        if lp_path.exists():
            entropies = []
            with lp_path.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    entropy = item.get("entropy_nats")
                    if isinstance(entropy, (int, float)):
                        entropies.append(entropy)
            if entropies:
                return {
                    "mean_entropy": fmean(entropies),
                    "n_tokens": len(entropies),
                    "max_entropy": max(entropies),
                    "p90_entropy": sorted(entropies)[int(0.9 * (len(entropies) - 1))],
                }
    # Try logprobs/ (Direct)
    lp_dir = run_dir / "logprobs"
    if lp_dir.exists():
        lp_path = lp_dir / f"{task_id}.jsonl"
        if lp_path.exists():
            entropies = []
            with lp_path.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    entropy = item.get("entropy_nats")
                    if isinstance(entropy, (int, float)):
                        entropies.append(entropy)
            if entropies:
                return {
                    "mean_entropy": fmean(entropies),
                    "n_tokens": len(entropies),
                    "max_entropy": max(entropies),
                    "p90_entropy": sorted(entropies)[int(0.9 * (len(entropies) - 1))],
                }
    return None


def is_valid_scored(record: dict) -> bool:
    if record.get("score_status") == "valid_scored":
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


def compute_accuracy(records: dict[str, dict]) -> tuple[int, int, int]:
    valid = [r for r in records.values() if is_valid_scored(r)]
    correct = sum(1 for r in valid if bool(r.get("correct")))
    errors = sum(1 for r in records.values() if r.get("score_status") in ERROR_STATUSES)
    return correct, len(valid), errors


# ─── 1. hle_accuracy_by_harness.csv ─────────────────────────────────────


def extract_accuracy_by_harness() -> list[dict]:
    out = []
    for harness, cfg in RUNS.items():
        records = load_task_records(cfg["dir"])
        correct, valid, errors = compute_accuracy(records)
        expected = cfg["expected"]
        out.append({
            "harness": harness,
            "expected_samples": expected,
            "task_records": len(records),
            "valid_scored": valid,
            "correct": correct,
            "error_records": errors,
            "accuracy": correct / valid if valid else float("nan"),
        })
    return out


# ─── 2. hle_entropy_by_outcome.csv ──────────────────────────────────────


def extract_entropy_by_outcome() -> list[dict]:
    out = []
    for harness, cfg in RUNS.items():
        records = load_task_records(cfg["dir"])
        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue
            s = record.get("token_entropy_stats", {})
            lp = load_logprobs_entropy(harness, cfg["dir"], task_id)
            row = {
                "harness": harness,
                "task_id": task_id,
                "correct": 1 if record.get("correct") else 0,
                "mean_entropy": s.get("mean", float("nan")),
                "n_tokens": s.get("n_tokens", 0),
                "wall_time_sec": record.get("wall_time_sec", 0),
                "traj_length": len(record.get("trajectory", [])),
                "logprob_mean_entropy": lp["mean_entropy"] if lp else float("nan"),
                "logprob_n_tokens": lp["n_tokens"] if lp else 0,
            }
            out.append(row)
    return out


# ─── 3. hle_operational_tax.csv ─────────────────────────────────────────


def extract_operational_tax() -> list[dict]:
    out = []
    for harness, cfg in RUNS.items():
        records = load_task_records(cfg["dir"])
        correct, valid, errors = compute_accuracy(records)
        expected = cfg["expected"]
        out.append({
            "harness": harness,
            "expected_samples": expected,
            "valid_scored": valid,
            "correct": correct,
            "error_records": errors,
            "behavioral_accuracy": correct / valid if valid else float("nan"),
            "operational_tax": (expected - valid) / expected,
            "deployable_accuracy": correct / expected,
        })
    return out


# ─── 4. hle_paired_net_gain.csv ─────────────────────────────────────────


def extract_paired_net_gain() -> list[dict]:
    direct_cfg = RUNS["directllm"]
    direct_records = load_task_records(direct_cfg["dir"])
    out = []
    for harness_name in ("opencode", "zeroclaw"):
        cfg = RUNS[harness_name]
        other_records = load_task_records(cfg["dir"])
        paired = 0
        rescue = 0
        regression = 0
        both_correct = 0
        both_wrong = 0
        skipped = 0
        for task_id, direct_record in direct_records.items():
            other_record = other_records.get(task_id)
            if not other_record:
                skipped += 1
                continue
            if not is_valid_scored(direct_record):
                skipped += 1
                continue
            if not is_valid_scored(other_record):
                skipped += 1
                continue
            paired += 1
            d_ok = bool(direct_record.get("correct"))
            h_ok = bool(other_record.get("correct"))
            if h_ok and not d_ok:
                rescue += 1
            elif d_ok and not h_ok:
                regression += 1
            elif d_ok and h_ok:
                both_correct += 1
            else:
                both_wrong += 1
        out.append({
            "harness": harness_name,
            "paired_valid_tasks": paired,
            "rescue": rescue,
            "regression": regression,
            "both_correct": both_correct,
            "both_wrong": both_wrong,
            "skipped_nonvalid_or_missing": skipped,
            "paired_net_gain": rescue - regression,
            "rescue_rate": rescue / paired if paired else float("nan"),
            "regression_rate": regression / paired if paired else float("nan"),
        })
    return out


# ─── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    print("Extracting HLE data...")

    # 1. Accuracy by harness
    acc_rows = extract_accuracy_by_harness()
    acc_fields = [
        "harness", "expected_samples", "task_records", "valid_scored",
        "correct", "error_records", "accuracy",
    ]
    acc_path = DATA_DIR / "hle_accuracy_by_harness.csv"
    with acc_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=acc_fields)
        writer.writeheader()
        writer.writerows(acc_rows)
    print(f"  wrote {acc_path} ({len(acc_rows)} rows)")
    for row in acc_rows:
        print(f"    {row['harness']}: {row['correct']}/{row['valid_scored']} = {row['accuracy']:.4f}")

    # 2. Entropy by outcome
    ent_rows = extract_entropy_by_outcome()
    ent_fields = [
        "harness", "task_id", "correct", "mean_entropy", "n_tokens",
        "wall_time_sec", "traj_length", "logprob_mean_entropy", "logprob_n_tokens",
    ]
    ent_path = DATA_DIR / "hle_entropy_by_outcome.csv"
    with ent_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ent_fields)
        writer.writeheader()
        writer.writerows(ent_rows)
    print(f"  wrote {ent_path} ({len(ent_rows)} rows)")

    # 3. Operational tax
    tax_rows = extract_operational_tax()
    tax_fields = [
        "harness", "expected_samples", "valid_scored", "correct",
        "error_records", "behavioral_accuracy", "operational_tax", "deployable_accuracy",
    ]
    tax_path = DATA_DIR / "hle_operational_tax.csv"
    with tax_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=tax_fields)
        writer.writeheader()
        writer.writerows(tax_rows)
    print(f"  wrote {tax_path} ({len(tax_rows)} rows)")
    for row in tax_rows:
        print(f"    {row['harness']}: beh={row['behavioral_accuracy']:.4f}, tax={row['operational_tax']:.4f}, dep={row['deployable_accuracy']:.4f}")

    # 4. Paired net gain
    gain_rows = extract_paired_net_gain()
    gain_fields = [
        "harness", "paired_valid_tasks", "rescue", "regression",
        "both_correct", "both_wrong", "skipped_nonvalid_or_missing",
        "paired_net_gain", "rescue_rate", "regression_rate",
    ]
    gain_path = DATA_DIR / "hle_paired_net_gain.csv"
    with gain_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=gain_fields)
        writer.writeheader()
        writer.writerows(gain_rows)
    print(f"  wrote {gain_path} ({len(gain_rows)} rows)")
    for row in gain_rows:
        print(f"    {row['harness']}: paired={row['paired_valid_tasks']}, net_gain={row['paired_net_gain']}")

    print("Done.")


if __name__ == "__main__":
    main()
