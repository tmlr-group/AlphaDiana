#!/usr/bin/env python3
"""
compute_trajectory_stats.py — aggregate trajectory statistics for all benchmarks.

Computes values for tables `tab:macro-behavioral-stats` and `tab:macro-uncertainty-stats`.

Run:
    python3 analyze_tools/compute_trajectory_stats.py

Outputs (analyze_tools/data/):
    trajectory_stats.csv   — behavioral stats per (model, harness, benchmark)
    uncertainty_stats.csv  — uncertainty stats per (model, harness)
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, median

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
RESULTS_DIR = ROOT / "results"

ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}

GPQA_RUNS = {
    "directllm": "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}

HLE_RUNS = {
    "directllm": {
        "dir": Path("/path/to/xxx/alphadiana_results/phase9_directllm_qwen35_27b_hle_logprobs"),
    },
    "opencode": {
        "dir": Path("/path/to/xxx/alphadiana-results/20260426-hle-opencode-qwen35_27b-v01"),
    },
    "zeroclaw": {
        "dir": Path("/path/to/xxx/alphadiana-results/20260426-hle-zeroclaw-qwen35_27b-v01"),
    },
}

CANONICAL_ACTIONS = ("plan", "reason", "tool_use", "verify", "recover", "answer")

# ─── Helpers ──────────────────────────────────────────────────────────────

def is_valid_scored(record: dict) -> bool:
    if record.get("score_status") == "valid_scored":
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


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


def load_gpqa_records(harness: str) -> dict[str, dict]:
    run_id = GPQA_RUNS[harness]
    run_dir = RESULTS_DIR / run_id
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.exists():
        tasks_dir = run_dir / run_id / "tasks"
    records: dict[str, dict] = {}
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


def classify_hle_canonical_action(step: dict, prev_action: str, step_idx: int, total_steps: int) -> str:
    """Classify a single HLE trajectory step into a canonical action."""
    t = step.get("type", "")
    content = (step.get("content") or "").lower()

    if t == "tool_use":
        return "tool_use"
    if t == "tool_result":
        return "observe"
    if t == "system":
        return "__instruction__"
    if t == "message":
        # Check for answer patterns in content
        if "\\boxed{" in content or "answer:" in content[:200] or "$$\\boxed{" in content:
            return "answer"
        # Check for verification patterns
        if "verify" in content[:200] or "check" in content[:200]:
            return "verify"
        # Check for recovery/retry patterns
        if "retry" in content[:100] or "try again" in content[:100] or "re-try" in content[:100]:
            return "recover"
        # Check for planning
        if "plan" in content[:100] or "approach" in content[:100] or "strategy" in content[:100]:
            return "plan"
        return "reason"
    return "other"


def compute_action_entropy(action_counts: dict[str, int]) -> float:
    total = sum(action_counts.values())
    if total <= 0:
        return 0.0
    return -sum(
        (c / total) * math.log2(c / total)
        for c in action_counts.values()
        if c > 0
    )


def load_logprobs_entropy_by_file(run_dir: Path, task_id: str) -> list[float] | None:
    """Load per-token entropies from logprobs files."""
    # Try logprobs_int16 first
    for lp_subdir in ("logprobs_int16", "logprobs"):
        lp_dir = run_dir / lp_subdir
        if not lp_dir.exists():
            continue
        # Try {task_id}.jsonl first
        lp_path = lp_dir / f"{task_id}.jsonl"
        if lp_path.exists():
            entropies = []
            with lp_path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    entropy = item.get("entropy_nats")
                    if isinstance(entropy, (int, float)):
                        entropies.append(entropy)
            if entropies:
                return entropies
        # For Direct runs with nested structure: task_id/sample_N.jsonl
        if lp_subdir == "logprobs":
            task_lp_dir = lp_dir / task_id
            if task_lp_dir.is_dir():
                for lp_file in sorted(task_lp_dir.glob("*.jsonl")):
                    entropies = []
                    with lp_file.open(encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            item = json.loads(line)
                            entropy = item.get("entropy_nats")
                            if isinstance(entropy, (int, float)):
                                entropies.append(entropy)
                    if entropies:
                        return entropies
    return None


# ─── GPQA behavioral stats (from existing action_events.csv) ─────────────

def compute_gpqa_behavioral_stats() -> dict[tuple[str, str], dict]:
    """Compute trajectory stats for GPQA using action_events.csv + task records."""
    events_path = ROOT / "results" / "phase14_gpqa_trajectory_analysis" / "action_events.csv"
    if not events_path.exists():
        print("WARNING: action_events.csv not found, cannot compute GPQA trajectory stats")
        return {}

    events: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with events_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["score_status"] != "valid_scored":
                continue
            events[(row["harness"], row["task_id"])].append(row)

    stats: dict[tuple[str, str], dict] = {}
    for harness in sorted(GPQA_RUNS):
        records = load_gpqa_records(harness)
        per_task: dict[str, dict] = {}

        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue

            key = (harness, task_id)
            task_events = events.get(key, [])

            # Sort events by step_id
            task_events.sort(key=lambda r: int(r["step_id"]) if r["step_id"].isdigit() else 0)

            # Trajectory length (from task record)
            traj_length = len(record.get("trajectory", []))

            # Canonical action counts
            action_counts = Counter()
            for event in task_events:
                action = event.get("canonical_action", "")
                if action in CANONICAL_ACTIONS:
                    action_counts[action] += 1

            # Retry (has recover)
            has_recover = action_counts.get("recover", 0) >= 1

            # Behavioral entropy
            beh_entropy = compute_action_entropy(dict(action_counts))

            # Token count (for solved-cost)
            token_stats = record.get("token_entropy_stats", {})
            n_tokens = int(token_stats.get("n_tokens", 0)) if token_stats else 0

            # Error recovery: count tool errors and check if followed by non-error
            tool_errors = 0
            recovered_errors = 0
            for i, event in enumerate(task_events):
                obs_status = event.get("observation_status", "")
                if obs_status in ("error", "fail", "timeout"):
                    tool_errors += 1
                    # Check if next non-trivial step exists
                    for j in range(i + 1, len(task_events)):
                        next_event = task_events[j]
                        if next_event.get("canonical_action") in ("tool_use", "reason", "verify", "answer", "plan"):
                            recovered_errors += 1
                            break

            correct = bool(record.get("correct"))
            per_task[task_id] = {
                "traj_length": traj_length,
                "action_counts": dict(action_counts),
                "beh_entropy": beh_entropy,
                "n_tokens": n_tokens,
                "has_recover": has_recover,
                "tool_errors": tool_errors,
                "recovered_errors": recovered_errors,
                "correct": correct,
            }

        if not per_task:
            continue

        # Aggregate
        values = list(per_task.values())
        avg_turns = fmean(v["traj_length"] for v in values)
        avg_tool_calls = fmean(v["action_counts"].get("tool_use", 0) for v in values)
        retry_rate = sum(1 for v in values if v["has_recover"]) / len(values)

        # Error-recovery rate: total recovered / total errors across all tasks
        total_errors = sum(v["tool_errors"] for v in values)
        total_recovered = sum(v["recovered_errors"] for v in values)
        error_recovery_rate = total_recovered / total_errors if total_errors > 0 else float("nan")

        # Behavioral entropy
        avg_beh_entropy = fmean(v["beh_entropy"] for v in values)

        # Avg solved-cost (correct only)
        correct_tokens = [v["n_tokens"] for v in values if v["correct"]]
        avg_solved_cost = fmean(correct_tokens) if correct_tokens else float("nan")

        stats[("Qwen3.5-27B", harness, "GPQA-Diamond")] = {
            "avg_turns": round(avg_turns, 1),
            "avg_tool_calls": round(avg_tool_calls, 1),
            "retry_rate": round(retry_rate, 4),
            "error_recovery_rate": round(error_recovery_rate, 4) if not math.isnan(error_recovery_rate) else float("nan"),
            "behavioral_entropy": round(avg_beh_entropy, 3),
            "avg_solved_cost": round(avg_solved_cost, 0) if not math.isnan(avg_solved_cost) else float("nan"),
            "n_tasks": len(values),
        }

    return stats


# ─── HLE behavioral stats (from trajectory inspection) ───────────────────

def compute_hle_behavioral_stats() -> dict[tuple[str, str], dict]:
    """Compute trajectory stats for HLE from task records + trajectory inspection."""
    stats: dict[tuple[str, str], dict] = {}

    for harness, cfg in HLE_RUNS.items():
        records = load_task_records(cfg["dir"])
        per_task: dict[str, dict] = {}

        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue

            trajectory = record.get("trajectory", [])
            traj_length = len(trajectory)

            # Classify canonical actions from trajectory
            action_counts: Counter = Counter()
            tool_errors = 0
            recovered_errors = 0
            prev_action = ""

            for idx, step in enumerate(trajectory):
                action = classify_hle_canonical_action(step, prev_action, idx, traj_length)
                if action in CANONICAL_ACTIONS:
                    action_counts[action] += 1
                if action != "__instruction__":
                    prev_action = action

            # Error recovery from tool_result with error status
            for idx, step in enumerate(trajectory):
                if step.get("type") == "tool_result":
                    content = step.get("content", "") or ""
                    try:
                        result_data = json.loads(content) if isinstance(content, str) else content
                        if isinstance(result_data, dict):
                            status = result_data.get("status", "")
                            if status in ("error", "fail", "timeout", "rate_limit"):
                                tool_errors += 1
                                # Check next actions for recovery
                                for j in range(idx + 1, min(idx + 5, len(trajectory))):
                                    next_action = classify_hle_canonical_action(trajectory[j], "", j, traj_length)
                                    if next_action == "tool_use":
                                        recovered_errors += 1
                                        break
                    except (json.JSONDecodeError, AttributeError):
                        pass

            has_recover = action_counts.get("recover", 0) >= 1
            beh_entropy = compute_action_entropy(dict(action_counts))

            token_stats = record.get("token_entropy_stats", {})
            n_tokens = int(token_stats.get("n_tokens", 0)) if token_stats else 0
            correct = bool(record.get("correct"))

            per_task[task_id] = {
                "traj_length": traj_length,
                "action_counts": dict(action_counts),
                "beh_entropy": beh_entropy,
                "n_tokens": n_tokens,
                "has_recover": has_recover,
                "tool_errors": tool_errors,
                "recovered_errors": recovered_errors,
                "correct": correct,
            }

        if not per_task:
            continue

        values = list(per_task.values())
        avg_turns = fmean(v["traj_length"] for v in values)
        avg_tool_calls = fmean(v["action_counts"].get("tool_use", 0) for v in values)
        retry_rate = sum(1 for v in values if v["has_recover"]) / len(values)

        total_errors = sum(v["tool_errors"] for v in values)
        total_recovered = sum(v["recovered_errors"] for v in values)
        error_recovery_rate = total_recovered / total_errors if total_errors > 0 else float("nan")

        avg_beh_entropy = fmean(v["beh_entropy"] for v in values)

        correct_tokens = [v["n_tokens"] for v in values if v["correct"]]
        avg_solved_cost = fmean(correct_tokens) if correct_tokens else float("nan")

        stats[("Qwen3.5-27B", harness, "HLE")] = {
            "avg_turns": round(avg_turns, 1),
            "avg_tool_calls": round(avg_tool_calls, 1),
            "retry_rate": round(retry_rate, 4),
            "error_recovery_rate": round(error_recovery_rate, 4) if not math.isnan(error_recovery_rate) else float("nan"),
            "behavioral_entropy": round(avg_beh_entropy, 3),
            "avg_solved_cost": round(avg_solved_cost, 0) if not math.isnan(avg_solved_cost) else float("nan"),
            "n_tasks": len(values),
        }

    return stats


# ─── Uncertainty stats ─────────────────────────────────────────────────

def compute_uncertainty_stats() -> dict[tuple[str, str], dict]:
    """Compute uncertainty stats for GPQA and HLE harnesses."""
    stats: dict[tuple[str, str], dict] = {}

    # GPQA
    for harness in sorted(GPQA_RUNS):
        run_id = GPQA_RUNS[harness]
        run_dir = RESULTS_DIR / run_id
        records = load_gpqa_records(harness)

        task_entropies: list[dict] = []
        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue
            correct = bool(record.get("correct"))

            lp_entropies = load_logprobs_entropy_by_file(run_dir, task_id)
            token_stats = record.get("token_entropy_stats", {})
            task_mean_entropy = float(token_stats.get("mean", 0)) if token_stats else 0
            n_tokens = int(token_stats.get("n_tokens", 0)) if token_stats else 0

            if lp_entropies and len(lp_entropies) > 0:
                avg_decision_lp = fmean(lp_entropies)
                # Final answer log-prob (last 5% of tokens)
                tail_start = int(len(lp_entropies) * 0.95)
                final_answer_lp = fmean(lp_entropies[tail_start:]) if tail_start < len(lp_entropies) else avg_decision_lp
                # Uncertainty volatility: std of per-turn mean entropy
                n_chunks = max(1, len(lp_entropies) // 50)
                chunk_size = len(lp_entropies) // n_chunks
                chunk_means = []
                for c in range(n_chunks):
                    chunk = lp_entropies[c * chunk_size : (c + 1) * chunk_size]
                    if chunk:
                        chunk_means.append(fmean(chunk))
                volatility = statistics.stdev(chunk_means) if len(chunk_means) >= 2 else float("nan")
            else:
                avg_decision_lp = task_mean_entropy
                final_answer_lp = float("nan")
                volatility = float("nan")

            task_entropies.append({
                "task_id": task_id,
                "correct": correct,
                "mean_entropy": avg_decision_lp,
                "final_lp": final_answer_lp,
                "volatility": volatility,
                "n_tokens": n_tokens,
            })

        if not task_entropies:
            continue

        # Avg decision log-prob
        valid_entropies = [t["mean_entropy"] for t in task_entropies if not math.isnan(t["mean_entropy"])]
        avg_decision_lp = fmean(valid_entropies) if valid_entropies else float("nan")

        # Avg final answer log-prob
        valid_final = [t["final_lp"] for t in task_entropies if not math.isnan(t["final_lp"])]
        final_answer_lp = fmean(valid_final) if valid_final else float("nan")

        # Uncertainty volatility
        valid_vol = [t["volatility"] for t in task_entropies if not math.isnan(t["volatility"])]
        uncertainty_vol = fmean(valid_vol) if valid_vol else float("nan")

        # Confidence-correctness gap: P(correct|low_entropy) - P(correct|high_entropy)
        median_entropy = median(t["mean_entropy"] for t in task_entropies if not math.isnan(t["mean_entropy"]))
        low_entropy = [t for t in task_entropies if t["mean_entropy"] <= median_entropy]
        high_entropy = [t for t in task_entropies if t["mean_entropy"] > median_entropy]
        p_correct_low = sum(1 for t in low_entropy if t["correct"]) / len(low_entropy) if low_entropy else float("nan")
        p_correct_high = sum(1 for t in high_entropy if t["correct"]) / len(high_entropy) if high_entropy else float("nan")
        conf_correct_gap = p_correct_low - p_correct_high

        stats[("Qwen3.5-27B", harness, "GPQA-Diamond")] = {
            "avg_decision_logprob": round(avg_decision_lp, 4),
            "final_answer_logprob": round(final_answer_lp, 4) if not math.isnan(final_answer_lp) else "logprob_unavailable",
            "uncertainty_volatility": round(uncertainty_vol, 4) if not math.isnan(uncertainty_vol) else "logprob_unavailable",
            "confidence_correctness_gap": round(conf_correct_gap, 4),
            "median_entropy_split": round(median_entropy, 4),
            "n_tasks": len(task_entropies),
        }

    # HLE
    for harness, cfg in HLE_RUNS.items():
        records = load_task_records(cfg["dir"])
        task_entropies = []

        for task_id, record in records.items():
            if not is_valid_scored(record):
                continue
            correct = bool(record.get("correct"))

            lp_entropies = load_logprobs_entropy_by_file(cfg["dir"], task_id)
            token_stats = record.get("token_entropy_stats", {})
            task_mean_entropy = float(token_stats.get("mean", 0)) if token_stats else 0
            n_tokens = int(token_stats.get("n_tokens", 0)) if token_stats else 0

            if lp_entropies and len(lp_entropies) > 0:
                avg_decision_lp = fmean(lp_entropies)
                tail_start = int(len(lp_entropies) * 0.95)
                final_answer_lp = fmean(lp_entropies[tail_start:]) if tail_start < len(lp_entropies) else avg_decision_lp
                n_chunks = max(1, len(lp_entropies) // 50)
                chunk_size = len(lp_entropies) // n_chunks
                chunk_means = [fmean(lp_entropies[c * chunk_size:(c + 1) * chunk_size]) for c in range(n_chunks) if lp_entropies[c * chunk_size:(c + 1) * chunk_size]]
                volatility = statistics.stdev(chunk_means) if len(chunk_means) >= 2 else float("nan")
            else:
                avg_decision_lp = task_mean_entropy if n_tokens > 0 else float("nan")
                final_answer_lp = float("nan")
                volatility = float("nan")

            task_entropies.append({
                "correct": correct,
                "mean_entropy": avg_decision_lp,
                "final_lp": final_answer_lp,
                "volatility": volatility,
                "n_tokens": n_tokens,
            })

        if not task_entropies:
            continue

        valid_entropies = [t["mean_entropy"] for t in task_entropies if not math.isnan(t["mean_entropy"])]
        avg_decision_lp = fmean(valid_entropies) if valid_entropies else float("nan")

        valid_final = [t["final_lp"] for t in task_entropies if not math.isnan(t["final_lp"])]
        final_answer_lp = fmean(valid_final) if valid_final else float("nan")

        valid_vol = [t["volatility"] for t in task_entropies if not math.isnan(t["volatility"])]
        uncertainty_vol = fmean(valid_vol) if valid_vol else float("nan")

        median_entropy = median(t["mean_entropy"] for t in task_entropies if not math.isnan(t["mean_entropy"]))
        low_entropy = [t for t in task_entropies if t["mean_entropy"] <= median_entropy]
        high_entropy = [t for t in task_entropies if t["mean_entropy"] > median_entropy]
        p_correct_low = sum(1 for t in low_entropy if t["correct"]) / len(low_entropy) if low_entropy else float("nan")
        p_correct_high = sum(1 for t in high_entropy if t["correct"]) / len(high_entropy) if high_entropy else float("nan")
        conf_correct_gap = p_correct_low - p_correct_high

        stats[("Qwen3.5-27B", harness, "HLE")] = {
            "avg_decision_logprob": round(avg_decision_lp, 4),
            "final_answer_logprob": round(final_answer_lp, 4) if not math.isnan(final_answer_lp) else "logprob_unavailable",
            "uncertainty_volatility": round(uncertainty_vol, 4) if not math.isnan(uncertainty_vol) else "logprob_unavailable",
            "confidence_correctness_gap": round(conf_correct_gap, 4),
            "median_entropy_split": round(median_entropy, 4),
            "n_tasks": len(task_entropies),
        }

    return stats


# ─── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    print("Computing trajectory statistics...")

    # Behavioral stats
    gpqa_stats = compute_gpqa_behavioral_stats()
    hle_stats = compute_hle_behavioral_stats()
    all_behavioral = {**gpqa_stats, **hle_stats}

    fields = [
        "model", "harness", "benchmark", "avg_turns", "avg_tool_calls",
        "retry_rate", "error_recovery_rate", "behavioral_entropy",
        "avg_solved_cost", "n_tasks",
    ]
    be_path = DATA_DIR / "trajectory_stats.csv"
    with be_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for (model, harness, benchmark), vals in sorted(all_behavioral.items()):
            row = {"model": model, "harness": harness, "benchmark": benchmark, **vals}
            writer.writerow(row)
            print(f"  {model}/{harness}/{benchmark}: turns={vals['avg_turns']}, tools={vals['avg_tool_calls']}, retry={vals['retry_rate']:.3f}, entropy={vals['behavioral_entropy']:.3f}")
    print(f"  wrote {be_path} ({len(all_behavioral)} rows)")

    # Uncertainty stats
    unc_stats = compute_uncertainty_stats()
    unc_fields = [
        "model", "harness", "benchmark", "avg_decision_logprob",
        "final_answer_logprob", "uncertainty_volatility",
        "confidence_correctness_gap", "median_entropy_split", "n_tasks",
    ]
    unc_path = DATA_DIR / "uncertainty_stats.csv"
    with unc_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=unc_fields)
        writer.writeheader()
        for (model, harness, benchmark), vals in sorted(unc_stats.items()):
            row = {"model": model, "harness": harness, "benchmark": benchmark, **vals}
            writer.writerow(row)
            print(f"  {model}/{harness}/{benchmark}: decision_lp={vals['avg_decision_logprob']}, final_lp={vals['final_answer_logprob']}, gap={vals['confidence_correctness_gap']:.4f}")
    print(f"  wrote {unc_path} ({len(unc_stats)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()
