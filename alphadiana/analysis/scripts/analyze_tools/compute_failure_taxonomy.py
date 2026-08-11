#!/usr/bin/env python3
"""
compute_failure_taxonomy.py — classify failed trajectories into failure taxonomy.

Computes Table `tab:macro-failure-taxonomy`.

Run:
    python3 analyze_tools/compute_failure_taxonomy.py

Outputs (analyze_tools/data/):
    failure_taxonomy.csv  — per (model, harness) failure mode breakdown
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", REPO_ROOT / "results")).expanduser()

GPQA_RUNS = {
    "directllm": "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}

HLE_RUNS = {
    "directllm": RESULTS_DIR / "phase9_directllm_qwen35_27b_hle_logprobs",
    "opencode": RESULTS_DIR / "20260426-hle-opencode-qwen35_27b-v01",
    "zeroclaw": RESULTS_DIR / "20260426-hle-zeroclaw-qwen35_27b-v01",
}

CAUSE_BUCKET_MAP = {
    # Existing degradation cause buckets -> failure taxonomy
    "answer_format_or_extraction": "Format/Verifier",
    "long_low_entropy_or_overrun": "Reasoning",
    "tool_use_not_integrated": "Tool Selection",
    "verification_without_conversion": "Observation",
    "planning_recovery_churn": "Recovery",
    "premature_answer": "Reasoning",  # Reasoning failure (premature commitment)
    "operational_error": "Execution",
    "answer_changed_valid": "Memory/State",
    "both_wrong": "Reasoning",  # Both Direct and harness wrong — reasoning failure
}

# FAILURE TAXONOMY: 8 categories
FAILURE_MODES = [
    "Reasoning",
    "Tool Selection",
    "Execution",
    "Observation",
    "Recovery",
    "Memory/State",
    "Budget",
    "Format/Verifier",
]


def is_valid_scored(record: dict) -> bool:
    if record.get("score_status") == "valid_scored":
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


def find_tasks_dir(run_dir: Path) -> Path | None:
    """Find tasks/ directory, handling nested run_id/tasks structure."""
    candidates = [
        run_dir / "tasks",
        run_dir / run_dir.name / "tasks",
    ]
    for candidate in candidates:
        if candidate.exists() and any(candidate.glob("*.json")):
            return candidate
    return None


def load_task_records(run_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    tasks_dir = find_tasks_dir(run_dir)
    if tasks_dir is None:
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


# ─── GPQA failure taxonomy from degradation diagnostics ────────────────

def compute_gpqa_failure_taxonomy() -> dict[tuple[str, str], Counter]:
    """Read degradation_cause_buckets.csv and map to failure taxonomy."""
    cause_path = DATA_DIR / "degradation_cause_buckets.csv"
    if not cause_path.exists():
        print("WARNING: degradation_cause_buckets.csv not found")
        return {}

    harness_failures: dict[str, Counter] = defaultdict(Counter)
    harness_total_failed: dict[str, int] = defaultdict(int)

    with cause_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            harness = row["harness"]
            cause = row["cause_bucket"]
            n = int(row["n"])
            outcome = row["paired_outcome"]

            # Only consider regression (harness-specific failures)
            if cause in ("both_correct", "both_wrong", "rescue"):
                continue

            failure_mode = CAUSE_BUCKET_MAP.get(cause, "Reasoning")
            harness_failures[harness][failure_mode] += n
            harness_total_failed[harness] += n

    # Add DirectLLM baseline: all regression tasks are Reasoning or Format/Verifier
    # For directllm, we look at degradation diagnostics output
    # Actually for DirectLLM, there's no paired analysis in degradation_diagnostics.py
    # Let's compute from task records instead

    results: dict[tuple[str, str], Counter] = {}
    for harness in GPQA_RUNS:
        results[("Qwen3.5-27B", harness)] = harness_failures.get(harness, Counter())

    return results


def compute_hle_failure_taxonomy() -> dict[tuple[str, str], Counter]:
    """Compute failure taxonomy for HLE by analyzing trajectory-level failure patterns."""
    results: dict[tuple[str, str], Counter] = {}

    for harness, run_dir in HLE_RUNS.items():
        records = load_task_records(run_dir)
        failure_counter: Counter = Counter()
        num_failed = 0

        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue
            correct = bool(record.get("correct"))
            if correct:
                continue  # skip correct tasks

            num_failed += 1
            failure_mode = classify_hle_failure(record, harness)
            failure_counter[failure_mode] += 1

        results[("Qwen3.5-27B", harness)] = failure_counter

    return results


def classify_hle_failure(record: dict, harness: str) -> str:
    """Classify a single HLE failed trajectory into the dominant failure mode."""
    predicted = str(record.get("predicted", ""))
    score_status = record.get("score_status", "")
    finish_reason = record.get("finish_reason", "")
    trajectory = record.get("trajectory", [])

    # Check for execution/operational errors
    ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}
    if score_status in ERROR_STATUSES:
        return "Execution"

    # Check for budget exhaustion
    if finish_reason in ("length", "max_tokens", "timeout"):
        return "Budget"

    # Check format/verifier issues (for DirectLLM)
    if harness == "directllm":
        if predicted in ("None", "", "none", "null", None):
            return "Format/Verifier"
        return "Reasoning"

    # For harness runs, analyze trajectory
    if not trajectory or len(trajectory) < 5:
        # Very short trajectory without enough tool interaction
        if harness == "opencode":
            # Check if tools were requested but not used
            tool_use_count = sum(1 for step in trajectory if step.get("type") == "tool_use")
            if tool_use_count == 0:
                return "Tool Selection"
        return "Reasoning"

    # Classify actions from trajectory
    tool_use_count = 0
    verify_in_content = 0
    error_or_fail = 0
    has_recovery_attempt = False
    has_repeated_no_progress = False

    action_pattern = []

    for step in trajectory:
        t = step.get("type", "")
        content = (step.get("content") or "").lower()

        if t == "tool_use":
            tool_use_count += 1
            action_pattern.append("tool")
        elif t == "message":
            if "verify" in content[:200] or "check" in content[:200]:
                verify_in_content += 1
                action_pattern.append("verify")
            if "retry" in content[:100] or "try again" in content[:100]:
                has_recovery_attempt = True
                action_pattern.append("recover")
            if "\\boxed{" in content or "answer:" in content[:100]:
                action_pattern.append("answer")

    # Budget: very long trajectory with many tools but no answer
    if len(trajectory) > 30 and "answer" not in action_pattern:
        return "Budget"

    # Tool Selection: OpenCode with zero tool use when tools are expected
    if harness == "opencode" and tool_use_count == 0 and len(trajectory) > 3:
        return "Tool Selection"

    # Execution: many tool errors
    if error_or_fail > 2:
        return "Execution"

    # Recovery: attempted recovery but still wrong
    if has_recovery_attempt:
        return "Recovery"

    # Memory/State: long repeated patterns without progress
    if len(trajectory) > 20 and "answer" not in action_pattern:
        # Check for repeated tool_use-message cycles without answer
        if len(set(str(s.get("type", "")) for s in trajectory[-10:])) <= 2:
            return "Memory/State"

    # Observation: has verification but no change after
    if verify_in_content > 1 and "answer" in action_pattern:
        # Verify then answer directly — no behavioral change
        last_verify_pos = max(i for i, a in enumerate(action_pattern) if a == "verify")
        last_answer_pos = max(i for i, a in enumerate(action_pattern) if a == "answer")
        if last_verify_pos < last_answer_pos:
            # Check if there are actions between verify and answer
            intervening = action_pattern[last_verify_pos + 1 : last_answer_pos]
            if not any(a not in ("verify", "answer") for a in intervening):
                return "Observation"

    # Default: Reasoning failure
    return "Reasoning"


def compute_directllm_failure_taxonomy() -> dict[tuple[str, str], Counter]:
    """Compute DirectLLM failure taxonomy from task records (GPQA + HLE)."""
    results: dict[tuple[str, str], Counter] = {}

    # GPQA Direct
    run_id = GPQA_RUNS["directllm"]
    run_dir = RESULTS_DIR / run_id
    records = load_task_records(run_dir)
    failure_counter: Counter = Counter()
    num_failed = 0
    for task_id, record in records.items():
        if not is_valid_scored(record):
            continue
        correct = bool(record.get("correct"))
        if correct:
            continue
        num_failed += 1
        predicted = str(record.get("predicted", ""))
        if predicted in ("None", "", "none", "null", "None"):
            failure_counter["Format/Verifier"] += 1
        else:
            failure_counter["Reasoning"] += 1
    results[("Qwen3.5-27B", "directllm", "GPQA-Diamond")] = failure_counter

    # HLE Direct
    run_dir = HLE_RUNS["directllm"]
    records = load_task_records(run_dir)
    failure_counter = Counter()
    num_failed = 0
    for task_id, record in records.items():
        if not is_valid_scored(record):
            continue
        correct = bool(record.get("correct"))
        if correct:
            continue
        num_failed += 1
        predicted = str(record.get("predicted", ""))
        if predicted in ("None", "", "none", "null", "None"):
            failure_counter["Format/Verifier"] += 1
        else:
            failure_counter["Reasoning"] += 1
    results[("Qwen3.5-27B", "directllm", "HLE")] = failure_counter

    return results


def main() -> None:
    print("Computing failure taxonomy...")

    # GPQA failure taxonomy (from degradation diagnostics, per-harness)
    gpqa_failures = compute_gpqa_failure_taxonomy()
    hle_failures = compute_hle_failure_taxonomy()
    directllm_failures = compute_directllm_failure_taxonomy()

    # Combine all results
    all_results: dict = {}

    # GPQA harness failures (OpenClaw, OpenCode, ZeroClaw)
    for (model, harness), counter in gpqa_failures.items():
        if harness == "directllm":
            continue  # handle separately
        total = sum(counter.values())
        if total == 0:
            continue
        harness_direct_failures = directllm_failures.get(("Qwen3.5-27B", "directllm", "GPQA-Diamond"), Counter())
        for mode in FAILURE_MODES:
            n = counter.get(mode, 0)
            row_key = (model, harness, "GPQA-Diamond")
            if row_key not in all_results:
                all_results[row_key] = {"total_failed": 0, "modes": Counter()}
            all_results[row_key]["modes"][mode] = n
            all_results[row_key]["total_failed"] += n

    # DirectLLM failures (GPQA + HLE)
    for (model, harness, benchmark), counter in directllm_failures.items():
        total = sum(counter.values())
        row_key = (model, harness, benchmark)
        if row_key not in all_results:
            all_results[row_key] = {"total_failed": 0, "modes": Counter()}
        for mode in FAILURE_MODES:
            n = counter.get(mode, 0)
            all_results[row_key]["modes"][mode] = n
            all_results[row_key]["total_failed"] += n

    # HLE failures
    for (model, harness), counter in hle_failures.items():
        if harness == "directllm":
            continue  # handled above
        total = sum(counter.values())
        if total == 0:
            continue
        row_key = (model, harness, "HLE")
        if row_key not in all_results:
            all_results[row_key] = {"total_failed": 0, "modes": Counter()}
        for mode in FAILURE_MODES:
            n = counter.get(mode, 0)
            all_results[row_key]["modes"][mode] = n
            all_results[row_key]["total_failed"] += n

    # Write CSV
    fields = ["model", "harness", "benchmark", "failure_mode", "n", "share_of_failed"]
    path = DATA_DIR / "failure_taxonomy.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for (model, harness, benchmark), data in sorted(all_results.items()):
            total = data["total_failed"]
            if total == 0:
                continue
            print(f"  {model}/{harness}/{benchmark}: {total} failed trajectories")
            for mode in FAILURE_MODES:
                n = data["modes"].get(mode, 0)
                share = n / total if total > 0 else 0.0
                if n > 0:
                    print(f"    {mode}: {n}/{total} = {share:.3f}")
                writer.writerow({
                    "model": model,
                    "harness": harness,
                    "benchmark": benchmark,
                    "failure_mode": mode,
                    "n": n,
                    "share_of_failed": round(share, 4),
                })
    print(f"  wrote {path}")
    print("Done.")


if __name__ == "__main__":
    main()
