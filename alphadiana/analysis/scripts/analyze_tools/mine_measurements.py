#!/usr/bin/env python3
"""
mine_measurements.py -- derive general-purpose agent measurement tables.

This script is intentionally offline-only. It reads persisted ResultStore
artifacts plus analyze_tools CSVs and writes compact CSV/JSON summaries under
analyze_tools/data/.

Run:
    python3 analyze_tools/mine_measurements.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DATA_DIR = ROOT / "analyze_tools" / "data"
PHASE14_DIR = RESULTS_DIR / "phase14_gpqa_trajectory_analysis"
csv.field_size_limit(sys.maxsize)

DIRECT_RUN = "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs"
RUNS = {
    "directllm": DIRECT_RUN,
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}
ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"  wrote {path}")


def load_task_records(run_id: str) -> dict[str, dict]:
    tasks_dir = RESULTS_DIR / run_id / "tasks"
    if not tasks_dir.exists():
        nested = RESULTS_DIR / run_id / run_id / "tasks"
        if nested.exists():
            tasks_dir = nested
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


def is_valid_scored(record: dict) -> bool:
    if record.get("score_status") == "valid_scored":
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


def accuracy_from_records(records: dict[str, dict]) -> tuple[int, int, int]:
    valid = [r for r in records.values() if is_valid_scored(r)]
    correct = sum(1 for r in valid if bool(r.get("correct")))
    errors = sum(1 for r in records.values() if r.get("score_status") in ERROR_STATUSES)
    return correct, len(valid), errors


def jsd(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)

    def kl(a: dict[str, float], b: dict[str, float]) -> float:
        total = 0.0
        for key in keys:
            av = a.get(key, 0.0)
            bv = b.get(key, 0.0)
            if av > 0 and bv > 0:
                total += av * math.log2(av / bv)
        return total

    m = {key: (p.get(key, 0.0) + q.get(key, 0.0)) / 2.0 for key in keys}
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def distribution(counter: Counter[str]) -> dict[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in counter.items()}


def extract_entropy_token_quadrants() -> list[dict[str, object]]:
    rows = read_csv(DATA_DIR / "openclaw_entropy_by_outcome.csv")
    token_values = [int(float(r["n_tokens"])) for r in rows if int(float(r["n_tokens"])) > 0]
    entropy_values = [float(r["mean_entropy"]) for r in rows if int(float(r["n_tokens"])) > 0]
    token_q75 = sorted(token_values)[int(0.75 * (len(token_values) - 1))]
    entropy_q25 = sorted(entropy_values)[int(0.25 * (len(entropy_values) - 1))]
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        n_tokens = int(float(row["n_tokens"]))
        if n_tokens <= 0:
            continue
        entropy = float(row["mean_entropy"])
        cost = "long" if n_tokens >= token_q75 else "short"
        confidence = "low_entropy" if entropy <= entropy_q25 else "higher_entropy"
        buckets[f"{confidence}_{cost}"].append(row)

    out = []
    for bucket, vals in sorted(buckets.items()):
        wrong = sum(1 for v in vals if v["correct"] == "0")
        out.append(
            {
                "bucket": bucket,
                "n": len(vals),
                "wrong_rate": wrong / len(vals),
                "median_tokens": median(int(float(v["n_tokens"])) for v in vals),
                "median_entropy": median(float(v["mean_entropy"]) for v in vals),
                "median_wall_time_min": median(float(v["wall_time_min"]) for v in vals),
                "token_threshold_q75": token_q75,
                "entropy_threshold_q25": entropy_q25,
            }
        )
    return out


def extract_confidence_inversion() -> list[dict[str, object]]:
    rows = [
        r
        for r in read_csv(DATA_DIR / "openclaw_entropy_by_outcome.csv")
        if int(float(r["n_tokens"])) > 0
    ]
    entropies = sorted({round(float(r["mean_entropy"]), 3) for r in rows})
    candidates = entropies[:: max(1, len(entropies) // 24)]
    if entropies[-1] not in candidates:
        candidates.append(entropies[-1])
    out = []
    for threshold in candidates:
        low = [r for r in rows if float(r["mean_entropy"]) <= threshold]
        high = [r for r in rows if float(r["mean_entropy"]) > threshold]
        if not low or not high:
            continue
        low_wrong = sum(1 for r in low if r["correct"] == "0") / len(low)
        high_wrong = sum(1 for r in high if r["correct"] == "0") / len(high)
        out.append(
            {
                "entropy_threshold": threshold,
                "low_entropy_n": len(low),
                "high_entropy_n": len(high),
                "wrong_rate_low_entropy": low_wrong,
                "wrong_rate_high_entropy": high_wrong,
                "inversion_lift": low_wrong - high_wrong,
            }
        )
    return out


def extract_posttool_state_shift() -> list[dict[str, object]]:
    rows = read_csv(DATA_DIR / "baseline_vs_posttool.csv")
    by_label: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        by_label[row["turn_label"]][int(row["correct"])] = row

    out = []
    baseline = by_label.get("baseline", {})
    baseline_delta = None
    if 0 in baseline and 1 in baseline:
        baseline_delta = float(baseline[0]["mean_entropy"]) - float(baseline[1]["mean_entropy"])
    for label in sorted(by_label):
        pair = by_label[label]
        if 0 not in pair or 1 not in pair:
            continue
        wrong_entropy = float(pair[0]["mean_entropy"])
        correct_entropy = float(pair[1]["mean_entropy"])
        delta = wrong_entropy - correct_entropy
        out.append(
            {
                "turn_label": label,
                "correct_entropy": correct_entropy,
                "wrong_entropy": wrong_entropy,
                "wrong_minus_correct_entropy": delta,
                "separation_gain_vs_baseline": (
                    delta - baseline_delta if baseline_delta is not None else float("nan")
                ),
                "correct_n": int(pair[1]["n"]),
                "wrong_n": int(pair[0]["n"]),
            }
        )

    boundary = read_csv(DATA_DIR / "tool_boundary_profile.csv")
    positive_offsets = [r for r in boundary if 0 <= int(r["offset"]) <= 15]
    shock = sum(float(r["mean_wrong"]) - float(r["mean_correct"]) for r in positive_offsets)
    out.append(
        {
            "turn_label": "boundary_shock_integral_0_15",
            "correct_entropy": "",
            "wrong_entropy": "",
            "wrong_minus_correct_entropy": shock,
            "separation_gain_vs_baseline": "",
            "correct_n": "",
            "wrong_n": "",
        }
    )
    return out


def extract_operational_tax() -> list[dict[str, object]]:
    out = []
    for harness, run_id in RUNS.items():
        records = load_task_records(run_id)
        correct, valid, errors = accuracy_from_records(records)
        expected = 198
        out.append(
            {
                "harness": harness,
                "expected_samples": expected,
                "task_records": len(records),
                "valid_scored": valid,
                "correct": correct,
                "error_records": errors,
                "behavioral_accuracy": correct / valid if valid else float("nan"),
                "operational_tax": (expected - valid) / expected,
                "deployable_accuracy": correct / expected,
            }
        )
    return out


def extract_paired_net_gain() -> list[dict[str, object]]:
    direct = load_task_records(DIRECT_RUN)
    out = []
    for harness, run_id in RUNS.items():
        if harness == "directllm":
            continue
        other = load_task_records(run_id)
        paired = 0
        rescue = 0
        regression = 0
        both_correct = 0
        both_wrong = 0
        skipped_nonvalid = 0
        for task_id, direct_record in direct.items():
            other_record = other.get(task_id)
            if not other_record:
                skipped_nonvalid += 1
                continue
            if not is_valid_scored(direct_record):
                skipped_nonvalid += 1
                continue
            if not is_valid_scored(other_record):
                skipped_nonvalid += 1
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
        out.append(
            {
                "harness": harness,
                "paired_valid_tasks": paired,
                "rescue": rescue,
                "regression": regression,
                "both_correct": both_correct,
                "both_wrong": both_wrong,
                "skipped_nonvalid_or_missing": skipped_nonvalid,
                "paired_net_gain": rescue - regression,
                "rescue_rate": rescue / paired if paired else float("nan"),
                "regression_rate": regression / paired if paired else float("nan"),
            }
        )
    return out


def load_action_sequences() -> dict[tuple[str, str], list[dict[str, str]]]:
    sequences: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(PHASE14_DIR / "action_events.csv"):
        if row["score_status"] != "valid_scored":
            continue
        sequences[(row["harness"], row["task_id"])].append(row)
    return sequences


def extract_action_space_distance() -> list[dict[str, object]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in read_csv(PHASE14_DIR / "action_events.csv"):
        if row["score_status"] != "valid_scored":
            continue
        counters[row["harness"]][row["canonical_action"]] += 1
    dists = {harness: distribution(counter) for harness, counter in counters.items()}
    harnesses = sorted(dists)
    out = []
    for i, left in enumerate(harnesses):
        for right in harnesses[i + 1 :]:
            support_left = {k for k, v in dists[left].items() if v > 0}
            support_right = {k for k, v in dists[right].items() if v > 0}
            union = support_left | support_right
            overlap = len(support_left & support_right) / len(union) if union else float("nan")
            out.append(
                {
                    "harness_a": left,
                    "harness_b": right,
                    "canonical_action_jsd": jsd(dists[left], dists[right]),
                    "action_support_overlap": overlap,
                    "support_a": " ".join(sorted(support_left)),
                    "support_b": " ".join(sorted(support_right)),
                }
            )
    return out


def extract_verification_conversion() -> list[dict[str, object]]:
    sequences = load_action_sequences()
    buckets: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for (harness, _task_id), events in sequences.items():
        events = sorted(events, key=lambda r: int(r["step_id"]) if r["step_id"].isdigit() else 0)
        actions = [event["canonical_action"] for event in events]
        correct = events[-1]["correct"]
        has_verify = "verify" in actions
        verify_before_answer = False
        post_verify_action_change = False
        if has_verify:
            first_verify = actions.index("verify")
            answer_positions = [i for i, action in enumerate(actions) if action == "answer"]
            verify_before_answer = any(pos > first_verify for pos in answer_positions)
            later_non_answer = {
                action
                for action in actions[first_verify + 1 :]
                if action not in {"verify", "answer"}
            }
            post_verify_action_change = bool(later_non_answer)
        buckets[(harness, correct)].append(
            {
                "has_verify": has_verify,
                "verify_before_answer": verify_before_answer,
                "post_verify_action_change": post_verify_action_change,
            }
        )

    out = []
    for (harness, correct), vals in sorted(buckets.items()):
        n = len(vals)
        out.append(
            {
                "harness": harness,
                "correct": correct,
                "n": n,
                "verify_rate": sum(v["has_verify"] for v in vals) / n,
                "verify_before_answer_rate": sum(v["verify_before_answer"] for v in vals) / n,
                "post_verify_action_change_rate": sum(v["post_verify_action_change"] for v in vals) / n,
            }
        )
    return out


def main() -> None:
    print("Mining general-purpose measurements...")
    tables = {
        "entropy_token_quadrants": extract_entropy_token_quadrants(),
        "confidence_inversion": extract_confidence_inversion(),
        "posttool_state_shift": extract_posttool_state_shift(),
        "operational_tax_adjusted_accuracy": extract_operational_tax(),
        "paired_net_gain": extract_paired_net_gain(),
        "action_space_distance": extract_action_space_distance(),
        "verification_conversion": extract_verification_conversion(),
    }
    fields = {
        "entropy_token_quadrants": [
            "bucket",
            "n",
            "wrong_rate",
            "median_tokens",
            "median_entropy",
            "median_wall_time_min",
            "token_threshold_q75",
            "entropy_threshold_q25",
        ],
        "confidence_inversion": [
            "entropy_threshold",
            "low_entropy_n",
            "high_entropy_n",
            "wrong_rate_low_entropy",
            "wrong_rate_high_entropy",
            "inversion_lift",
        ],
        "posttool_state_shift": [
            "turn_label",
            "correct_entropy",
            "wrong_entropy",
            "wrong_minus_correct_entropy",
            "separation_gain_vs_baseline",
            "correct_n",
            "wrong_n",
        ],
        "operational_tax_adjusted_accuracy": [
            "harness",
            "expected_samples",
            "task_records",
            "valid_scored",
            "correct",
            "error_records",
            "behavioral_accuracy",
            "operational_tax",
            "deployable_accuracy",
        ],
        "paired_net_gain": [
            "harness",
            "paired_valid_tasks",
            "rescue",
            "regression",
            "both_correct",
            "both_wrong",
            "skipped_nonvalid_or_missing",
            "paired_net_gain",
            "rescue_rate",
            "regression_rate",
        ],
        "action_space_distance": [
            "harness_a",
            "harness_b",
            "canonical_action_jsd",
            "action_support_overlap",
            "support_a",
            "support_b",
        ],
        "verification_conversion": [
            "harness",
            "correct",
            "n",
            "verify_rate",
            "verify_before_answer_rate",
            "post_verify_action_change_rate",
        ],
    }
    for name, rows in tables.items():
        write_csv(DATA_DIR / f"{name}.csv", rows, fields[name])
    write_json(DATA_DIR / "measurement_summary.json", tables)
    print("Done.")


if __name__ == "__main__":
    main()
